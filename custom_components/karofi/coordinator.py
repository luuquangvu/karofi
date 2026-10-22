"""DataUpdateCoordinator for Karofi integration."""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import TypeGuard

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.service import BleakGATTService, BleakGATTServiceCollection
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from habluetooth import BluetoothReachabilityIntent
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_address_reachability_diagnostics,
    async_ble_device_from_address,
    async_discovered_service_info,
    async_register_callback,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    BLE_CONNECT_ATTEMPTS,
    BLE_TIMEOUT_SECONDS,
    DEFAULT_FILTER_LIFETIME_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DIRECT_TDS_RETURN_RESERVE_SECONDS,
    MAX_VALID_TDS,
    NOTIFY_UUID_PREFIX,
    REQUEST_READ_DATA_COMMAND,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    SUPPLEMENTAL_TELEMETRY_TIMEOUT_SECONDS,
    TDS_PAYLOAD_KEYS,
    TDS_SENTINEL_ERROR,
    TDS_THRESHOLD_FAIR,
    TDS_THRESHOLD_GOOD,
    TELEMETRY_PAYLOAD_KEYS,
    GattProperty,
    KarofiCharacteristic,
    KarofiFilter,
    KarofiShortUUID,
    MakiposEventType,
    MakiposPayloadKey,
    MakiposPropertyCode,
    WaterQuality,
    build_reset_filter_command,
)
from .models import KarofiData, KarofiFilterData
from .util import (
    filter_index_to_key,
    filter_key_to_index,
    hide_mac,
    is_valid_filter_key,
    mac_addresses_match,
    normalize_mac,
)

_LOGGER = logging.getLogger(__name__)


