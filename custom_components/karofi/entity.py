"""Base entity definition for Karofi integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import BaseCoordinatorEntity
from propcache.api import cached_property

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
        self._attr_available = True

    @cached_property
    def available(self) -> bool:
        """Return whether the coordinator and entity-specific state are available.

        Returns:
            True only when the coordinator's latest update succeeded and the
            entity's own availability rules allow it.
        """
        return self.coordinator.last_update_success and self._attr_available

    @callback
    def _handle_coordinator_update(self) -> None:
        """Invalidate cached availability before publishing coordinator state."""
        # Availability depends on both the manual _attr_available flag and the
        # coordinator's last update result, so the cached value must be rebuilt
        # on every coordinator notification.
        vars(self).pop("available", None)
        super()._handle_coordinator_update()

    async def async_update(self) -> None:
        """Update the entity via the data coordinator.

        Only used by the generic entity update service.
        """
        if not self.enabled:
            return
        await self.coordinator.async_request_refresh()
