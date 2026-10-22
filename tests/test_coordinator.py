"""Tests for Karofi data coordinator."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.service import BleakGATTServiceCollection
from bleak.exc import BleakError
from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothServiceInfoBleak,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.karofi.const import (
    DEFAULT_FILTER_LIFETIME_DAYS,
    DEFAULT_NAME,
    NOTIFY_UUID_PREFIX,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
    GattProperty,
    KarofiCharacteristic,
    KarofiShortUUID,
    WaterQuality,
)
from custom_components.karofi.coordinator import KarofiDataCoordinator
from custom_components.karofi.models import KarofiFilterData


@pytest.fixture
def coordinator(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> KarofiDataCoordinator:
    """Fixture providing a KarofiDataCoordinator instance.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.

    Returns:
        KarofiDataCoordinator initialized for tests.
    """
    mock_config_entry.add_to_hass(hass)
    return KarofiDataCoordinator(
        hass,
        mock_config_entry,
        mac_address="aa:bb:cc:11:22:33",
        name=DEFAULT_NAME,
    )


def _mock_characteristic(uuid: str, properties: list[str] | None = None) -> MagicMock:
    """Create a mock BleakGATTCharacteristic with specified uuid and properties.

    Args:
        uuid: UUID string for the characteristic.
        properties: Optional list of characteristic properties (e.g. read, write, notify).

    Returns:
        MagicMock instance configured as a BleakGATTCharacteristic.
    """
    char = MagicMock(spec=BleakGATTCharacteristic)
    char.uuid = uuid
    if properties is not None:
        char.properties = properties
    return char


async def test_coordinator_setup_loads_stored_dates(
    coordinator: KarofiDataCoordinator,
    hass_storage: dict[str, Any],
) -> None:
    """Test coordinator setup loading existing dates from storage.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        hass_storage: Fixture providing mock storage from test harness.
    """
    key = f"{STORAGE_KEY_PREFIX}_{coordinator.entry.entry_id}"
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "data": {
            "filter_1": "2026-01-01T00:00:00",
            "filter_2": "2026-02-01T00:00:00",
        },
    }
    await coordinator.async_setup()
    assert coordinator.data.filter_reset_dates["filter_1"] == "2026-01-01T00:00:00"
    assert coordinator.data.filter_reset_dates["filter_2"] == "2026-02-01T00:00:00"
    assert len(coordinator.data.filter_reset_dates) == 2
    assert coordinator.get_known_filter_keys() == ["filter_1", "filter_2"]


async def test_coordinator_setup_handles_empty_store(
    coordinator: KarofiDataCoordinator,
    hass_storage: dict[str, Any],
) -> None:
    """Test coordinator setup handling None or corrupt storage.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        hass_storage: Fixture providing mock storage from test harness.
    """
    await coordinator.async_setup()
    assert coordinator.data.filter_reset_dates == {}
    assert coordinator.get_known_filter_keys() == []


async def test_coordinator_setup_no_save_when_valid(
    coordinator: KarofiDataCoordinator,
    hass_storage: dict[str, Any],
) -> None:
    """Test coordinator setup does not save when storage already contains valid dates.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        hass_storage: Fixture providing mock storage from test harness.
    """
    key = f"{STORAGE_KEY_PREFIX}_{coordinator.entry.entry_id}"
    valid_dates = dict.fromkeys(DEFAULT_FILTER_LIFETIME_DAYS, "2026-01-01T00:00:00+00:00")
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "data": valid_dates,
    }
    with patch.object(coordinator._store, "async_save") as mock_save:
        await coordinator.async_setup()
        mock_save.assert_not_called()


async def test_coordinator_reset_filter(
    coordinator: KarofiDataCoordinator,
    hass_storage: dict[str, Any],
) -> None:
    """Test resetting filter date updates store and notification.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        hass_storage: Fixture providing mock storage from test harness.
    """
    key = f"{STORAGE_KEY_PREFIX}_{coordinator.entry.entry_id}"
    coordinator.data.filter_reset_dates["filter_1"] = "2020-01-01T00:00:00"
    frozen_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    with patch("homeassistant.util.dt.utcnow", return_value=frozen_now):
        await coordinator.async_reset_filter("filter_1")
        assert coordinator.data.filter_reset_dates["filter_1"] == frozen_now.isoformat()
        assert hass_storage[key]["data"]["filter_1"] == frozen_now.isoformat()
        assert coordinator.get_filter_remaining_days("filter_1") == 180


async def test_coordinator_update_data_device_not_found(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _async_update_data when device is not reachable by any proxy.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    with patch(
        "custom_components.karofi.coordinator.async_ble_device_from_address",
        return_value=None,
    ):
        data = await coordinator._async_update_data()
        assert data.connected is False


async def test_coordinator_update_data_success_water_levels(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test _async_update_data reading TDS and setting quality levels.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    mock_client = AsyncMock()
    mock_client.disconnect = AsyncMock()

    # 1. Good water (< 50 ppm)
    mock_client.read_gatt_char.return_value = b'{"pc":"iro_tds","d":30}'
    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds == 30
        assert data.quality == WaterQuality.GOOD
        assert data.connected is True
        assert isinstance(data.last_seen, datetime)
        mock_client.read_gatt_char.assert_awaited_with(KarofiCharacteristic.TDS_SENSOR)
        mock_client.disconnect.assert_awaited_once()

    # 2. Fair water (50-150 ppm)
    mock_client.disconnect.reset_mock()
    mock_client.read_gatt_char.return_value = b'{"pc":"iro_tds","d":120}'
    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds == 120
        assert data.quality == WaterQuality.FAIR
        mock_client.disconnect.assert_awaited_once()

    # 3. Poor water (> 150 ppm)
    mock_client.disconnect.reset_mock()
    mock_client.read_gatt_char.return_value = b'{"pc":"iro_tds","d":250}'
    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds == 250
        assert data.quality == WaterQuality.POOR
        mock_client.disconnect.assert_awaited_once()


async def test_coordinator_update_data_sentinel_error(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test _async_update_data with 0xFFFF sentinel error payload.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    mock_client = AsyncMock()
    mock_client.read_gatt_char.return_value = b'{"pc":"iro_tds","d":65535}'
    mock_client.disconnect = AsyncMock()

    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.connected is True
        assert data.tds is None
        assert data.quality == WaterQuality.UNKNOWN
        mock_client.disconnect.assert_awaited_once()


async def test_coordinator_update_data_short_payload(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test _async_update_data with unexpected payload length.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    mock_client = AsyncMock()
    mock_client.read_gatt_char.return_value = b"\x01"
    mock_client.disconnect = AsyncMock()

    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        coordinator.data.tds = 50
        data = await coordinator._async_update_data()
        assert data.connected is False
        mock_client.disconnect.assert_awaited_once()


async def test_coordinator_update_data_connection_errors(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test _async_update_data handling BleakError and OSError.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(side_effect=BleakError("Connection failed")),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.connected is False

    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(side_effect=OSError("Socket closed")),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.connected is False


async def test_coordinator_calibration_date_and_days(
    coordinator: KarofiDataCoordinator,
    hass_storage: dict[str, Any],
) -> None:
    """Test async_set_filter_date and async_set_filter_used_days methods.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        hass_storage: Fixture providing mock storage from test harness.
    """
    key = f"{STORAGE_KEY_PREFIX}_{coordinator.entry.entry_id}"
    # 1. ISO string
    await coordinator.async_set_filter_date("filter_1", "2026-01-10T12:00:00+00:00")
    assert coordinator.data.filter_reset_dates["filter_1"] == "2026-01-10T12:00:00+00:00"
    assert hass_storage[key]["data"]["filter_1"] == "2026-01-10T12:00:00+00:00"

    # 2. Date string YYYY-MM-DD
    await coordinator.async_set_filter_date("filter_1", "2026-02-15")
    assert "2026-02-15" in coordinator.data.filter_reset_dates["filter_1"]
    assert hass_storage[key]["data"]["filter_1"] == coordinator.data.filter_reset_dates["filter_1"]

    # 3. Datetime object
    dt_val = datetime(2026, 3, 20, 10, 0, 0, tzinfo=UTC)
    await coordinator.async_set_filter_date("filter_1", dt_val)
    assert coordinator.data.filter_reset_dates["filter_1"] == dt_val.isoformat()
    assert hass_storage[key]["data"]["filter_1"] == dt_val.isoformat()

    # 4. Invalid date string raises ValueError
    with pytest.raises(ValueError, match="Invalid date format"):
        await coordinator.async_set_filter_date("filter_1", "invalid-date-string")

    # 5. Non-existent filter key is ignored
    prev_stored = dict(hass_storage[key]["data"])
    await coordinator.async_set_filter_date("unknown_filter", "2026-01-01")
    assert hass_storage[key]["data"] == prev_stored

    frozen_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    with patch("homeassistant.util.dt.utcnow", return_value=frozen_now):
        # 6. Used days calibration
        await coordinator.async_set_filter_used_days("filter_2", 60)
        expected_f2 = (frozen_now - timedelta(days=60)).isoformat()
        assert coordinator.data.filter_reset_dates["filter_2"] == expected_f2
        assert hass_storage[key]["data"]["filter_2"] == expected_f2
        assert coordinator.get_filter_remaining_days("filter_2") == 210

        # 7. Non-existent filter for used days is ignored
        prev_stored = dict(hass_storage[key]["data"])
        await coordinator.async_set_filter_used_days("unknown_filter", 10)
        assert hass_storage[key]["data"] == prev_stored

        # 8. Calibrate all filters
        await coordinator.async_calibrate_all_filters(
            {"filter_1": 15, "filter_3": 30, "unknown": 10}
        )
        expected_f1 = (frozen_now - timedelta(days=15)).isoformat()
        expected_f3 = (frozen_now - timedelta(days=30)).isoformat()
        assert coordinator.data.filter_reset_dates["filter_1"] == expected_f1
        assert coordinator.data.filter_reset_dates["filter_3"] == expected_f3
        assert hass_storage[key]["data"]["filter_1"] == expected_f1
        assert hass_storage[key]["data"]["filter_3"] == expected_f3
        assert coordinator.get_filter_remaining_days("filter_1") == 165
        assert coordinator.get_filter_remaining_days("filter_3") == 240

        # 9. Remaining days calibration
        await coordinator.async_set_filter_remaining_days("filter_1", 100)
        expected_f1_rem = (frozen_now - timedelta(days=80)).isoformat()
        assert coordinator.data.filter_reset_dates["filter_1"] == expected_f1_rem
        assert hass_storage[key]["data"]["filter_1"] == expected_f1_rem
        assert coordinator.get_filter_remaining_days("filter_1") == 100

        # 10. Non-existent filter for remaining days is ignored
        prev_stored = dict(hass_storage[key]["data"])
        await coordinator.async_set_filter_remaining_days("unknown_filter", 50)
        assert hass_storage[key]["data"] == prev_stored


async def test_coordinator_bluetooth_event_logging(coordinator: KarofiDataCoordinator) -> None:
    """Test _async_handle_bluetooth_event executes without error.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_service_info = MagicMock(spec=BluetoothServiceInfoBleak)
    mock_service_info.address = "AA:BB:CC:11:22:33"
    mock_service_info.rssi = -60
    coordinator._async_handle_bluetooth_event(mock_service_info, BluetoothChange.ADVERTISEMENT)


