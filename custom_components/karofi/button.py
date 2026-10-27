"""Button platform for Karofi integration."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KarofiFilter, KarofiIcon
from .coordinator import KarofiDataCoordinator
from .entity import KarofiBaseEntity
from .util import TRANSLATED_FILTER_STAGE_KEYS, filter_key_to_index


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Karofi buttons from a config entry.

    Args:
        hass: HomeAssistant instance.
        entry: Integration config entry.
        async_add_entities: Callback to register entities.
    """
    coordinator: KarofiDataCoordinator = hass.data[DOMAIN][entry.entry_id]

    known_keys: set[str] = set()
    initial_buttons: list[ButtonEntity] = []

    # Generate reset buttons during initialization ONLY if data is found; otherwise hide them
    for filter_key in coordinator.get_known_filter_keys():
        known_keys.add(filter_key)
        initial_buttons.append(KarofiResetFilterButton(coordinator, entry, filter_key))

    if initial_buttons:
        async_add_entities(initial_buttons)

    @callback
    def _async_check_and_add_buttons() -> None:
        """Check for newly discovered filters and register reset buttons."""
        new_keys = [k for k in coordinator.get_known_filter_keys() if k not in known_keys]
        if not new_keys:
            return
        new_buttons: list[ButtonEntity] = []
        for filter_key in new_keys:
            known_keys.add(filter_key)
            new_buttons.append(KarofiResetFilterButton(coordinator, entry, filter_key))
        if new_buttons:
            async_add_entities(new_buttons)

    entry.async_on_unload(coordinator.async_add_listener(_async_check_and_add_buttons))


class KarofiResetFilterButton(KarofiBaseEntity, ButtonEntity):
    """Button to reset the installation date of a filter."""

    _attr_icon = KarofiIcon.RESTORE

    def __init__(
        self,
        coordinator: KarofiDataCoordinator,
        entry: ConfigEntry,
        filter_stage: KarofiFilter | str,
    ) -> None:
        """Initialize the reset filter button.

        Args:
            coordinator: Coordinator providing data.
            entry: Configuration entry.
            filter_stage: Filter stage identifier or enum.
        """
        super().__init__(coordinator, entry)
        self.filter_key: str = (
            filter_stage.value if isinstance(filter_stage, KarofiFilter) else str(filter_stage)
        )
        if custom_name := self.coordinator.get_filter_name(self.filter_key):
            self._attr_name = f"Reset {custom_name}"
        elif self.filter_key in TRANSLATED_FILTER_STAGE_KEYS:
            self._attr_translation_key = f"reset_{self.filter_key}"
        else:
            idx = filter_key_to_index(self.filter_key)
            self._attr_name = f"Reset filter {idx + 1}"
        self._attr_unique_id = f"{self._mac}_btn_reset_{self.filter_key}"
        self._attr_available = coordinator.data.connected

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if custom_name := self.coordinator.get_filter_name(self.filter_key):
            self._attr_name = f"Reset {custom_name}"
            self._attr_translation_key = None
        self._attr_available = self.coordinator.data.connected
        super()._handle_coordinator_update()

    async def async_press(self) -> None:
        """Handle the button press by resetting filter date."""
        await self.coordinator.async_reset_filter(self.filter_key)
