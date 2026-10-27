"""Sensor platform for the Karofi integration."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, UNIT_PPM, KarofiFilter, KarofiIcon, WaterQuality
from .coordinator import KarofiDataCoordinator
from .entity import KarofiBaseEntity
from .util import TRANSLATED_FILTER_STAGE_KEYS, filter_key_to_index


def _staleness_attributes(
    coordinator: KarofiDataCoordinator,
    filter_key: str,
) -> dict[str, object]:
    """Return diagnostic attributes describing filter-data freshness.

    Args:
        coordinator: Coordinator providing data and last-seen state.
        filter_key: Filter stage key whose freshness is reported.

    Returns:
        Attributes with the ISO timestamp of the filter stage's last dynamic
        telemetry and a stale flag.
    """
    fdata = coordinator.data.filters.get(filter_key)
    last_seen = fdata.last_seen if fdata is not None else None
    return {
        "last_seen": last_seen.isoformat() if last_seen else None,
        "stale": last_seen is not None and coordinator.is_filter_data_stale(filter_key),
    }


def _is_filter_data_available(
    coordinator: KarofiDataCoordinator,
    filter_key: str,
) -> bool:
    """Return whether filter sensors should be available.

    Args:
        coordinator: Coordinator providing data and last-seen state.
        filter_key: Filter stage key being checked.

    Returns:
        True while the purifier is connected and either the stage has no
        dynamic telemetry yet (date-based values are valid) or its dynamic
        telemetry is still fresh.
    """
    if not coordinator.data.connected:
        return False
    fdata = coordinator.data.filters.get(filter_key)
    if fdata is None or fdata.last_seen is None:
        return True
    return not coordinator.is_filter_data_stale(filter_key)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Karofi sensors from a config entry.

    Args:
        hass: HomeAssistant instance.
        entry: Integration config entry.
        async_add_entities: Callback to register entities.
    """
    coordinator: KarofiDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        KarofiTDSSensor(coordinator, entry),
        KarofiQualitySensor(coordinator, entry),
        KarofiSignalStrengthSensor(coordinator, entry),
    ]

    known_keys: set[str] = set()

    # Generate filter sensors during initialization ONLY if data is found; otherwise hide them
    for filter_key in coordinator.get_known_filter_keys():
        known_keys.add(filter_key)
        max_days = coordinator.get_filter_lifetime_days(filter_key)
        entities.append(KarofiFilterPercentSensor(coordinator, entry, filter_key, max_days))
        entities.append(KarofiFilterDaysRemainingSensor(coordinator, entry, filter_key, max_days))
        entities.append(
            KarofiFilterUsageHoursRemainingSensor(coordinator, entry, filter_key, max_days)
        )

    async_add_entities(entities)

    @callback
    def _async_check_and_add_filter_sensors() -> None:
        """Check for newly discovered filters and register sensor entities."""
        new_keys = [k for k in coordinator.get_known_filter_keys() if k not in known_keys]
        if not new_keys:
            return
        new_entities: list[SensorEntity] = []
        for filter_key in new_keys:
            known_keys.add(filter_key)
            max_days = coordinator.get_filter_lifetime_days(filter_key)
            new_entities.append(KarofiFilterPercentSensor(coordinator, entry, filter_key, max_days))
            new_entities.append(
                KarofiFilterDaysRemainingSensor(coordinator, entry, filter_key, max_days)
            )
            new_entities.append(
                KarofiFilterUsageHoursRemainingSensor(coordinator, entry, filter_key, max_days)
            )
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_check_and_add_filter_sensors))


class KarofiTDSSensor(KarofiBaseEntity, SensorEntity):
    """Sensor for measuring Total Dissolved Solids (TDS) in ppm."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UNIT_PPM
    _attr_icon = KarofiIcon.TDS
    _attr_translation_key = "tds"

    def __init__(self, coordinator: KarofiDataCoordinator, entry: ConfigEntry) -> None:
        """Initialize the TDS sensor.

        Args:
            coordinator: Coordinator providing data.
            entry: Configuration entry.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_tds"
        self._attr_native_value = coordinator.data.tds
        self._attr_available = coordinator.data.connected

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_available = self.coordinator.data.connected
        self._attr_native_value = self.coordinator.data.tds
        super()._handle_coordinator_update()


