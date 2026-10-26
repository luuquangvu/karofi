"""DataUpdateCoordinator for Karofi integration."""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass, field
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
    async_register_advertisement_callback,
    async_register_callback,
    async_track_unavailable,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    BLE_COMMAND_CHUNK_SIZE,
    BLE_CONNECT_ATTEMPTS,
    BLE_TIMEOUT_SECONDS,
    DEFAULT_FILTER_LIFETIME_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DIRECT_TDS_RETURN_RESERVE_SECONDS,
    FILTER_DATA_STALE_AFTER_SECONDS,
    FULL_TELEMETRY_SCAN_INTERVAL,
    MAX_FILTER_STAGES,
    MAX_TELEMETRY_CACHE_ENTRIES,
    MAX_VALID_TDS,
    NOTIFICATION_TELEMETRY_GRACE_SECONDS,
    NOTIFICATION_WAIT_SECONDS,
    NOTIFY_UUID_PREFIX,
    PENDING_RESET_STORAGE_SUFFIX,
    REQUEST_READ_DATA_COMMAND,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    SUPPLEMENTAL_TELEMETRY_TIMEOUT_SECONDS,
    SUPPORTED_EVENT_TYPES,
    SUPPORTED_PAYLOAD_KEYS,
    SUPPORTED_PROPERTY_CODES,
    TDS_PAYLOAD_KEYS,
    TDS_SENTINEL_ERROR,
    TDS_THRESHOLD_FAIR,
    TDS_THRESHOLD_GOOD,
    TELEMETRY_PAYLOAD_KEYS,
    WARNING_LOG_INTERVAL_SECONDS,
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
from .models import (
    KarofiData,
    KarofiFilterData,
    PendingFilterReset,
    PendingResetOutcome,
    ResolvedGattTargets,
)
from .util import (
    filter_index_to_key,
    filter_key_to_index,
    hide_mac,
    is_valid_filter_key,
    mac_addresses_match,
    normalize_mac,
)

_LOGGER = logging.getLogger(__name__)
RESET_CONFIRMATION_POLLS = 3
RESET_COMMAND_SETTLE_SECONDS = 1.0
MAX_TRACKED_UNKNOWN_PAYLOAD_FIELDS = 256


@dataclass
class _TelemetryScanState:
    """Accumulated state while scanning GATT characteristics."""

    telemetry_payloads: list[bytearray] = field(default_factory=list)
    tds_payload: bytearray | None = None
    fallback_payload: bytearray = field(default_factory=bytearray)
    discovered_char_uuids: list[str] = field(default_factory=list)
    failed_char_uuids: list[str] = field(default_factory=list)
    filter_telemetry_found: bool = False


