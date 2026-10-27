"""Tests for Karofi button platform."""

from collections.abc import Iterable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.karofi.button import KarofiResetFilterButton, async_setup_entry
from custom_components.karofi.const import DOMAIN, KarofiFilter
from custom_components.karofi.coordinator import KarofiDataCoordinator
from custom_components.karofi.models import KarofiFilterData


def _init_test_button(button: KarofiResetFilterButton, entity_id: str, hass: HomeAssistant) -> None:
    """Initialize mock platform data for standalone button unit tests.

    Args:
        button: The KarofiResetFilterButton instance to initialize.
        entity_id: The mock entity_id.
        hass: HomeAssistant test instance.
    """
    button.hass = hass
    button.entity_id = entity_id
    button.platform = MagicMock(platform_name="button")
    button.platform_data = MagicMock(
        platform_name="button",
        domain=DOMAIN,
        default_language_platform_translations={},
        platform_translations={},
    )


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
    """Test button platform setup adds buttons dynamically when discovered.

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

    # Initial setup without known filters: 0 buttons added
    await async_setup_entry(hass, mock_config_entry, mock_add_entities)
    assert not added_entities

    # Simulate discovery of filters via coordinator data
    mock_coordinator.data.filter_reset_dates["filter_1"] = "2026-01-01T00:00:00"
    mock_coordinator.data.filter_reset_dates["filter_2"] = "2026-01-01T00:00:00"
    mock_coordinator.async_update_listeners()

    assert len(added_entities) == 2
    assert isinstance(added_entities[0], KarofiResetFilterButton)
    assert added_entities[0].filter_key == "filter_1"
    assert isinstance(added_entities[1], KarofiResetFilterButton)
    assert added_entities[1].filter_key == "filter_2"


async def test_button_press_resets_filter(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test pressing the reset button triggers coordinator.async_reset_filter.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    button = KarofiResetFilterButton(
        mock_coordinator,
        mock_config_entry,
        KarofiFilter.FILTER_1,
    )
    _init_test_button(button, "button.karofi_btn_reset_filter_1", hass)
    assert button.unique_id == "aa:bb:cc:11:22:33_btn_reset_filter_1"
    assert button.translation_key == "reset_filter_1"
    assert button.icon == "mdi:restore"
    assert button.available is False

    # Becomes available when the device is connected.
    mock_coordinator.data.connected = True
    button._handle_coordinator_update()
    assert button.available is True

    # Unavailable again when the device disconnects.
    mock_coordinator.data.connected = False
    button._handle_coordinator_update()
    assert button.available is False

    with patch.object(mock_coordinator, "async_reset_filter", AsyncMock()) as mock_reset:
        await button.async_press()
        mock_reset.assert_awaited_once_with("filter_1")


async def test_button_dynamic_name(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test button uses dynamic name and updates name on coordinator update.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_coordinator.data.filters["filter_1"] = KarofiFilterData(name="Pre-Carbon Filter")
    button = KarofiResetFilterButton(
        mock_coordinator,
        mock_config_entry,
        KarofiFilter.FILTER_1,
    )
    _init_test_button(button, "button.karofi_btn_reset_filter_1", hass)
    assert button.name == "Reset Pre-Carbon Filter"
    assert button.translation_key is None

    # Simulate name change and coordinator update
    mock_coordinator.data.filters["filter_1"].name = "Post-Carbon Filter"
    button._handle_coordinator_update()
    assert button.name == "Reset Post-Carbon Filter"
    assert button.translation_key is None

    # Entity initialized with default translation key updates name when custom name arrives
    b2 = KarofiResetFilterButton(mock_coordinator, mock_config_entry, KarofiFilter.FILTER_2)
    _init_test_button(b2, "button.karofi_btn_reset_filter_2", hass)
    assert b2.translation_key == "reset_filter_2"
    mock_coordinator.data.filters["filter_2"] = KarofiFilterData(name="Mineral Filter")
    b2._handle_coordinator_update()
    assert b2.name == "Reset Mineral Filter"
    assert b2.translation_key is None


async def test_button_11_12_and_dynamic_fallback(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test button for stages 11, 12, and dynamic fallback beyond 12 stages.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    b11 = KarofiResetFilterButton(mock_coordinator, mock_config_entry, "filter_11")
    _init_test_button(b11, "button.karofi_btn_reset_filter_11", hass)
    assert b11.translation_key == "reset_filter_11"

    b12 = KarofiResetFilterButton(mock_coordinator, mock_config_entry, "filter_12")
    _init_test_button(b12, "button.karofi_btn_reset_filter_12", hass)
    assert b12.translation_key == "reset_filter_12"

    b13 = KarofiResetFilterButton(mock_coordinator, mock_config_entry, "filter_13")
    _init_test_button(b13, "button.karofi_btn_reset_filter_13", hass)
    assert b13.translation_key == "reset_filter_13"

    b17 = KarofiResetFilterButton(mock_coordinator, mock_config_entry, "filter_17")
    _init_test_button(b17, "button.karofi_btn_reset_filter_17", hass)
    assert b17.name == "Reset filter 17"
    assert b17.translation_key is None