class KarofiQualitySensor(KarofiBaseEntity, SensorEntity):
    """Sensor indicating general water quality category."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = KarofiIcon.QUALITY
    _attr_translation_key = "water_quality"

    def __init__(self, coordinator: KarofiDataCoordinator, entry: ConfigEntry) -> None:
        """Initialize the water quality sensor.

        Args:
            coordinator: Coordinator providing data.
            entry: Configuration entry.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_quality"
        self._attr_options = [q.value for q in WaterQuality]
        self._attr_native_value = (
            coordinator.data.quality.value
            if isinstance(coordinator.data.quality, WaterQuality)
            else coordinator.data.quality
        )
        self._attr_available = coordinator.data.connected

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_available = self.coordinator.data.connected
        self._attr_native_value = (
            self.coordinator.data.quality.value
            if isinstance(self.coordinator.data.quality, WaterQuality)
            else self.coordinator.data.quality
        )
        super()._handle_coordinator_update()


class KarofiSignalStrengthSensor(KarofiBaseEntity, SensorEntity):
    """Sensor showing the Bluetooth signal strength reported by the device."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = KarofiIcon.SIGNAL
    _attr_translation_key = "signal_strength"

    def __init__(self, coordinator: KarofiDataCoordinator, entry: ConfigEntry) -> None:
        """Initialize the Bluetooth signal strength sensor.

        Use the current advertisement RSSI in dBm, with None making the sensor
        unavailable regardless of GATT connection status.

        Args:
            coordinator: Coordinator providing data.
            entry: Configuration entry containing the device MAC address.

        Raises:
            KeyError: If the configuration entry has no MAC address.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_signal_strength"
        self._attr_native_value = coordinator.data.signal_strength
        self._attr_available = coordinator.data.signal_strength is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Publish RSSI in dBm, marking the sensor unavailable when RSSI is None."""
        signal_strength = self.coordinator.data.signal_strength
        self._attr_native_value = signal_strength
        self._attr_available = signal_strength is not None
        super()._handle_coordinator_update()


class KarofiFilterPercentSensor(KarofiBaseEntity, SensorEntity):
    """Sensor showing remaining filter lifespan percentage."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = KarofiIcon.FILTER

    def __init__(
        self,
        coordinator: KarofiDataCoordinator,
        entry: ConfigEntry,
        filter_stage: KarofiFilter | str,
        max_days: int,
    ) -> None:
        """Initialize the filter percentage sensor.

        Args:
            coordinator: Coordinator providing data.
            entry: Configuration entry.
            filter_stage: Filter stage identifier or enum.
            max_days: Rated lifespan in days.
        """
        super().__init__(coordinator, entry)
        self.filter_key: str = (
            filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        )
        self.max_days = max_days
        if custom_name := self.coordinator.get_filter_name(self.filter_key):
            self._attr_name = f"{custom_name} life"
        elif self.filter_key in TRANSLATED_FILTER_STAGE_KEYS:
            self._attr_translation_key = f"{self.filter_key}_percent"
        else:
            idx = filter_key_to_index(self.filter_key)
            self._attr_name = f"Filter {idx + 1} life"
        self._attr_unique_id = f"{self._mac}_{self.filter_key}_pct"
        self._attr_available = _is_filter_data_available(coordinator, self.filter_key)
        self._attr_native_value = self._calculate_percent()
        self._attr_extra_state_attributes = self._calculate_attributes()

    def _calculate_attributes(self) -> dict[str, object]:
        """Return the calendar/runtime components and capacities behind the state.

        Returns:
            Extra state attributes describing both lifespan sources.
        """
        return {
            "calendar_percent": self.coordinator.get_filter_calendar_percent(
                self.filter_key, self.max_days
            ),
            "runtime_percent": self.coordinator.get_filter_runtime_percent(self.filter_key),
            "capacity_days": self.max_days,
            "capacity_hours": self.coordinator.get_filter_runtime_total_hours(self.filter_key),
            **_staleness_attributes(self.coordinator, self.filter_key),
        }

    def _calculate_percent(self) -> int:
        """Calculate filter remaining percentage.

        Returns:
            Remaining life percentage (0-100).
        """
        return self.coordinator.get_filter_percent(self.filter_key, self.max_days)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if custom_name := self.coordinator.get_filter_name(self.filter_key):
            self._attr_name = f"{custom_name} life"
            self._attr_translation_key = None
        self.max_days = self.coordinator.get_filter_lifetime_days(self.filter_key)
        self._attr_available = _is_filter_data_available(self.coordinator, self.filter_key)
        self._attr_native_value = self._calculate_percent()
        self._attr_extra_state_attributes = self._calculate_attributes()
        super()._handle_coordinator_update()