@dataclass
class _NotificationWaitState:
    """State for a notification subscription waiting for TDS or telemetry."""

    notify_future: asyncio.Future[bytearray]
    telemetry: bytearray | None = None
    timer: asyncio.TimerHandle | None = None
    allow_early_telemetry_return: bool = True
    pending_id_reads: int = 0


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
        masked_mac = hide_mac(mac_address)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{name} ({masked_mac})",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        self.mac_address = mac_address
        self.masked_mac: str = masked_mac
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
            filters_last_seen=None,
            filter_reset_dates={},
            filters={},
            errors=[],
            signal_strength=None,
        )
        self._known_tds_char_uuid: str | None = None
        self._telemetry_char_uuids: list[str] = []
        self._polls_since_full_scan = 0
        self._ble_lock = asyncio.Lock()
        self._latest_advertisement_time: float | None = None
        self._debug_log_times: dict[str, float] = {}
        self._warning_log_times: dict[str, float] = {}
        self._logged_unknown_payload_fields: dict[str, None] = {}
        self._pending_unknown_payload_fields: dict[str, str] = {}
        self._unknown_payload_flush_handle: asyncio.TimerHandle | None = None
        self._telemetry_generation = 0
        self._pending_reset_store: Store[dict[str, int | None]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}_{entry.entry_id}_{PENDING_RESET_STORAGE_SUFFIX}",
        )
        self._pending_filter_resets: dict[str, PendingFilterReset] = {}
        self._mac_candidates: tuple[str, str] = (
            normalize_mac(mac_address, uppercase=True),
            normalize_mac(mac_address, uppercase=False),
        )

    def _debug_log_due(self, category: str) -> bool:
        """Return whether a rate-limited debug message may be emitted now.

        Background Bluetooth callbacks can run far more often than the regular
        poll. Each diagnostics category is therefore limited to at most one
        message per ``DEFAULT_SCAN_INTERVAL`` seconds.

        Args:
            category: Cooldown bucket identifying the debug message category.

        Returns:
            True when the caller may emit the category's debug message.
        """
        now = time.monotonic()
        last_logged = self._debug_log_times.get(category)
        if last_logged is not None and now - last_logged < DEFAULT_SCAN_INTERVAL:
            return False
        self._debug_log_times[category] = now
        return True

    def _warning_due(self, category: str) -> bool:
        """Return whether a rate-limited warning may be emitted now.

        Invalid device payloads can persist across every poll. Warnings are
        therefore limited to one message per category per
        ``WARNING_LOG_INTERVAL_SECONDS`` so a persistent fault does not flood
        the log; repeat occurrences are emitted at debug level by the caller.

        Args:
            category: Cooldown bucket identifying the warning category.

        Returns:
            True when the caller may emit the category's warning message.
        """
        now = time.monotonic()
        last_logged = self._warning_log_times.get(category)
        if last_logged is not None and now - last_logged < WARNING_LOG_INTERVAL_SECONDS:
            return False
        self._warning_log_times[category] = now
        return True

    def is_filter_data_stale(self, filter_key: str | None = None) -> bool:
        """Return whether filter telemetry is older than the stale threshold.

        Args:
            filter_key: Optional filter stage key. When provided, freshness is
                evaluated from that stage's dynamic telemetry timestamp; when
                omitted, the most recent dynamic telemetry from any stage is
                used.

        Returns:
            True when the relevant dynamic filter telemetry is older than
            ``FILTER_DATA_STALE_AFTER_SECONDS``, or when a filter record exists
            but no dynamic filter telemetry has arrived since startup.
        """
        if filter_key is not None:
            fdata = self.data.filters.get(filter_key)
            if fdata is None:
                return True
            filters_last_seen = fdata.last_seen
            if filters_last_seen is None:
                return True
        else:
            filters_last_seen = self.data.filters_last_seen
            if filters_last_seen is None:
                # last_seen also advances for TDS/handshake payloads, so it
                # cannot prove filter telemetry is fresh. Stored filters are
                # stale until the first dynamic filter-bearing payload arrives.
                return bool(self.data.filters)
        age = (dt_util.utcnow() - dt_util.as_utc(filters_last_seen)).total_seconds()
        return age > FILTER_DATA_STALE_AFTER_SECONDS

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange = BluetoothChange.ADVERTISEMENT,
    ) -> None:
        """Update the cached advertisement RSSI without publishing every change.

        RSSI fluctuates by a few dBm between advertisements, so prompting the
        coordinator to notify listeners here would write a state update for
        every packet. The first reading is published immediately to make the
        sensor available; later readings are coalesced into the coordinator's
        regular refresh, which runs every ``DEFAULT_SCAN_INTERVAL`` seconds.
        Advertisement diagnostics are likewise limited to one message per
        polling interval.

        Args:
            service_info: Scanner report containing the device's RSSI in dBm.
            change: Type of change event.
        """
        rssi = service_info.rssi
        if rssi == 0:
            return

        advertisement_time = service_info.time
        if (
            self._latest_advertisement_time is not None
            and advertisement_time < self._latest_advertisement_time
        ):
            return

        self._latest_advertisement_time = advertisement_time
        had_signal_strength = self.data.signal_strength is not None
        self.data.signal_strength = rssi
        if not had_signal_strength:
            # Publish the first valid reading immediately so the diagnostic
            # sensor becomes available without waiting for the next poll.
            self.async_update_listeners()

        if _LOGGER.isEnabledFor(logging.DEBUG) and self._debug_log_due("advertisement"):
            _LOGGER.debug(
                "Karofi advertisement for %s: RSSI %s dBm (change: %s)",
                self.masked_mac,
                rssi,
                change,
            )

    @callback
    def _async_handle_bluetooth_unavailable(self, service_info: BluetoothServiceInfoBleak) -> None:
        """Clear RSSI when advertisements for the device expire.

        Args:
            service_info: Last service info for the unavailable device.
        """
        if _LOGGER.isEnabledFor(logging.DEBUG) and self._debug_log_due("advertisement"):
            _LOGGER.debug("Karofi advertisements expired for %s", self.masked_mac)
        if self.data.signal_strength is not None:
            self.data.signal_strength = None
            # Expiry is a one-off transition, so publish it immediately.
            self.async_update_listeners()

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
        """Resolve filter dates from persistent storage.

        Args:
            stored: Loaded storage dictionary or None.

        Returns:
            Tuple of resolved filter reset dates mapping and save-required flag.
        """
        stored_dict = stored if isinstance(stored, dict) else {}
        resolved: dict[str, str] = {}
        for key, val in self._parse_filter_dates_entry(stored_dict):
            if key not in resolved:
                resolved[key] = val

        needs_save = len(resolved) != len(stored_dict)
        return resolved, needs_save

    def _async_register_bluetooth_callbacks(self, address: str) -> None:
        """Register Bluetooth callbacks for one address without cross-failure.

        Args:
            address: Device address to track.
        """
        registrations: tuple[tuple[str, Callable[[], Callable[[], None]]], ...] = (
            (
                "active scan",
                lambda: async_register_callback(
                    self.hass,
                    self._async_handle_bluetooth_event,
                    BluetoothCallbackMatcher(address=address, connectable=True),
                    BluetoothScanningMode.ACTIVE,
                ),
            ),
            (
                "advertisement",
                lambda: async_register_advertisement_callback(
                    self.hass,
                    self._async_handle_bluetooth_event,
                    address,
                ),
            ),
            (
                "unavailable",
                lambda: async_track_unavailable(
                    self.hass,
                    self._async_handle_bluetooth_unavailable,
                    address,
                    connectable=False,
                ),
            ),
        )
        for description, register in registrations:
            try:
                self.entry.async_on_unload(register())
            except RuntimeError, KeyError:
                _LOGGER.debug(
                    "Bluetooth manager not available to register %s callback for %s",
                    description,
                    hide_mac(address),
                )

    async def async_shutdown(self) -> None:
        """Cancel pending diagnostics and shut down the coordinator."""
        if self._unknown_payload_flush_handle is not None:
            self._unknown_payload_flush_handle.cancel()
            self._unknown_payload_flush_handle = None
        self._pending_unknown_payload_fields.clear()
        await super().async_shutdown()

    async def async_setup(self) -> None:
        """Restore filter tracking state and register Bluetooth signal tracking.

        Request active scanning and subscribe to advertisements and their expiry,
        with callback cleanup on config entry unload. Registration failures are
        isolated per callback so filter restoration can continue.
        Restore filter dates and pending resets, save corrected state when needed,
        and notify listeners. Errors from loading storage propagate to the caller.
        """
        for addr in self._mac_candidates:
            # Keep the active-scan request; per-advertisement callbacks do not provide it.
            self._async_register_bluetooth_callbacks(addr)

        stored = await self._store.async_load()
        resolved_dates, needs_save = self._resolve_initial_dates(stored)
        self.data.filter_reset_dates.update(resolved_dates)

        for key in resolved_dates:
            if key not in self.data.filters:
                self.data.filters[key] = KarofiFilterData(
                    lifetime_days=self.get_filter_lifetime_days(key),
                )

        stored_pending_resets = await self._pending_reset_store.async_load()
        valid_pending_resets: dict[str, int | None] = {}
        if isinstance(stored_pending_resets, dict):
            for key, lifetime_hours in stored_pending_resets.items():
                if (
                    not isinstance(key, str)
                    or not is_valid_filter_key(key)
                    or (
                        lifetime_hours is not None
                        and (
                            not isinstance(lifetime_hours, int)
                            or isinstance(lifetime_hours, bool)
                            or lifetime_hours <= 0
                        )
                    )
                ):
                    continue

                if key not in resolved_dates:
                    reset_date = dt_util.utcnow()
                    resolved_dates[key] = reset_date.isoformat()
                    self.data.filter_reset_dates[key] = resolved_dates[key]
                    self.data.filters[key] = KarofiFilterData(
                        lifetime_days=self.get_filter_lifetime_days(key),
                    )
                    needs_save = True
                else:
                    reset_date = dt_util.parse_datetime(resolved_dates[key])
                if reset_date is None:
                    continue

                fdata = self.data.filters[key]
                if lifetime_hours is not None:
                    fdata.lifetime_hours = lifetime_hours
                    fdata.lifetime_days = max(1, round(lifetime_hours / 24.0))
                self._apply_filter_installation_date(key, reset_date)
                if lifetime_hours is not None:
                    fdata.remaining_hours = lifetime_hours
                    fdata.remaining_days = fdata.lifetime_days
                    fdata.percent = 100
                self._pending_filter_resets[key] = PendingFilterReset(
                    generation=self._telemetry_generation,
                    lifetime_hours=lifetime_hours,
                )
                valid_pending_resets[key] = lifetime_hours

        if stored_pending_resets is not None and valid_pending_resets != stored_pending_resets:
            await self._pending_reset_store.async_save(valid_pending_resets)

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
        supported_keys: set[str] = set()
        for key in known_keys:
            try:
                index = filter_key_to_index(key)
            except ValueError:
                continue
            if index < MAX_FILTER_STAGES:
                supported_keys.add(key)
        return sorted(supported_keys, key=_sort_key)

    def _get_pending_reset_storage(self) -> dict[str, int | None]:
        """Return pending reset metadata for persistent storage."""
        return {
            key: pending_reset.lifetime_hours
            for key, pending_reset in self._pending_filter_resets.items()
        }

    async def _async_save_filter_tracking(self) -> None:
        """Persist filter installation dates."""
        await self._store.async_save(self.data.filter_reset_dates)

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

    def _remaining_days_from_reset_date(self, key: str, lifetime: int) -> int | None:
        """Return calendar remaining days derived from the stored reset date.

        Args:
            key: Filter stage key.
            lifetime: Rated lifespan in days.

        Returns:
            Remaining days from the reset date, or None when no valid date exists.
        """
        iso_date = self.data.filter_reset_dates.get(key)
        if not iso_date:
            return None
        start_date = dt_util.parse_datetime(iso_date)
        if start_date is None:
            return None
        used_days = (dt_util.utcnow() - dt_util.as_utc(start_date)).days
        return max(0, min(lifetime, lifetime - used_days))

    def get_filter_remaining_days(
        self,
        filter_stage: KarofiFilter | str,
        max_days: int | None = None,
    ) -> int:
        """Calculate remaining days of filter lifespan.

        While a reset is pending, the optimistic device value is combined with
        the value derived from the stored reset date so the sensor keeps aging
        even if the device never confirms the reset.

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

        pending_filter = self.data.filters.get(key)
        pending_remaining = pending_filter.remaining_days if pending_filter is not None else None
        if key in self._pending_filter_resets and pending_remaining is not None:
            remaining_days: int = pending_remaining
            date_based = self._remaining_days_from_reset_date(key, lifetime)
            if date_based is not None:
                remaining_days = min(remaining_days, date_based)
            return max(0, min(lifetime, remaining_days))

        if (
            key in self.data.filters
            and self.data.filters[key].telemetry_synced
            and self.data.filters[key].remaining_days is not None
        ):
            rem_days = self.data.filters[key].remaining_days
            if rem_days is not None:
                return max(0, min(lifetime, rem_days))

        date_based = self._remaining_days_from_reset_date(key, lifetime)
        return lifetime if date_based is None else date_based

    def get_filter_calendar_percent(
        self,
        filter_stage: KarofiFilter | str,
        max_days: int | None = None,
    ) -> int:
        """Calculate remaining calendar-lifetime percentage of a filter.

        Args:
            filter_stage: Filter identifier or KarofiFilter enum.
            max_days: Optional rated lifespan in days overriding defaults.

        Returns:
            Remaining calendar life percentage (0-100).
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

    def get_filter_percent(
        self,
        filter_stage: KarofiFilter | str,
        max_days: int | None = None,
    ) -> int:
        """Calculate the effective remaining percentage of a filter.

        The effective value is the lower of the calendar-lifetime percentage and
        the runtime-usage percentage. Runtime telemetry is optional, so filters
        without it keep using calendar life alone.

        Args:
            filter_stage: Filter identifier or KarofiFilter enum.
            max_days: Optional rated lifespan in days overriding defaults.

        Returns:
            Effective remaining life percentage (0-100).
        """
        key = filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        calendar_percent = self.get_filter_calendar_percent(filter_stage, max_days)

        # During a pending reset the calendar value is optimistic and runtime
        # telemetry may still be stale. Keep the effective value on the calendar
        # path until the pending reset clears or is abandoned.
        if key in self._pending_filter_resets:
            return calendar_percent

        runtime_percent = self.get_filter_runtime_percent(key)
        if runtime_percent is None:
            return calendar_percent
        return min(calendar_percent, runtime_percent)

    def get_filter_runtime_percent(self, filter_stage: KarofiFilter | str) -> int | None:
        """Get the remaining runtime-usage percentage of a filter.

        Args:
            filter_stage: Filter identifier or KarofiFilter enum.

        Returns:
            Remaining runtime percentage (0-100), or None when unavailable.
        """
        key = filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        fdata = self.data.filters.get(key)
        if fdata is None or fdata.runtime_percent is None:
            return None
        return max(0, min(100, fdata.runtime_percent))

    def get_filter_runtime_remaining_hours(self, filter_stage: KarofiFilter | str) -> int | None:
        """Get the remaining runtime hours reported for a filter.

        Args:
            filter_stage: Filter identifier or KarofiFilter enum.

        Returns:
            Remaining runtime hours, or None when unavailable.
        """
        key = filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        fdata = self.data.filters.get(key)
        return fdata.runtime_remaining_hours if fdata is not None else None

    def get_filter_runtime_total_hours(self, filter_stage: KarofiFilter | str) -> int | None:
        """Get the rated runtime hours reported for a filter.

        Args:
            filter_stage: Filter identifier or KarofiFilter enum.

        Returns:
            Rated runtime hours, or None when unavailable.
        """
        key = filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        fdata = self.data.filters.get(key)
        return fdata.runtime_total_hours if fdata is not None else None

    async def _async_send_reset_command(self, filter_index: int) -> bool:
        """Send the BLE hardware reset command to the device.

        Serializes with the polling session so a reset cannot race the regular
        coordinator refresh for the device's BLE connection.

        Args:
            filter_index: 0-based integer index of the filter.

        Returns:
            True if command was successfully transmitted, False otherwise.
        """
        async with self._ble_lock:
            return await self._async_send_reset_command_locked(filter_index)

    async def _async_send_reset_command_locked(self, filter_index: int) -> bool:
        """Send the BLE hardware reset command while holding the session lock.

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
                    targets = self._resolve_io_targets(client)
                    notify_target = targets.notify_target
                    cmd_target = targets.command_target
                    # The vendor application writes commands to its notify/write
                    # characteristic (ac02), not the separate WiFi command
                    # characteristic (ab02).
                    reset_target = (
                        notify_target
                        if isinstance(notify_target, BleakGATTCharacteristic)
                        and (
                            GattProperty.WRITE in notify_target.properties
                            or GattProperty.WRITE_WITHOUT_RESPONSE in notify_target.properties
                        )
                        else cmd_target or notify_target
                    )
                    reset_notify_target = (
                        reset_target
                        if isinstance(reset_target, BleakGATTCharacteristic)
                        and GattProperty.NOTIFY in reset_target.properties
                        else notify_target
                    )
                    if (
                        isinstance(reset_notify_target, BleakGATTCharacteristic)
                        and GattProperty.NOTIFY in reset_notify_target.properties
                    ):
                        try:
                            await client.start_notify(
                                reset_notify_target,
                                self._log_reset_notification,
                            )
                        except (BleakError, OSError) as err:
                            _LOGGER.debug(
                                "Could not enable reset notifications on %s for %s: %s",
                                reset_notify_target.uuid,
                                self.masked_mac,
                                err,
                            )
                        else:
                            _LOGGER.debug(
                                "Enabled reset notifications on %s for %s",
                                reset_notify_target.uuid,
                                self.masked_mac,
                            )

                    # The vendor app subscribes to the notification characteristic
                    # and waits briefly before issuing reset commands.
                    await asyncio.sleep(RESET_COMMAND_SETTLE_SECONDS)
                    _LOGGER.debug(
                        "Sending reset command for filter_%d to %s via %s (hex=%s): %s",
                        filter_index + 1,
                        self.masked_mac,
                        getattr(reset_target, "uuid", reset_target),
                        cmd.hex(),
                        cmd.decode("utf-8", errors="replace"),
                    )
                    write_succeeded = await self._write_command_with_fallback(
                        client,
                        reset_target,
                        cmd,
                        prefer_with_response=True,
                        chunked=False,
                    )
                    if write_succeeded:
                        # Keep the connection open briefly after the write so the
                        # BLE stack can finish transmitting before disconnecting.
                        await asyncio.sleep(RESET_COMMAND_SETTLE_SECONDS)
                    _LOGGER.debug(
                        "Reset command write for filter_%d to %s via %s completed (success=%s)",
                        filter_index + 1,
                        self.masked_mac,
                        getattr(reset_target, "uuid", reset_target),
                        write_succeeded,
                    )
                    return write_succeeded
                finally:
                    await client.disconnect()
        except (BleakError, OSError) as err:
            _LOGGER.debug(
                "Failed to send reset filter command to %s: %s",
                self.masked_mac,
                err,
            )
            return False

    def _log_reset_notification(
        self,
        characteristic: BleakGATTCharacteristic,
        data: bytearray,
    ) -> None:
        """Log command-characteristic notifications received during a reset."""
        _LOGGER.debug(
            "Reset characteristic notification from %s via %s (%d bytes, hex=%s, text=%s)",
            self.masked_mac,
            characteristic.uuid,
            len(data),
            bytes(data).hex(),
            data.decode("utf-8", errors="replace"),
        )

    async def async_reset_filter(self, filter_key: str) -> None:
        """Reset the installation date and telemetry of a filter to now.

        Args:
            filter_key: The dictionary key of the filter to reset.

        Raises:
            HomeAssistantError: If the reset command cannot be sent to the device.
        """
        try:
            filter_index = filter_key_to_index(filter_key)
        except ValueError:
            _LOGGER.warning("Cannot reset invalid filter key: %s", filter_key)
            return

        if not await self._async_send_reset_command(filter_index):
            _LOGGER.warning(
                "Hardware reset command failed for %s on device %s",
                filter_key,
                self.masked_mac,
            )
            raise HomeAssistantError(f"Failed to send reset command for {filter_key}")

        fdata = self.data.filters.get(filter_key)
        lifetime_hours = (
            fdata.lifetime_hours
            if fdata is not None and fdata.lifetime_hours is not None and fdata.lifetime_hours > 0
            else self.get_filter_lifetime_days(filter_key) * 24
        )
        existing_pending = self._pending_filter_resets.get(filter_key)
        if existing_pending is not None:
            baseline_hours = existing_pending.baseline_hours
        else:
            baseline_hours = fdata.remaining_hours if fdata is not None else None
        self._apply_filter_installation_date(filter_key, dt_util.utcnow())
        runtime_reset_applied = self._apply_runtime_reset(filter_key)
        self._pending_filter_resets[filter_key] = PendingFilterReset(
            generation=self._telemetry_generation,
            lifetime_hours=lifetime_hours,
            baseline_hours=baseline_hours,
            runtime_reset_applied=runtime_reset_applied,
        )
        await self._pending_reset_store.async_save(self._get_pending_reset_storage())
        await self._async_save_filter_tracking()
        self.async_set_updated_data(self.data)

    def _apply_filter_installation_date(
        self,
        filter_key: str,
        dt: datetime,
    ) -> None:
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
                lifetime_days=lifetime,
                remaining_days=rem_days,
                percent=pct,
                remaining_hours=rem_hours,
                telemetry_synced=False,
            )
        else:
            fdata = self.data.filters[filter_key]
            fdata.remaining_days = rem_days
            fdata.percent = pct
            fdata.remaining_hours = rem_hours
            fdata.telemetry_synced = False

    def _apply_runtime_reset(self, filter_key: str) -> bool:
        """Optimistically restore runtime capacity after a successful reset.

        The vendor app sends a single cartridge-reset command and trusts the
        device to reset both the calendar and runtime counters. When the rated
        runtime capacity is known, mirror that expectation locally until fresh
        telemetry arrives.

        Args:
            filter_key: Filter stage key that was reset.

        Returns:
            True when an optimistic runtime value was applied, False otherwise.
        """
        fdata = self.data.filters.get(filter_key)
        if fdata is None:
            return False
        total = fdata.runtime_total_hours
        if total is None or total <= 0:
            return False
        fdata.runtime_remaining_hours = total
        fdata.runtime_percent = 100
        return True

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

        command_service = services.get_service(
            KarofiCharacteristic.COMMAND_SERVICE
        ) or services.get_service(KarofiShortUUID.COMMAND_SERVICE)
        if command_service is not None:
            command_char = next(
                (
                    char
                    for char in command_service.characteristics
                    if char.uuid.lower() == KarofiCharacteristic.COMMAND.lower()
                    and (
                        GattProperty.WRITE in char.properties
                        or GattProperty.WRITE_WITHOUT_RESPONSE in char.properties
                    )
                ),
                None,
            )
            if command_char is not None:
                return command_char

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
        *,
        prefer_with_response: bool = False,
        chunked: bool = True,
    ) -> bool:
        """Write command bytes to target characteristic handling response properties.

        Args:
            client: Connected Bleak client instance.
            target: Characteristic object or UUID string to write command to.
            command: Command payload bytes to transmit.
            prefer_with_response: Prefer an acknowledged write when the characteristic
                supports it. Characteristic properties always take precedence.
            chunked: Split the command into BLE-sized writes. Disable this when the
                peripheral expects the full frame in a single GATT write.

        Returns:
            True if write succeeded, False otherwise.
        """
        props = target.properties if isinstance(target, BleakGATTCharacteristic) else []
        supports_write = GattProperty.WRITE in props
        supports_write_without_response = GattProperty.WRITE_WITHOUT_RESPONSE in props
        if supports_write or supports_write_without_response:
            prefer_no_resp = supports_write_without_response and (
                not supports_write or not prefer_with_response
            )
        else:
            prefer_no_resp = not prefer_with_response
        response_mode = not prefer_no_resp

        chunks = (
            [
                command[offset : offset + BLE_COMMAND_CHUNK_SIZE]
                for offset in range(0, len(command), BLE_COMMAND_CHUNK_SIZE)
            ]
            if chunked
            else [command]
        )
        _LOGGER.debug(
            "Writing %d bytes to %s in %d chunk(s) using response=%s (chunked=%s, properties=%s)",
            len(command),
            getattr(target, "uuid", target),
            len(chunks),
            response_mode,
            chunked,
            props,
        )

        for chunk_index, chunk in enumerate(chunks, start=1):
            try:
                await client.write_gatt_char(target, chunk, response=response_mode)
                _LOGGER.debug(
                    "BLE write chunk %d/%d to %s completed with response=%s",
                    chunk_index,
                    len(chunks),
                    getattr(target, "uuid", target),
                    response_mode,
                )
            except (BleakError, TypeError) as err:
                _LOGGER.debug(
                    "BLE write chunk %d/%d to %s with response=%s failed: %s; "
                    "trying the other mode",
                    chunk_index,
                    len(chunks),
                    getattr(target, "uuid", target),
                    response_mode,
                    err,
                )
                try:
                    await client.write_gatt_char(target, chunk, response=not response_mode)
                    _LOGGER.debug(
                        "BLE write chunk %d/%d to %s completed with response=%s",
                        chunk_index,
                        len(chunks),
                        getattr(target, "uuid", target),
                        not response_mode,
                    )
                except (BleakError, OSError) as fallback_err:
                    _LOGGER.debug(
                        "BLE write chunk %d/%d to %s with response=%s also failed: %s",
                        chunk_index,
                        len(chunks),
                        getattr(target, "uuid", target),
                        not response_mode,
                        fallback_err,
                    )
                    return False
                response_mode = not response_mode
            except OSError:
                return False

        return True

    @staticmethod
    async def _read_characteristic_for_scan(
        client: BleakClientWithServiceCache,
        target: BleakGATTCharacteristic | str,
    ) -> tuple[bytearray, bool]:
        """Read a characteristic and report whether the transport read succeeded.

        Args:
            client: Connected Bleak client instance.
            target: Characteristic object or UUID string to read.

        Returns:
            Tuple of read bytes (empty when the characteristic had no data) and
            whether the BLE read completed without a transport error.
        """
        try:
            res = await client.read_gatt_char(target)
            return (bytearray(res) if res else bytearray()), True
        except (BleakError, OSError) as err:
            _LOGGER.debug(
                "Direct read of %s failed: %s",
                getattr(target, "uuid", target),
                err,
            )
            return bytearray(), False

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
        data, _ = await KarofiDataCoordinator._read_characteristic_for_scan(client, target)
        return data

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

    def _has_filter_telemetry_payload(self, buffer: bytearray | bytes) -> bool:
        """Check whether a buffer contains filter or error telemetry.

        Args:
            buffer: Raw payload bytes to check.

        Returns:
            True when the buffer contains a filter or error telemetry field.
        """
        return any(
            self._has_filter_or_telemetry_field(payload)
            for payload in self._extract_all_payload_dicts(buffer)
        )

    @staticmethod
    def _find_packet_match(
        packets: list[bytearray],
        predicate: Callable[[bytearray], bool],
    ) -> bytearray | None:
        """Find a packet or concatenated stream matching a predicate.

        Args:
            packets: List of raw bytearray packets.
            predicate: Callable deciding whether a buffer contains the wanted data.

        Returns:
            Combined buffer, matching single packet, or None.
        """
        if len(packets) > 1:
            combined = bytearray()
            for pkt in packets:
                combined.extend(pkt)
            if predicate(combined):
                return combined
        return next((pkt for pkt in reversed(packets) if predicate(pkt)), None)

    def _find_tds_in_packets(self, packets: list[bytearray]) -> bytearray | None:
        """Find valid TDS or telemetry payload from single packet or concatenated packets.

        Args:
            packets: List of raw bytearray packets.

        Returns:
            Matching bytearray or None.
        """
        return self._find_packet_match(packets, self._has_telemetry_payload)

    def _find_tds_reading_in_packets(self, packets: list[bytearray]) -> bytearray | None:
        """Find packet or combined buffer containing a valid TDS reading.

        Args:
            packets: List of raw bytearray packets.

        Returns:
            Matching bytearray containing TDS reading or None.
        """
        return self._find_packet_match(
            packets,
            lambda payload: self._parse_tds_reading(payload) is not None,
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
        wait_state: _NotificationWaitState | None = None,
    ) -> None:
        """Check if notification data contains an ID key and schedule a read task.

        Args:
            client: Connected Bleak client instance.
            data: Raw notification bytes.
            default_target: Fallback read characteristic target.
            bg_tasks: Active background task set tracking spawned tasks.
            read_func: Async read callback to execute.
            wait_state: Optional notification wait state used to defer the
                filter-telemetry grace timer until the read completes.
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
            if wait_state is not None:
                wait_state.pending_id_reads += 1

                def _mark_id_read_done(_: asyncio.Task[None]) -> None:
                    """Release the deferred grace timer when the read completes."""
                    wait_state.pending_id_reads = max(0, wait_state.pending_id_reads - 1)

                task.add_done_callback(_mark_id_read_done)

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
        wait_state: _NotificationWaitState,
    ) -> None:
        """Read characteristic after receiving an ID notification from the device.

        Args:
            client: Connected Bleak client instance.
            char_target: Characteristic object or UUID string to read.
            received_packets: List tracking all raw packets received.
            notify_future: Future to resolve when valid TDS reading is received.
            wait_state: Per-subscription state holding buffered telemetry.
        """
        try:
            res = await client.read_gatt_char(char_target)
            if not res:
                return
            res_bytes = bytearray(res)
            received_packets.append(res_bytes)
            self._log_unknown_payload_fields(res_bytes)
            _LOGGER.debug(
                "Read char %s after ID notify (%d bytes, hex: %s, text: %r)",
                getattr(char_target, "uuid", char_target),
                len(res_bytes),
                res_bytes.hex(),
                res_bytes.decode("utf-8", errors="replace"),
            )
            if self._parse_tds_reading(res_bytes) is not None:
                self._log_assembled_notification_fields(received_packets)
                self._known_tds_char_uuid = getattr(char_target, "uuid", str(char_target))
                if wait_state.timer is not None:
                    wait_state.timer.cancel()
                    wait_state.timer = None
                if not notify_future.done():
                    # Preserve filter telemetry buffered during the grace
                    # window when the follow-up read supplies TDS.
                    notify_future.set_result(
                        bytearray(b"\n".join((wait_state.telemetry, res_bytes)))
                        if wait_state.telemetry is not None
                        else res_bytes
                    )
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
        wait_state: _NotificationWaitState,
    ) -> None:
        """Process notification packet received from BLE device.

        Args:
            client: Connected Bleak client instance.
            data: Raw notification bytes received.
            target: Characteristic object or UUID subscribed to.
            read_target: Read characteristic target when ID notify arrives.
            received_packets: List tracking all received packets.
            notify_future: Future to resolve when valid TDS or telemetry data is received.
            bg_tasks: Active background task set tracking spawned tasks.
            wait_state: Per-subscription state for the telemetry grace window.
        """
        received_packets.append(data)
        self._log_unknown_payload_fields(data)
        if _LOGGER.isEnabledFor(logging.DEBUG) and self._debug_log_due("notification"):
            _LOGGER.debug(
                "Received BLE notification on %s from device %s: %d bytes (hex: %s, text: %r)",
                getattr(target, "uuid", target),
                self.masked_mac,
                len(data),
                data.hex(),
                data.decode("utf-8", errors="replace"),
            )
        if match := self._find_tds_reading_in_packets(received_packets):
            self._log_assembled_notification_fields(received_packets)
            if wait_state.timer is not None:
                wait_state.timer.cancel()
                wait_state.timer = None
            if not notify_future.done():
                notify_future.set_result(match)
            return

        # Filter telemetry is useful even when this poll never reports TDS.
        # Do not resolve immediately: keep listening for a short grace window
        # so a TDS frame later in the same poll still wins. If no TDS arrives,
        # the grace timer resolves with the latest filter telemetry.
        if match := self._find_tds_in_packets(received_packets):
            self._log_assembled_notification_fields(received_packets)
            if wait_state.notify_future.done():
                return
            # Always retain the latest filter telemetry: an ID-triggered read
            # may later supply TDS and must return both frames. The flag only
            # controls whether the grace timer may resolve the wait early.
            wait_state.telemetry = match
            if wait_state.allow_early_telemetry_return and wait_state.timer is None:
                wait_state.timer = self.hass.loop.call_later(
                    NOTIFICATION_TELEMETRY_GRACE_SECONDS,
                    self._resolve_notification_telemetry,
                    wait_state,
                )
            # Fall through to ID-pointer handling: the current packet may also
            # request a characteristic read for a later TDS frame during the
            # grace window.

        self._check_and_schedule_id_read(
            client,
            data,
            read_target,
            bg_tasks,
            lambda char: self._async_read_char_on_id_notify(
                client, char, received_packets, notify_future, wait_state
            ),
            wait_state,
        )

    def _resolve_notification_telemetry(self, wait_state: _NotificationWaitState) -> None:
        """Resolve a notification wait with buffered filter telemetry.

        Called by the grace timer when no TDS frame arrived after filter
        telemetry was observed. In-flight ID-triggered reads are allowed to
        finish first, since they may still supply TDS.

        Args:
            wait_state: Per-subscription state holding the latest telemetry.
        """
        wait_state.timer = None
        if wait_state.notify_future.done() or wait_state.telemetry is None:
            return
        if wait_state.pending_id_reads > 0:
            wait_state.timer = self.hass.loop.call_later(
                NOTIFICATION_TELEMETRY_GRACE_SECONDS,
                self._resolve_notification_telemetry,
                wait_state,
            )
            return
        wait_state.notify_future.set_result(wait_state.telemetry)

    def _log_assembled_notification_fields(self, received_packets: list[bytearray]) -> None:
        """Log unknown fields from the assembled notification stream.

        Args:
            received_packets: Packets received for the current notification.
        """
        if received_packets:
            self._log_unknown_payload_fields(b"".join(received_packets))

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
            "Timed out (%gs) waiting for TDS notification from Karofi device %s",
            NOTIFICATION_WAIT_SECONDS,
            self.masked_mac,
        )
        self._log_assembled_notification_fields(received_packets)
        if match := self._find_tds_in_packets(received_packets):
            return match
        return received_packets[-1] if received_packets else bytearray()

    async def _subscribe_tds_notification(
        self,
        client: BleakClientWithServiceCache,
        target: BleakGATTCharacteristic | str,
        cmd_target: BleakGATTCharacteristic | str | None = None,
        read_target: BleakGATTCharacteristic | str | None = None,
        *,
        allow_early_telemetry_return: bool = True,
    ) -> bytearray:
        """Subscribe to TDS notifications and await sensor packet.

        Args:
            client: Connected Bleak client instance.
            target: Characteristic object or UUID string to subscribe to.
            cmd_target: Optional write characteristic to transmit request command to.
            read_target: Optional read characteristic to query when device requests read.
            allow_early_telemetry_return: Whether filter-only telemetry may
                resolve the wait before TDS arrives. This is only safe when a
                readable TDS characteristic can recover TDS later in the poll.

        Returns:
            Raw bytearray received from the device notification or subsequent read.
        """
        notify_future: asyncio.Future[bytearray] = asyncio.get_running_loop().create_future()
        wait_state = _NotificationWaitState(
            notify_future=notify_future,
            allow_early_telemetry_return=allow_early_telemetry_return,
        )
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
                wait_state,
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
                async with asyncio.timeout(NOTIFICATION_WAIT_SECONDS):
                    return await notify_future
        except TimeoutError:
            return self._resolve_notify_timeout(received_packets)
        finally:
            if wait_state.timer is not None:
                wait_state.timer.cancel()
                wait_state.timer = None
            self._cleanup_bg_tasks(bg_tasks)
            if subscribed:
                await self._stop_notify_safe(client, target, self.masked_mac)

        return bytearray()

    def _resolve_io_targets(self, client: BleakClientWithServiceCache) -> ResolvedGattTargets:
        """Resolve READ, NOTIFY, and COMMAND characteristics from connected client.

        Args:
            client: Connected Bleak client instance.

        Returns:
            Resolved GATT targets for reads, notifications, and commands.
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
        return ResolvedGattTargets(
            read_target=read_target,
            notify_target=notify_target,
            command_target=cmd_target,
            read_characteristic=read_char,
            can_read=can_read,
        )

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
            self._log_unknown_payload_fields(direct_bytes)
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

    def _remember_telemetry_char(self, uuid: str) -> None:
        """Remember a characteristic that returned usable telemetry.

        The cache is bounded and most-recently-used first so subsequent polls
        can read only known data characteristics instead of scanning the whole
        service.

        Args:
            uuid: UUID of the characteristic that returned telemetry.
        """
        key = uuid.lower()
        with contextlib.suppress(ValueError):
            self._telemetry_char_uuids.remove(key)
        self._telemetry_char_uuids.insert(0, key)
        del self._telemetry_char_uuids[MAX_TELEMETRY_CACHE_ENTRIES:]

    def _prepare_scan_phases(
        self,
        data_chars: list[BleakGATTCharacteristic],
    ) -> tuple[list[list[BleakGATTCharacteristic]], bool, bool]:
        """Build the characteristic read phases for a telemetry scan.

        Args:
            data_chars: Readable data characteristics in service order.

        Returns:
            Tuple of read phases, whether a full scan is required, and whether
            the first phase came from the telemetry-character cache.
        """
        self._polls_since_full_scan += 1
        if self._polls_since_full_scan >= FULL_TELEMETRY_SCAN_INTERVAL:
            self._telemetry_char_uuids.clear()

        cached_uuids = {uuid.lower() for uuid in self._telemetry_char_uuids}
        cached_chars = [char for char in data_chars if char.uuid.lower() in cached_uuids]
        if self._telemetry_char_uuids and not cached_chars:
            self._telemetry_char_uuids.clear()

        known_tds_uuid = (self._known_tds_char_uuid or "").lower()
        if cached_chars and known_tds_uuid and known_tds_uuid not in cached_uuids:
            known_tds_char = next(
                (char for char in data_chars if char.uuid.lower() == known_tds_uuid),
                None,
            )
            if known_tds_char is not None:
                cached_chars.insert(0, known_tds_char)

        phases: list[list[BleakGATTCharacteristic]] = []
        if cached_chars:
            phases.append(cached_chars)
            cached_char_uuids = {char.uuid.lower() for char in cached_chars}
            if remaining_chars := [
                char for char in data_chars if char.uuid.lower() not in cached_char_uuids
            ]:
                phases.append(remaining_chars)
        else:
            phases.append(data_chars)

        return phases, not cached_chars, bool(cached_chars)

    def _record_candidate_bytes(
        self,
        candidate: BleakGATTCharacteristic,
        cand_bytes: bytearray,
        update_tds_uuid: bool,
        state: _TelemetryScanState,
    ) -> None:
        """Record telemetry from one characteristic read.

        Args:
            candidate: Characteristic that produced the bytes.
            cand_bytes: Raw bytes read from the characteristic.
            update_tds_uuid: Whether to remember a discovered TDS UUID.
            state: Accumulated scan state.
        """
        self._log_unknown_payload_fields(cand_bytes)
        _LOGGER.debug(
            "Data characteristic %s read returned %d bytes (hex: %s, text: %r)",
            candidate.uuid,
            len(cand_bytes),
            cand_bytes.hex(),
            cand_bytes.decode("utf-8", errors="replace"),
        )
        if self._has_telemetry_payload(cand_bytes):
            state.telemetry_payloads.append(cand_bytes)
            if self._has_filter_telemetry_payload(cand_bytes):
                state.filter_telemetry_found = True
                state.discovered_char_uuids.append(candidate.uuid)
        if self._parse_tds_reading(cand_bytes) is not None:
            if update_tds_uuid:
                self._known_tds_char_uuid = candidate.uuid
                _LOGGER.debug(
                    "Discovered TDS sensor characteristic on %s: %s (%s ppm)",
                    self.masked_mac,
                    candidate.uuid,
                    self._parse_tds_reading(cand_bytes),
                )
            state.tds_payload = cand_bytes
        if not state.fallback_payload and not self._is_handshake_payload(cand_bytes):
            state.fallback_payload = cand_bytes

    async def _read_candidate_phase(
        self,
        client: BleakClientWithServiceCache,
        candidates: list[BleakGATTCharacteristic],
        read_char: BleakGATTCharacteristic | None,
        direct_bytes: bytearray,
        update_tds_uuid: bool,
        state: _TelemetryScanState,
    ) -> None:
        """Read one characteristic phase into the scan state.

        Args:
            client: Connected Bleak client instance.
            candidates: Characteristics to read in this phase.
            read_char: Initially resolved read characteristic.
            direct_bytes: Bytes already read in the direct-read step.
            update_tds_uuid: Whether to remember a discovered TDS UUID.
            state: Accumulated scan state.
        """
        for candidate in candidates:
            if candidate == read_char and direct_bytes:
                continue
            cand_bytes, read_ok = await self._read_characteristic_for_scan(client, candidate)
            if cand_bytes:
                self._record_candidate_bytes(candidate, cand_bytes, update_tds_uuid, state)
            elif not read_ok:
                # A transport failure is not the same as a legitimately empty
                # characteristic: retry the full service on the next poll so
                # the cache is not seeded with only the successful reads.
                state.failed_char_uuids.append(candidate.uuid)

    def _commit_telemetry_cache(
        self,
        discovered_char_uuids: list[str],
        full_scan: bool,
        timed_out: bool,
    ) -> None:
        """Persist the telemetry-character cache after a scan.

        Args:
            discovered_char_uuids: Characteristics that returned filter telemetry.
            full_scan: Whether the scan covered all readable characteristics.
            timed_out: Whether the scan timed out before completion.
        """
        if timed_out:
            return
        if full_scan:
            self._polls_since_full_scan = 0
        for uuid in reversed(discovered_char_uuids):
            self._remember_telemetry_char(uuid)

    async def _scan_candidate_data_chars(
        self,
        client: BleakClientWithServiceCache,
        read_char: BleakGATTCharacteristic | None,
        direct_bytes: bytearray,
        *,
        timeout_at: float | None = None,
        update_tds_uuid: bool = True,
    ) -> tuple[bytearray | None, bytearray]:
        """Scan service characteristics and collect TDS and filter telemetry.

        Characteristics that previously returned telemetry are read first and
        are the only ones read while they keep producing data. If the cached
        set stops producing telemetry, the full readable service is scanned
        again and the cache is refreshed. A periodic full scan also refreshes
        the cache to discover new characteristics.

        Args:
            client: Connected Bleak client instance.
            read_char: Initially resolved read characteristic.
            direct_bytes: Bytes already read in direct read step.
            timeout_at: Optional event loop time at which to stop scanning.
            update_tds_uuid: Whether to remember a discovered TDS characteristic UUID.

        Returns:
            Tuple of collected telemetry bytes or None and fallback payload.
        """
        services = getattr(client, "services", None)
        if not isinstance(services, BleakGATTServiceCollection):
            return None, bytearray()

        data_chars = self._get_readable_data_characteristics(client)
        phases, full_scan, used_cache = self._prepare_scan_phases(data_chars)
        state = _TelemetryScanState()
        timed_out = False
        try:
            async with asyncio.timeout_at(timeout_at):
                for phase_index, candidates in enumerate(phases):
                    await self._read_candidate_phase(
                        client,
                        candidates,
                        read_char,
                        direct_bytes,
                        update_tds_uuid,
                        state,
                    )
                    if used_cache and phase_index == 0:
                        if state.filter_telemetry_found:
                            break
                        # The cached set produced no filter telemetry; refresh
                        # it with a full scan before giving up on this poll.
                        self._telemetry_char_uuids.clear()
                        full_scan = True
        except TimeoutError:
            timed_out = True
            _LOGGER.debug(
                "Timed out scanning data characteristics for %s; using telemetry collected so far",
                self.masked_mac,
            )
        if state.failed_char_uuids and not timed_out:
            # Do not treat a scan with read failures as a complete cache seed.
            # Leave the cache empty so the next poll performs a true full scan
            # and retries every characteristic, including the failed ones.
            self._telemetry_char_uuids.clear()
            if self._debug_log_due("telemetry_cache_incomplete"):
                _LOGGER.debug(
                    "Skipping telemetry-character cache update for %s after %d failed read(s)",
                    self.masked_mac,
                    len(state.failed_char_uuids),
                )
        else:
            self._commit_telemetry_cache(state.discovered_char_uuids, full_scan, timed_out)
        if state.telemetry_payloads:
            return bytearray(b"\n".join(state.telemetry_payloads)), state.fallback_payload
        return state.tds_payload, state.fallback_payload

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
                self._log_unknown_payload_fields(post_cmd_bytes)
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
        *,
        allow_early_telemetry_return: bool = True,
    ) -> tuple[bytearray | None, bytearray]:
        """Attempt active polling via notification subscription and command request.

        Args:
            client: Connected Bleak client instance.
            notify_target: Characteristic to subscribe for notifications.
            cmd_target: Characteristic to write read command to.
            read_target: Characteristic to read if ID notification arrives.
            allow_early_telemetry_return: Whether filter-only telemetry may
                resolve the notification wait before TDS arrives.

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
            client,
            notify_target,
            cmd_target=cmd_target,
            read_target=read_target,
            allow_early_telemetry_return=allow_early_telemetry_return,
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

        targets = self._resolve_io_targets(client)
        read_target = targets.read_target
        notify_target = targets.notify_target
        cmd_target = targets.command_target
        read_char = targets.read_characteristic
        can_read = targets.can_read
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
                    update_tds_uuid=False,
                )
            except (BleakError, OSError) as err:
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

        # Step 2: Active polling via notification subscription and command request.
        # Early filter-only resolution is only safe when TDS is readable and can
        # be recovered by the later scan/follow-up read in this same poll.
        allow_early_telemetry_return = read_char is not None and GattProperty.READ in getattr(
            read_char, "properties", []
        )
        polling_result, notify_bytes = await self._try_active_polling(
            client,
            notify_target,
            cmd_target,
            read_target,
            allow_early_telemetry_return=allow_early_telemetry_return,
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
        parsed_items = cls._extract_all_payload_dicts(raw_bytes)
        for item in parsed_items:
            if cls._is_priority_tds_payload(item):
                return item
        for item in parsed_items:
            if cls._has_tds_field(item):
                return item
        return parsed_items[0] if parsed_items else None

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
    def _coerce_scalar(val: object) -> int | None:
        """Coerce a scalar value to an integer, or None if invalid.

        Args:
            val: Raw scalar candidate.

        Returns:
            Parsed integer or None if invalid.
        """
        if isinstance(val, (int, float, str)) and not isinstance(val, bool):
            try:
                return int(float(val))
            except ValueError, TypeError, OverflowError:
                return None
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

        return KarofiDataCoordinator._coerce_scalar(val)

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
        return KarofiDataCoordinator._coerce_scalar(item)

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
            raw_items = [part.strip() for part in val.split(",")]
            if not preserve_none:
                raw_items = [item for item in raw_items if item]
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
            MakiposPropertyCode.IRO_HSD,
            MakiposPropertyCode.IRO_ERRS,
        ):
            return True
        return any(
            k in payload
            for k in (
                MakiposPayloadKey.IRO_RTR,
                MakiposPayloadKey.IRO_LTIME,
                MakiposPayloadKey.IRO_RTT,
                MakiposPayloadKey.IRO_HSD,
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
            if self._warning_due("invalid_tds"):
                _LOGGER.warning(
                    "Received invalid TDS reading (%s ppm, raw: %s) from Karofi device %s",
                    tds,
                    raw_bytes.hex(),
                    self.masked_mac,
                )
            else:
                _LOGGER.debug(
                    "Received invalid TDS reading (%s ppm) from Karofi device %s",
                    tds,
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
        remaining_hours: int | None,
        lifetime_hours: int | None,
        name: str | None = None,
        *,
        runtime_remaining_hours: int | None = None,
        runtime_total_hours: int | None = None,
        telemetry_generation: int | None = None,
    ) -> None:
        """Update telemetry, lifetime, and optional name for a single filter stage.

        The device reports the remaining filter time via ``iro_lTime`` and the rated
        lifetime via ``iro_hsd``. Both values are in hours. Runtime-usage telemetry
        (``iro_rtr`` / ``iro_rtt``) is optional and tracked separately.

        Args:
            idx: 0-based filter stage index.
            remaining_hours: Remaining filter hours or None.
            lifetime_hours: Rated filter lifetime in hours or None.
            name: Optional dynamic filter name from device payload.
            runtime_remaining_hours: Remaining runtime hours or None.
            runtime_total_hours: Rated runtime hours or None.
            telemetry_generation: Poll generation associated with this telemetry.
        """
        key = filter_index_to_key(idx)
        fdata = self.data.filters.setdefault(key, KarofiFilterData())
        if name:
            fdata.name = name
        if remaining_hours is not None or runtime_remaining_hours is not None:
            fdata.last_seen = dt_util.utcnow()

        pending_reset = self._pending_filter_resets.get(key)
        outcome = self._evaluate_pending_reset(
            key=key,
            fdata=fdata,
            pending_reset=pending_reset,
            remaining_hours=remaining_hours,
            lifetime_hours=lifetime_hours,
            telemetry_generation=telemetry_generation,
        )
        self._update_filter_lifetime(
            key=key,
            fdata=fdata,
            lifetime_hours=lifetime_hours,
            is_stale=outcome.is_stale,
            allow_device_value=(pending_reset is None or outcome.resolved or outcome.abandoned),
        )
        if remaining_hours is not None and not outcome.use_pending_value:
            self._update_filter_remaining(fdata, remaining_hours)
        if not outcome.is_stale:
            self._update_filter_percent(fdata)
        # Hold the optimistic runtime reset value for the whole confirmation
        # window. Devices may take several polls to reset runtime counters even
        # though a fresh poll already carries generation > pending generation.
        hold_runtime_reset = (
            pending_reset is not None
            and pending_reset.runtime_reset_applied
            and not outcome.resolved
            and not outcome.abandoned
        )
        if not hold_runtime_reset:
            self._update_filter_runtime(
                fdata,
                runtime_remaining_hours,
                runtime_total_hours,
                pending_reset,
                telemetry_generation,
            )
        self._ensure_filter_reset_date(key, fdata)

    @staticmethod
    def _is_stale_reset_response(
        pending_reset: PendingFilterReset | None,
        telemetry_generation: int | None,
    ) -> bool:
        """Return whether telemetry predates the pending reset command."""
        return (
            pending_reset is not None
            and telemetry_generation is not None
            and telemetry_generation <= pending_reset.generation
        )

    @staticmethod
    def _resolve_rated_hours(
        fdata: KarofiFilterData,
        lifetime_hours: int | None,
        pending_reset: PendingFilterReset | None,
    ) -> int | None:
        """Resolve the rated lifetime used to confirm a pending reset."""
        if lifetime_hours is not None and lifetime_hours > 0:
            return lifetime_hours
        if (
            pending_reset is not None
            and pending_reset.lifetime_hours is not None
            and pending_reset.lifetime_hours > 0
        ):
            return pending_reset.lifetime_hours
        return fdata.lifetime_hours

    @staticmethod
    def _is_reset_confirmed(
        remaining_hours: int,
        rated_hours: int | None,
        baseline_hours: int | None,
    ) -> bool:
        """Return whether telemetry confirms a pending reset."""
        if rated_hours is None or rated_hours <= 0:
            return False
        confirmation_tolerance = min(24.0, rated_hours * 0.1)
        near_rated_lifetime = (
            0 <= remaining_hours <= rated_hours
            and rated_hours - remaining_hours <= confirmation_tolerance
        )
        shows_meaningful_increase = (
            remaining_hours >= rated_hours
            if baseline_hours is None
            else remaining_hours > baseline_hours
        )
        return near_rated_lifetime and shows_meaningful_increase

    def _clear_pending_reset(self, key: str) -> None:
        """Remove a pending reset and schedule persistence."""
        self._pending_filter_resets.pop(key, None)
        self._pending_reset_store.async_delay_save(self._get_pending_reset_storage, 1)

    def _advance_pending_reset(
        self,
        *,
        key: str,
        pending_reset: PendingFilterReset,
        remaining_hours: int,
        rated_hours: int | None,
        telemetry_generation: int | None,
        outcome: PendingResetOutcome,
    ) -> PendingResetOutcome:
        """Advance mismatch tracking for a pending filter reset."""
        mismatch_count = min(pending_reset.mismatch_count + 1, RESET_CONFIRMATION_POLLS)
        if pending_reset.mismatch_count < RESET_CONFIRMATION_POLLS:
            _LOGGER.debug(
                "Filter reset telemetry for %s: remaining=%d h, rated lifetime=%s h, "
                "confirmation poll=%d/%d",
                key,
                remaining_hours,
                rated_hours,
                mismatch_count,
                RESET_CONFIRMATION_POLLS,
            )
        if mismatch_count == RESET_CONFIRMATION_POLLS:
            self._clear_pending_reset(key)
            outcome.abandoned = True
            outcome.use_pending_value = False
            _LOGGER.warning(
                "Device did not confirm reset for %s after %d telemetry polls "
                "(remaining=%d h, rated lifetime=%s h); abandoning the local reset "
                "and reverting to device telemetry",
                key,
                mismatch_count,
                remaining_hours,
                rated_hours,
            )
            return outcome

        pending_reset.mismatch_count = mismatch_count
        pending_reset.last_counted_generation = telemetry_generation
        pending_reset.last_counted_missing = False
        outcome.use_pending_value = True
        return outcome

    def _advance_missing_telemetry_reset(
        self,
        *,
        key: str,
        pending_reset: PendingFilterReset,
        telemetry_generation: int | None,
        outcome: PendingResetOutcome,
    ) -> PendingResetOutcome:
        """Advance a pending reset that received no calendar telemetry.

        A device that never reports ``iro_lTime`` would otherwise leave the
        pending reset unresolved forever, freezing the filter sensors at full
        life. Fresh polls without calendar telemetry count toward the same
        abandonment threshold as unconfirmed telemetry.

        The threshold is recorded here, but abandonment is finalized only
        after all payload dictionaries in the generation have been processed.
        This prevents a names-only frame from abandoning a reset before a later
        frame in the same generation supplies ``iro_lTime`` for the stage.

        Args:
            key: Filter stage key.
            pending_reset: Pending reset state.
            telemetry_generation: Poll generation associated with this telemetry.
            outcome: Outcome to update.

        Returns:
            Updated pending reset outcome.
        """
        if (
            telemetry_generation is None
            or telemetry_generation == pending_reset.last_counted_generation
        ):
            outcome.use_pending_value = True
            # The threshold may have been reached on an earlier frame of this
            # generation. Keep releasing optimistic values for later frames so
            # runtime telemetry from those frames is not held back.
            if (
                pending_reset.last_counted_missing
                and pending_reset.missing_telemetry_polls >= RESET_CONFIRMATION_POLLS
            ):
                outcome.abandoned = True
                outcome.use_pending_value = False
            return outcome

        pending_reset.missing_telemetry_polls = min(
            pending_reset.missing_telemetry_polls + 1,
            RESET_CONFIRMATION_POLLS,
        )
        pending_reset.last_counted_generation = telemetry_generation
        pending_reset.last_counted_missing = True
        if pending_reset.missing_telemetry_polls >= RESET_CONFIRMATION_POLLS:
            # Release the optimistic values for this frame, but keep the pending
            # reset until the generation is complete. A later frame with
            # calendar telemetry can still undo the missing count and continue
            # normal confirmation.
            outcome.abandoned = True
            outcome.use_pending_value = False
            _LOGGER.debug(
                "Missing calendar telemetry threshold reached for %s in generation %s; "
                "awaiting completion of the current payload batch",
                key,
                telemetry_generation,
            )
            return outcome

        outcome.use_pending_value = True
        return outcome

    def _finalize_missing_telemetry_resets(self, telemetry_generation: int | None) -> None:
        """Abandon pending resets that had no calendar telemetry in a generation.

        Args:
            telemetry_generation: Generation whose payload dictionaries have all
                been processed.
        """
        if telemetry_generation is None:
            return
        for key, pending_reset in list(self._pending_filter_resets.items()):
            if (
                pending_reset.last_counted_generation != telemetry_generation
                or not pending_reset.last_counted_missing
                or pending_reset.missing_telemetry_polls < RESET_CONFIRMATION_POLLS
            ):
                continue
            self._clear_pending_reset(key)
            _LOGGER.warning(
                "Device did not report calendar telemetry for %s after %d polls; "
                "abandoning the local reset and reverting to date-based filter life",
                key,
                pending_reset.missing_telemetry_polls,
            )

    def _evaluate_pending_reset(
        self,
        *,
        key: str,
        fdata: KarofiFilterData,
        pending_reset: PendingFilterReset | None,
        remaining_hours: int | None,
        lifetime_hours: int | None,
        telemetry_generation: int | None,
    ) -> PendingResetOutcome:
        """Evaluate telemetry against a pending filter reset.

        Args:
            key: Filter stage key.
            fdata: Filter telemetry record.
            pending_reset: Pending reset state or None.
            remaining_hours: Remaining filter hours from telemetry or None.
            lifetime_hours: Rated filter lifetime from telemetry or None.
            telemetry_generation: Poll generation associated with this telemetry.

        Returns:
            Pending reset outcome controlling downstream telemetry updates.
        """
        is_stale = self._is_stale_reset_response(pending_reset, telemetry_generation)
        outcome = PendingResetOutcome(is_stale=is_stale, use_pending_value=is_stale)
        if pending_reset is None or is_stale:
            return outcome
        if remaining_hours is None:
            return self._advance_missing_telemetry_reset(
                key=key,
                pending_reset=pending_reset,
                telemetry_generation=telemetry_generation,
                outcome=outcome,
            )

        if (
            telemetry_generation is not None
            and telemetry_generation == pending_reset.last_counted_generation
            and not pending_reset.last_counted_missing
        ):
            outcome.use_pending_value = True
            return outcome

        if pending_reset.last_counted_missing:
            # The earlier frame in this poll had no calendar telemetry, but a
            # later frame does. Undo that false count and evaluate normally.
            pending_reset.last_counted_generation = None
        pending_reset.missing_telemetry_polls = 0
        pending_reset.last_counted_missing = False
        rated_hours = self._resolve_rated_hours(fdata, lifetime_hours, pending_reset)
        if self._is_reset_confirmed(
            remaining_hours,
            rated_hours,
            pending_reset.baseline_hours,
        ):
            self._clear_pending_reset(key)
            outcome.resolved = True
            _LOGGER.debug(
                "Device confirmed reset for %s: remaining=%d h of %d h rated lifetime",
                key,
                remaining_hours,
                rated_hours,
            )
            return outcome

        if (
            telemetry_generation is not None
            and telemetry_generation == pending_reset.last_counted_generation
        ):
            outcome.use_pending_value = True
            return outcome

        return self._advance_pending_reset(
            key=key,
            pending_reset=pending_reset,
            remaining_hours=remaining_hours,
            rated_hours=rated_hours,
            telemetry_generation=telemetry_generation,
            outcome=outcome,
        )

    def _update_filter_lifetime(
        self,
        *,
        key: str,
        fdata: KarofiFilterData,
        lifetime_hours: int | None,
        is_stale: bool,
        allow_device_value: bool,
    ) -> None:
        """Update rated lifetime from telemetry when the reset state allows it."""
        if (
            lifetime_hours is not None
            and lifetime_hours > 0
            and not is_stale
            and allow_device_value
        ):
            fdata.lifetime_hours = lifetime_hours
            fdata.lifetime_days = max(1, round(lifetime_hours / 24.0))
        elif fdata.lifetime_days is None:
            fdata.lifetime_days = self.get_filter_lifetime_days(key)

    @staticmethod
    def _update_filter_remaining(fdata: KarofiFilterData, remaining_hours: int) -> None:
        """Apply device remaining hours to a filter stage."""
        fdata.remaining_hours = remaining_hours
        fdata.remaining_days = max(0, round(remaining_hours / 24.0))
        fdata.telemetry_synced = True

    @staticmethod
    def _update_filter_percent(fdata: KarofiFilterData) -> None:
        """Recalculate the remaining percentage from current filter values."""
        if fdata.remaining_hours is None:
            return
        lifetime_hours = fdata.lifetime_hours
        if lifetime_hours is None or lifetime_hours <= 0:
            if fdata.lifetime_days is None or fdata.lifetime_days <= 0:
                return
            lifetime_hours = fdata.lifetime_days * 24
        fdata.percent = max(
            0,
            min(
                100,
                round((fdata.remaining_hours / lifetime_hours) * 100),
            ),
        )

    @staticmethod
    def _update_filter_runtime(
        fdata: KarofiFilterData,
        runtime_remaining_hours: int | None,
        runtime_total_hours: int | None,
        pending_reset: PendingFilterReset | None,
        telemetry_generation: int | None,
    ) -> None:
        """Update runtime-usage telemetry for a filter stage.

        Missing values keep the previously known runtime counters. A missing or
        non-positive rated runtime makes the runtime percentage unavailable.

        Args:
            fdata: Filter telemetry record to update.
            runtime_remaining_hours: Remaining runtime hours or None.
            runtime_total_hours: Rated runtime hours or None.
            pending_reset: Pending reset state or None.
            telemetry_generation: Poll generation associated with this telemetry.
        """
        # Ignore pre-reset runtime telemetry until a newer generation arrives.
        if (
            pending_reset is not None
            and telemetry_generation is not None
            and telemetry_generation <= pending_reset.generation
        ):
            return

        if runtime_remaining_hours is not None:
            fdata.runtime_remaining_hours = max(0, runtime_remaining_hours)
        if runtime_total_hours is not None:
            fdata.runtime_total_hours = max(0, runtime_total_hours)

        remaining = fdata.runtime_remaining_hours
        total = fdata.runtime_total_hours
        if remaining is None or total is None or total <= 0:
            fdata.runtime_percent = None
            return

        fdata.runtime_percent = max(
            0,
            min(100, round(remaining / total * 100)),
        )

    def _ensure_filter_reset_date(self, key: str, fdata: KarofiFilterData) -> None:
        """Infer a missing reset date from remaining filter life."""
        if key in self.data.filter_reset_dates or fdata.remaining_days is None:
            return
        lifetime_days = fdata.lifetime_days or DEFAULT_FILTER_LIFETIME_DAYS.get(key, 365)
        used_days = max(0, lifetime_days - fdata.remaining_days)
        self.data.filter_reset_dates[key] = (
            dt_util.utcnow() - timedelta(days=used_days)
        ).isoformat()

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

    def _process_filter_stages_from_payload(
        self,
        payload: dict[str, object],
        telemetry_generation: int | None = None,
    ) -> bool:
        """Process filter telemetry (calendar, runtime, names) from a payload.

        Args:
            payload: Parsed JSON payload dictionary.
            telemetry_generation: Poll generation associated with this payload.

        Returns:
            True if filter telemetry was found and processed, False otherwise.
        """
        ltime_list = self._coerce_int_list(
            self._get_payload_property_val(
                payload, MakiposPropertyCode.IRO_LTIME, MakiposPayloadKey.IRO_LTIME
            ),
            preserve_none=True,
        )
        hsd_list = self._coerce_int_list(
            self._get_payload_property_val(
                payload, MakiposPropertyCode.IRO_HSD, MakiposPayloadKey.IRO_HSD
            ),
            preserve_none=True,
        )
        rtr_list = self._coerce_int_list(
            self._get_payload_property_val(
                payload, MakiposPropertyCode.IRO_RTR, MakiposPayloadKey.IRO_RTR
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

        if (
            ltime_list is None
            and hsd_list is None
            and rtr_list is None
            and rtt_list is None
            and names_list is None
        ):
            return False

        raw_filter_count = max(
            len(ltime_list) if ltime_list else 0,
            len(hsd_list) if hsd_list else 0,
            len(rtr_list) if rtr_list else 0,
            len(rtt_list) if rtt_list else 0,
            len(names_list) if names_list else 0,
        )
        filter_count = min(raw_filter_count, MAX_FILTER_STAGES)
        if raw_filter_count > MAX_FILTER_STAGES and self._warning_due("filter_stage_cap"):
            _LOGGER.warning(
                "Ignoring %d filter stage(s) beyond the supported maximum of %d for %s",
                raw_filter_count - MAX_FILTER_STAGES,
                MAX_FILTER_STAGES,
                self.masked_mac,
            )
        for idx in range(filter_count):
            self._update_single_filter_stage(
                idx,
                self._get_list_element(ltime_list, idx),
                self._get_list_element(hsd_list, idx),
                self._get_list_element(names_list, idx),
                runtime_remaining_hours=self._get_list_element(rtr_list, idx),
                runtime_total_hours=self._get_list_element(rtt_list, idx),
                telemetry_generation=telemetry_generation,
            )

        has_dynamic_telemetry = any(value is not None for value in (ltime_list or ())) or any(
            value is not None for value in (rtr_list or ())
        )
        if filter_count and has_dynamic_telemetry:
            self.data.filters_last_seen = dt_util.utcnow()
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
        self,
        payload_dicts: list[dict[str, object]],
        raw_bytes: bytearray | bytes,
        telemetry_generation: int | None = None,
    ) -> tuple[bool, bool]:
        """Process all decoded payload dictionaries.

        Args:
            payload_dicts: List of decoded dictionary frames.
            raw_bytes: Raw bytes for debug logging.
            telemetry_generation: Poll generation associated with this response.

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
            if self._process_filter_stages_from_payload(payload, telemetry_generation):
                has_sensor_data = True
                filters_updated = True
            if self._process_errors_from_payload(payload):
                has_sensor_data = True
        self._finalize_missing_telemetry_resets(telemetry_generation)
        return has_sensor_data, filters_updated

    def _log_unknown_payload_fields(self, raw_bytes: bytearray | bytes) -> None:
        """Log unsupported payload keys and protocol values once for model support.

        Newer Karofi models may report fields that this integration does not
        parse yet. Unknown fields are logged at most once per polling interval,
        and each field is reported only once within a bounded history, so users
        can capture a debug log without flooding it on every characteristic read.
        Fields discovered during the cooldown are queued and flushed when the
        cooldown expires.

        Args:
            raw_bytes: Payload bytes read from a characteristic or notification.
        """
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return

        unknown_fields: dict[str, str] = {}
        for payload in self._extract_all_payload_dicts(raw_bytes):
            for key, value in payload.items():
                escaped_key = self._escape_log_identifier(key)
                field_id = f"key:{escaped_key}"
                if (
                    key not in SUPPORTED_PAYLOAD_KEYS
                    and field_id not in self._logged_unknown_payload_fields
                ):
                    unknown_fields[field_id] = f"{escaped_key}={value!r}"

            property_code = payload.get(MakiposPayloadKey.PROPERTY_CODE)
            if MakiposPayloadKey.PROPERTY_CODE in payload and (
                not isinstance(property_code, str) or property_code not in SUPPORTED_PROPERTY_CODES
            ):
                field_id = f"property_code:{property_code!r}"
                if field_id not in self._logged_unknown_payload_fields:
                    unknown_fields[field_id] = (
                        f"{MakiposPayloadKey.PROPERTY_CODE}={property_code!r}"
                    )

            event_type = payload.get(MakiposPayloadKey.EVENT_TYPE)
            if MakiposPayloadKey.EVENT_TYPE in payload and (
                not isinstance(event_type, str) or event_type not in SUPPORTED_EVENT_TYPES
            ):
                field_id = f"event_type:{event_type!r}"
                if field_id not in self._logged_unknown_payload_fields:
                    unknown_fields[field_id] = f"{MakiposPayloadKey.EVENT_TYPE}={event_type!r}"

        self._pending_unknown_payload_fields.update(unknown_fields)
        self._trim_pending_unknown_payload_fields()
        self._flush_pending_unknown_payload_fields()

    def _flush_pending_unknown_payload_fields(self) -> None:
        """Emit queued unknown-payload diagnostics when the cooldown has elapsed."""
        if not self._pending_unknown_payload_fields:
            return
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            self._pending_unknown_payload_fields.clear()
            return
        if not self._debug_log_due("unknown_payload"):
            self._schedule_unknown_payload_flush()
            return

        if self._unknown_payload_flush_handle is not None:
            self._unknown_payload_flush_handle.cancel()
            self._unknown_payload_flush_handle = None

        pending_fields = self._pending_unknown_payload_fields
        self._pending_unknown_payload_fields = {}
        self._remember_logged_unknown_payload_fields(pending_fields)
        _LOGGER.debug(
            "Unknown Karofi payload key/value from device %s: %s; "
            "please share this debug log to help add support for newer models",
            self.masked_mac,
            ", ".join(pending_fields.values()),
        )

    def _schedule_unknown_payload_flush(self) -> None:
        """Schedule a delayed flush for diagnostics queued during the cooldown."""
        if self._unknown_payload_flush_handle is not None:
            return
        last_logged = self._debug_log_times.get("unknown_payload")
        if last_logged is None:
            delay = 0.0
        else:
            elapsed = time.monotonic() - last_logged
            delay = max(0.0, DEFAULT_SCAN_INTERVAL - elapsed) + 0.01
        self._unknown_payload_flush_handle = self.hass.loop.call_later(
            delay, self._handle_unknown_payload_flush
        )

    def _handle_unknown_payload_flush(self) -> None:
        """Flush queued unknown-payload diagnostics after the cooldown."""
        self._unknown_payload_flush_handle = None
        self._flush_pending_unknown_payload_fields()

    def _remember_logged_unknown_payload_fields(self, field_ids: Iterable[str]) -> None:
        """Record reported field IDs while keeping the tracking history bounded.

        Args:
            field_ids: Field identifiers that have been reported.
        """
        for field_id in field_ids:
            self._logged_unknown_payload_fields.pop(field_id, None)
            self._logged_unknown_payload_fields[field_id] = None
        while len(self._logged_unknown_payload_fields) > MAX_TRACKED_UNKNOWN_PAYLOAD_FIELDS:
            self._logged_unknown_payload_fields.pop(next(iter(self._logged_unknown_payload_fields)))

    def _trim_pending_unknown_payload_fields(self) -> None:
        """Drop the oldest queued diagnostics beyond the tracking cap."""
        while len(self._pending_unknown_payload_fields) > MAX_TRACKED_UNKNOWN_PAYLOAD_FIELDS:
            self._pending_unknown_payload_fields.pop(
                next(iter(self._pending_unknown_payload_fields))
            )

    @staticmethod
    def _escape_log_identifier(value: str) -> str:
        """Escape control characters in an untrusted identifier for logging.

        Args:
            value: Untrusted identifier text.

        Returns:
            ASCII-safe text with control characters escaped.
        """
        return value.encode("unicode_escape").decode("ascii")

    def _process_tds_payload(
        self,
        raw_bytes: bytearray | bytes,
        telemetry_generation: int | None = None,
    ) -> None:
        """Process raw bytes payload from the TDS sensor and purifier telemetry.

        Args:
            raw_bytes: Payload bytes read from the characteristic.
            telemetry_generation: Poll generation associated with this response.
        """
        if not self._is_valid_json_buffer(raw_bytes):
            self.data.connected = False
            if self._warning_due("invalid_payload"):
                _LOGGER.warning(
                    "Received invalid non-JSON payload (%d bytes, raw: %s) from Karofi device %s",
                    len(raw_bytes),
                    raw_bytes.hex() if raw_bytes else "",
                    self.masked_mac,
                )
            else:
                _LOGGER.debug(
                    "Received invalid non-JSON payload (%d bytes) from Karofi device %s",
                    len(raw_bytes),
                    self.masked_mac,
                )
            return

        self.data.connected = True
        self.data.last_seen = dt_util.utcnow()
        self._log_unknown_payload_fields(raw_bytes)

        if self._is_handshake_payload(raw_bytes):
            _LOGGER.debug(
                "Received Karofi protocol polling ACK/heartbeat (raw: %s) from device %s",
                raw_bytes.hex(),
                self.masked_mac,
            )
            return

        payload_dicts = self._extract_all_payload_dicts(raw_bytes)
        if not payload_dicts:
            if self._warning_due("malformed_payload"):
                _LOGGER.warning(
                    "Received malformed JSON payload (raw: %s) from Karofi device %s",
                    raw_bytes.hex(),
                    self.masked_mac,
                )
            else:
                _LOGGER.debug(
                    "Received malformed JSON payload from Karofi device %s",
                    self.masked_mac,
                )
            return

        reset_dates_snapshot = dict(self.data.filter_reset_dates)
        has_sensor_data, filters_updated = self._process_all_payload_dicts(
            payload_dicts, raw_bytes, telemetry_generation
        )

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

        Serializes the BLE session with filter-reset commands so both cannot
        connect to the device concurrently.

        Returns:
            Updated KarofiData object with the latest readings.
        """
        async with self._ble_lock:
            return await self._async_update_data_locked()

    async def _async_update_data_locked(self) -> KarofiData:
        """Query BLE data while holding the session lock.

        Returns:
            Updated KarofiData object with the latest readings.
        """
        self._telemetry_generation += 1
        telemetry_generation = self._telemetry_generation
        ble_device = self._get_ble_device()
        if not ble_device:
            self.data.connected = False
            diag = "Unavailable"
            with contextlib.suppress(RuntimeError, KeyError):
                diag = async_address_reachability_diagnostics(
                    self.hass,
                    self._mac_candidates[0],
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
                    self._process_tds_payload(raw_bytes, telemetry_generation)
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
