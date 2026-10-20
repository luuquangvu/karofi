"""Tests for the Karofi component initialization and unloading."""

from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.karofi import async_remove_entry
from custom_components.karofi.const import DOMAIN


async def test_setup_and_unload_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test successful setup and unload of a config entry.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.karofi.coordinator.KarofiDataCoordinator._async_update_data",
            return_value=None,
        ),
        patch(
            "custom_components.karofi.coordinator.Store.async_load",
            return_value=None,
        ),
        patch(
            "custom_components.karofi.coordinator.Store.async_save",
            return_value=None,
        ),
    ):
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        assert DOMAIN in hass.data
        assert mock_config_entry.entry_id in hass.data[DOMAIN]

        unload_result = await hass.config_entries.async_unload(mock_config_entry.entry_id)
        assert unload_result is True
        await hass.async_block_till_done()

        assert mock_config_entry.entry_id not in hass.data[DOMAIN]


async def test_remove_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test removing a config entry cleans up persistent storage.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
    """
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.karofi.Store.async_remove",
        return_value=None,
    ) as mock_store_remove:
        await async_remove_entry(hass, mock_config_entry)
        mock_store_remove.assert_awaited_once()
