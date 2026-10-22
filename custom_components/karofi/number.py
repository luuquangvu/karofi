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

from .const import DOMAIN, KarofiFilter, KarofiIcon
from .coordinator import KarofiDataCoordinator
from .entity import KarofiBaseEntity
from .util import FILTER_STAGE_KEYS, filter_key_to_index


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

    known_keys: set[str] = set()
    initial_numbers: list[NumberEntity] = []

    # Generate number entities during initialization ONLY if data is found; otherwise hide them
    for filter_key in coordinator.get_known_filter_keys():
        known_keys.add(filter_key)
        initial_numbers.append(KarofiFilterRemainingDaysNumber(coordinator, entry, filter_key))

    if initial_numbers:
        async_add_entities(initial_numbers)

    @callback
    def _async_check_and_add_numbers() -> None:
        """Check for newly discovered filters and register number entities."""
        new_keys = [k for k in coordinator.get_known_filter_keys() if k not in known_keys]
        if not new_keys:
            return
        new_numbers: list[NumberEntity] = []
        for filter_key in new_keys:
            known_keys.add(filter_key)
            new_numbers.append(KarofiFilterRemainingDaysNumber(coordinator, entry, filter_key))
        if new_numbers:
            async_add_entities(new_numbers)

    entry.async_on_unload(coordinator.async_add_listener(_async_check_and_add_numbers))


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
        filter_key: KarofiFilter | str,
    ) -> None:
        """Initialize the filter remaining days number entity.

        Args:
            coordinator: Data coordinator instance.
            entry: Config entry instance.
            filter_key: Filter stage identifier or enum.
        """
        super().__init__(coordinator, entry)
        self.filter_key: str = (
            filter_key.value if isinstance(filter_key, KarofiFilter) else str(filter_key)
        )
        if custom_name := self.coordinator.get_filter_name(self.filter_key):
            self._attr_name = f"{custom_name} remaining days"
        elif self.filter_key in FILTER_STAGE_KEYS:
            self._attr_translation_key = f"{self.filter_key}_remaining_days"
        else:
            idx = filter_key_to_index(self.filter_key)
            self._attr_name = f"Filter {idx + 1} remaining days"
        self._attr_unique_id = f"{self._mac}_num_{self.filter_key}_remaining_days"
        self._lifetime_days = coordinator.get_filter_lifetime_days(self.filter_key)
        self._attr_native_max_value = float(self._lifetime_days)
        self._attr_native_value = self._calculate_remaining_days()

    def _calculate_remaining_days(self) -> float:
        """Calculate remaining filter days from coordinator.

        Returns:
            Remaining days as float bounded between 0 and max lifetime.
        """
        return float(
            self.coordinator.get_filter_remaining_days(self.filter_key, self._lifetime_days)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if custom_name := self.coordinator.get_filter_name(self.filter_key):
            self._attr_name = f"{custom_name} remaining days"
            self._attr_translation_key = None
        self._lifetime_days = self.coordinator.get_filter_lifetime_days(self.filter_key)
        self._attr_native_max_value = float(self._lifetime_days)
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
        await self.coordinator.async_set_filter_remaining_days(self.filter_key, clamped)
