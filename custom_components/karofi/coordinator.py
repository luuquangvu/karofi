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
    DEFAULT_SCAN_INTERVAL,
    FILTER_LIFETIME_DAYS,
    MAX_VALID_TDS,
    NOTIFY_UUID_PREFIX,
    REQUEST_READ_DATA_COMMAND,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    TDS_PAYLOAD_KEYS,
    TDS_SENTINEL_ERROR,
    TDS_THRESHOLD_FAIR,
    TDS_THRESHOLD_GOOD,
    GattProperty,
    KarofiCharacteristic,
    KarofiFilter,
    KarofiShortUUID,
    MakiposEventType,
    MakiposPayloadKey,
    MakiposPropertyCode,
    WaterQuality,
)
from .models import KarofiData
from .util import hide_mac, mac_addresses_match, normalize_mac

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
            filter_reset_dates={
                k.value: dt_util.utcnow().isoformat() for k in FILTER_LIFETIME_DAYS
            },
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

    def _resolve_initial_dates(self, stored: object) -> tuple[dict[str, str], bool]:
        """Resolve filter dates from storage or initial config options.

        Args:
            stored: Loaded storage dictionary or None.

        Returns:
            Tuple of resolved filter reset dates mapping and save-required flag.
        """
        stored_dict = stored if isinstance(stored, dict) else {}
        initial_dates = self.entry.options.get("initial_filter_reset_dates", {})
        resolved: dict[str, str] = {}
        needs_save = not isinstance(stored, dict)

        for filter_stage in FILTER_LIFETIME_DAYS:
            key = filter_stage.value
            val = stored_dict.get(key)
            if isinstance(val, str) and dt_util.parse_datetime(val) is not None:
                resolved[key] = val
            else:
                resolved[key] = initial_dates.get(key, dt_util.utcnow().isoformat())
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

        if needs_save:
            await self._store.async_save(self.data.filter_reset_dates)

        self.async_update_listeners()

    async def async_reset_filter(self, filter_key: str) -> None:
        """Reset the installation date of a filter to now.

        Args:
            filter_key: The dictionary key of the filter to reset.
        """
        await self.async_set_filter_used_days(filter_key, 0)

    async def async_set_filter_date(self, filter_key: str, date: datetime | str) -> None:
        """Set the installation/replacement date of a filter.

        Args:
            filter_key: The dictionary key of the filter to calibrate.
            date: Datetime or ISO format string of the installation date.

        Raises:
            ValueError: If date string cannot be parsed.
        """
        if filter_key not in self.data.filter_reset_dates:
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

        iso_str = dt_util.as_utc(dt).isoformat()
        self.data.filter_reset_dates[filter_key] = iso_str
        await self._store.async_save(self.data.filter_reset_dates)
        self.async_set_updated_data(self.data)

    async def async_set_filter_used_days(self, filter_key: str, used_days: int) -> None:
        """Set the filter installation date based on days already in use.

        Args:
            filter_key: The dictionary key of the filter to calibrate.
            used_days: Number of days the filter has been in use.
        """
        if filter_key not in self.data.filter_reset_dates:
            return
        installation_date = dt_util.utcnow() - timedelta(days=max(0, used_days))
        await self.async_set_filter_date(filter_key, installation_date)

    async def async_set_filter_remaining_days(self, filter_key: str, remaining_days: int) -> None:
        """Set filter installation date based on remaining lifetime days.

        Args:
            filter_key: The dictionary key of the filter to calibrate.
            remaining_days: Remaining days before filter replacement.
        """
        if filter_key not in self.data.filter_reset_dates:
            return
        enum_key = KarofiFilter(filter_key)
        lifetime = FILTER_LIFETIME_DAYS.get(enum_key, 365)
        used_days = max(0, lifetime - remaining_days)
        await self.async_set_filter_used_days(filter_key, used_days)

    async def async_calibrate_all_filters(self, used_days_map: dict[str, int]) -> None:
        """Calibrate multiple filters at once by specifying days in use.

        Args:
            used_days_map: Mapping of filter stage key to days in use.
        """
        now = dt_util.utcnow()
        for key, used_days in used_days_map.items():
            if key in self.data.filter_reset_dates:
                inst_date = now - timedelta(days=max(0, used_days))
                self.data.filter_reset_dates[key] = inst_date.isoformat()

        await self._store.async_save(self.data.filter_reset_dates)
        self.async_set_updated_data(self.data)

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
        try:
            enum_stage: KarofiFilter | None = KarofiFilter(key)
        except ValueError:
            enum_stage = None

        if max_days is not None:
            lifetime = max_days
        elif enum_stage is not None:
            lifetime = FILTER_LIFETIME_DAYS.get(enum_stage, 365)
        else:
            lifetime = 365

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
        try:
            enum_stage: KarofiFilter | None = KarofiFilter(key)
        except ValueError:
            enum_stage = None

        if max_days is not None:
            lifetime = max_days
        elif enum_stage is not None:
            lifetime = FILTER_LIFETIME_DAYS.get(enum_stage, 365)
        else:
            lifetime = 365

        remaining = self.get_filter_remaining_days(filter_stage, max_days=lifetime)
        return max(0, min(100, round(remaining / lifetime * 100)))

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
                    if (
                        et in (MakiposEventType.NP, MakiposEventType.CP)
                        and MakiposPayloadKey.PROPERTY_CODE not in payload
                        and MakiposPayloadKey.IRO_TDS not in payload
                    ):
                        return True
        except ValueError, TypeError:
            pass

        return False

    def _find_tds_in_packets(self, packets: list[bytearray]) -> bytearray | None:
        """Find valid TDS payload from single packet or concatenated packets.

        Args:
            packets: List of raw bytearray packets.

        Returns:
            Matching bytearray or None.
        """
        for pkt in reversed(packets):
            if self._parse_tds_reading(pkt) is not None:
                return pkt
        if len(packets) > 1:
            combined = bytearray()
            for pkt in packets:
                combined.extend(pkt)
            if self._parse_tds_reading(combined) is not None:
                return combined
        return None

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
        if match := self._find_tds_in_packets(received_packets):
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
    ) -> tuple[bytearray | None, bytearray]:
        """Scan candidate characteristics under Karofi service for TDS readings.

        Args:
            client: Connected Bleak client instance.
            read_char: Initially resolved read characteristic.
            direct_bytes: Bytes already read in direct read step.

        Returns:
            Tuple of (valid_tds_bytes or None, fallback_payload).
        """
        services = getattr(client, "services", None)
        if not isinstance(services, BleakGATTServiceCollection):
            return None, bytearray()

        fallback_payload = bytearray()
        data_chars = self._get_readable_data_characteristics(client)
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
                if self._parse_tds_reading(cand_bytes) is not None:
                    self._known_tds_char_uuid = candidate.uuid
                    _LOGGER.debug(
                        "Discovered TDS sensor characteristic on %s: %s (%s ppm)",
                        self.masked_mac,
                        candidate.uuid,
                        self._parse_tds_reading(cand_bytes),
                    )
                    return cand_bytes, cand_bytes
                if not fallback_payload and not self._is_handshake_payload(cand_bytes):
                    fallback_payload = cand_bytes
        return None, fallback_payload

    async def _try_follow_up_read(
        self,
        client: BleakClientWithServiceCache,
        read_target: BleakGATTCharacteristic | str,
        can_read: bool,
        fallback_payload: bytearray,
    ) -> tuple[bytearray | None, bytearray]:
        """Perform follow-up read on read target after polling commands.

        Args:
            client: Connected Bleak client instance.
            read_target: Target characteristic to read.
            can_read: Whether characteristic supports reading.
            fallback_payload: Current fallback payload.

        Returns:
            Tuple of (valid_tds_bytes or None, updated fallback_payload).
        """
        if can_read and (not fallback_payload or self._is_handshake_payload(fallback_payload)):
            post_cmd_bytes = await self._read_characteristic_safe(client, read_target)
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
        if notify_bytes and self._parse_tds_reading(notify_bytes) is not None:
            _LOGGER.debug(
                "Active polling returned valid TDS reading (%s ppm)",
                self._parse_tds_reading(notify_bytes),
            )
            return notify_bytes, notify_bytes
        return None, notify_bytes

    async def _read_tds_characteristic(self, client: BleakClientWithServiceCache) -> bytearray:
        """Find and read the TDS sensor characteristic.

        Logs discovered GATT services and characteristics for debugging.
        Resolves the appropriate characteristics for READ, NOTIFY, and COMMAND.
        Attempts direct read first if characteristic has READ property.
        If direct read already returns valid TDS, returns it immediately.
        Otherwise, triggers the device via command write and notification subscription.
        If notifications do not arrive, attempts a follow-up read.
        Falls back to handshake bytes to maintain connection state if available.

        Args:
            client: Connected Bleak client instance.

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
            return tds_result

        # Step 2: Active polling via notification subscription and command request
        tds_result, notify_bytes = await self._try_active_polling(
            client, notify_target, cmd_target, read_target
        )
        if tds_result is not None:
            return tds_result

        # Step 3: Scan candidate data characteristics under service
        tds_result, cand_fallback = await self._scan_candidate_data_chars(
            client, read_char, direct_bytes
        )
        if tds_result is not None:
            return tds_result

        # Step 4: Follow-up read of read_target after command write
        fallback_payload = direct_bytes or cand_fallback
        tds_result, follow_up_fallback = await self._try_follow_up_read(
            client, read_target, can_read, fallback_payload
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

    def _process_tds_payload(self, raw_bytes: bytearray | bytes) -> None:
        """Process raw bytes payload from the TDS sensor.

        Args:
            raw_bytes: Payload bytes read from the characteristic.
        """
        if not raw_bytes or (b"{" not in raw_bytes and b"[" not in raw_bytes):
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

        payload = self._parse_json_payload(raw_bytes)
        if payload is None:
            _LOGGER.warning(
                "Received malformed JSON payload (raw: %s) from Karofi device %s",
                raw_bytes.hex(),
                self.masked_mac,
            )
            return

        if not self._has_tds_field(payload):
            _LOGGER.debug(
                "Payload did not contain a TDS reading (raw: %s, parsed: %s) from Karofi device %s",
                raw_bytes.hex(),
                payload,
                self.masked_mac,
            )
            return

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
            return

        self.data.tds = tds
        self.data.quality = self._classify_water_quality(tds)
        _LOGGER.debug(
            "Updated TDS reading for Karofi device %s: %d ppm (quality: %s)",
            self.masked_mac,
            self.data.tds,
            self.data.quality,
        )

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
            async with asyncio.timeout(BLE_TIMEOUT_SECONDS):
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    self.name,
                    max_attempts=BLE_CONNECT_ATTEMPTS,
                )
                try:
                    raw_bytes = await self._read_tds_characteristic(client)
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
