"""The Karofi component."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    DEFAULT_NAME,
    DOMAIN,
    PENDING_RESET_STORAGE_SUFFIX,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)
from .coordinator import KarofiDataCoordinator
from .util import normalize_mac

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Karofi from a config entry.

    Args:
        hass: HomeAssistant instance.
        entry: Integration config entry.

    Returns:
        True if integration setup succeeded.
    """
    mac: str = normalize_mac(str(entry.data[CONF_MAC]), uppercase=False)
    name: str = entry.data.get(CONF_NAME, DEFAULT_NAME)

    coordinator = KarofiDataCoordinator(hass, entry, mac, name)
    await coordinator.async_setup()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_create_background_task(
        hass,
        coordinator.async_refresh(),
        name=f"{DOMAIN}_{entry.entry_id}_refresh",
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Args:
        hass: HomeAssistant instance.
        entry: Integration config entry to unload.

    Returns:
        True if integration unload succeeded.
    """
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry and clean up associated storage.

    Args:
        hass: HomeAssistant instance.
        entry: Integration config entry being removed.
    """
    await Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry.entry_id}").async_remove()
    await Store(
        hass,
        STORAGE_VERSION,
        f"{STORAGE_KEY_PREFIX}_{entry.entry_id}_{PENDING_RESET_STORAGE_SUFFIX}",
    ).async_remove()
