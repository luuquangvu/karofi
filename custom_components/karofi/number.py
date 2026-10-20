"""Number platform for Karofi integration."""

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FILTER_LIFETIME_DAYS, KarofiFilter, KarofiIcon
from .coordinator import KarofiDataCoordinator
from .entity import KarofiBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Karofi number entities from a config entry.

    Args:
        hass: HomeAssistant instance.
        entry: ConfigEntry instance.
        async_add_entities: Callback to register entities.
    """
    coordinator: KarofiDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = [
        KarofiFilterRemainingDaysNumber(coordinator, entry, filter_key)
        for filter_key in KarofiFilter
    ]
    async_add_entities(entities)


class KarofiFilterRemainingDaysNumber(KarofiBaseEntity, NumberEntity):
    """Number entity allowing direct modification of filter remaining days."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_step = 1.0
    _attr_native_min_value = 0.0
    _attr_icon = KarofiIcon.CALENDAR

    def __init__(
        self,
        coordinator: KarofiDataCoordinator,
        entry: ConfigEntry,
        filter_key: KarofiFilter,
    ) -> None:
        """Initialize the filter remaining days number entity.

        Args:
            coordinator: Data coordinator instance.
            entry: Config entry instance.
            filter_key: Filter stage identifier.
        """
        super().__init__(coordinator, entry)
        self.filter_key = filter_key
        self._attr_translation_key = f"{filter_key.value}_remaining_days"
        self._attr_unique_id = f"{self._mac}_num_{filter_key.value}_remaining_days"
        self._lifetime_days = FILTER_LIFETIME_DAYS[filter_key]
        self._attr_native_max_value = float(self._lifetime_days)
        self._attr_native_value = self._calculate_remaining_days()

    def _calculate_remaining_days(self) -> float:
        """Calculate remaining filter days from stored reset date.

        Returns:
            Remaining days as float bounded between 0 and max lifetime.
        """
        return float(
            self.coordinator.get_filter_remaining_days(self.filter_key, self._lifetime_days)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_native_value = self._calculate_remaining_days()
        self._attr_available = self.coordinator.last_update_success
        super()._handle_coordinator_update()

    async def async_set_native_value(self, value: float) -> None:
        """Set the remaining filter days.

        Args:
            value: Desired remaining days before filter replacement.
        """
        clamped = max(0, min(self._lifetime_days, round(value)))
        self._attr_native_value = float(clamped)
        await self.coordinator.async_set_filter_remaining_days(self.filter_key.value, clamped)
