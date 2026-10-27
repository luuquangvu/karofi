"""Base entity definition for Karofi integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
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
        # Home Assistant only normalizes CONNECTION_NETWORK_MAC in the device
        # registry. Bluetooth connections must use the scanner address format,
        # which is uppercase for BLE MAC addresses.
        self._bluetooth_address: str = normalize_mac(str(entry.data[CONF_MAC]), uppercase=True)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._mac)},
            name=entry.data.get(CONF_NAME, DEFAULT_NAME),
            manufacturer=DEFAULT_MANUFACTURER,
            model=DEFAULT_MODEL,
            connections={(CONNECTION_BLUETOOTH, self._bluetooth_address)},
        )
        self._attr_available = coordinator.last_update_success

    async def async_update(self) -> None:
        """Update the entity via the data coordinator.

        Only used by the generic entity update service.
        """
        if not self.enabled:
            return
        await self.coordinator.async_request_refresh()
