"""Base entity definition for Karofi integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import BaseCoordinatorEntity

from .const import DEFAULT_MANUFACTURER, DEFAULT_MODEL, DEFAULT_NAME, DOMAIN
from .coordinator import KarofiDataCoordinator
from .util import normalize_mac


class KarofiBaseEntity(BaseCoordinatorEntity[KarofiDataCoordinator]):
    """Base class for Karofi entities with common device info."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KarofiDataCoordinator, entry: ConfigEntry) -> None:
        """Initialize the base entity.

        Args:
            coordinator: Data coordinator for state updates.
            entry: Integration config entry.
        """
        super().__init__(coordinator)
        self._entry = entry
        self._mac: str = normalize_mac(str(entry.data[CONF_MAC]), uppercase=False)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._mac)},
            name=entry.data.get(CONF_NAME, DEFAULT_NAME),
            manufacturer=DEFAULT_MANUFACTURER,
            model=DEFAULT_MODEL,
            connections={(CONNECTION_NETWORK_MAC, self._mac)},
        )
        self._attr_available = coordinator.last_update_success

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        super()._handle_coordinator_update()

    async def async_update(self) -> None:
        """Update the entity via the data coordinator.

        Only used by the generic entity update service.
        """
        if not self.enabled:
            return
        await self.coordinator.async_request_refresh()