class KarofiDataCoordinator(DataUpdateCoordinator[KarofiData]):
    """Coordinator to manage BLE connections and fetch Karofi water purifier status."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        mac_address: str,
        name: str,
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: HomeAssistant core instance.
            entry: Integration config entry.
            mac_address: Bluetooth MAC address of the Karofi device.
            name: Display name of the device.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{name} ({hide_mac(mac_address)})",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        self.mac_address = mac_address
        self.masked_mac: str = hide_mac(mac_address)
        self.device_name = name
        self._store: Store[dict[str, str]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry.entry_id}",
        )
        self.data = KarofiData(
            tds=None,
            quality=WaterQuality.UNKNOWN,
            connected=False,
            last_seen=None,
            filter_reset_dates={},
            filters={},
            errors=[],
        )
        self._known_tds_char_uuid: str | None = None
        self._mac_candidates: tuple[str, str] = (
            normalize_mac(mac_address, uppercase=True),
            normalize_mac(mac_address, uppercase=False),
        )

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """Handle bluetooth discovery and advertisement events.

        Args:
            service_info: Service info from Bluetooth scanner.
            change: Type of change event.
        """
        _LOGGER.debug(
            "Karofi advertisement seen for %s (RSSI: %s, change: %s)",
            hide_mac(service_info.address),
            service_info.rssi,
            change,
        )

    def _get_ble_device(self) -> BLEDevice | None:
        """Locate the BLEDevice across known address formats and active scanners.

        Returns:
            The discovered BLEDevice or None if currently unreachable.
        """
        for addr in self._mac_candidates:
            with contextlib.suppress(RuntimeError, KeyError):
                if dev := async_ble_device_from_address(self.hass, addr, connectable=True):
                    return dev

        with contextlib.suppress(RuntimeError, KeyError):
            for service_info in async_discovered_service_info(self.hass, connectable=True):
                if mac_addresses_match(service_info.address, self.mac_address):
                    return service_info.device

        return None

    @staticmethod
    def _parse_filter_dates_entry(
        data: object,
    ) -> list[tuple[str, str]]:
        """Parse raw mapping into validated (filter_key, val) tuples.

        Args:
            data: Raw dictionary or mapping object.

        Returns:
            List of tuples (filter_key, date_string).
        """
        if not isinstance(data, dict):
            return []
        entries: list[tuple[str, str]] = []
        for key, val in data.items():
            if not isinstance(key, str) or not isinstance(val, str):
                continue
            if is_valid_filter_key(key) and dt_util.parse_datetime(val) is not None:
                entries.append((key, val))
        return entries

    def _resolve_initial_dates(self, stored: object) -> tuple[dict[str, str], bool]:
        """Resolve filter dates from storage or initial config options.

        Args:
            stored: Loaded storage dictionary or None.

        Returns:
            Tuple of resolved filter reset dates mapping and save-required flag.
        """
        stored_dict = stored if isinstance(stored, dict) else {}
        initial_dates = self.entry.options.get("initial_filter_reset_dates", {})
        needs_save = False

        resolved: dict[str, str] = {}
        for key, val in self._parse_filter_dates_entry(stored_dict):
            if key not in resolved:
                resolved[key] = val

        if len(resolved) != len(stored_dict):
            needs_save = True

        for key, val in self._parse_filter_dates_entry(initial_dates):
            if key not in resolved:
                resolved[key] = val
                needs_save = True

        return resolved, needs_save

    async def async_setup(self) -> None:
        """Load persistent filter tracking dates and register active Bluetooth scanner."""
        try:
            for addr in self._mac_candidates:
                self.entry.async_on_unload(
                    async_register_callback(
                        self.hass,
                        self._async_handle_bluetooth_event,
                        BluetoothCallbackMatcher(address=addr, connectable=True),
                        BluetoothScanningMode.ACTIVE,
                    )
                )
        except RuntimeError, KeyError:
            _LOGGER.debug("Bluetooth manager not available to register active scanner callback")

        stored = await self._store.async_load()
        resolved_dates, needs_save = self._resolve_initial_dates(stored)
        self.data.filter_reset_dates.update(resolved_dates)

        for key in resolved_dates:
            if key not in self.data.filters:
                self.data.filters[key] = KarofiFilterData(
                    index=filter_key_to_index(key),
                    key=key,
                    lifetime_days=self.get_filter_lifetime_days(key),
                    reset_date=resolved_dates[key],
                )

        if needs_save:
            await self._store.async_save(self.data.filter_reset_dates)

        self.async_update_listeners()

    def get_known_filter_keys(self) -> list[str]:
        """Return list of known filter stage keys sorted by stage index.

        Returns:
            Sorted list of filter stage keys.
        """

        def _sort_key(k: str) -> int:
            try:
                return filter_key_to_index(k)
            except ValueError:
                return 999999

        known_keys = set(self.data.filters.keys()) | set(self.data.filter_reset_dates.keys())
        return sorted(known_keys, key=_sort_key)

    def get_filter_name(self, filter_stage: KarofiFilter | str) -> str | None:
        """Get dynamic filter name if provided by device telemetry.

        Args:
            filter_stage: Filter stage identifier or enum.

        Returns:
            Custom filter name string if provided, or None.
        """
        key = filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        return self.data.filters[key].name if key in self.data.filters else None

    def get_filter_lifetime_days(self, filter_stage: KarofiFilter | str) -> int:
        """Get rated lifetime in days for a filter stage.

        Args:
            filter_stage: Filter identifier or KarofiFilter enum.

        Returns:
            Lifetime in days from telemetry or default mapping.
        """
        key = filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        if key in self.data.filters and self.data.filters[key].lifetime_days is not None:
            lifetime = self.data.filters[key].lifetime_days
            if lifetime is not None and lifetime > 0:
                return lifetime
        return DEFAULT_FILTER_LIFETIME_DAYS.get(key, 365)

    def get_filter_remaining_days(
        self,
        filter_stage: KarofiFilter | str,
        max_days: int | None = None,
    ) -> int:
        """Calculate remaining days of filter lifespan.

        Args:
            filter_stage: Filter identifier or KarofiFilter enum.
            max_days: Optional rated lifespan in days overriding defaults.

        Returns:
            Remaining lifespan in days, clamped between 0 and max lifetime.
        """
        key = filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        lifetime = max_days if max_days is not None else self.get_filter_lifetime_days(key)
        if lifetime <= 0:
            return 0

        if (
            key in self.data.filters
            and self.data.filters[key].telemetry_synced
            and self.data.filters[key].remaining_days is not None
        ):
            rem_days = self.data.filters[key].remaining_days
            if rem_days is not None:
                return max(0, min(lifetime, rem_days))

        iso_date = self.data.filter_reset_dates.get(key)
        if not iso_date:
            return lifetime
        start_date = dt_util.parse_datetime(iso_date)
        if start_date is None:
            return lifetime
        used_days = (dt_util.utcnow() - dt_util.as_utc(start_date)).days
        return max(0, min(lifetime, lifetime - used_days))

    def get_filter_percent(
        self,
        filter_stage: KarofiFilter | str,
        max_days: int | None = None,
    ) -> int:
        """Calculate remaining percentage of filter lifespan.

        Args:
            filter_stage: Filter identifier or KarofiFilter enum.
            max_days: Optional rated lifespan in days overriding defaults.

        Returns:
            Remaining life percentage (0-100).
        """
        key = filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        if (
            key in self.data.filters
            and self.data.filters[key].telemetry_synced
            and self.data.filters[key].percent is not None
        ):
            pct = self.data.filters[key].percent
            if pct is not None:
                return max(0, min(100, pct))

        lifetime = max_days if max_days is not None else self.get_filter_lifetime_days(key)
        if lifetime <= 0:
            return 0
        remaining = self.get_filter_remaining_days(key, max_days=lifetime)
        return max(0, min(100, round(remaining / lifetime * 100)))

    async def _async_send_reset_command(self, filter_index: int) -> bool:
        """Send the BLE hardware reset command to the device.

        Args:
            filter_index: 0-based integer index of the filter.

        Returns:
            True if command was successfully transmitted, False otherwise.
        """
        ble_device = self._get_ble_device()
        if not ble_device:
            _LOGGER.debug(
                "Cannot send reset filter command to %s: device not reachable",
                self.masked_mac,
            )
            return False

        cmd = build_reset_filter_command(filter_index)
        try:
            async with asyncio.timeout(BLE_TIMEOUT_SECONDS):
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    self.name,
                    max_attempts=BLE_CONNECT_ATTEMPTS,
                )
                try:
                    _, notify_target, cmd_target, _, _ = self._resolve_io_targets(client)
                    actual_cmd_target = cmd_target or notify_target
                    return await self._write_command_with_fallback(client, actual_cmd_target, cmd)
                finally:
                    await client.disconnect()
        except (BleakError, OSError) as err:
            _LOGGER.debug(
                "Failed to send reset filter command to %s: %s",
                self.masked_mac,
                err,
            )
            return False

    async def async_reset_filter(self, filter_key: str) -> None:
        """Reset the installation date and telemetry of a filter to now.

        Args:
            filter_key: The dictionary key of the filter to reset.
        """
        try:
            filter_index = filter_key_to_index(filter_key)
        except ValueError:
            _LOGGER.warning("Cannot reset invalid filter key: %s", filter_key)
            return

        if not await self._async_send_reset_command(filter_index):
            _LOGGER.warning(
                "Hardware reset command failed for %s on device %s; updated local tracking only",
                filter_key,
                self.masked_mac,
            )

        if filter_key in self.data.filters:
            fdata = self.data.filters[filter_key]
            fdata.remaining_hours = fdata.lifetime_hours
            fdata.remaining_days = fdata.lifetime_days
            fdata.percent = 100

        await self.async_set_filter_used_days(filter_key, 0)

    def _apply_filter_installation_date(self, filter_key: str, dt: datetime) -> None:
        """Apply installation date and update or create filter record.

        Args:
            filter_key: The dictionary key of the filter stage.
            dt: Installation datetime.
        """
        iso_str = dt_util.as_utc(dt).isoformat()
        self.data.filter_reset_dates[filter_key] = iso_str
        lifetime = self.get_filter_lifetime_days(filter_key)
        used_days = (dt_util.utcnow() - dt_util.as_utc(dt)).days
        rem_days = max(0, min(lifetime, lifetime - max(0, used_days)))
        pct = max(0, min(100, round((rem_days / lifetime) * 100)))
        rem_hours = rem_days * 24

        if filter_key not in self.data.filters:
            self.data.filters[filter_key] = KarofiFilterData(
                index=filter_key_to_index(filter_key),
                key=filter_key,
                lifetime_days=lifetime,
                reset_date=iso_str,
                remaining_days=rem_days,
                percent=pct,
                remaining_hours=rem_hours,
                telemetry_synced=False,
            )
        else:
            fdata = self.data.filters[filter_key]
            fdata.reset_date = iso_str
            fdata.remaining_days = rem_days
            fdata.percent = pct
            fdata.remaining_hours = rem_hours
            fdata.telemetry_synced = False

    async def async_set_filter_date(self, filter_key: str, date: datetime | str) -> None:
        """Set the installation/replacement date of a filter.

        Args:
            filter_key: The dictionary key of the filter to calibrate.
            date: Datetime or ISO format string of the installation date.

        Raises:
            ValueError: If date string cannot be parsed.
        """
        if (
            filter_key not in self.data.filter_reset_dates
            and filter_key not in self.data.filters
            and not is_valid_filter_key(filter_key)
        ):
            return

        if isinstance(date, str):
            dt = dt_util.parse_datetime(date)
            if dt is None:
                parsed_date = dt_util.parse_date(date)
                if parsed_date is not None:
                    dt = dt_util.start_of_local_day(parsed_date)
            if dt is None:
                raise ValueError(f"Invalid date format: {date}")
        else:
            dt = date

        self._apply_filter_installation_date(filter_key, dt)
        await self._store.async_save(self.data.filter_reset_dates)
        self.async_set_updated_data(self.data)

    async def async_set_filter_used_days(self, filter_key: str, used_days: int) -> None:
        """Set the filter installation date based on days already in use.

        Args:
            filter_key: The dictionary key of the filter to calibrate.
            used_days: Number of days the filter has been in use.
        """
        installation_date = dt_util.utcnow() - timedelta(days=max(0, used_days))
        await self.async_set_filter_date(filter_key, installation_date)

    async def async_set_filter_remaining_days(self, filter_key: str, remaining_days: int) -> None:
        """Set filter installation date based on remaining lifetime days.

        Args:
            filter_key: The dictionary key of the filter to calibrate.
            remaining_days: Remaining days before filter replacement.
        """
        lifetime = self.get_filter_lifetime_days(filter_key)
        used_days = max(0, lifetime - remaining_days)
        if filter_key in self.data.filters:
            fdata = self.data.filters[filter_key]
            fdata.remaining_days = remaining_days
            fdata.percent = max(0, min(100, round((remaining_days / lifetime) * 100)))
            fdata.remaining_hours = remaining_days * 24
        await self.async_set_filter_used_days(filter_key, used_days)

    async def async_calibrate_all_filters(self, used_days_map: dict[str, int]) -> None:
        """Calibrate multiple filters at once by specifying days in use.

        Args:
            used_days_map: Mapping of filter stage key to days in use.
        """
        now = dt_util.utcnow()
        for key, used_days in used_days_map.items():
            if (
                key not in self.data.filter_reset_dates
                and key not in self.data.filters
                and not is_valid_filter_key(key)
            ):
                continue
            inst_date = now - timedelta(days=max(0, used_days))
            self._apply_filter_installation_date(key, inst_date)

        await self._store.async_save(self.data.filter_reset_dates)
        self.async_set_updated_data(self.data)

    def _log_discovered_services(self, client: BleakClientWithServiceCache) -> None:
        """Log discovered GATT services and characteristics for debugging.

        Args:
            client: Connected Bleak client instance.
        """
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return

        services = getattr(client, "services", None)
        if not isinstance(services, BleakGATTServiceCollection):
            _LOGGER.debug(
                "Device %s does not have an active BleakGATTServiceCollection",
                self.masked_mac,
            )
            return

        for service in services:
            chars_info = [f"{c.uuid} (props={c.properties})" for c in service.characteristics]
            _LOGGER.debug(
                "Discovered GATT service on %s: %s -> characteristics: %s",
                self.masked_mac,
                service.uuid,
                chars_info,
            )

    @staticmethod
    def _is_command_characteristic(char: BleakGATTCharacteristic | str) -> bool:
        """Check if characteristic is the Karofi command or OTA characteristic.

        Args:
            char: Characteristic object or UUID string.

        Returns:
            True if characteristic is a command/control channel.
        """
        uuid_str = getattr(char, "uuid", str(char)).lower()
        return (
            uuid_str == KarofiCharacteristic.NOTIFY.lower()
            or uuid_str == KarofiCharacteristic.COMMAND.lower()
            or uuid_str.startswith(NOTIFY_UUID_PREFIX)
            or uuid_str == KarofiShortUUID.NOTIFY.lower()
            or uuid_str == KarofiShortUUID.COMMAND.lower()
        )

    def _get_readable_data_characteristics(
        self, client: BleakClientWithServiceCache
    ) -> list[BleakGATTCharacteristic]:
        """Find readable data characteristics under Karofi service, prioritizing known TDS UUID.

        Args:
            client: Connected Bleak client instance.

        Returns:
            List of candidate BleakGATTCharacteristic instances.
        """
        services = getattr(client, "services", None)
        if not isinstance(services, BleakGATTServiceCollection):
            return []

        service = services.get_service(KarofiCharacteristic.SERVICE) or services.get_service(
            KarofiShortUUID.SERVICE
        )
        if service is None or not service.characteristics:
            return []

        candidates: list[BleakGATTCharacteristic] = []
        candidates.extend(
            char
            for char in service.characteristics
            if GattProperty.READ in char.properties and not self._is_command_characteristic(char)
        )
        known_uuid = self._known_tds_char_uuid
        if known_uuid is not None:
            candidates.sort(key=lambda c: 0 if c.uuid.lower() == known_uuid.lower() else 1)

        return candidates

    @staticmethod
    def _get_char_by_uuids(
        services: BleakGATTServiceCollection,
        uuids: tuple[str, ...],
    ) -> BleakGATTCharacteristic | None:
        """Look up characteristic by priority UUIDs.

        Args:
            services: Bleak service collection.
            uuids: Candidate UUID strings in priority order.

        Returns:
            First matching characteristic or None.
        """
        for candidate_uuid in uuids:
            with contextlib.suppress(BleakError):
                char = services.get_characteristic(candidate_uuid)
                if isinstance(char, BleakGATTCharacteristic):
                    return char
        return None

    @classmethod
    def _find_service_tds_candidate(
        cls,
        service: BleakGATTService | None,
    ) -> BleakGATTCharacteristic | None:
        """Find readable TDS candidate characteristic under Karofi service.

        Args:
            service: Discovered Karofi GATT service or None.

        Returns:
            Candidate characteristic or None.
        """
        if service is None or not service.characteristics:
            return None
        for candidate in service.characteristics:
            if GattProperty.READ in candidate.properties and not cls._is_command_characteristic(
                candidate
            ):
                return candidate
        return next(
            (
                candidate
                for candidate in service.characteristics
                if (
                    GattProperty.READ in candidate.properties
                    or GattProperty.NOTIFY in candidate.properties
                )
            ),
            None,
        )

    def _resolve_tds_characteristic(
        self, client: BleakClientWithServiceCache
    ) -> BleakGATTCharacteristic | None:
        """Locate TDS characteristic from client service collection.

        Args:
            client: Connected Bleak client instance.

        Returns:
            Resolved BleakGATTCharacteristic or None.
        """
        services = getattr(client, "services", None)
        if not isinstance(services, BleakGATTServiceCollection):
            return None

        priority_uuids = ((self._known_tds_char_uuid,) if self._known_tds_char_uuid else ()) + (
            KarofiCharacteristic.TDS_SENSOR,
            KarofiShortUUID.TDS_SENSOR,
        )
        if char := self._get_char_by_uuids(services, priority_uuids):
            return char

        service = services.get_service(KarofiCharacteristic.SERVICE) or services.get_service(
            KarofiShortUUID.SERVICE
        )
        return self._find_service_tds_candidate(service)

    @staticmethod
    def _find_char_by_property(
        services: BleakGATTServiceCollection,
        candidate_uuids: tuple[str, ...],
        properties: tuple[str, ...],
    ) -> BleakGATTCharacteristic | None:
        """Find characteristic matching candidate UUIDs or candidate service by property.

        Args:
            services: Service collection from connected client.
            candidate_uuids: Priority UUIDs to look up.
            properties: Properties to match against.

        Returns:
            Matching BleakGATTCharacteristic or None.
        """
        for uuid in candidate_uuids:
            with contextlib.suppress(BleakError):
                char = services.get_characteristic(uuid)
                if isinstance(char, BleakGATTCharacteristic) and any(
                    p in char.properties for p in properties
                ):
                    return char

        service = services.get_service(KarofiCharacteristic.SERVICE) or services.get_service(
            KarofiShortUUID.SERVICE
        )
        if service is not None:
            for char in service.characteristics:
                if any(p in char.properties for p in properties):
                    return char

        return None

    def _resolve_notify_characteristic(
        self, client: BleakClientWithServiceCache
    ) -> BleakGATTCharacteristic | None:
        """Locate GATT notification characteristic from client service collection.

        Args:
            client: Connected Bleak client instance.

        Returns:
            Resolved BleakGATTCharacteristic or None.
        """
        services = getattr(client, "services", None)
        if not isinstance(services, BleakGATTServiceCollection):
            return None

        return self._find_char_by_property(
            services,
            (
                KarofiCharacteristic.NOTIFY,
                KarofiShortUUID.NOTIFY,
                KarofiCharacteristic.TDS_SENSOR,
                KarofiShortUUID.TDS_SENSOR,
            ),
            (GattProperty.NOTIFY, GattProperty.INDICATE),
        )

    def _resolve_command_characteristic(
        self, client: BleakClientWithServiceCache
    ) -> BleakGATTCharacteristic | None:
        """Locate GATT command write characteristic from client service collection.

        Args:
            client: Connected Bleak client instance.

        Returns:
            Resolved BleakGATTCharacteristic or None.
        """
        services = getattr(client, "services", None)
        if not isinstance(services, BleakGATTServiceCollection):
            return None

        return self._find_char_by_property(
            services,
            (
                KarofiCharacteristic.NOTIFY,
                KarofiShortUUID.NOTIFY,
                KarofiCharacteristic.COMMAND,
                KarofiShortUUID.COMMAND,
                KarofiCharacteristic.TDS_SENSOR,
                KarofiShortUUID.TDS_SENSOR,
            ),
            (GattProperty.WRITE, GattProperty.WRITE_WITHOUT_RESPONSE),
        )

    @staticmethod
    async def _write_command_with_fallback(
        client: BleakClientWithServiceCache,
        target: BleakGATTCharacteristic | str,
        command: bytes,
    ) -> bool:
        """Write command bytes to target characteristic handling response properties.

        Args:
            client: Connected Bleak client instance.
            target: Characteristic object or UUID string to write command to.
            command: Command payload bytes to transmit.

        Returns:
            True if write succeeded, False otherwise.
        """
        props = target.properties if isinstance(target, BleakGATTCharacteristic) else []
        prefer_no_resp = (
            GattProperty.WRITE_WITHOUT_RESPONSE in props and GattProperty.WRITE not in props
        )

        try:
            await client.write_gatt_char(target, command, response=not prefer_no_resp)
            return True
        except BleakError, TypeError:
            try:
                await client.write_gatt_char(target, command, response=prefer_no_resp)
                return True
            except BleakError, OSError:
                return False
        except OSError:
            return False

    @staticmethod
    async def _read_characteristic_safe(
        client: BleakClientWithServiceCache,
        target: BleakGATTCharacteristic | str,
    ) -> bytearray:
        """Safely read characteristic bytes handling errors.

        Args:
            client: Connected Bleak client instance.
            target: Characteristic object or UUID string to read.

        Returns:
            Read bytes as bytearray, or empty bytearray on failure.
        """
        try:
            res = await client.read_gatt_char(target)
            if res:
                return bytearray(res)
        except (BleakError, OSError) as err:
            _LOGGER.debug(
                "Direct read of %s failed: %s",
                getattr(target, "uuid", target),
                err,
            )
        return bytearray()

    @staticmethod
    def _is_handshake_payload(raw_bytes: bytearray | bytes) -> bool:
        """Check if payload is a protocol handshake or command frame without sensor data.

        Args:
            raw_bytes: Payload bytes to check.

        Returns:
            True if the payload is a protocol handshake or polling packet without sensor data.
        """
        try:
            text = raw_bytes.decode("utf-8", errors="ignore")
            start_brace = text.find("{")
            end_brace = text.rfind("}")
            if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
                payload = json.loads(text[start_brace : end_brace + 1])
                if isinstance(payload, dict):
                    et = payload.get(MakiposPayloadKey.EVENT_TYPE)
                    return et in (MakiposEventType.NP, MakiposEventType.CP) and not any(
                        k in payload for k in TELEMETRY_PAYLOAD_KEYS
                    )
        except ValueError, TypeError:
            pass

        return False

    def _has_telemetry_payload(self, buffer: bytearray | bytes) -> bool:
        """Check if buffer contains valid TDS or filter telemetry payload.

        Args:
            buffer: Raw payload bytes to check.

        Returns:
            True if buffer contains parseable TDS or telemetry payload.
        """
        if self._parse_tds_reading(buffer) is not None:
            return True
        return any(
            self._has_filter_or_telemetry_field(p) for p in self._extract_all_payload_dicts(buffer)
        )

    def _find_tds_in_packets(self, packets: list[bytearray]) -> bytearray | None:
        """Find valid TDS or telemetry payload from single packet or concatenated packets.

        Args:
            packets: List of raw bytearray packets.

        Returns:
            Matching bytearray or None.
        """
        if len(packets) > 1:
            combined = bytearray()
            for pkt in packets:
                combined.extend(pkt)
            if self._has_telemetry_payload(combined):
                return combined
        return next(
            (pkt for pkt in reversed(packets) if self._has_telemetry_payload(pkt)),
            None,
        )

    def _find_tds_reading_in_packets(self, packets: list[bytearray]) -> bytearray | None:
        """Find packet or combined buffer containing a valid TDS reading.

        Args:
            packets: List of raw bytearray packets.

        Returns:
            Matching bytearray containing TDS reading or None.
        """
        if len(packets) > 1:
            combined = bytearray()
            for pkt in packets:
                combined.extend(pkt)
            if self._parse_tds_reading(combined) is not None:
                return combined
        return next(
            (pkt for pkt in reversed(packets) if self._parse_tds_reading(pkt) is not None),
            None,
        )

    @staticmethod
    def _resolve_notified_char(
        client: BleakClientWithServiceCache,
        target_id: str,
        default_target: BleakGATTCharacteristic | str,
    ) -> BleakGATTCharacteristic | str:
        """Resolve characteristic to read when device sends an ID update notification.

        Args:
            client: Connected Bleak client instance.
            target_id: Characteristic UUID fragment from device payload.
            default_target: Default characteristic if ID cannot be matched.

        Returns:
            Resolved BleakGATTCharacteristic or default target.
        """
        services = getattr(client, "services", None)
        if isinstance(services, BleakGATTServiceCollection):
            service = services.get_service(KarofiCharacteristic.SERVICE) or services.get_service(
                KarofiShortUUID.SERVICE
            )
            if service is not None:
                for ch in service.characteristics:
                    if target_id in ch.uuid.lower():
                        return ch
        return default_target

    def _check_and_schedule_id_read(
        self,
        client: BleakClientWithServiceCache,
        data: bytearray,
        default_target: BleakGATTCharacteristic | str,
        bg_tasks: set[asyncio.Task[None]],
        read_func: Callable[[BleakGATTCharacteristic | str], Coroutine[object, object, None]],
    ) -> None:
        """Check if notification data contains an ID key and schedule a read task.

        Args:
            client: Connected Bleak client instance.
            data: Raw notification bytes.
            default_target: Fallback read characteristic target.
            bg_tasks: Active background task set tracking spawned tasks.
            read_func: Async read callback to execute.
        """
        payload = self._parse_json_payload(data)
        if payload is not None and MakiposPayloadKey.ID in payload:
            target_id = str(payload[MakiposPayloadKey.ID]).lower()
            resolved = self._resolve_notified_char(client, target_id, default_target)
            _LOGGER.debug(
                "Device %s notified characteristic update for ID: %s; scheduling read of %s",
                self.masked_mac,
                target_id,
                getattr(resolved, "uuid", resolved),
            )
            task = asyncio.create_task(read_func(resolved))
            bg_tasks.add(task)
            task.add_done_callback(bg_tasks.discard)

    async def _send_read_request(
        self,
        client: BleakClientWithServiceCache,
        primary_target: BleakGATTCharacteristic | str,
        fallback_target: BleakGATTCharacteristic | str,
    ) -> None:
        """Transmit REQUEST_READ_DATA_COMMAND with fallback target if primary write fails.

        Args:
            client: Connected Bleak client instance.
            primary_target: Primary characteristic to write to.
            fallback_target: Fallback characteristic if primary write fails.
        """
        write_success = await self._write_command_with_fallback(
            client, primary_target, REQUEST_READ_DATA_COMMAND
        )
        _LOGGER.debug(
            "Sent REQUEST_READ_DATA_COMMAND to %s on %s (success=%s)",
            getattr(primary_target, "uuid", primary_target),
            self.masked_mac,
            write_success,
        )
        if not write_success and primary_target != fallback_target:
            await self._write_command_with_fallback(
                client, fallback_target, REQUEST_READ_DATA_COMMAND
            )

    async def _async_read_char_on_id_notify(
        self,
        client: BleakClientWithServiceCache,
        char_target: BleakGATTCharacteristic | str,
        received_packets: list[bytearray],
        notify_future: asyncio.Future[bytearray],
    ) -> None:
        """Read characteristic after receiving an ID notification from the device.

        Args:
            client: Connected Bleak client instance.
            char_target: Characteristic object or UUID string to read.
            received_packets: List tracking all raw packets received.
            notify_future: Future to resolve when valid TDS reading is received.
        """
        try:
            res = await client.read_gatt_char(char_target)
            if not res:
                return
            res_bytes = bytearray(res)
            received_packets.append(res_bytes)
            _LOGGER.debug(
                "Read char %s after ID notify (%d bytes, hex: %s, text: %r)",
                getattr(char_target, "uuid", char_target),
                len(res_bytes),
                res_bytes.hex(),
                res_bytes.decode("utf-8", errors="replace"),
            )
            if self._parse_tds_reading(res_bytes) is not None:
                self._known_tds_char_uuid = getattr(char_target, "uuid", str(char_target))
                if not notify_future.done():
                    notify_future.set_result(res_bytes)
        except (BleakError, OSError) as err:
            _LOGGER.debug("Read after ID notification failed on %s: %s", char_target, err)

    def _handle_notify_packet(
        self,
        client: BleakClientWithServiceCache,
        data: bytearray,
        target: BleakGATTCharacteristic | str,
        read_target: BleakGATTCharacteristic | str,
        received_packets: list[bytearray],
        notify_future: asyncio.Future[bytearray],
        bg_tasks: set[asyncio.Task[None]],
    ) -> None:
        """Process notification packet received from BLE device.

        Args:
            client: Connected Bleak client instance.
            data: Raw notification bytes received.
            target: Characteristic object or UUID subscribed to.
            read_target: Read characteristic target when ID notify arrives.
            received_packets: List tracking all received packets.
            notify_future: Future to resolve when valid TDS packet is received.
            bg_tasks: Active background task set tracking spawned tasks.
        """
        received_packets.append(data)
        _LOGGER.debug(
            "Received BLE notification on %s from device %s: %d bytes (hex: %s, text: %r)",
            getattr(target, "uuid", target),
            self.masked_mac,
            len(data),
            data.hex(),
            data.decode("utf-8", errors="replace"),
        )
        if match := self._find_tds_reading_in_packets(received_packets):
            if not notify_future.done():
                notify_future.set_result(match)
            return

        self._check_and_schedule_id_read(
            client,
            data,
            read_target,
            bg_tasks,
            lambda char: self._async_read_char_on_id_notify(
                client, char, received_packets, notify_future
            ),
        )

    @staticmethod
    async def _stop_notify_safe(
        client: BleakClientWithServiceCache,
        target: BleakGATTCharacteristic | str,
        masked_mac: str,
    ) -> None:
        """Stop BLE notifications on target characteristic without raising.

        Args:
            client: Connected Bleak client instance.
            target: Characteristic object or UUID to stop notifying.
            masked_mac: Masked MAC string for logging.
        """
        with contextlib.suppress(BleakError, OSError):
            await client.stop_notify(target)
            _LOGGER.debug(
                "Stopped notifications on %s for Karofi device %s",
                getattr(target, "uuid", target),
                masked_mac,
            )

    @staticmethod
    def _cleanup_bg_tasks(bg_tasks: set[asyncio.Task[None]]) -> None:
        """Cancel all pending background tasks.

        Args:
            bg_tasks: Set of asyncio tasks to cancel.
        """
        for bg_task in set(bg_tasks):
            if not bg_task.done():
                bg_task.cancel()

    def _resolve_notify_timeout(self, received_packets: list[bytearray]) -> bytearray:
        """Resolve timeout result from received packets.

        Args:
            received_packets: List of packets received before timeout.

        Returns:
            Matched TDS packet, last received packet, or empty bytearray.
        """
        _LOGGER.debug(
            "Timed out (3.5s) waiting for TDS notification from Karofi device %s",
            self.masked_mac,
        )
        if match := self._find_tds_in_packets(received_packets):
            return match
        return received_packets[-1] if received_packets else bytearray()

    async def _subscribe_tds_notification(
        self,
        client: BleakClientWithServiceCache,
        target: BleakGATTCharacteristic | str,
        cmd_target: BleakGATTCharacteristic | str | None = None,
        read_target: BleakGATTCharacteristic | str | None = None,
    ) -> bytearray:
        """Subscribe to TDS notifications and await sensor packet.

        Args:
            client: Connected Bleak client instance.
            target: Characteristic object or UUID string to subscribe to.
            cmd_target: Optional write characteristic to transmit request command to.
            read_target: Optional read characteristic to query when device requests read.

        Returns:
            Raw bytearray received from the device notification or subsequent read.
        """
        notify_future: asyncio.Future[bytearray] = asyncio.get_running_loop().create_future()
        received_packets: list[bytearray] = []
        bg_tasks: set[asyncio.Task[None]] = set()
        actual_cmd_target = cmd_target or target
        actual_read_target = read_target or target

        def _notify_callback(_: BleakGATTCharacteristic | int, data: bytearray) -> None:
            """Handle raw notification packet from BLE client."""
            self._handle_notify_packet(
                client,
                data,
                target,
                actual_read_target,
                received_packets,
                notify_future,
                bg_tasks,
            )

        subscribed = False
        try:
            await client.start_notify(target, _notify_callback)
            subscribed = True
            _LOGGER.debug(
                "Subscribed to notifications on %s for Karofi device %s",
                getattr(target, "uuid", target),
                self.masked_mac,
            )
        except (BleakError, OSError) as err:
            _LOGGER.debug(
                "Failed to subscribe to notifications on %s: %s; continuing with command write",
                getattr(target, "uuid", target),
                err,
            )

        try:
            await self._send_read_request(client, actual_cmd_target, target)

            if subscribed:
                async with asyncio.timeout(3.5):
                    return await notify_future
        except TimeoutError:
            return self._resolve_notify_timeout(received_packets)
        finally:
            self._cleanup_bg_tasks(bg_tasks)
            if subscribed:
                await self._stop_notify_safe(client, target, self.masked_mac)

        return bytearray()

    def _resolve_io_targets(
        self, client: BleakClientWithServiceCache
    ) -> tuple[
        BleakGATTCharacteristic | str,
        BleakGATTCharacteristic | str,
        BleakGATTCharacteristic | str,
        BleakGATTCharacteristic | None,
        bool,
    ]:
        """Resolve READ, NOTIFY, and COMMAND characteristics from connected client.

        Args:
            client: Connected Bleak client instance.

        Returns:
            Tuple of (read_target, notify_target, cmd_target, read_char, can_read).
        """
        read_char = self._resolve_tds_characteristic(client)
        notify_char = self._resolve_notify_characteristic(client)
        cmd_char = self._resolve_command_characteristic(client)

        read_target: BleakGATTCharacteristic | str = (
            read_char if read_char is not None else KarofiCharacteristic.TDS_SENSOR
        )
        if notify_char is not None:
            notify_target: BleakGATTCharacteristic | str = notify_char
        elif read_char is not None:
            notify_target = read_char
        else:
            notify_target = KarofiCharacteristic.NOTIFY

        if cmd_char is not None:
            cmd_target: BleakGATTCharacteristic | str = cmd_char
        elif notify_char is not None:
            cmd_target = notify_char
        else:
            cmd_target = KarofiCharacteristic.NOTIFY

        can_read = read_char is None or GattProperty.READ in getattr(read_char, "properties", [])
        return read_target, notify_target, cmd_target, read_char, can_read

    async def _try_direct_read(
        self,
        client: BleakClientWithServiceCache,
        read_target: BleakGATTCharacteristic | str,
        can_read: bool,
    ) -> tuple[bytearray | None, bytearray]:
        """Attempt direct read from target characteristic.

        Args:
            client: Connected Bleak client instance.
            read_target: Target characteristic to read.
            can_read: Whether characteristic supports direct read.

        Returns:
            Tuple of (valid_tds_bytes or None, raw_read_bytes).
        """
        if not can_read:
            return None, bytearray()
        direct_bytes = await self._read_characteristic_safe(client, read_target)
        if direct_bytes:
            _LOGGER.debug(
                "Direct read from %s returned %d bytes (hex: %s, text: %r)",
                getattr(read_target, "uuid", read_target),
                len(direct_bytes),
                direct_bytes.hex(),
                direct_bytes.decode("utf-8", errors="replace"),
            )
            if self._parse_tds_reading(direct_bytes) is not None:
                _LOGGER.debug(
                    "Direct read on %s provided valid TDS reading (%s ppm)",
                    getattr(read_target, "uuid", read_target),
                    self._parse_tds_reading(direct_bytes),
                )
                self._known_tds_char_uuid = getattr(read_target, "uuid", str(read_target))
                return direct_bytes, direct_bytes
        return None, direct_bytes

    async def _scan_candidate_data_chars(
        self,
        client: BleakClientWithServiceCache,
        read_char: BleakGATTCharacteristic | None,
        direct_bytes: bytearray,
        *,
        timeout_at: float | None = None,
    ) -> tuple[bytearray | None, bytearray]:
        """Scan service characteristics and collect TDS and filter telemetry.

        Args:
            client: Connected Bleak client instance.
            read_char: Initially resolved read characteristic.
            direct_bytes: Bytes already read in direct read step.
            timeout_at: Optional event loop time at which to stop scanning.

        Returns:
            Tuple of collected telemetry bytes or None and fallback payload.
        """
        services = getattr(client, "services", None)
        if not isinstance(services, BleakGATTServiceCollection):
            return None, bytearray()

        fallback_payload = bytearray()
        telemetry_payloads: list[bytearray] = []
        tds_payload: bytearray | None = None
        data_chars = self._get_readable_data_characteristics(client)
        try:
            async with asyncio.timeout_at(timeout_at):
                for candidate in data_chars:
                    if candidate == read_char and direct_bytes:
                        continue
                    cand_bytes = await self._read_characteristic_safe(client, candidate)
                    if cand_bytes:
                        _LOGGER.debug(
                            "Data characteristic %s read returned %d bytes (hex: %s, text: %r)",
                            candidate.uuid,
                            len(cand_bytes),
                            cand_bytes.hex(),
                            cand_bytes.decode("utf-8", errors="replace"),
                        )
                        if self._has_telemetry_payload(cand_bytes):
                            telemetry_payloads.append(cand_bytes)
                        if self._parse_tds_reading(cand_bytes) is not None:
                            self._known_tds_char_uuid = candidate.uuid
                            _LOGGER.debug(
                                "Discovered TDS sensor characteristic on %s: %s (%s ppm)",
                                self.masked_mac,
                                candidate.uuid,
                                self._parse_tds_reading(cand_bytes),
                            )
                            tds_payload = cand_bytes
                        if not fallback_payload and not self._is_handshake_payload(cand_bytes):
                            fallback_payload = cand_bytes
        except TimeoutError:
            _LOGGER.debug(
                "Timed out scanning data characteristics for %s; using telemetry collected so far",
                self.masked_mac,
            )
        if telemetry_payloads:
            return bytearray(b"\n".join(telemetry_payloads)), fallback_payload
        return tds_payload, fallback_payload

    async def _try_follow_up_read(
        self,
        client: BleakClientWithServiceCache,
        read_target: BleakGATTCharacteristic | str,
        can_read: bool,
        fallback_payload: bytearray,
        *,
        timeout_at: float | None = None,
    ) -> tuple[bytearray | None, bytearray]:
        """Perform follow-up read on read target after polling commands.

        Args:
            client: Connected Bleak client instance.
            read_target: Target characteristic to read.
            can_read: Whether characteristic supports reading.
            fallback_payload: Current fallback payload.
            timeout_at: Optional event loop time at which to stop reading.

        Returns:
            Tuple of (valid_tds_bytes or None, updated fallback_payload).
        """
        if can_read and (not fallback_payload or self._is_handshake_payload(fallback_payload)):
            try:
                async with asyncio.timeout_at(timeout_at):
                    post_cmd_bytes = await self._read_characteristic_safe(client, read_target)
            except TimeoutError:
                _LOGGER.debug(
                    "Timed out reading follow-up data for %s; keeping payload collected so far",
                    self.masked_mac,
                )
                return None, fallback_payload
            if post_cmd_bytes:
                _LOGGER.debug(
                    "Follow-up read of %s after polling returned %d bytes (hex: %s, text: %r)",
                    getattr(read_target, "uuid", read_target),
                    len(post_cmd_bytes),
                    post_cmd_bytes.hex(),
                    post_cmd_bytes.decode("utf-8", errors="replace"),
                )
                if self._parse_tds_reading(post_cmd_bytes) is not None:
                    return post_cmd_bytes, post_cmd_bytes
                if not fallback_payload:
                    fallback_payload = post_cmd_bytes
        return None, fallback_payload

    async def _try_active_polling(
        self,
        client: BleakClientWithServiceCache,
        notify_target: BleakGATTCharacteristic | str,
        cmd_target: BleakGATTCharacteristic | str,
        read_target: BleakGATTCharacteristic | str,
    ) -> tuple[bytearray | None, bytearray]:
        """Attempt active polling via notification subscription and command request.

        Args:
            client: Connected Bleak client instance.
            notify_target: Characteristic to subscribe for notifications.
            cmd_target: Characteristic to write read command to.
            read_target: Characteristic to read if ID notification arrives.

        Returns:
            Tuple of (valid_tds_bytes or None, raw_notify_bytes).
        """
        _LOGGER.debug(
            "Subscribing to notifications on %s and sending read command to %s for device %s",
            getattr(notify_target, "uuid", notify_target),
            getattr(cmd_target, "uuid", cmd_target),
            self.masked_mac,
        )
        notify_bytes = await self._subscribe_tds_notification(
            client, notify_target, cmd_target=cmd_target, read_target=read_target
        )
        if notify_bytes and self._has_telemetry_payload(notify_bytes):
            _LOGGER.debug(
                "Active polling returned valid telemetry payload (%d bytes, hex: %s)",
                len(notify_bytes),
                notify_bytes.hex(),
            )
            return notify_bytes, notify_bytes
        return None, notify_bytes

    async def _read_tds_characteristic(
        self,
        client: BleakClientWithServiceCache,
        *,
        deadline: float | None = None,
    ) -> bytearray:
        """Find and read the TDS sensor characteristic.

        Logs discovered GATT services and characteristics for debugging.
        Resolves the appropriate characteristics for READ, NOTIFY, and COMMAND.
        Attempts direct read first if characteristic has READ property.
        Reads filter telemetry characteristics even when direct TDS succeeds.
        Otherwise, triggers the device via command write and notification subscription.
        If notifications do not arrive, attempts a follow-up read.
        Falls back to handshake bytes to maintain connection state if available.

        Args:
            client: Connected Bleak client instance.
            deadline: Optional event loop time deadline for the full BLE update.

        Returns:
            Raw bytearray returned by characteristic read or notification.
        """
        self._log_discovered_services(client)

        (
            read_target,
            notify_target,
            cmd_target,
            read_char,
            can_read,
        ) = self._resolve_io_targets(client)
        _LOGGER.debug(
            "Resolved BLE characteristics for device %s: read=%s, notify=%s, cmd=%s",
            self.masked_mac,
            getattr(read_target, "uuid", read_target),
            getattr(notify_target, "uuid", notify_target),
            getattr(cmd_target, "uuid", cmd_target),
        )

        # Step 1: Direct read attempt if supported
        tds_result, direct_bytes = await self._try_direct_read(client, read_target, can_read)
        if tds_result is not None:
            # The purifier exposes filter state on separate characteristics. A TDS
            # direct read must not short-circuit the poll and discard that state.
            loop = asyncio.get_running_loop()
            remaining_seconds = deadline - loop.time() if deadline is not None else float("inf")
            supplemental_seconds = min(
                SUPPLEMENTAL_TELEMETRY_TIMEOUT_SECONDS,
                remaining_seconds - DIRECT_TDS_RETURN_RESERVE_SECONDS,
            )
            if supplemental_seconds <= 0:
                _LOGGER.debug(
                    "Skipping supplemental telemetry for %s to preserve direct TDS",
                    self.masked_mac,
                )
                return tds_result

            supplemental_deadline = loop.time() + supplemental_seconds
            telemetry_bytes = bytearray()
            try:
                async with asyncio.timeout_at(supplemental_deadline):
                    await self._send_read_request(client, cmd_target, notify_target)
                telemetry_bytes, _ = await self._scan_candidate_data_chars(
                    client,
                    read_char,
                    direct_bytes,
                    timeout_at=supplemental_deadline,
                )
            except (BleakError, OSError, TimeoutError) as err:
                _LOGGER.debug(
                    "Supplemental filter telemetry read failed for %s; keeping direct TDS: %s",
                    self.masked_mac,
                    err,
                )
            return (
                bytearray(b"\n".join((tds_result, telemetry_bytes)))
                if telemetry_bytes
                else tds_result
            )

        # Step 2: Active polling via notification subscription and command request
        polling_result, notify_bytes = await self._try_active_polling(
            client, notify_target, cmd_target, read_target
        )

        # Reserve time to return notification TDS if a candidate characteristic stalls.
        scan_timeout_at = (
            deadline - DIRECT_TDS_RETURN_RESERVE_SECONDS if deadline is not None else None
        )

        # Step 3: Scan candidate data characteristics under service
        candidate_result, cand_fallback = await self._scan_candidate_data_chars(
            client,
            read_char,
            direct_bytes,
            timeout_at=scan_timeout_at,
        )
        telemetry_payloads: list[bytearray] = []
        for payload in (polling_result, candidate_result, notify_bytes):
            if (
                payload
                and self._has_telemetry_payload(payload)
                and payload not in telemetry_payloads
            ):
                telemetry_payloads.append(payload)
        telemetry_bytes = bytearray(b"\n".join(telemetry_payloads))
        if telemetry_bytes and self._parse_tds_reading(telemetry_bytes) is None:
            follow_up_tds, _ = await self._try_follow_up_read(
                client,
                read_target,
                can_read,
                bytearray(),
                timeout_at=scan_timeout_at,
            )
            if follow_up_tds is not None:
                return bytearray(b"\n".join((follow_up_tds, telemetry_bytes)))
        if telemetry_bytes:
            return telemetry_bytes

        # Step 4: Follow-up read of read_target after command write
        fallback_payload = direct_bytes or cand_fallback
        tds_result, follow_up_fallback = await self._try_follow_up_read(
            client,
            read_target,
            can_read,
            fallback_payload,
            timeout_at=scan_timeout_at,
        )
        if tds_result is not None:
            return tds_result

        if fallback := (fallback_payload or follow_up_fallback or notify_bytes):
            _LOGGER.debug(
                "No sensor reading obtained; using fallback payload (%d bytes, hex: %s)",
                len(fallback),
                fallback.hex(),
            )
            return fallback

        _LOGGER.debug(
            "No data received from Karofi device %s via direct read or notification",
            self.masked_mac,
        )
        return bytearray()

    @staticmethod
    def _extract_dict_candidates(obj: object) -> list[dict[str, object]]:
        """Extract dictionary items from a parsed JSON object or array.

        Args:
            obj: Parsed JSON object (dict, list, or primitive).

        Returns:
            List of dictionary items found.
        """
        if isinstance(obj, dict):
            return [obj]
        if isinstance(obj, list):
            return [item for item in obj if isinstance(item, dict)]
        return []

    @staticmethod
    def _is_priority_tds_payload(item: dict[str, object]) -> bool:
        """Check if dictionary has high-priority TDS indicators.

        Args:
            item: Decoded JSON dictionary.

        Returns:
            True if dictionary targets a priority TDS property.
        """
        return item.get(MakiposPayloadKey.PROPERTY_CODE) == MakiposPropertyCode.IRO_TDS or any(
            k in item for k in TDS_PAYLOAD_KEYS
        )

    @classmethod
    def _parse_json_payload(cls, raw_bytes: bytearray | bytes) -> dict[str, object] | None:
        """Extract and parse JSON dictionary or array from raw bytes.

        Supports single frames, arrays, and concatenated multi-frame streams.

        Args:
            raw_bytes: Payload bytes to decode.

        Returns:
            Decoded dictionary or None if unparseable.
        """
        try:
            text = raw_bytes.decode("utf-8", errors="ignore").strip()
            if not text:
                return None

            decoder = json.JSONDecoder()
            idx = 0
            parsed_items: list[dict[str, object]] = []

            while idx < len(text):
                brace_pos = text.find("{", idx)
                bracket_pos = text.find("[", idx)
                starts = [p for p in (brace_pos, bracket_pos) if p != -1]
                if not starts:
                    break
                start = min(starts)
                try:
                    obj, end_offset = decoder.raw_decode(text[start:])
                    idx = start + end_offset
                    for candidate in cls._extract_dict_candidates(obj):
                        if cls._is_priority_tds_payload(candidate):
                            return candidate
                        parsed_items.append(candidate)
                except ValueError, TypeError:
                    idx = start + 1

            for item in parsed_items:
                if cls._has_tds_field(item):
                    return item

            return parsed_items[0] if parsed_items else None
        except ValueError, TypeError:
            return None

    @staticmethod
    def _has_tds_field(payload: dict[str, object]) -> bool:
        """Check if payload dictionary contains a TDS property code or key.

        Args:
            payload: Decoded JSON dictionary.

        Returns:
            True if dictionary targets a TDS reading.
        """
        if payload.get(MakiposPayloadKey.PROPERTY_CODE) == MakiposPropertyCode.IRO_TDS:
            return True
        if any(k in payload for k in TDS_PAYLOAD_KEYS):
            return True
        if (
            MakiposPayloadKey.DATA in payload
            and MakiposPayloadKey.EVENT_TYPE not in payload
            and MakiposPayloadKey.PROPERTY_CODE not in payload
        ):
            return set(payload.keys()).issubset({MakiposPayloadKey.DATA, MakiposPayloadKey.ID})
        return False

    @staticmethod
    def _extract_raw_val(payload: dict[str, object]) -> object:
        """Extract candidate raw TDS value from parsed dictionary payload.

        Args:
            payload: Decoded JSON dictionary.

        Returns:
            Raw value candidate or None.
        """
        if payload.get(MakiposPayloadKey.PROPERTY_CODE) == MakiposPropertyCode.IRO_TDS:
            return payload.get(MakiposPayloadKey.DATA)
        for key in TDS_PAYLOAD_KEYS:
            if key in payload:
                return payload.get(key)
        if (
            MakiposPayloadKey.DATA in payload
            and MakiposPayloadKey.EVENT_TYPE not in payload
            and MakiposPayloadKey.PROPERTY_CODE not in payload
            and set(payload.keys()).issubset({MakiposPayloadKey.DATA, MakiposPayloadKey.ID})
        ):
            return payload.get(MakiposPayloadKey.DATA)
        return None

    @staticmethod
    def _coerce_int(val: object) -> int | None:
        """Coerce list, string, or numeric value to an integer reading.

        Args:
            val: Raw value candidate to parse as integer.

        Returns:
            Parsed integer or None if invalid.
        """
        if isinstance(val, list):
            val = val[0] if val else None
        elif isinstance(val, str) and "," in val:
            parts = val.split(",")
            val = parts[0].strip() if parts else None

        if isinstance(val, (int, float, str)) and not isinstance(val, bool):
            try:
                return int(float(val))
            except ValueError, TypeError, OverflowError:
                return None
        return None

    @staticmethod
    def _is_valid_tds(tds: int | None) -> TypeGuard[int]:
        """Check if parsed TDS integer reading falls within valid limits.

        Args:
            tds: Candidate integer TDS reading.

        Returns:
            True if reading is within valid sensor bounds and not error sentinel.
        """
        return tds is not None and 0 <= tds <= MAX_VALID_TDS and tds != TDS_SENTINEL_ERROR

    @staticmethod
    def _classify_water_quality(tds: int) -> WaterQuality:
        """Classify water quality based on TDS measurement value.

        Args:
            tds: Water TDS reading in ppm.

        Returns:
            WaterQuality enum corresponding to threshold bands.
        """
        if tds < TDS_THRESHOLD_GOOD:
            return WaterQuality.GOOD
        return WaterQuality.FAIR if tds <= TDS_THRESHOLD_FAIR else WaterQuality.POOR

    def _parse_tds_reading(self, raw_bytes: bytearray | bytes) -> int | None:
        """Parse TDS reading from raw bytes JSON payload.

        Args:
            raw_bytes: Payload bytes read from characteristic or notification.

        Returns:
            TDS integer reading in ppm if validly decoded, None otherwise.
        """
        payload = self._parse_json_payload(raw_bytes)
        if payload is not None and self._has_tds_field(payload):
            tds = self._coerce_int(self._extract_raw_val(payload))
            if self._is_valid_tds(tds):
                return tds
        return None

    @classmethod
    def _parse_next_json_candidate(
        cls, decoder: json.JSONDecoder, text: str, idx: int
    ) -> tuple[list[dict[str, object]], int]:
        """Parse next JSON dictionary candidate from text starting at idx.

        Args:
            decoder: JSONDecoder instance.
            text: Raw decoded string.
            idx: Starting character index.

        Returns:
            Tuple of extracted dictionary candidates and next index.
        """
        brace_pos = text.find("{", idx)
        bracket_pos = text.find("[", idx)
        starts = [p for p in (brace_pos, bracket_pos) if p != -1]
        if not starts:
            return [], len(text)
        start = min(starts)
        try:
            obj, end_offset = decoder.raw_decode(text[start:])
            return cls._extract_dict_candidates(obj), start + end_offset
        except ValueError, TypeError:
            return [], start + 1

    @classmethod
    def _extract_all_payload_dicts(cls, raw_bytes: bytearray | bytes) -> list[dict[str, object]]:
        """Extract and parse all JSON dictionaries or array items from raw bytes.

        Supports single frames, arrays, and concatenated multi-frame streams.

        Args:
            raw_bytes: Payload bytes to decode.

        Returns:
            List of decoded dictionaries found in raw bytes.
        """
        try:
            text = raw_bytes.decode("utf-8", errors="ignore").strip()
            if not text:
                return []

            decoder = json.JSONDecoder()
            idx = 0
            parsed_items: list[dict[str, object]] = []

            while idx < len(text):
                candidates, next_idx = cls._parse_next_json_candidate(decoder, text, idx)
                parsed_items.extend(candidates)
                idx = next_idx

            return parsed_items
        except ValueError, TypeError:
            return []

    @staticmethod
    def _parse_int_item(item: object) -> int | None:
        """Parse a single item into an integer, or None if invalid.

        Args:
            item: Raw item to parse.

        Returns:
            Parsed integer value or None if invalid.
        """
        if isinstance(item, (int, float, str)) and not isinstance(item, bool):
            try:
                return int(float(item))
            except ValueError, TypeError, OverflowError:
                pass
        return None

    @classmethod
    def _coerce_int_list(
        cls, val: object, *, preserve_none: bool = False
    ) -> list[int | None] | None:
        """Coerce list or comma-separated string to a list of integers.

        Args:
            val: Raw value candidate to parse as integer list.
            preserve_none: If True, invalid entries retain their positions as None.

        Returns:
            Parsed list of integers or None if invalid.
        """
        raw_items: list[object]
        if isinstance(val, list):
            raw_items = val
        elif isinstance(val, str) and "," in val:
            raw_items = [part.strip() for part in val.split(",") if part.strip()]
        else:
            return None

        items: list[int | None] = []
        for item in raw_items:
            parsed = cls._parse_int_item(item)
            if parsed is not None:
                items.append(parsed)
            elif preserve_none:
                items.append(None)

        if not items or (preserve_none and all(x is None for x in items)):
            return None
        return items

    @staticmethod
    def _has_filter_or_telemetry_field(payload: dict[str, object]) -> bool:
        """Check if payload dictionary contains filter telemetry or errors.

        Args:
            payload: Decoded JSON dictionary.

        Returns:
            True if dictionary targets filter telemetry, lifetime, or error data.
        """
        prop_code = payload.get(MakiposPayloadKey.PROPERTY_CODE)
        if prop_code in (
            MakiposPropertyCode.IRO_RTR,
            MakiposPropertyCode.IRO_LTIME,
            MakiposPropertyCode.IRO_RTT,
            MakiposPropertyCode.IRO_ERRS,
        ):
            return True
        return any(
            k in payload
            for k in (
                MakiposPayloadKey.IRO_RTR,
                MakiposPayloadKey.IRO_LTIME,
                MakiposPayloadKey.IRO_RTT,
                MakiposPayloadKey.IRO_ERRS,
                MakiposPayloadKey.IRO_NAMES,
                MakiposPayloadKey.FILTER_NAMES,
                MakiposPayloadKey.NAMES,
            )
        )

    def _process_tds_reading_from_payload(
        self, payload: dict[str, object], raw_bytes: bytearray | bytes
    ) -> bool:
        """Process TDS measurement from a single dictionary payload.

        Args:
            payload: Parsed JSON payload dictionary.
            raw_bytes: Raw bytes for debug logging.

        Returns:
            True if a TDS field was found in the payload, False otherwise.
        """
        if not self._has_tds_field(payload):
            return False
        tds = self._coerce_int(self._extract_raw_val(payload))
        if not self._is_valid_tds(tds):
            _LOGGER.warning(
                "Received invalid TDS reading (%s ppm, raw: %s) from Karofi device %s",
                tds,
                raw_bytes.hex(),
                self.masked_mac,
            )
            self.data.tds = None
            self.data.quality = WaterQuality.UNKNOWN
        else:
            self.data.tds = tds
            self.data.quality = self._classify_water_quality(tds)
            _LOGGER.debug(
                "Updated TDS reading for Karofi device %s: %d ppm (quality: %s)",
                self.masked_mac,
                self.data.tds,
                self.data.quality,
            )
        return True

    @staticmethod
    def _get_payload_property_val(
        payload: dict[str, object], prop_code: MakiposPropertyCode, key: MakiposPayloadKey
    ) -> object:
        """Extract property value from payload either via property code or direct key.

        Args:
            payload: Decoded JSON dictionary.
            prop_code: Property code to match.
            key: Fallback direct key in payload.

        Returns:
            Property value object or None.
        """
        if payload.get(MakiposPayloadKey.PROPERTY_CODE) == prop_code:
            return payload.get(MakiposPayloadKey.DATA)
        return payload.get(key)

    def _update_single_filter_stage(
        self,
        idx: int,
        rtr_h: int | None,
        ltime_h: int | None,
        rtt_h: int | None,
        name: str | None = None,
    ) -> None:
        """Update telemetry, lifetime, and optional name for a single filter stage.

        Args:
            idx: 0-based filter stage index.
            rtr_h: Remaining hours or None.
            ltime_h: Lifetime hours or None.
            rtt_h: Total run hours or None.
            name: Optional dynamic filter name from device payload.
        """
        key = filter_index_to_key(idx)
        fdata = self.data.filters.setdefault(key, KarofiFilterData(index=idx, key=key))
        if name:
            fdata.name = name

        if rtr_h is not None:
            fdata.remaining_hours = rtr_h
            fdata.remaining_days = max(0, round(rtr_h / 24.0))
            fdata.telemetry_synced = True

        if ltime_h is not None:
            fdata.lifetime_hours = ltime_h
            fdata.lifetime_days = max(1, round(ltime_h / 24.0))
        elif fdata.lifetime_days is None:
            fdata.lifetime_days = self.get_filter_lifetime_days(key)

        if fdata.remaining_days is not None and fdata.lifetime_days:
            fdata.percent = max(
                0, min(100, round((fdata.remaining_days / fdata.lifetime_days) * 100))
            )

        if key not in self.data.filter_reset_dates and fdata.remaining_days is not None:
            life = fdata.lifetime_days or DEFAULT_FILTER_LIFETIME_DAYS.get(key, 365)
            used_days = max(0, life - fdata.remaining_days)
            self.data.filter_reset_dates[key] = (
                dt_util.utcnow() - timedelta(days=used_days)
            ).isoformat()

        if rtt_h is not None:
            fdata.total_hours = rtt_h

    @staticmethod
    def _extract_names_list(payload: dict[str, object]) -> list[str] | None:
        """Extract list of filter names from payload dictionary if present.

        Args:
            payload: Parsed JSON payload dictionary.

        Returns:
            List of string names or None.
        """
        for key in (
            MakiposPayloadKey.IRO_NAMES,
            MakiposPayloadKey.FILTER_NAMES,
            MakiposPayloadKey.NAMES,
        ):
            val = payload.get(key)
            if isinstance(val, list):
                return [str(item) for item in val]
        return None

    @staticmethod
    def _get_list_element[T](lst: list[T] | None, idx: int) -> T | None:
        """Safely fetch element from list by index.

        Args:
            lst: Optional list of elements.
            idx: 0-based integer index.

        Returns:
            Element at idx or None.
        """
        if lst and 0 <= idx < len(lst):
            return lst[idx]
        return None

    def _process_filter_stages_from_payload(self, payload: dict[str, object]) -> bool:
        """Process filter telemetry (rtr, lTime, rtt) and names from payload dictionary.

        Args:
            payload: Parsed JSON payload dictionary.

        Returns:
            True if filter telemetry was found and processed, False otherwise.
        """
        rtr_list = self._coerce_int_list(
            self._get_payload_property_val(
                payload, MakiposPropertyCode.IRO_RTR, MakiposPayloadKey.IRO_RTR
            ),
            preserve_none=True,
        )
        ltime_list = self._coerce_int_list(
            self._get_payload_property_val(
                payload, MakiposPropertyCode.IRO_LTIME, MakiposPayloadKey.IRO_LTIME
            ),
            preserve_none=True,
        )
        rtt_list = self._coerce_int_list(
            self._get_payload_property_val(
                payload, MakiposPropertyCode.IRO_RTT, MakiposPayloadKey.IRO_RTT
            ),
            preserve_none=True,
        )
        names_list = self._extract_names_list(payload)

        if rtr_list is None and ltime_list is None and names_list is None:
            return False

        filter_count = max(
            len(rtr_list) if rtr_list else 0,
            len(ltime_list) if ltime_list else 0,
            len(names_list) if names_list else 0,
        )
        for idx in range(filter_count):
            self._update_single_filter_stage(
                idx,
                self._get_list_element(rtr_list, idx),
                self._get_list_element(ltime_list, idx),
                self._get_list_element(rtt_list, idx),
                self._get_list_element(names_list, idx),
            )

        return True

    def _process_errors_from_payload(self, payload: dict[str, object]) -> bool:
        """Extract and store device error codes from payload dictionary.

        Args:
            payload: Parsed JSON payload dictionary.

        Returns:
            True if error codes were found and stored, False otherwise.
        """
        errs_val = self._get_payload_property_val(
            payload, MakiposPropertyCode.IRO_ERRS, MakiposPayloadKey.IRO_ERRS
        )
        if isinstance(errs_val, list) and not errs_val:
            self.data.errors = []
            return True
        if (errs_list := self._coerce_int_list(errs_val)) is not None:
            self.data.errors = [e for e in errs_list if e is not None]
            return True
        return False

    @staticmethod
    def _is_valid_json_buffer(raw_bytes: bytearray | bytes) -> bool:
        """Check if buffer is non-empty and contains JSON braces or brackets.

        Args:
            raw_bytes: Payload bytes to check.

        Returns:
            True if buffer appears to contain JSON.
        """
        return bool(raw_bytes and (b"{" in raw_bytes or b"[" in raw_bytes))

    def _process_all_payload_dicts(
        self, payload_dicts: list[dict[str, object]], raw_bytes: bytearray | bytes
    ) -> tuple[bool, bool]:
        """Process all decoded payload dictionaries.

        Args:
            payload_dicts: List of decoded dictionary frames.
            raw_bytes: Raw bytes for debug logging.

        Returns:
            Tuple of (has_sensor_data, filters_updated).
        """
        has_sensor_data = False
        filters_updated = False
        tds_payloads = [
            payload
            for payload in payload_dicts
            if self._has_tds_field(payload) and self._is_priority_tds_payload(payload)
        ]
        tds_payloads.extend(
            payload
            for payload in payload_dicts
            if self._has_tds_field(payload) and not self._is_priority_tds_payload(payload)
        )
        for payload in tds_payloads:
            if self._process_tds_reading_from_payload(payload, raw_bytes):
                has_sensor_data = True
                if self.data.tds is not None:
                    break
        for payload in payload_dicts:
            if self._process_filter_stages_from_payload(payload):
                has_sensor_data = True
                filters_updated = True
            if self._process_errors_from_payload(payload):
                has_sensor_data = True
        return has_sensor_data, filters_updated

    def _process_tds_payload(self, raw_bytes: bytearray | bytes) -> None:
        """Process raw bytes payload from the TDS sensor and purifier telemetry.

        Args:
            raw_bytes: Payload bytes read from the characteristic.
        """
        if not self._is_valid_json_buffer(raw_bytes):
            self.data.connected = False
            _LOGGER.warning(
                "Received invalid non-JSON payload (%d bytes, raw: %s) from Karofi device %s",
                len(raw_bytes),
                raw_bytes.hex() if raw_bytes else "",
                self.masked_mac,
            )
            return

        self.data.connected = True
        self.data.last_seen = dt_util.utcnow()

        if self._is_handshake_payload(raw_bytes):
            _LOGGER.debug(
                "Received Karofi protocol polling ACK/heartbeat (raw: %s) from device %s",
                raw_bytes.hex(),
                self.masked_mac,
            )
            return

        payload_dicts = self._extract_all_payload_dicts(raw_bytes)
        if not payload_dicts:
            _LOGGER.warning(
                "Received malformed JSON payload (raw: %s) from Karofi device %s",
                raw_bytes.hex(),
                self.masked_mac,
            )
            return

        reset_dates_snapshot = dict(self.data.filter_reset_dates)
        has_sensor_data, filters_updated = self._process_all_payload_dicts(payload_dicts, raw_bytes)

        if not has_sensor_data:
            _LOGGER.debug(
                "Payload did not contain sensor or filter data (raw: %s) from Karofi device %s",
                raw_bytes.hex(),
                self.masked_mac,
            )

        if filters_updated and self.data.filter_reset_dates != reset_dates_snapshot:
            with contextlib.suppress(RuntimeError, AttributeError):
                self._store.async_delay_save(lambda: self.data.filter_reset_dates, 10)

    async def _async_update_data(self) -> KarofiData:
        """Query BLE data via ESPHome or local Bluetooth proxy.

        Returns:
            Updated KarofiData object with the latest readings.
        """
        ble_device = self._get_ble_device()
        if not ble_device:
            self.data.connected = False
            diag = "Unavailable"
            with contextlib.suppress(RuntimeError, KeyError):
                diag = async_address_reachability_diagnostics(
                    self.hass,
                    normalize_mac(self.mac_address, uppercase=True),
                    BluetoothReachabilityIntent.CONNECTION,
                )
            _LOGGER.debug(
                "BLE Device %s is not currently reachable by any active Bluetooth Proxy (%s)",
                self.masked_mac,
                diag,
            )
            return self.data

        try:
            async with asyncio.timeout(BLE_TIMEOUT_SECONDS) as ble_timeout:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    self.name,
                    max_attempts=BLE_CONNECT_ATTEMPTS,
                )
                try:
                    raw_bytes = await self._read_tds_characteristic(
                        client, deadline=ble_timeout.when()
                    )
                    self._process_tds_payload(raw_bytes)
                finally:
                    await client.disconnect()
        except TimeoutError:
            _LOGGER.warning(
                "Timed out after %ss connecting or reading Karofi device %s",
                BLE_TIMEOUT_SECONDS,
                self.masked_mac,
            )
            self.data.connected = False
        except (BleakError, OSError) as err:
            if self.data.connected:
                _LOGGER.warning(
                    "Lost connection or failed reading Karofi device %s: %s",
                    self.masked_mac,
                    err,
                )
            else:
                _LOGGER.debug(
                    "Error connecting or reading Karofi characteristic on %s: %s",
                    self.masked_mac,
                    err,
                )
            self.data.connected = False

        return self.data
