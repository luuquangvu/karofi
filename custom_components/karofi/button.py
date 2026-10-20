"""Button platform for Karofi integration."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FILTER_LIFETIME_DAYS, KarofiFilter, KarofiIcon
from .coordinator import KarofiDataCoordinator
from .entity import KarofiBaseEntity


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

    buttons: list[ButtonEntity] = [
        KarofiResetFilterButton(coordinator, entry, filter_stage)
        for filter_stage in FILTER_LIFETIME_DAYS
    ]

    async_add_entities(buttons)


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
        self._attr_translation_key = f"reset_{self.filter_key}"
        self._attr_unique_id = f"{self._mac}_btn_reset_{self.filter_key}"
        self._attr_available = True

    async def async_press(self) -> None:
        """Handle the button press by resetting filter date."""
        await self.coordinator.async_reset_filter(self.filter_key)
