"""Binary sensor platform for the Karofi integration."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import KarofiDataCoordinator
from .entity import KarofiBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Karofi binary sensor from a config entry.

    Args:
        hass: HomeAssistant instance.
        entry: Integration config entry.
        async_add_entities: Callback to register entities.
    """
    coordinator: KarofiDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KarofiConnectivityBinarySensor(coordinator, entry)])


class KarofiConnectivityBinarySensor(KarofiBaseEntity, BinarySensorEntity):
    """Binary sensor indicating device Bluetooth connectivity."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "connectivity"

    def __init__(self, coordinator: KarofiDataCoordinator, entry: ConfigEntry) -> None:
        """Initialize the connectivity binary sensor.

        Args:
            coordinator: Coordinator providing data.
            entry: Configuration entry.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_connectivity"
        self._attr_is_on = coordinator.data.connected

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = self.coordinator.data.connected
        super()._handle_coordinator_update()
