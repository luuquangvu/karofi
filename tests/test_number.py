"""Tests for Karofi number platform."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.karofi.const import (
    DOMAIN,
    STORAGE_KEY_PREFIX,
    KarofiFilter,
)
from custom_components.karofi.coordinator import KarofiDataCoordinator
from custom_components.karofi.models import KarofiFilterData
from custom_components.karofi.number import (
    KarofiFilterRemainingDaysNumber,
    async_setup_entry,
)


def _init_test_number(
    number: KarofiFilterRemainingDaysNumber, entity_id: str, hass: HomeAssistant
) -> None:
    """Initialize mock platform data for standalone number unit tests.

    Args:
        number: The KarofiFilterRemainingDaysNumber instance to initialize.
        entity_id: The mock entity_id.
        hass: HomeAssistant test instance.
    """
    number.hass = hass
    number.entity_id = entity_id
    number.platform = MagicMock(platform_name="number")
    number.platform_data = MagicMock(
        platform_name="number",
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


async def test_number_async_setup_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test number platform setup registers number entities dynamically.

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

    # Initial setup without known filters: 0 number entities added
    await async_setup_entry(hass, mock_config_entry, mock_add_entities)
    assert not added_entities

    # Simulate discovery of filters via coordinator data
    mock_coordinator.data.filter_reset_dates["filter_1"] = "2026-01-01T00:00:00"
    mock_coordinator.data.filter_reset_dates["filter_2"] = "2026-01-01T00:00:00"
    mock_coordinator.async_update_listeners()

    assert len(added_entities) == 2
    first_entity = added_entities[0]
    assert isinstance(first_entity, KarofiFilterRemainingDaysNumber)
    assert first_entity.mode == NumberMode.BOX
    assert first_entity.device_class == NumberDeviceClass.DURATION
    assert first_entity.native_unit_of_measurement == UnitOfTime.DAYS
    assert first_entity.entity_category == EntityCategory.CONFIG
    assert first_entity.native_min_value == 0.0
    assert first_entity.native_step == 1.0


async def test_number_native_value_calculations(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test number native_value calculates remaining days accurately.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    now = dt_util.utcnow()

    # Brand new filter installed right now
    mock_coordinator.data.filter_reset_dates["filter_1"] = now.isoformat()
    entity1 = KarofiFilterRemainingDaysNumber(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_1
    )
    assert entity1.native_value == 180.0

    # Filter installed 30 days ago
    mock_coordinator.data.filter_reset_dates["filter_2"] = (now - timedelta(days=30)).isoformat()
    entity2 = KarofiFilterRemainingDaysNumber(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_2
    )
    assert entity2.native_value == 240.0

    # Filter expired (installed 800 days ago, lifetime 730)
    mock_coordinator.data.filter_reset_dates["filter_4"] = (now - timedelta(days=800)).isoformat()
    entity_4 = KarofiFilterRemainingDaysNumber(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_4
    )
    assert entity_4.native_value == 0.0

    # Corrupt or missing date
    mock_coordinator.data.filter_reset_dates["filter_5"] = "invalid-date"
    entity_5 = KarofiFilterRemainingDaysNumber(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_5
    )
    assert entity_5.native_value == 365.0

    # Naive ISO date string without timezone offset
    mock_coordinator.data.filter_reset_dates["filter_3"] = "2026-01-01T00:00:00"
    entity3 = KarofiFilterRemainingDaysNumber(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_3
    )
    assert isinstance(entity3.native_value, float)


async def test_number_dynamic_name(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test number uses dynamic name and updates name on coordinator update.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_coordinator.data.filters["filter_1"] = KarofiFilterData(
        index=0, key="filter_1", name="Pre-Carbon"
    )
    entity = KarofiFilterRemainingDaysNumber(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_1
    )
    _init_test_number(entity, "number.karofi_filter_1_remaining_days", hass)
    assert entity.name == "Pre-Carbon remaining days"
    assert entity.translation_key is None

    # Simulate name change and coordinator update
    mock_coordinator.data.filters["filter_1"].name = "Post-Carbon"
    entity._handle_coordinator_update()
    assert entity.name == "Post-Carbon remaining days"
    assert entity.translation_key is None

    # Entity initialized with default translation key updates name when custom name arrives
    n2 = KarofiFilterRemainingDaysNumber(mock_coordinator, mock_config_entry, KarofiFilter.FILTER_2)
    _init_test_number(n2, "number.karofi_filter_2_remaining_days", hass)
    assert n2.translation_key == "filter_2_remaining_days"
    mock_coordinator.data.filters["filter_2"] = KarofiFilterData(
        index=1, key="filter_2", name="Mineral"
    )
    n2._handle_coordinator_update()
    assert n2.name == "Mineral remaining days"
    assert n2.translation_key is None


async def test_number_set_native_value(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
    hass_storage: dict[str, Any],
) -> None:
    """Test async_set_native_value modifies remaining days and persists calibration.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
        hass_storage: Fixture providing mock storage from test harness.
    """
    key = f"{STORAGE_KEY_PREFIX}_{mock_config_entry.entry_id}"
    mock_coordinator.data.filter_reset_dates["filter_1"] = dt_util.utcnow().isoformat()
    entity = KarofiFilterRemainingDaysNumber(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_1
    )
    _init_test_number(entity, "number.karofi_filter_1_remaining_days", hass)

    frozen_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    with patch("homeassistant.util.dt.utcnow", return_value=frozen_now):
        # Set remaining days to 60 days
        await entity.async_set_native_value(60.0)
        assert entity.native_value == 60.0
        expected_60 = (frozen_now - timedelta(days=120)).isoformat()
        assert mock_coordinator.data.filter_reset_dates["filter_1"] == expected_60
        assert hass_storage[key]["data"]["filter_1"] == expected_60
        assert mock_coordinator.get_filter_remaining_days("filter_1") == 60
        assert entity._calculate_remaining_days() == 60.0

        # Set remaining days clamped to max lifetime (lifetime 180)
        await entity.async_set_native_value(999.0)
        assert entity.native_value == 180.0
        expected_max = frozen_now.isoformat()
        assert mock_coordinator.data.filter_reset_dates["filter_1"] == expected_max
        assert hass_storage[key]["data"]["filter_1"] == expected_max
        assert mock_coordinator.get_filter_remaining_days("filter_1") == 180
        assert entity._calculate_remaining_days() == 180.0

        # Set negative remaining days clamped to 0
        await entity.async_set_native_value(-10.0)
        assert entity.native_value == 0.0
        expected_zero = (frozen_now - timedelta(days=180)).isoformat()
        assert mock_coordinator.data.filter_reset_dates["filter_1"] == expected_zero
        assert hass_storage[key]["data"]["filter_1"] == expected_zero
        assert mock_coordinator.get_filter_remaining_days("filter_1") == 0
        assert entity._calculate_remaining_days() == 0.0


async def test_number_handle_coordinator_update(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test _handle_coordinator_update refreshes state and availability.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    now = dt_util.utcnow()
    mock_coordinator.data.filter_reset_dates["filter_1"] = now.isoformat()
    entity = KarofiFilterRemainingDaysNumber(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_1
    )
    _init_test_number(entity, "number.karofi_filter_1_remaining_days", hass)

    # Simulate 50 days elapsed
    mock_coordinator.data.filter_reset_dates["filter_1"] = (now - timedelta(days=50)).isoformat()
    mock_coordinator.last_update_success = True
    entity._handle_coordinator_update()

    assert entity.native_value == 130.0
    assert entity.available is True


async def test_number_11_12_and_dynamic_fallback(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test number entity for stages 11, 12, and dynamic fallback beyond 12 stages.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    n11 = KarofiFilterRemainingDaysNumber(mock_coordinator, mock_config_entry, "filter_11")
    _init_test_number(n11, "number.karofi_filter_11_remaining_days", hass)
    assert n11.translation_key == "filter_11_remaining_days"

    n12 = KarofiFilterRemainingDaysNumber(mock_coordinator, mock_config_entry, "filter_12")
    _init_test_number(n12, "number.karofi_filter_12_remaining_days", hass)
    assert n12.translation_key == "filter_12_remaining_days"

    n13 = KarofiFilterRemainingDaysNumber(mock_coordinator, mock_config_entry, "filter_13")
    _init_test_number(n13, "number.karofi_filter_13_remaining_days", hass)
    assert n13.name == "Filter 13 remaining days"
    assert n13.translation_key is None
