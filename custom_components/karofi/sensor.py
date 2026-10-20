"""Sensor platform for the Karofi integration."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    FILTER_LIFETIME_DAYS,
    UNIT_PPM,
    KarofiFilter,
    KarofiIcon,
    WaterQuality,
)
from .coordinator import KarofiDataCoordinator
from .entity import KarofiBaseEntity


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
    ]

    for filter_stage, days in FILTER_LIFETIME_DAYS.items():
        entities.append(KarofiFilterPercentSensor(coordinator, entry, filter_stage, days))
        entities.append(KarofiFilterDaysRemainingSensor(coordinator, entry, filter_stage, days))

    async_add_entities(entities)


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
        self._attr_translation_key = f"{self.filter_key}_percent"
        self._attr_unique_id = f"{self._mac}_{self.filter_key}_pct"
        self._attr_native_value = self._calculate_percent()

    def _calculate_percent(self) -> int:
        """Calculate filter remaining percentage.

        Returns:
            Remaining life percentage (0-100).
        """
        return self.coordinator.get_filter_percent(self.filter_key, self.max_days)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self._calculate_percent()
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
        self._attr_translation_key = f"{self.filter_key}_days"
        self._attr_unique_id = f"{self._mac}_{self.filter_key}_days"
        self._attr_native_value = self._calculate_days()

    def _calculate_days(self) -> int:
        """Calculate remaining days before replacement.

        Returns:
            Number of days remaining.
        """
        return self.coordinator.get_filter_remaining_days(self.filter_key, self.max_days)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self._calculate_days()
        super()._handle_coordinator_update()
