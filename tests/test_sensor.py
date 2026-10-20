"""Tests for Karofi sensors."""

from collections.abc import Iterable
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.karofi.const import (
    DOMAIN,
    FILTER_LIFETIME_DAYS,
    KarofiFilter,
    WaterQuality,
)
from custom_components.karofi.coordinator import KarofiDataCoordinator
from custom_components.karofi.entity import KarofiBaseEntity
from custom_components.karofi.sensor import (
    KarofiFilterDaysRemainingSensor,
    KarofiFilterPercentSensor,
    KarofiQualitySensor,
    KarofiTDSSensor,
    async_setup_entry,
)


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


def _init_test_sensor(sensor: SensorEntity, entity_id: str, hass: HomeAssistant) -> None:
    """Initialize mock platform data for standalone sensor unit tests.

    Args:
        sensor: The SensorEntity instance to initialize.
        entity_id: The mock entity_id.
        hass: HomeAssistant test instance.
    """
    sensor.hass = hass
    sensor.entity_id = entity_id
    sensor.platform = MagicMock(platform_name="sensor")
    sensor.platform_data = MagicMock(
        platform_name="sensor",
        domain=DOMAIN,
        default_language_platform_translations={},
        platform_translations={},
    )


async def test_sensor_async_setup_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test setting up all sensors via platform setup.

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

    # 1 TDS + 1 Quality + 5 Percent + 5 Days = 12 total entities
    assert len(added_entities) == 2 + 2 * len(FILTER_LIFETIME_DAYS)


