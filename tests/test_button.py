"""Tests for Karofi button platform."""

from collections.abc import Iterable
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.karofi.button import KarofiResetFilterButton, async_setup_entry
from custom_components.karofi.const import DOMAIN, FILTER_LIFETIME_DAYS, KarofiFilter
from custom_components.karofi.coordinator import KarofiDataCoordinator


@pytest.fixture
def mock_coordinator(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> KarofiDataCoordinator:
    """Fixture providing a KarofiDataCoordinator instance.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.

    Returns:
        KarofiDataCoordinator initialized for tests.
    """
    mock_config_entry.add_to_hass(hass)
    coordinator = KarofiDataCoordinator(
        hass,
        mock_config_entry,
        mac_address="aa:bb:cc:11:22:33",
        name="Karofi Purifier",
    )
    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coordinator
    return coordinator


async def test_button_async_setup_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test button platform setup adds buttons for all default filters.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    added_entities: list[Entity] = []

    def mock_add_entities(new_entities: Iterable[Entity], update_before_add: bool = False) -> None:
        """Mock callback to collect added entities.

        Args:
            new_entities: Iterable of entities to add.
            update_before_add: Whether to update entities before adding.
        """
        added_entities.extend(new_entities)

    await async_setup_entry(hass, mock_config_entry, mock_add_entities)
    assert len(added_entities) == len(FILTER_LIFETIME_DAYS)


async def test_button_press_resets_filter(
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test pressing the reset button triggers coordinator.async_reset_filter.

    Args:
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    button = KarofiResetFilterButton(
        mock_coordinator,
        mock_config_entry,
        KarofiFilter.FILTER_1,
    )
    assert button.unique_id == "aa:bb:cc:11:22:33_btn_reset_filter_1"
    assert button.translation_key == "reset_filter_1"
    assert button.icon == "mdi:restore"
    assert button.available is True

    # Remains available even if coordinator update failed
    mock_coordinator.last_update_success = False
    assert button.available is True

    with patch.object(mock_coordinator, "async_reset_filter", AsyncMock()) as mock_reset:
        await button.async_press()
        mock_reset.assert_awaited_once_with("filter_1")