async def test_coordinator_notify_fallback_success(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test _read_tds_characteristic using GATT notification subscription fallback.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    mock_char = MagicMock(spec=BleakGATTCharacteristic)
    mock_char.uuid = KarofiCharacteristic.TDS_SENSOR
    mock_char.properties = ["notify"]

    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    mock_services.get_characteristic.return_value = mock_char

    mock_client = AsyncMock()
    mock_client.services = mock_services

    async def _mock_start_notify(char: object, callback: Any) -> None:
        """Mock start_notify callback execution.

        Args:
            char: Characteristic object.
            callback: Notification callback callable.
        """
        callback(char, bytearray(b'{"pc":"iro_tds","d":42}'))

    mock_client.start_notify.side_effect = _mock_start_notify
    mock_client.stop_notify = AsyncMock()
    mock_client.disconnect = AsyncMock()

    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds == 42
        assert data.quality == WaterQuality.GOOD
        assert data.connected is True
        mock_client.start_notify.assert_awaited_once()
        mock_client.stop_notify.assert_awaited_once()


async def test_coordinator_notify_split_packet_reassembly(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test reassembly of fragmented BLE notification packets.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    mock_char = MagicMock(spec=BleakGATTCharacteristic)
    mock_char.uuid = KarofiCharacteristic.TDS_SENSOR
    mock_char.properties = ["notify"]

    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    mock_services.get_characteristic.return_value = mock_char

    mock_client = AsyncMock()
    mock_client.services = mock_services

    async def _mock_start_notify_split(char: object, callback: Any) -> None:
        """Deliver split notification frames."""
        callback(char, bytearray(b'\x01{"pc":"iro_td'))
        callback(char, bytearray(b's","d":42}\x04'))

    mock_client.start_notify.side_effect = _mock_start_notify_split
    mock_client.stop_notify = AsyncMock()
    mock_client.disconnect = AsyncMock()

    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds == 42
        assert data.quality == WaterQuality.GOOD
        assert data.connected is True
        mock_client.start_notify.assert_awaited_once()
        mock_client.stop_notify.assert_awaited_once()


async def test_coordinator_lost_connection_logging(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test error logging when connection is lost after previously being connected.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    coordinator.data.connected = True
    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(side_effect=BleakError("Connection drop")),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.connected is False


async def test_coordinator_update_data_json_payloads(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test _async_update_data correctly parsing various JSON payloads from device.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    mock_client = AsyncMock()
    mock_client.disconnect = AsyncMock()

    # Case 1: Framed SOH/EOT JSON payload with pc='iro_tds' and int data
    mock_client.read_gatt_char.return_value = b'\x01{"pc":"iro_tds","d":35}\x04'
    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds == 35
        assert data.quality == WaterQuality.GOOD
        assert data.connected is True

    # Case 2: Clean JSON payload with string data in 'd'
    mock_client.read_gatt_char.return_value = b'{"pc":"iro_tds","d":"85"}'
    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds == 85
        assert data.quality == WaterQuality.FAIR

    # Case 3: List data payload
    mock_client.read_gatt_char.return_value = b'{"d":[180]}'
    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds == 180
        assert data.quality == WaterQuality.POOR

    # Case 4: Invalid JSON or malformed content
    mock_client.read_gatt_char.return_value = b'{"pc":"iro_tds","d":"invalid"}'
    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds is None
        assert data.quality == WaterQuality.UNKNOWN


async def test_coordinator_process_handshake_payload(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _process_tds_payload handling protocol handshake without logging warning.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    raw_np = bytes.fromhex("017b226574223a224e50227d04")
    coordinator.data.tds = 75
    coordinator.data.quality = WaterQuality.FAIR
    coordinator.data.connected = False

    with patch("custom_components.karofi.coordinator._LOGGER.warning") as mock_warn:
        coordinator._process_tds_payload(raw_np)
        assert coordinator.data.connected
        assert coordinator.data.last_seen is not None
        assert coordinator.data.tds == 75
        assert coordinator.data.quality == WaterQuality.FAIR
        mock_warn.assert_not_called()


async def test_coordinator_update_data_direct_read_handshake(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test _async_update_data attempts polling and falls back to handshake frame.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    mock_client = AsyncMock()
    mock_client.read_gatt_char.return_value = bytes.fromhex("017b226574223a224e50227d04")
    mock_client.disconnect = AsyncMock()
    coordinator.data.tds = 50
    coordinator.data.quality = WaterQuality.GOOD

    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds == 50
        assert data.quality == WaterQuality.GOOD
        assert data.connected is True
        assert data.last_seen is not None
        mock_client.read_gatt_char.assert_awaited()
        mock_client.start_notify.assert_awaited_once()


async def test_coordinator_update_data_fallback_when_direct_read_fails(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test _async_update_data polls via notify when direct read raises BleakError.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    mock_client = AsyncMock()
    mock_client.read_gatt_char.side_effect = BleakError("Direct read not supported")
    mock_client.disconnect = AsyncMock()

    async def _mock_start_notify(char: object, callback: Any) -> None:
        """Simulate device sending TDS telemetry notification after write."""
        callback(char, bytearray(b'{"pc":"iro_tds","d":45}'))

    mock_client.start_notify.side_effect = _mock_start_notify

    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds == 45
        assert data.quality == WaterQuality.GOOD
        assert data.connected is True
        mock_client.read_gatt_char.assert_awaited_once()
        mock_client.start_notify.assert_awaited_once()
        mock_client.write_gatt_char.assert_awaited_once()


async def test_coordinator_notify_skips_handshake_echo(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test notification listener skips handshake echo and resolves on actual sensor packet.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    mock_char = MagicMock(spec=BleakGATTCharacteristic)
    mock_char.uuid = KarofiCharacteristic.TDS_SENSOR
    mock_char.properties = ["notify"]

    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    mock_services.get_characteristic.return_value = mock_char

    mock_client = AsyncMock()
    mock_client.services = mock_services

    async def _mock_start_notify(char: object, callback: Any) -> None:
        """Simulate device echoing handshake first, then sending TDS telemetry."""
        callback(char, bytearray(bytes.fromhex("017b226574223a224e50227d04")))
        callback(char, bytearray(b'{"pc":"iro_tds","d":60}'))

    mock_client.start_notify.side_effect = _mock_start_notify
    mock_client.disconnect = AsyncMock()

    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            AsyncMock(return_value=mock_client),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.tds == 60
        assert data.quality == WaterQuality.FAIR
        assert data.connected is True


def test_coordinator_resolve_tds_characteristic(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test characteristic resolution prioritization.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_client = MagicMock()
    mock_client.services = None
    assert coordinator._resolve_tds_characteristic(mock_client) is None

    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    mock_char = MagicMock(spec=BleakGATTCharacteristic)
    mock_services.get_characteristic.return_value = mock_char
    mock_client.services = mock_services
    assert coordinator._resolve_tds_characteristic(mock_client) is mock_char

    # Fallback to service characteristics when direct UUID match not present
    mock_services.get_characteristic.return_value = None
    mock_service = MagicMock()
    candidate_char = MagicMock(spec=BleakGATTCharacteristic)
    candidate_char.properties = ["read"]
    mock_service.characteristics = [candidate_char]
    mock_services.get_service.return_value = mock_service
    assert coordinator._resolve_tds_characteristic(mock_client) is candidate_char


def test_coordinator_parse_tds_reading_variations(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test parsing various payload formats and edge cases.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    assert coordinator._parse_tds_reading(b'{"pc":"iro_tds","d":15}') == 15
    assert coordinator._parse_tds_reading(b'{"pc":"iro_tds","d":[25]}') == 25
    assert coordinator._parse_tds_reading(b'{"pc":"iro_tds","d":"35,120"}') == 35
    assert coordinator._parse_tds_reading(b'{"iro_tds":45}') == 45
    assert coordinator._parse_tds_reading(b'{"pure_index":55}') == 55
    assert coordinator._parse_tds_reading(b'{"pi":65}') == 65
    assert coordinator._parse_tds_reading(b'{"d":75}') == 75
    # Property codes other than iro_tds must return None
    assert coordinator._parse_tds_reading(b'{"pc":"iro_errs","d":[0]}') is None
    assert coordinator._parse_tds_reading(b'{"pc":"iro_rtr","d":[180]}') is None
    # Handshake payload
    assert coordinator._parse_tds_reading(bytes.fromhex("017b226574223a224e50227d04")) is None


def test_coordinator_is_handshake_payload() -> None:
    """Test KarofiDataCoordinator._is_handshake_payload with various inputs."""
    assert KarofiDataCoordinator._is_handshake_payload(bytes.fromhex("017b226574223a224e50227d04"))
    assert KarofiDataCoordinator._is_handshake_payload(b'{"et":"NP"}')
    assert KarofiDataCoordinator._is_handshake_payload(b'{"et":"CP"}')
    assert not KarofiDataCoordinator._is_handshake_payload(b'{"pc":"iro_tds","d":30}')
    assert not KarofiDataCoordinator._is_handshake_payload(b'{"pc":"iro_tds","d":"invalid"}')
    assert not KarofiDataCoordinator._is_handshake_payload(b"invalid-json")
    assert not KarofiDataCoordinator._is_handshake_payload(b"")


def test_coordinator_log_discovered_services(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _log_discovered_services handles None, debug guard, and valid collections.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_client = MagicMock()
    mock_client.services = None
    coordinator._log_discovered_services(mock_client)

    mock_char = MagicMock(spec=BleakGATTCharacteristic)
    mock_char.uuid = KarofiCharacteristic.NOTIFY
    mock_char.properties = [GattProperty.NOTIFY, GattProperty.WRITE_WITHOUT_RESPONSE]

    mock_service = MagicMock()
    mock_service.uuid = "000012ac-0000-1000-8000-00805f9b34fb"
    mock_service.characteristics = [mock_char]

    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    mock_services.__iter__.return_value = iter([mock_service])
    mock_client.services = mock_services

    with patch("custom_components.karofi.coordinator._LOGGER.isEnabledFor", return_value=True):
        coordinator._log_discovered_services(mock_client)

        mock_client.services = None
        coordinator._log_discovered_services(mock_client)


def test_is_command_characteristic() -> None:
    """Test _is_command_characteristic matches command and notify UUIDs and short prefixes."""
    assert KarofiDataCoordinator._is_command_characteristic(KarofiCharacteristic.NOTIFY)
    assert KarofiDataCoordinator._is_command_characteristic(KarofiCharacteristic.COMMAND)
    assert KarofiDataCoordinator._is_command_characteristic(KarofiShortUUID.NOTIFY)
    assert KarofiDataCoordinator._is_command_characteristic(KarofiShortUUID.COMMAND)
    assert KarofiDataCoordinator._is_command_characteristic("0000ac02-0000-1000-8000-00805f9b34fb")
    assert KarofiDataCoordinator._is_command_characteristic(f"{NOTIFY_UUID_PREFIX}-custom")

    # Non-command characteristics
    assert not KarofiDataCoordinator._is_command_characteristic(KarofiCharacteristic.SERVICE)
    assert not KarofiDataCoordinator._is_command_characteristic(KarofiShortUUID.SERVICE)
    assert not KarofiDataCoordinator._is_command_characteristic(
        "000012ac-0000-1000-8000-00805f9b34fb"
    )
    assert not KarofiDataCoordinator._is_command_characteristic("random-char-uuid")


def test_coordinator_find_char_by_property() -> None:
    """Test _find_char_by_property with direct UUID matches, service fallbacks, and misses."""
    mock_services = MagicMock(spec=BleakGATTServiceCollection)

    # 1. Match by candidate UUID
    char_uuid = MagicMock(spec=BleakGATTCharacteristic)
    char_uuid.properties = ["notify"]
    mock_services.get_characteristic.side_effect = lambda u: char_uuid if u == "uuid1" else None

    found = KarofiDataCoordinator._find_char_by_property(
        mock_services, ("uuid1", "uuid2"), ("notify",)
    )
    assert found is char_uuid

    # 2. Match by candidate service fallback
    mock_services.get_characteristic.return_value = None
    service_char = MagicMock(spec=BleakGATTCharacteristic)
    service_char.properties = ["write"]
    mock_service = MagicMock()
    mock_service.characteristics = [service_char]
    mock_services.get_service.side_effect = lambda s: mock_service if "12ac" in s else None

    found_service = KarofiDataCoordinator._find_char_by_property(
        mock_services, ("uuid3",), ("write",)
    )
    assert found_service is service_char

    # 3. No match found
    mock_services.get_service.return_value = None
    not_found = KarofiDataCoordinator._find_char_by_property(
        mock_services, ("uuid3",), ("indicate",)
    )
    assert not_found is None


def test_coordinator_resolve_notify_and_cmd_characteristics(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test characteristic resolution for NOTIFY and COMMAND characteristics.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_client = MagicMock()
    mock_client.services = None
    assert coordinator._resolve_notify_characteristic(mock_client) is None
    assert coordinator._resolve_command_characteristic(mock_client) is None

    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    notify_char = MagicMock(spec=BleakGATTCharacteristic)
    notify_char.properties = ["notify"]
    cmd_char = MagicMock(spec=BleakGATTCharacteristic)
    cmd_char.properties = ["write"]

    def _get_char(uuid: str) -> BleakGATTCharacteristic | None:
        """Mock get characteristic method.

        Args:
            uuid: UUID of the characteristic to get.

        Returns:
            The characteristic if found, None otherwise.
        """
        return notify_char if "ac02" in uuid else None

    mock_services.get_characteristic.side_effect = _get_char
    mock_services.get_service.return_value = None
    mock_client.services = mock_services

    assert coordinator._resolve_notify_characteristic(mock_client) is notify_char


def test_coordinator_resolve_command_characteristic_uses_command_service(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Prefer the writable command characteristic in COMMAND_SERVICE when UUIDs collide."""
    non_writable_char = _mock_characteristic(KarofiCharacteristic.COMMAND, [GattProperty.NOTIFY])
    writable_command_char = _mock_characteristic(KarofiCharacteristic.COMMAND, [GattProperty.WRITE])
    command_service = MagicMock()
    command_service.characteristics = [writable_command_char]

    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    mock_services.get_characteristic.return_value = non_writable_char
    mock_services.get_service.side_effect = lambda uuid: (
        command_service
        if uuid in (KarofiCharacteristic.COMMAND_SERVICE, KarofiShortUUID.COMMAND_SERVICE)
        else None
    )
    mock_client = MagicMock()
    mock_client.services = mock_services

    assert coordinator._resolve_command_characteristic(mock_client) is writable_command_char


async def test_coordinator_write_command_with_fallback() -> None:
    """Test _write_command_with_fallback handling responses and error fallbacks."""
    mock_client = AsyncMock()
    mock_char = MagicMock(spec=BleakGATTCharacteristic)
    mock_char.properties = ["write"]

    # 1. Success on normal write
    mock_client.write_gatt_char.return_value = None
    res = await KarofiDataCoordinator._write_command_with_fallback(
        mock_client, mock_char, b"\x01test\x04"
    )
    assert res is True

    # 2. Prefer no response when write-without-response is only option
    mock_char_no_resp = MagicMock(spec=BleakGATTCharacteristic)
    mock_char_no_resp.properties = ["write-without-response"]
    res_no_resp = await KarofiDataCoordinator._write_command_with_fallback(
        mock_client, mock_char_no_resp, b"\x01test\x04"
    )
    assert res_no_resp is True

    # 3. Fallback on BleakError to opposite response mode
    mock_client.write_gatt_char.side_effect = [BleakError("Write failed"), None]
    res_fallback = await KarofiDataCoordinator._write_command_with_fallback(
        mock_client, mock_char, b"\x01test\x04"
    )
    assert res_fallback is True

    # 4. Both attempts fail
    mock_client.write_gatt_char.side_effect = BleakError("All write attempts failed")
    res_fail = await KarofiDataCoordinator._write_command_with_fallback(
        mock_client, mock_char, b"\x01test\x04"
    )
    assert res_fail is False

    # 5. Direct OSError failure
    mock_client.write_gatt_char.side_effect = OSError("Connection aborted")
    res_os_fail = await KarofiDataCoordinator._write_command_with_fallback(
        mock_client, mock_char, b"\x01test\x04"
    )
    assert res_os_fail is False


async def test_coordinator_read_characteristic_safe() -> None:
    """Test _read_characteristic_safe returns bytearray or handles BleakError and OSError."""
    mock_client = AsyncMock()

    # Success
    mock_client.read_gatt_char.return_value = b"test_data"
    res = await KarofiDataCoordinator._read_characteristic_safe(mock_client, "uuid1")
    assert res == bytearray(b"test_data")

    # BleakError
    mock_client.read_gatt_char.side_effect = BleakError("GATT read error")
    res_bleak_err = await KarofiDataCoordinator._read_characteristic_safe(mock_client, "uuid1")
    assert res_bleak_err == bytearray()

    # OSError
    mock_client.read_gatt_char.side_effect = OSError("Socket error")
    res_os_err = await KarofiDataCoordinator._read_characteristic_safe(mock_client, "uuid1")
    assert res_os_err == bytearray()


async def test_coordinator_subscribe_tds_notification_id_notify(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _subscribe_tds_notification when device notifies ID triggering characteristic read.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_client = AsyncMock()

    async def _mock_start_notify(char: object, callback: Any) -> None:
        """Simulate device notifying ID update.

        Args:
            char: Characteristic object.
            callback: Notification callback callable.
        """
        callback(char, bytearray(b'{"id":"12ac"}'))

    mock_client.start_notify.side_effect = _mock_start_notify
    mock_client.read_gatt_char.return_value = b'{"pc":"iro_tds","d":18}'
    mock_client.write_gatt_char.return_value = None

    result = await coordinator._subscribe_tds_notification(
        mock_client,
        target="0000ac02-0000-1000-8000-00805f9b34fb",
        read_target="000012ac-0000-1000-8000-00805f9b34fb",
    )
    assert coordinator._parse_tds_reading(result) == 18


async def test_coordinator_subscribe_tds_notification_start_notify_failure(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _subscribe_tds_notification returns empty bytearray when start_notify fails.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_client = AsyncMock()
    mock_client.start_notify.side_effect = BleakError("Failed to enable notifications")

    result = await coordinator._subscribe_tds_notification(
        mock_client, target="0000ac02-0000-1000-8000-00805f9b34fb"
    )
    assert result == bytearray()


async def test_coordinator_read_tds_characteristic_post_cmd_read(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _read_tds_characteristic resolves TDS on follow-up read after command.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_client = AsyncMock()
    mock_client.services = None

    # First read (direct) returns handshake frame
    # Follow-up read returns sensor data
    mock_client.read_gatt_char.side_effect = [
        bytes.fromhex("017b226574223a224e50227d04"),
        b'{"pc":"iro_tds","d":22}',
    ]

    async def _mock_start_notify(char: object, callback: Any) -> None:
        """Notification does not produce TDS (e.g. handshake echo)."""
        callback(char, bytearray(bytes.fromhex("017b226574223a224e50227d04")))

    mock_client.start_notify.side_effect = _mock_start_notify

    result = await coordinator._read_tds_characteristic(mock_client)
    assert coordinator._parse_tds_reading(result) == 22


def test_coordinator_parse_json_payload_array(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _parse_json_payload decodes JSON array payloads with TDS.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    assert coordinator._parse_json_payload(b'[{"pc":"iro_tds","d":25}]') == {
        "pc": "iro_tds",
        "d": 25,
    }
    assert coordinator._parse_json_payload(b'[{"pc":"other","d":1},{"pc":"iro_tds","d":35}]') == {
        "pc": "iro_tds",
        "d": 35,
    }
    assert coordinator._parse_json_payload(b'[{"id":"12ac"}]') == {"id": "12ac"}
    assert coordinator._parse_json_payload(b"[not valid json]") is None


def test_coordinator_process_tds_payload_array(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _process_tds_payload handles JSON array containing TDS object.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    coordinator.data.tds = None
    coordinator.data.quality = WaterQuality.UNKNOWN

    coordinator._process_tds_payload(b'[{"pc":"iro_tds","d":20}]')
    assert coordinator.data.tds == 20
    assert coordinator.data.quality == WaterQuality.GOOD
    assert coordinator.data.connected is True


def test_coordinator_get_readable_data_characteristics(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test filtering and ordering of candidate readable data characteristics.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_client = MagicMock()
    mock_client.services = None
    assert coordinator._get_readable_data_characteristics(mock_client) == []

    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    mock_client.services = mock_services
    mock_services.get_service.return_value = None
    assert coordinator._get_readable_data_characteristics(mock_client) == []

    mock_service = MagicMock()
    char_cmd = _mock_characteristic(
        "0000ac02-0000-1000-8000-00805f9b34fb", ["read", "write", "notify"]
    )
    char_data1 = _mock_characteristic("0000ac03-0000-1000-8000-00805f9b34fb", ["read", "notify"])
    char_data2 = _mock_characteristic("0000ac04-0000-1000-8000-00805f9b34fb", ["read", "notify"])

    mock_service.characteristics = [char_cmd, char_data1, char_data2]
    mock_services.get_service.return_value = mock_service

    # ac02 is excluded as a command channel
    candidates = coordinator._get_readable_data_characteristics(mock_client)
    assert candidates == [char_data1, char_data2]

    # Prioritizes known TDS UUID
    coordinator._known_tds_char_uuid = "0000ac04-0000-1000-8000-00805f9b34fb"
    candidates_sorted = coordinator._get_readable_data_characteristics(mock_client)
    assert candidates_sorted == [char_data2, char_data1]


async def test_coordinator_scan_data_characteristics_success(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test scanning service data characteristics when direct read only returns handshake echo.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_client = AsyncMock()
    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    mock_client.services = mock_services

    char_cmd = _mock_characteristic(
        "0000ac02-0000-1000-8000-00805f9b34fb", ["read", "write", "notify"]
    )
    char_tds = _mock_characteristic("0000ac03-0000-1000-8000-00805f9b34fb", ["read", "notify"])
    char_filter_lifetime = _mock_characteristic(
        "0000ac04-0000-1000-8000-00805f9b34fb", ["read", "notify"]
    )
    char_filter_remaining = _mock_characteristic(
        "0000ac05-0000-1000-8000-00805f9b34fb", ["read", "notify"]
    )

    mock_service = MagicMock()
    mock_service.characteristics = [char_cmd, char_tds, char_filter_lifetime, char_filter_remaining]
    mock_services.get_service.return_value = mock_service
    mock_services.get_characteristic.side_effect = lambda u: next(
        (char for char in mock_service.characteristics if char.uuid == str(u)), None
    )

    async def _mock_read(target: object) -> bytes:
        """Simulate reading characteristic bytes based on target UUID.

        Args:
            target: Characteristic target object or UUID.

        Returns:
            Mocked payload bytes.
        """
        target_uuid = getattr(target, "uuid", str(target))
        if "ac02" in target_uuid:
            return bytes.fromhex("017b226574223a224e50227d04")
        if "ac03" in target_uuid:
            return b'{"pc":"iro_tds","d":42}'
        if "ac04" in target_uuid:
            return b'{"pc":"iro_lTime","d":[4320,8640]}'
        if "ac05" in target_uuid:
            return b'{"pc":"iro_rtr","d":[2160,4320]}'
        return b""

    mock_client.read_gatt_char.side_effect = _mock_read
    mock_client.write_gatt_char.return_value = None
    mock_client.start_notify.side_effect = BleakError("CCCD descriptor missing")

    coordinator._known_tds_char_uuid = None
    result = await coordinator._read_tds_characteristic(mock_client)

    assert coordinator._parse_tds_reading(result) == 42
    assert coordinator._known_tds_char_uuid == "0000ac03-0000-1000-8000-00805f9b34fb"
    coordinator._process_tds_payload(result)
    assert coordinator.get_known_filter_keys() == ["filter_1", "filter_2"]
    assert coordinator.get_filter_lifetime_days("filter_1") == 180
    assert coordinator.get_filter_remaining_days("filter_1") == 90


async def test_coordinator_direct_tds_read_still_collects_filter_telemetry(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test a successful direct TDS read does not skip filter telemetry polling.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    tds_payload = bytearray(b'{"pc":"iro_tds","d":9}')
    filter_payload = bytearray(b'{"pc":"iro_rtr","d":[1200]}')
    read_char = _mock_characteristic("0000ac0a-0000-1000-8000-00805f9b34fb", ["read"])
    notify_char = _mock_characteristic("0000ac02-0000-1000-8000-00805f9b34fb", ["notify"])
    mock_client = AsyncMock()
    send_read_request = AsyncMock()
    scan_characteristics = AsyncMock(return_value=(filter_payload, bytearray()))

    with (
        patch.object(
            coordinator,
            "_resolve_io_targets",
            return_value=(read_char, notify_char, notify_char, read_char, True),
        ),
        patch.object(
            coordinator,
            "_try_direct_read",
            AsyncMock(return_value=(tds_payload, tds_payload)),
        ),
        patch.object(coordinator, "_send_read_request", send_read_request),
        patch.object(coordinator, "_scan_candidate_data_chars", scan_characteristics),
    ):
        result = await coordinator._read_tds_characteristic(mock_client)

    assert coordinator._parse_tds_reading(result) == 9
    assert [payload["pc"] for payload in coordinator._extract_all_payload_dicts(result)] == [
        "iro_tds",
        "iro_rtr",
    ]
    send_read_request.assert_awaited_once_with(mock_client, notify_char, notify_char)
    scan_args = scan_characteristics.await_args
    assert scan_args is not None
    assert scan_args.args == (mock_client, read_char, tds_payload)
    assert isinstance(scan_args.kwargs["timeout_at"], float)


async def test_coordinator_supplemental_scan_preserves_direct_tds_uuid(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Keep the direct TDS UUID when supplemental telemetry includes another TDS source."""
    tds_payload = bytearray(b'{"pc":"iro_tds","d":9}')
    candidate_payload = bytearray(b'{"pc":"iro_tds","d":42}')
    filter_payload = bytearray(b'{"pc":"iro_rtr","d":[1200]}')
    read_char = _mock_characteristic("0000ac0a-0000-1000-8000-00805f9b34fb", ["read"])
    candidate_char = _mock_characteristic("0000ac03-0000-1000-8000-00805f9b34fb", ["read"])
    filter_char = _mock_characteristic("0000ac04-0000-1000-8000-00805f9b34fb", ["read"])
    mock_client = AsyncMock()
    mock_client.services = MagicMock(spec=BleakGATTServiceCollection)
    mock_client.read_gatt_char.side_effect = [tds_payload, candidate_payload, filter_payload]

    with (
        patch.object(
            coordinator,
            "_resolve_io_targets",
            return_value=(read_char, read_char, read_char, read_char, True),
        ),
        patch.object(
            coordinator,
            "_get_readable_data_characteristics",
            return_value=[read_char, candidate_char, filter_char],
        ),
        patch.object(coordinator, "_send_read_request", AsyncMock()),
    ):
        result = await coordinator._read_tds_characteristic(mock_client)

    assert coordinator._known_tds_char_uuid == read_char.uuid
    assert coordinator._parse_tds_reading(result) == 9
    assert [payload["d"] for payload in coordinator._extract_all_payload_dicts(result)] == [
        9,
        42,
        [1200],
    ]


async def test_coordinator_direct_tds_survives_supplemental_poll_timeout(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test supplemental telemetry timeout does not discard a direct TDS reading.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    tds_payload = bytearray(b'{"pc":"iro_tds","d":9}')
    read_char = _mock_characteristic("0000ac0a-0000-1000-8000-00805f9b34fb", ["read"])
    notify_char = _mock_characteristic("0000ac02-0000-1000-8000-00805f9b34fb", ["notify"])
    mock_client = AsyncMock()
    scan_characteristics = AsyncMock()

    with (
        patch.object(
            coordinator,
            "_resolve_io_targets",
            return_value=(read_char, notify_char, notify_char, read_char, True),
        ),
        patch.object(
            coordinator,
            "_try_direct_read",
            AsyncMock(return_value=(tds_payload, tds_payload)),
        ),
        patch.object(
            coordinator,
            "_send_read_request",
            AsyncMock(side_effect=TimeoutError),
        ),
        patch.object(coordinator, "_scan_candidate_data_chars", scan_characteristics),
    ):
        result = await coordinator._read_tds_characteristic(mock_client)

    assert result == tds_payload
    scan_characteristics.assert_not_awaited()


async def test_coordinator_skips_supplemental_poll_near_ble_deadline(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test little remaining BLE time skips supplemental polling after direct TDS.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    tds_payload = bytearray(b'{"pc":"iro_tds","d":9}')
    read_char = _mock_characteristic("0000ac0a-0000-1000-8000-00805f9b34fb", ["read"])
    notify_char = _mock_characteristic("0000ac02-0000-1000-8000-00805f9b34fb", ["notify"])
    mock_client = AsyncMock()
    send_read_request = AsyncMock()
    scan_characteristics = AsyncMock()
    deadline = asyncio.get_running_loop().time() + 1

    with (
        patch.object(
            coordinator,
            "_resolve_io_targets",
            return_value=(read_char, notify_char, notify_char, read_char, True),
        ),
        patch.object(
            coordinator,
            "_try_direct_read",
            AsyncMock(return_value=(tds_payload, tds_payload)),
        ),
        patch.object(coordinator, "_send_read_request", send_read_request),
        patch.object(coordinator, "_scan_candidate_data_chars", scan_characteristics),
    ):
        result = await coordinator._read_tds_characteristic(mock_client, deadline=deadline)

    assert result == tds_payload
    send_read_request.assert_not_awaited()
    scan_characteristics.assert_not_awaited()


async def test_coordinator_characteristic_scan_timeout_returns_partial_telemetry(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test characteristic scan timeout retains telemetry read before expiration.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    first_char = _mock_characteristic("0000ac03-0000-1000-8000-00805f9b34fb", ["read"])
    second_char = _mock_characteristic("0000ac04-0000-1000-8000-00805f9b34fb", ["read"])
    mock_client = MagicMock()
    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    mock_client.services = mock_services
    mock_service = MagicMock()
    mock_service.characteristics = [first_char, second_char]
    mock_services.get_service.return_value = mock_service
    filter_payload = bytearray(b'{"pc":"iro_rtr","d":[1200]}')

    async def _read_characteristic(
        _client: object, characteristic: BleakGATTCharacteristic
    ) -> bytearray:
        """Return filter data once, then wait long enough to trigger scan timeout."""
        if characteristic == first_char:
            return filter_payload
        await asyncio.sleep(1)
        return bytearray()

    with patch.object(
        coordinator,
        "_read_characteristic_safe",
        side_effect=_read_characteristic,
    ):
        telemetry, _ = await coordinator._scan_candidate_data_chars(
            mock_client, None, bytearray(), timeout_at=asyncio.get_running_loop().time() + 0.02
        )

    assert telemetry == filter_payload


async def test_coordinator_filter_telemetry_uses_follow_up_tds(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test filter-only telemetry is retained when follow-up reading finds TDS.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    filter_payload = bytearray(b'{"pc":"iro_rtr","d":[1200]}')
    tds_payload = bytearray(b'{"pc":"iro_tds","d":9}')
    read_char = _mock_characteristic("0000ac0a-0000-1000-8000-00805f9b34fb", ["read"])
    notify_char = _mock_characteristic("0000ac02-0000-1000-8000-00805f9b34fb", ["notify"])
    mock_client = AsyncMock()
    follow_up_read = AsyncMock(return_value=(tds_payload, bytearray()))

    with (
        patch.object(
            coordinator,
            "_resolve_io_targets",
            return_value=(read_char, notify_char, notify_char, read_char, True),
        ),
        patch.object(
            coordinator,
            "_try_direct_read",
            AsyncMock(return_value=(None, bytearray())),
        ),
        patch.object(
            coordinator,
            "_try_active_polling",
            AsyncMock(return_value=(None, filter_payload)),
        ),
        patch.object(
            coordinator,
            "_scan_candidate_data_chars",
            AsyncMock(return_value=(None, bytearray())),
        ),
        patch.object(coordinator, "_try_follow_up_read", follow_up_read),
    ):
        result = await coordinator._read_tds_characteristic(mock_client)

    assert [payload["pc"] for payload in coordinator._extract_all_payload_dicts(result)] == [
        "iro_tds",
        "iro_rtr",
    ]
    follow_up_read.assert_awaited_once_with(
        mock_client, read_char, True, bytearray(), timeout_at=None
    )


async def test_coordinator_active_polling_tds_precedes_candidate_tds(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Keep active-polling TDS first while retaining candidate telemetry."""
    polling_payload = bytearray(b'{"pc":"iro_tds","d":9}')
    candidate_payload = bytearray(b'{"pc":"iro_tds","d":42}')
    read_char = _mock_characteristic("0000ac0a-0000-1000-8000-00805f9b34fb", ["read"])
    notify_char = _mock_characteristic("0000ac02-0000-1000-8000-00805f9b34fb", ["notify"])
    mock_client = AsyncMock()

    with (
        patch.object(
            coordinator,
            "_resolve_io_targets",
            return_value=(read_char, notify_char, notify_char, read_char, True),
        ),
        patch.object(coordinator, "_try_direct_read", AsyncMock(return_value=(None, bytearray()))),
        patch.object(
            coordinator,
            "_try_active_polling",
            AsyncMock(return_value=(polling_payload, polling_payload)),
        ),
        patch.object(
            coordinator,
            "_scan_candidate_data_chars",
            AsyncMock(return_value=(candidate_payload, bytearray())),
        ) as scan_characteristics,
    ):
        result = await coordinator._read_tds_characteristic(mock_client)

    assert coordinator._parse_tds_reading(result) == 9
    assert [payload["d"] for payload in coordinator._extract_all_payload_dicts(result)] == [9, 42]
    scan_characteristics.assert_awaited_once_with(
        mock_client, read_char, bytearray(), timeout_at=None
    )


def test_coordinator_process_invalid_then_valid_tds_frame(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Continue past invalid TDS frames and store the first valid reading."""
    coordinator._process_tds_payload(b'{"pc":"iro_tds","d":65535}\n{"pc":"iro_tds","d":27}')

    assert coordinator.data.tds == 27
    assert coordinator.data.quality == WaterQuality.GOOD


async def test_coordinator_notify_id_resolves_service_characteristic(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test notification containing ID resolves matching service characteristic and reads TDS.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_client = AsyncMock()
    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    mock_client.services = mock_services

    char_tds = MagicMock(spec=BleakGATTCharacteristic)
    char_tds.uuid = "0000ac03-0000-1000-8000-00805f9b34fb"

    mock_service = MagicMock()
    mock_service.characteristics = [char_tds]
    mock_services.get_service.return_value = mock_service

    async def _mock_start_notify(char: object, callback: Any) -> None:
        """Simulate notification with AC03 ID.

        Args:
            char: Characteristic object.
            callback: Notification callback callable.
        """
        callback(char, bytearray(b'{"id":"AC03"}'))

    mock_client.start_notify.side_effect = _mock_start_notify
    mock_client.read_gatt_char.return_value = b'{"pc":"iro_tds","d":28}'
    mock_client.write_gatt_char.return_value = None

    result = await coordinator._subscribe_tds_notification(
        mock_client,
        target="0000ac02-0000-1000-8000-00805f9b34fb",
    )
    assert coordinator._parse_tds_reading(result) == 28
    assert coordinator._known_tds_char_uuid == "0000ac03-0000-1000-8000-00805f9b34fb"


def test_coordinator_parse_concatenated_json_stream(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test parsing concatenated multi-frame JSON packets from BLE notification stream.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    # 1. ACK handshake packet followed by TDS data packet in single buffer
    stream_payload = b'\x01{"et":"NP"}\x04\x01{"pc":"iro_tds","d":45}\x04'
    assert coordinator._parse_tds_reading(stream_payload) == 45

    # 2. Back-to-back JSON objects without framing bytes
    raw_concatenated = b'{"et":"NP"}{"pc":"iro_tds","d":52}'
    assert coordinator._parse_tds_reading(raw_concatenated) == 52

    # 3. Concatenated list with TDS data
    stream_list = b'{"et":"NP"}[{"pc":"iro_tds","d":63}]'
    assert coordinator._parse_tds_reading(stream_list) == 63


def test_coordinator_coerce_int_overflow_and_invalid() -> None:
    """Test _coerce_int handles infinite, NaN, and corrupt float values safely."""
    assert KarofiDataCoordinator._coerce_int("inf") is None
    assert KarofiDataCoordinator._coerce_int("-inf") is None
    assert KarofiDataCoordinator._coerce_int(float("inf")) is None
    assert KarofiDataCoordinator._coerce_int(float("nan")) is None
    assert KarofiDataCoordinator._coerce_int("invalid") is None
    assert KarofiDataCoordinator._coerce_int(None) is None


def test_coordinator_non_tds_d_field_ignored(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test payloads with generic 'd' field and non-TDS keys are rejected.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    # Payload with 'state' and 'd' must not be mistaken for TDS reading
    assert coordinator._parse_tds_reading(b'{"state":"idle","d":0}') is None
    assert coordinator._parse_tds_reading(b'{"status":"running","d":1}') is None
    # Payload with only 'd' should be accepted
    assert coordinator._parse_tds_reading(b'{"d":75}') == 75
    # Payload with 'd' and 'id' should be accepted
    assert coordinator._parse_tds_reading(b'{"d":80,"id":"12ac"}') == 80


def test_coordinator_filter_calculation_helpers(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test get_filter_remaining_days and get_filter_percent coordinator methods.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    now = dt_util.utcnow()
    # Filter installed 30 days ago (180 - 30 = 150 days remaining)
    coordinator.data.filter_reset_dates["filter_1"] = (now - timedelta(days=30)).isoformat()
    assert coordinator.get_filter_remaining_days("filter_1") == 150
    # 150 / 180 * 100 = 83%
    assert coordinator.get_filter_percent("filter_1") == 83

    # Filter installed in the future (NTP skew): clamped to lifetime
    coordinator.data.filter_reset_dates["filter_2"] = (now + timedelta(days=10)).isoformat()
    assert coordinator.get_filter_remaining_days("filter_2") == 270
    assert coordinator.get_filter_percent("filter_2") == 100

    # Unknown filter key: fallback to 365
    assert coordinator.get_filter_remaining_days("unknown_stage") == 365
    assert coordinator.get_filter_percent("unknown_stage") == 100

    # Zero or negative max_days: guarded against ZeroDivisionError and returns 0
    assert coordinator.get_filter_remaining_days("filter_1", max_days=0) == 0
    assert coordinator.get_filter_percent("filter_1", max_days=0) == 0
    assert coordinator.get_filter_remaining_days("filter_1", max_days=-5) == 0
    assert coordinator.get_filter_percent("filter_1", max_days=-5) == 0


async def test_coordinator_bleak_error_duplicate_uuid_handled(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test BleakError on duplicate characteristic UUIDs is suppressed gracefully.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    mock_client = MagicMock()
    mock_services = MagicMock(spec=BleakGATTServiceCollection)
    mock_client.services = mock_services

    # Simulate BleakError when querying get_characteristic with candidate UUID
    mock_services.get_characteristic.side_effect = BleakError(
        "Multiple Characteristics with this UUID"
    )

    char_fallback = MagicMock(spec=BleakGATTCharacteristic)
    char_fallback.uuid = "000012ac-0000-1000-8000-00805f9b34fb"
    char_fallback.properties = ["read"]

    mock_service = MagicMock()
    mock_service.characteristics = [char_fallback]
    mock_services.get_service.return_value = mock_service

    resolved = coordinator._resolve_tds_characteristic(mock_client)
    assert resolved == char_fallback


async def test_coordinator_update_data_timeout_handled(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test timeout during BLE connection in _async_update_data is handled.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    coordinator.data.connected = True

    with (
        patch.object(coordinator, "_get_ble_device", return_value=mock_ble_device),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            side_effect=TimeoutError("BLE connection timeout"),
        ),
    ):
        data = await coordinator._async_update_data()
        assert data.connected is False


def test_coordinator_get_ble_device_discovered_service_info_fallback(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test _get_ble_device fallback to async_discovered_service_info.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    mock_service_info = MagicMock(spec=BluetoothServiceInfoBleak)
    mock_service_info.address = "AA:BB:CC:11:22:33"
    mock_service_info.device = mock_ble_device

    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.karofi.coordinator.async_discovered_service_info",
            return_value=[mock_service_info],
        ),
    ):
        dev = coordinator._get_ble_device()
        assert dev == mock_ble_device


@pytest.mark.parametrize(
    "payload",
    [
        b"{{malformed:json}",
        b'{"et":"unknown_event","info":"test"}',
    ],
)
def test_coordinator_process_tds_payload_malformed_and_missing_tds(
    coordinator: KarofiDataCoordinator, payload: bytes
) -> None:
    """Test _process_tds_payload with malformed JSON and payload lacking TDS.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        payload: Byte payload to test without valid TDS.
    """
    coordinator.data.connected = True
    coordinator._process_tds_payload(payload)
    assert coordinator.data.connected
    assert coordinator.data.tds is None


def test_coordinator_masked_mac(coordinator: KarofiDataCoordinator) -> None:
    """Test coordinator exposes masked_mac without leaking sensitive info.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    assert coordinator.masked_mac == "**:**:**:**:22:33"


def test_coordinator_process_tds_payload_dynamic_filters_and_names(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _process_tds_payload dynamically creates filters and parses custom names.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    raw_payload = (
        b'\x01{"et":"NP","iro_tds":18,'
        b'"iro_rtr":[1200,2400,3600,4800,6000,7200,8400],'
        b'"iro_lTime":[4320,8640,8640,17280,8640,8640,8640],'
        b'"iro_names":['
        b'"Sediment","OCB-GAC","PP 1M","RO Membrane","Mineral","Hydrogen","Nano Silver"]}'
        b"\x04"
    )
    coordinator._process_tds_payload(raw_payload)

    assert coordinator.data.tds == 18
    assert coordinator.data.quality == WaterQuality.GOOD
    assert len(coordinator.data.filters) == 7
    assert coordinator.get_known_filter_keys() == [
        "filter_1",
        "filter_2",
        "filter_3",
        "filter_4",
        "filter_5",
        "filter_6",
        "filter_7",
    ]
    assert coordinator.get_filter_name("filter_1") == "Sediment"
    assert coordinator.get_filter_name("filter_2") == "OCB-GAC"
    assert coordinator.get_filter_name("filter_4") == "RO Membrane"
    assert coordinator.get_filter_name("filter_7") == "Nano Silver"
    assert coordinator.get_filter_remaining_days("filter_2") == 100  # 2400 hours // 24 = 100 days
    assert coordinator.get_filter_lifetime_days("filter_2") == 360  # 8640 hours // 24 = 360 days


def test_coordinator_process_payloads_preserves_first_tds_reading(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test later conflicting TDS frames do not replace the prioritized value.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    coordinator._process_tds_payload(
        b'{"d":12}\n{"pc":"iro_tds","d":9}\n{"pc":"iro_tds","d":99}\n{"pc":"iro_rtr","d":[1200]}'
    )

    assert coordinator.data.tds == 9
    assert coordinator.data.filters["filter_1"].remaining_days == 50


async def test_coordinator_async_send_reset_command_ble(
    coordinator: KarofiDataCoordinator,
    mock_ble_device: BLEDevice,
) -> None:
    """Test _async_send_reset_command sends command via BLE write characteristic.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        mock_ble_device: Fixture providing mock BLEDevice.
    """
    mock_client = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.write_gatt_char = AsyncMock()

    write_char = _mock_characteristic(
        KarofiCharacteristic.COMMAND,
        properties=[GattProperty.WRITE, GattProperty.WRITE_WITHOUT_RESPONSE],
    )
    mock_services = MagicMock(spec=BleakGATTServiceCollection)

    def _get_char(uuid: str) -> BleakGATTCharacteristic | None:
        """Return write characteristic if uuid matches COMMAND."""
        return write_char if uuid == KarofiCharacteristic.COMMAND else None

    mock_services.get_characteristic.side_effect = _get_char
    mock_client.services = mock_services

    with (
        patch(
            "custom_components.karofi.coordinator.async_ble_device_from_address",
            return_value=mock_ble_device,
        ),
        patch(
            "custom_components.karofi.coordinator.establish_connection",
            return_value=mock_client,
        ),
    ):
        await coordinator._async_send_reset_command(1)

        expected_command = b'\x01{"et":"CP","pc":"iro_rsft","d":1}\x04'
        mock_client.write_gatt_char.assert_awaited_once_with(
            write_char,
            expected_command,
            response=True,
        )


def test_coordinator_find_tds_prefers_combined_packets(coordinator: KarofiDataCoordinator) -> None:
    """Test _find_tds_in_packets prefers combined buffer when multiple packets arrive.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    # Single packet
    p_single = bytearray(b'{"et":"NP","iro_tds":25}')
    assert coordinator._find_tds_in_packets([p_single]) == p_single

    # Incomplete packet with marker does not match
    p1 = bytearray(b'\x01{"et":"NP","iro_rtr":[1200, 2400],')
    assert coordinator._find_tds_in_packets([p1]) is None

    # Multiple packets with telemetry split across frames match once combined
    p2 = bytearray(b'"iro_lTime":[4320, 8640]}\x04')
    combined = coordinator._find_tds_in_packets([p1, p2])
    assert combined is not None
    assert combined == p1 + p2


def test_coordinator_resolve_initial_dates_legacy_keys_filtered(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _resolve_initial_dates excludes legacy and non-standard keys.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    stored = {
        "filter_ro": "2026-01-01T00:00:00",
        "filter_1": "2026-01-01T00:00:00",
        "filter_mineral": "2026-01-01T00:00:00",
        "unsupported_key": "2026-01-01T00:00:00",
    }
    coordinator.hass.config_entries.async_update_entry(
        coordinator.entry,
        options={
            "initial_filter_reset_dates": {
                "filter_ro": "2026-01-01T00:00:00",
                "filter_2": "2026-02-01T00:00:00",
            }
        },
    )
    resolved, needs_save = coordinator._resolve_initial_dates(stored)
    assert resolved == {
        "filter_1": "2026-01-01T00:00:00",
        "filter_2": "2026-02-01T00:00:00",
    }
    assert "filter_ro" not in resolved
    assert "filter_mineral" not in resolved
    assert "unsupported_key" not in resolved
    assert needs_save is True


async def test_coordinator_set_filter_date_updates_existing_record(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test async_set_filter_date updates existing KarofiFilterData record fields.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    coordinator.data.filters["filter_1"] = KarofiFilterData(
        index=0,
        key="filter_1",
        lifetime_days=180,
        reset_date="2020-01-01T00:00:00+00:00",
        remaining_days=0,
        percent=0,
        remaining_hours=0,
    )
    frozen_now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    with patch("homeassistant.util.dt.utcnow", return_value=frozen_now):
        # Set to 30 days ago
        inst_date = frozen_now - timedelta(days=30)
        await coordinator.async_set_filter_date("filter_1", inst_date)
        fdata = coordinator.data.filters["filter_1"]
        assert fdata.reset_date == inst_date.isoformat()
        assert fdata.remaining_days == 150
        assert fdata.percent == round((150 / 180) * 100)
        assert fdata.remaining_hours == 150 * 24


def test_coordinator_process_tds_payload_delays_save(coordinator: KarofiDataCoordinator) -> None:
    """Test _process_tds_payload calls async_delay_save on filter updates.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    with patch.object(coordinator._store, "async_delay_save") as mock_delay_save:
        raw_payload = b'\x01{"et":"NP","iro_rtr":[1200],"iro_lTime":[4320]}\x04'
        coordinator._process_tds_payload(raw_payload)
        mock_delay_save.assert_called_once()
        args, _ = mock_delay_save.call_args
        assert callable(args[0])
        assert args[1] == 10

        # Subsequent payload with unchanged reset dates does not schedule another save
        coordinator._process_tds_payload(raw_payload)
        assert mock_delay_save.call_count == 1


async def test_coordinator_reset_filter_command_warning(
    coordinator: KarofiDataCoordinator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test async_reset_filter logs warning on BLE command failure while maintaining local reset.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
        caplog: Fixture capturing log messages.
    """
    with patch.object(coordinator, "_async_send_reset_command", AsyncMock(return_value=False)):
        await coordinator.async_reset_filter("filter_1")
        assert "Hardware reset command failed for filter_1" in caplog.text
        assert "filter_1" in coordinator.data.filter_reset_dates


def test_coordinator_coerce_int_list_preserves_none(coordinator: KarofiDataCoordinator) -> None:
    """Test _coerce_int_list preserves None for invalid entries when requested.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    raw = [1200, "corrupt", 3600, False, 4800]
    assert coordinator._coerce_int_list(raw, preserve_none=True) == [1200, None, 3600, None, 4800]
    assert coordinator._coerce_int_list(raw, preserve_none=False) == [1200, 3600, 4800]


def test_coordinator_update_single_filter_stage_leaves_date_unset_without_rtr(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _update_single_filter_stage does not fabricate a reset date when rtr_h is None.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    coordinator._update_single_filter_stage(0, None, 4320, None, "Sediment")
    assert "filter_1" not in coordinator.data.filter_reset_dates
    assert coordinator.data.filters["filter_1"].remaining_days is None


def test_coordinator_process_errors_empty_list_clears_errors(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _process_errors_from_payload clears errors on empty list.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    coordinator.data.errors = [1, 2, 3]
    assert coordinator._process_errors_from_payload({"iro_errs": []}) is True
    assert not coordinator.data.errors


def test_coordinator_is_handshake_payload_excludes_telemetry(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _is_handshake_payload excludes frames containing telemetry fields.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    assert coordinator._is_handshake_payload(b'{"et":"NP"}') is True
    assert coordinator._is_handshake_payload(b'{"et":"CP"}') is True

    assert coordinator._is_handshake_payload(b'{"et":"NP","iro_rtt":[100]}') is False
    assert coordinator._is_handshake_payload(b'{"et":"NP","iro_errs":[1]}') is False
    assert coordinator._is_handshake_payload(b'{"et":"NP","names":["Sediment"]}') is False
    assert coordinator._is_handshake_payload(b'{"et":"NP","tds":45}') is False


def test_coordinator_telemetry_synced_tracking(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test telemetry_synced tracks whether filter status originated from telemetry or reset date.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    # 1. Update from telemetry sets telemetry_synced to True
    coordinator._update_single_filter_stage(0, 2400, 4320, None, "Sediment")
    assert coordinator.data.filters["filter_1"].telemetry_synced is True
    assert coordinator.data.filters["filter_1"].remaining_days == 100
    assert coordinator.get_filter_remaining_days("filter_1") == 100

    # 2. Applying installation date clears telemetry_synced to False and calculates from date
    past_date = dt_util.utcnow() - timedelta(days=20)
    coordinator._apply_filter_installation_date("filter_1", past_date)
    assert coordinator.data.filters["filter_1"].telemetry_synced is False
    assert coordinator.get_filter_remaining_days("filter_1") == 160

    # Telemetry from the poll that was already in progress is stale.
    coordinator._process_tds_payload(
        b'{"iro_rtr":[1200],"iro_lTime":[8760],"names":["Sediment"]}',
        telemetry_generation=0,
    )
    fdata = coordinator.data.filters["filter_1"]
    assert fdata.telemetry_synced is False
    assert fdata.remaining_days == 160
    assert fdata.lifetime_hours == 4320
    assert fdata.lifetime_days == 180
    assert fdata.percent == round((160 / 180) * 100)
    assert coordinator.get_filter_remaining_days("filter_1") == 160

    # A later poll is authoritative and reconciles the local value with the device.
    coordinator._process_tds_payload(
        b'{"iro_rtr":[1200],"iro_lTime":[8760],"names":["Sediment"]}',
        telemetry_generation=1,
    )
    assert fdata.telemetry_synced is True
    assert fdata.remaining_days == 50
    assert fdata.lifetime_hours == 8760
    assert fdata.lifetime_days == 365
    assert fdata.percent == round((50 / 365) * 100)
    assert coordinator.get_filter_remaining_days("filter_1") == 50


async def test_coordinator_try_active_polling_accepts_filter_only_telemetry(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _try_active_polling accepts filter-only telemetry without requiring TDS reading.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    client = MagicMock()
    filter_only_payload = bytearray(b'{"et":"NP","iro_rtr":[1200],"iro_lTime":[4320]}')
    with patch.object(
        coordinator,
        "_subscribe_tds_notification",
        AsyncMock(return_value=filter_only_payload),
    ):
        result, raw = await coordinator._try_active_polling(
            client, "char_notify", "char_cmd", "char_read"
        )
        assert result == filter_only_payload
        assert raw == filter_only_payload


async def test_coordinator_handle_notify_packet_resolves_only_on_tds(
    coordinator: KarofiDataCoordinator,
) -> None:
    """Test _handle_notify_packet does not resolve future on filter-only telemetry but does on TDS.

    Args:
        coordinator: Fixture providing KarofiDataCoordinator.
    """
    client = MagicMock()
    loop = asyncio.get_running_loop()
    notify_future: asyncio.Future[bytearray] = loop.create_future()
    received_packets: list[bytearray] = []
    bg_tasks: set[asyncio.Task[None]] = set()

    # 1. Filter-only packet does not resolve future
    filter_packet = bytearray(b'{"et":"NP","iro_rtr":[1200],"iro_lTime":[4320]}')
    coordinator._handle_notify_packet(
        client,
        filter_packet,
        "char_notify",
        "char_read",
        received_packets,
        notify_future,
        bg_tasks,
    )
    assert not notify_future.done()
    assert len(received_packets) == 1

    # 2. Packet with TDS resolves future early
    tds_packet = bytearray(b'{"tds":45}')
    coordinator._handle_notify_packet(
        client,
        tds_packet,
        "char_notify",
        "char_read",
        received_packets,
        notify_future,
        bg_tasks,
    )
    assert notify_future.done()
    assert notify_future.result() == filter_packet + tds_packet
    assert len(received_packets) == 2