async def test_tds_sensor(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test KarofiTDSSensor attributes and coordinator updates.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    sensor = KarofiTDSSensor(mock_coordinator, mock_config_entry)
    _init_test_sensor(sensor, "sensor.karofi_tds", hass)
    assert sensor.translation_key == "tds"
    assert sensor.unique_id == "aa:bb:cc:11:22:33_tds"
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == "ppm"
    assert sensor.native_value is None
    assert sensor.available is False

    # Connect device and update coordinator data
    mock_coordinator.data.connected = True
    mock_coordinator.data.tds = 42
    sensor._handle_coordinator_update()
    assert sensor.native_value == 42
    assert sensor.available is True

    # Disconnect device
    mock_coordinator.data.connected = False
    sensor._handle_coordinator_update()
    assert sensor.available is False


async def test_quality_sensor(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test KarofiQualitySensor attributes and coordinator updates.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    sensor = KarofiQualitySensor(mock_coordinator, mock_config_entry)
    _init_test_sensor(sensor, "sensor.karofi_quality", hass)
    assert sensor.translation_key == "water_quality"
    assert sensor.device_class == SensorDeviceClass.ENUM
    assert sensor.options == [q.value for q in WaterQuality]
    assert sensor.unique_id == "aa:bb:cc:11:22:33_quality"
    assert sensor.icon == "mdi:water-check"
    assert sensor.available is False

    # Connect device and update coordinator data
    mock_coordinator.data.connected = True
    mock_coordinator.data.quality = WaterQuality.GOOD
    sensor._handle_coordinator_update()
    assert sensor.native_value == WaterQuality.GOOD.value
    assert sensor.available is True

    # Disconnect device
    mock_coordinator.data.connected = False
    sensor._handle_coordinator_update()
    assert sensor.available is False


async def test_filter_percent_sensor_calculations(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test KarofiFilterPercentSensor calculation edge cases.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    sensor = KarofiFilterPercentSensor(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_1, 100
    )
    _init_test_sensor(sensor, "sensor.karofi_filter_1_pct", hass)
    assert sensor.translation_key == "filter_1_percent"
    assert sensor.native_unit_of_measurement == PERCENTAGE
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.available is True

    # 1. 50 days used out of 100 days = 50%
    start_50_days_ago = (dt_util.utcnow() - timedelta(days=50)).isoformat()
    mock_coordinator.data.filter_reset_dates["filter_1"] = start_50_days_ago
    sensor._handle_coordinator_update()
    assert sensor.native_value == 50

    # 2. Overused (150 days used out of 100) -> clamped to 0%
    start_150_days_ago = (dt_util.utcnow() - timedelta(days=150)).isoformat()
    mock_coordinator.data.filter_reset_dates["filter_1"] = start_150_days_ago
    sensor._handle_coordinator_update()
    assert sensor.native_value == 0

    # 3. Missing key -> 100%
    mock_coordinator.data.filter_reset_dates.pop("filter_1", None)
    sensor._handle_coordinator_update()
    assert sensor.native_value == 100

    # 4. Corrupt / invalid date format -> 100%
    mock_coordinator.data.filter_reset_dates["filter_1"] = "invalid-date-string"
    sensor._handle_coordinator_update()
    assert sensor.native_value == 100

    # 5. Timezone-aware date string
    start_aware = (dt_util.utcnow() - timedelta(days=20)).isoformat()
    mock_coordinator.data.filter_reset_dates["filter_1"] = start_aware
    sensor._handle_coordinator_update()
    assert sensor.native_value == 80


async def test_filter_days_remaining_sensor_calculations(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test KarofiFilterDaysRemainingSensor calculation edge cases.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    sensor = KarofiFilterDaysRemainingSensor(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_RO, 365
    )
    _init_test_sensor(sensor, "sensor.karofi_filter_ro_days", hass)
    assert sensor.translation_key == "filter_ro_days"
    assert sensor.native_unit_of_measurement == UnitOfTime.DAYS
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.available is True

    # 1. 65 days used out of 365 -> 300 days remaining
    start_65_days_ago = (dt_util.utcnow() - timedelta(days=65)).isoformat()
    mock_coordinator.data.filter_reset_dates["filter_ro"] = start_65_days_ago
    sensor._handle_coordinator_update()
    assert sensor.native_value == 300

    # 2. Overused (400 days used out of 365) -> clamped to 0 days
    start_400_days_ago = (dt_util.utcnow() - timedelta(days=400)).isoformat()
    mock_coordinator.data.filter_reset_dates["filter_ro"] = start_400_days_ago
    sensor._handle_coordinator_update()
    assert sensor.native_value == 0

    # 3. Missing key -> max_days (365)
    mock_coordinator.data.filter_reset_dates.pop("filter_ro", None)
    sensor._handle_coordinator_update()
    assert sensor.native_value == 365

    # 4. Corrupt date string -> max_days (365)
    mock_coordinator.data.filter_reset_dates["filter_ro"] = "invalid-date-string"
    sensor._handle_coordinator_update()
    assert sensor.native_value == 365

    # 5. Future date / clock drift (e.g. -5 days used) -> clamped to max_days (365)
    start_future = (dt_util.utcnow() + timedelta(days=5)).isoformat()
    mock_coordinator.data.filter_reset_dates["filter_ro"] = start_future
    sensor._handle_coordinator_update()
    assert sensor.native_value == 365

    # 6. Timezone-aware date string
    start_aware = (dt_util.utcnow() - timedelta(days=100)).isoformat()
    mock_coordinator.data.filter_reset_dates["filter_ro"] = start_aware
    sensor._handle_coordinator_update()
    assert sensor.native_value == 265


async def test_base_entity_async_update(
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test async_update requests refresh on coordinator when entity enabled.

    Args:
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    sensor = KarofiTDSSensor(mock_coordinator, mock_config_entry)

    with patch.object(mock_coordinator, "async_request_refresh", AsyncMock()) as mock_refresh:
        # 1. Enabled
        await sensor.async_update()
        mock_refresh.assert_awaited_once()

    with (
        patch.object(mock_coordinator, "async_request_refresh", AsyncMock()) as mock_refresh,
        patch.object(KarofiBaseEntity, "enabled", new_callable=PropertyMock, return_value=False),
    ):
        # 2. Disabled
        await sensor.async_update()
        mock_refresh.assert_not_called()
