"""Tests for Karofi sensors."""

from collections.abc import Iterable
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.karofi.const import (
    DOMAIN,
    KarofiFilter,
    WaterQuality,
)
from custom_components.karofi.coordinator import KarofiDataCoordinator
from custom_components.karofi.entity import KarofiBaseEntity
from custom_components.karofi.models import KarofiFilterData
from custom_components.karofi.sensor import (
    KarofiFilterDaysRemainingSensor,
    KarofiFilterPercentSensor,
    KarofiQualitySensor,
    KarofiSignalStrengthSensor,
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
    """Test setting up sensors dynamically via platform setup.

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

    # Initially without known filters: 3 sensors (TDS + Quality + Signal strength)
    await async_setup_entry(hass, mock_config_entry, mock_add_entities)
    assert len(added_entities) == 3

    # Simulate discovery of 2 filters via coordinator data
    mock_coordinator.data.filter_reset_dates["filter_1"] = "2026-01-01T00:00:00"
    mock_coordinator.data.filter_reset_dates["filter_2"] = "2026-01-01T00:00:00"
    mock_coordinator.async_update_listeners()

    # 3 base sensors + 4 filter sensors (2 per filter)
    assert len(added_entities) == 7


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


async def test_signal_strength_sensor(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test BLE signal strength sensor attributes and coordinator updates.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    sensor = KarofiSignalStrengthSensor(mock_coordinator, mock_config_entry)
    _init_test_sensor(sensor, "sensor.karofi_signal_strength", hass)
    assert sensor.translation_key == "signal_strength"
    assert sensor.unique_id == "aa:bb:cc:11:22:33_signal_strength"
    assert sensor.device_class == SensorDeviceClass.SIGNAL_STRENGTH
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.native_unit_of_measurement == SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert sensor.entity_registry_enabled_default is True
    assert sensor.native_value is None
    assert sensor.available is False

    # An advertisement provides an RSSI reading even before a GATT connection.
    mock_coordinator.data.signal_strength = -67
    sensor._handle_coordinator_update()
    assert sensor.native_value == -67
    assert sensor.available is True

    mock_coordinator.data.signal_strength = -82
    sensor._handle_coordinator_update()
    assert sensor.native_value == -82
    assert sensor.available is True

    mock_coordinator.data.signal_strength = None
    sensor._handle_coordinator_update()
    assert sensor.native_value is None
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
    mock_coordinator.data.filters["filter_1"] = KarofiFilterData(
        index=0, key="filter_1", lifetime_days=100
    )
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

    # 6. Telemetry updates filter lifetime
    mock_coordinator.data.filters["filter_1"].lifetime_days = 200
    sensor._handle_coordinator_update()
    assert sensor.max_days == 200
    assert sensor.native_value == 90


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
    mock_coordinator.data.filters["filter_4"] = KarofiFilterData(
        index=3, key="filter_4", lifetime_days=365
    )
    sensor = KarofiFilterDaysRemainingSensor(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_4, 365
    )
    _init_test_sensor(sensor, "sensor.karofi_filter_4_days", hass)
    assert sensor.translation_key == "filter_4_days"
    assert sensor.native_unit_of_measurement == UnitOfTime.DAYS
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.available is True

    # 1. 65 days used out of 365 -> 300 days remaining
    start_65_days_ago = (dt_util.utcnow() - timedelta(days=65)).isoformat()
    mock_coordinator.data.filter_reset_dates["filter_4"] = start_65_days_ago
    sensor._handle_coordinator_update()
    assert sensor.native_value == 300

    # 2. Overused (400 days used out of 365) -> clamped to 0 days
    start_400_days_ago = (dt_util.utcnow() - timedelta(days=400)).isoformat()
    mock_coordinator.data.filter_reset_dates["filter_4"] = start_400_days_ago
    sensor._handle_coordinator_update()
    assert sensor.native_value == 0

    # 3. Missing key -> max_days (365)
    mock_coordinator.data.filter_reset_dates.pop("filter_4", None)
    sensor._handle_coordinator_update()
    assert sensor.native_value == 365

    # 4. Corrupt date string -> max_days (365)
    mock_coordinator.data.filter_reset_dates["filter_4"] = "invalid-date-string"
    sensor._handle_coordinator_update()
    assert sensor.native_value == 365

    # 5. Future date / clock drift (e.g. -5 days used) -> clamped to max_days (365)
    start_future = (dt_util.utcnow() + timedelta(days=5)).isoformat()
    mock_coordinator.data.filter_reset_dates["filter_4"] = start_future
    sensor._handle_coordinator_update()
    assert sensor.native_value == 365

    # 6. Timezone-aware date string
    start_aware = (dt_util.utcnow() - timedelta(days=100)).isoformat()
    mock_coordinator.data.filter_reset_dates["filter_4"] = start_aware
    sensor._handle_coordinator_update()
    assert sensor.native_value == 265

    # 7. Telemetry updates filter lifetime
    mock_coordinator.data.filters["filter_4"].lifetime_days = 500
    sensor._handle_coordinator_update()
    assert sensor.max_days == 500
    assert sensor.native_value == 400


async def test_filter_sensors_dynamic_name(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test filter sensors use dynamic names if provided by coordinator.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_coordinator.data.filters["filter_1"] = KarofiFilterData(
        index=0, key="filter_1", name="Nano Silver"
    )
    percent_sensor = KarofiFilterPercentSensor(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_1, 180
    )
    days_sensor = KarofiFilterDaysRemainingSensor(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_1, 180
    )
    _init_test_sensor(percent_sensor, "sensor.karofi_filter_1_percent", hass)
    _init_test_sensor(days_sensor, "sensor.karofi_filter_1_days", hass)

    assert percent_sensor.name == "Nano Silver life"
    assert percent_sensor.translation_key is None
    assert days_sensor.name == "Nano Silver days remaining"
    assert days_sensor.translation_key is None

    # Simulate dynamic name update
    mock_coordinator.data.filters["filter_1"].name = "OCB-GAC"
    percent_sensor._handle_coordinator_update()
    days_sensor._handle_coordinator_update()

    assert percent_sensor.name == "OCB-GAC life"
    assert percent_sensor.translation_key is None
    assert days_sensor.name == "OCB-GAC days remaining"
    assert days_sensor.translation_key is None

    # Entity initialized with default translation key updates name when custom name arrives
    p2 = KarofiFilterPercentSensor(mock_coordinator, mock_config_entry, KarofiFilter.FILTER_2, 365)
    d2 = KarofiFilterDaysRemainingSensor(
        mock_coordinator, mock_config_entry, KarofiFilter.FILTER_2, 365
    )
    _init_test_sensor(p2, "sensor.karofi_filter_2_percent", hass)
    _init_test_sensor(d2, "sensor.karofi_filter_2_days", hass)
    assert p2.translation_key == "filter_2_percent"
    assert d2.translation_key == "filter_2_days"
    mock_coordinator.data.filters["filter_2"] = KarofiFilterData(
        index=1, key="filter_2", name="Mineral"
    )
    p2._handle_coordinator_update()
    d2._handle_coordinator_update()
    assert p2.name == "Mineral life"
    assert p2.translation_key is None
    assert d2.name == "Mineral days remaining"
    assert d2.translation_key is None


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


async def test_filter_sensors_11_12_and_dynamic_fallback(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_coordinator: KarofiDataCoordinator,
) -> None:
    """Test filter sensors for stages 11, 12, and beyond with dynamic fallback naming.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        mock_coordinator: Fixture providing KarofiDataCoordinator.
    """
    # 1. Filter 11 & 12 (standard translation keys)
    s11 = KarofiFilterPercentSensor(mock_coordinator, mock_config_entry, "filter_11", 365)
    s12 = KarofiFilterDaysRemainingSensor(mock_coordinator, mock_config_entry, "filter_12", 365)
    _init_test_sensor(s11, "sensor.karofi_filter_11_percent", hass)
    _init_test_sensor(s12, "sensor.karofi_filter_12_days", hass)

    assert s11.translation_key == "filter_11_percent"
    assert s12.translation_key == "filter_12_days"

    # 2. Filter 13 (beyond 12 stages, dynamic fallback)
    s13_pct = KarofiFilterPercentSensor(mock_coordinator, mock_config_entry, "filter_13", 365)
    s13_days = KarofiFilterDaysRemainingSensor(
        mock_coordinator, mock_config_entry, "filter_13", 365
    )
    _init_test_sensor(s13_pct, "sensor.karofi_filter_13_percent", hass)
    _init_test_sensor(s13_days, "sensor.karofi_filter_13_days", hass)

    assert s13_pct.name == "Filter 13 life"
    assert s13_pct.translation_key is None
    assert s13_days.name == "Filter 13 days remaining"
    assert s13_days.translation_key is None