class KarofiFilterDaysRemainingSensor(KarofiBaseEntity, SensorEntity):
    """Sensor showing remaining days of filter lifespan."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_icon = KarofiIcon.CALENDAR

    def __init__(
        self,
        coordinator: KarofiDataCoordinator,
        entry: ConfigEntry,
        filter_stage: KarofiFilter | str,
        max_days: int,
    ) -> None:
        """Initialize the filter days remaining sensor.

        Args:
            coordinator: Coordinator providing data.
            entry: Configuration entry.
            filter_stage: Filter stage identifier or enum.
            max_days: Rated lifespan in days.
        """
        super().__init__(coordinator, entry)
        self.filter_key: str = (
            filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        )
        self.max_days = max_days
        if custom_name := self.coordinator.get_filter_name(self.filter_key):
            self._attr_name = f"{custom_name} days remaining"
        elif self.filter_key in TRANSLATED_FILTER_STAGE_KEYS:
            self._attr_translation_key = f"{self.filter_key}_days"
        else:
            idx = filter_key_to_index(self.filter_key)
            self._attr_name = f"Filter {idx + 1} days remaining"
        self._attr_unique_id = f"{self._mac}_{self.filter_key}_days"
        self._attr_available = _is_filter_data_available(coordinator, self.filter_key)
        self._attr_native_value = self._calculate_days()
        self._attr_extra_state_attributes = self._calculate_attributes()

    def _calculate_attributes(self) -> dict[str, object]:
        """Return the rated calendar lifespan behind the remaining days.

        Returns:
            Extra state attributes with the calendar capacity.
        """
        return {
            "capacity_days": self.max_days,
            **_staleness_attributes(self.coordinator, self.filter_key),
        }

    def _calculate_days(self) -> int:
        """Calculate remaining days before replacement.

        Returns:
            Number of days remaining.
        """
        return self.coordinator.get_filter_remaining_days(self.filter_key, self.max_days)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if custom_name := self.coordinator.get_filter_name(self.filter_key):
            self._attr_name = f"{custom_name} days remaining"
            self._attr_translation_key = None
        self.max_days = self.coordinator.get_filter_lifetime_days(self.filter_key)
        self._attr_available = _is_filter_data_available(self.coordinator, self.filter_key)
        self._attr_native_value = self._calculate_days()
        self._attr_extra_state_attributes = self._calculate_attributes()
        super()._handle_coordinator_update()


class KarofiFilterUsageHoursRemainingSensor(KarofiBaseEntity, SensorEntity):
    """Sensor showing remaining runtime usage hours of filter lifespan."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_icon = KarofiIcon.CLOCK

    def __init__(
        self,
        coordinator: KarofiDataCoordinator,
        entry: ConfigEntry,
        filter_stage: KarofiFilter | str,
        max_days: int,
    ) -> None:
        """Initialize the filter usage hours remaining sensor.

        Args:
            coordinator: Coordinator providing data.
            entry: Configuration entry.
            filter_stage: Filter stage identifier or enum.
            max_days: Rated calendar lifespan in days.
        """
        super().__init__(coordinator, entry)
        self.filter_key: str = (
            filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        )
        self.max_days = max_days
        if custom_name := self.coordinator.get_filter_name(self.filter_key):
            self._attr_name = f"{custom_name} usage hours remaining"
        elif self.filter_key in TRANSLATED_FILTER_STAGE_KEYS:
            self._attr_translation_key = f"{self.filter_key}_hours"
        else:
            idx = filter_key_to_index(self.filter_key)
            self._attr_name = f"Filter {idx + 1} usage hours remaining"
        self._attr_unique_id = f"{self._mac}_{self.filter_key}_hours"
        self._attr_available = _is_filter_data_available(coordinator, self.filter_key)
        self._attr_native_value = self._calculate_hours()
        self._attr_extra_state_attributes = self._calculate_attributes()

    def _calculate_attributes(self) -> dict[str, object]:
        """Return the rated runtime capacity behind the remaining hours.

        Returns:
            Extra state attributes with the runtime capacity.
        """
        return {
            "capacity_hours": self.coordinator.get_filter_runtime_total_hours(self.filter_key),
            **_staleness_attributes(self.coordinator, self.filter_key),
        }

    def _calculate_hours(self) -> int | None:
        """Return the remaining runtime hours reported by the device.

        Returns:
            Remaining runtime hours, or None when runtime telemetry is unavailable.
        """
        return self.coordinator.get_filter_runtime_remaining_hours(self.filter_key)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if custom_name := self.coordinator.get_filter_name(self.filter_key):
            self._attr_name = f"{custom_name} usage hours remaining"
            self._attr_translation_key = None
        self.max_days = self.coordinator.get_filter_lifetime_days(self.filter_key)
        self._attr_available = _is_filter_data_available(self.coordinator, self.filter_key)
        self._attr_native_value = self._calculate_hours()
        self._attr_extra_state_attributes = self._calculate_attributes()
        super()._handle_coordinator_update()
