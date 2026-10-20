"""Tests for Karofi binary sensor platform."""

from collections.abc import Iterable
from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.karofi.binary_sensor import (
    KarofiConnectivityBinarySensor,
    async_setup_entry,
)
from custom_components.karofi.const import DOMAIN
from custom_components.karofi.coordinator import KarofiDataCoordinator


@pytest.fixture
def mock_coordinator(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> KarofiDataCoordinator:
    """Fixture providing a KarofiDataCoordinator with initialized data.

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
        name="Karofi Device",
    )
    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coordinator
    return coordinator


def _init_test_binary_sensor(
    sensor: BinarySensorEntity, entity_id: str, hass: HomeAssistant
) -> None:
    """Initialize mock platform data for standalone binary sensor unit tests.

    Args:
        sensor: The BinarySensorEntity instance to initialize.
        entity_id: The mock entity_id.
        hass: HomeAssistant test instance.
    """
    sensor.hass = hass
    sensor.entity_id = entity_id
    sensor.platform = MagicMock(platform_name="binary_sensor")
    sensor.platform_data = MagicMock(
        platform_name="binary_sensor",
        domain=DOMAIN,
        default_language_platform_translations={},
        platform_translations={},
    )


async def test_binary_sensor_async_setup_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test setting up binary sensors via platform setup.

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
    assert len(added_entities) == 1
    assert isinstance(added_entities[0], KarofiConnectivityBinarySensor)


async def test_connectivity_binary_sensor(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test KarofiConnectivityBinarySensor attributes and updates.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    sensor = KarofiConnectivityBinarySensor(mock_coordinator, mock_config_entry)
    _init_test_binary_sensor(sensor, "binary_sensor.karofi_connectivity", hass)

    assert sensor.translation_key == "connectivity"
    assert sensor.device_class == BinarySensorDeviceClass.CONNECTIVITY
    assert sensor.unique_id == "aa:bb:cc:11:22:33_connectivity"
    assert sensor.is_on is False
    assert sensor.available is True

    # Connect device
    mock_coordinator.data.connected = True
    sensor._handle_coordinator_update()
    assert sensor.is_on is True

    # Disconnect device
    mock_coordinator.data.connected = False
    sensor._handle_coordinator_update()
    assert sensor.is_on is False
