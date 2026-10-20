"""Tests for Karofi config flow."""

from unittest.mock import patch

from bleak.backends.device import BLEDevice
from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.karofi.const import DOMAIN, KarofiCharacteristic


def _create_service_info(
    name: str,
    address: str,
    service_uuids: list[str] | None = None,
    rssi: int = -65,
) -> BluetoothServiceInfoBleak:
    """Create a typed BluetoothServiceInfoBleak test object.

    Args:
        name: BLE advertised device name.
        address: MAC address.
        service_uuids: List of advertised service UUIDs.
        rssi: Received Signal Strength Indicator.

    Returns:
        Constructed BluetoothServiceInfoBleak instance.
    """
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=rssi,
        manufacturer_data={},
        service_data={},
        service_uuids=service_uuids or [],
        source="local",
        device=BLEDevice(address, name, None),
        advertisement=None,
        connectable=True,
        time=0.0,
        tx_power=None,
    )


async def test_user_flow_with_discovered_device(hass: HomeAssistant) -> None:
    """Test user flow when devices are discovered via Bluetooth.

    Args:
        hass: HomeAssistant test instance.
    """
    mock_discovery = _create_service_info(
        name="Karofi Device 1",
        address="AA:BB:CC:DD:EE:FF",
        service_uuids=[KarofiCharacteristic.SERVICE],
    )

    with patch(
        "custom_components.karofi.config_flow.async_discovered_service_info",
        return_value=[mock_discovery],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        assert result.get("type") is FlowResultType.FORM
        assert result.get("step_id") == "user"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_MAC: "aa:bb:cc:dd:ee:ff",
                CONF_NAME: "Máy lọc nước tầng 1",
            },
        )
        assert result2.get("type") is FlowResultType.CREATE_ENTRY
        assert result2.get("title") == "Máy lọc nước tầng 1"
        data = result2.get("data")
        assert isinstance(data, dict)
        assert data[CONF_MAC] == "aa:bb:cc:dd:ee:ff"
        assert data[CONF_NAME] == "Máy lọc nước tầng 1"


async def test_user_flow_no_devices_redirects_to_manual(hass: HomeAssistant) -> None:
    """Test user flow redirects to manual input when no devices are discovered.

    Args:
        hass: HomeAssistant test instance.
    """
    with patch(
        "custom_components.karofi.config_flow.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        assert result.get("type") is FlowResultType.FORM
        assert result.get("step_id") == "manual"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_MAC: "11:22:33:44:55:66",
                CONF_NAME: "Máy lọc nước phòng khách",
            },
        )
        assert result2.get("type") is FlowResultType.CREATE_ENTRY
        assert result2.get("title") == "Máy lọc nước phòng khách"
        data = result2.get("data")
        assert isinstance(data, dict)
        assert data[CONF_MAC] == "11:22:33:44:55:66"
        assert data[CONF_NAME] == "Máy lọc nước phòng khách"


async def test_user_flow_select_manual_option(hass: HomeAssistant) -> None:
    """Test user selecting manual option from device dropdown.

    Args:
        hass: HomeAssistant test instance.
    """
    mock_discovery = _create_service_info(
        name="TCM-112233",
        address="AA:BB:CC:DD:EE:00",
        rssi=-70,
    )

    with patch(
        "custom_components.karofi.config_flow.async_discovered_service_info",
        return_value=[mock_discovery],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        assert result.get("type") is FlowResultType.FORM
        assert result.get("step_id") == "user"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_MAC: "manual",
                CONF_NAME: "Máy lọc nước Karofi",
            },
        )
        assert result2.get("type") is FlowResultType.FORM
        assert result2.get("step_id") == "manual"

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {
                CONF_MAC: "A1:B2:C3:D4:E5:F6",
                CONF_NAME: "Máy lọc nước nhà bếp",
            },
        )
        assert result3.get("type") is FlowResultType.CREATE_ENTRY
        assert result3.get("title") == "Máy lọc nước nhà bếp"
        data = result3.get("data")
        assert isinstance(data, dict)
        assert data[CONF_MAC] == "a1:b2:c3:d4:e5:f6"
        assert data[CONF_NAME] == "Máy lọc nước nhà bếp"


async def test_user_flow_filters_third_party_devices(hass: HomeAssistant) -> None:
    """Test user flow filters out non-Karofi Bluetooth devices and redirects to manual.

    Args:
        hass: HomeAssistant test instance.
    """
    mock_discovery = _create_service_info(
        name="Smart TV / Speaker",
        address="11:22:33:AA:BB:CC",
        rssi=-50,
    )

    with patch(
        "custom_components.karofi.config_flow.async_discovered_service_info",
        return_value=[mock_discovery],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        # Because the discovered device is not Karofi, it gets filtered out -> redirects to manual
        assert result.get("type") is FlowResultType.FORM
        assert result.get("step_id") == "manual"


async def test_user_flow_invalid_mac(hass: HomeAssistant) -> None:
    """Test user entering an invalid MAC address returns form with errors.

    Args:
        hass: HomeAssistant test instance.
    """
    with patch(
        "custom_components.karofi.config_flow.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        assert result.get("step_id") == "manual"

        # 1. Non-hex characters
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_MAC: "AA:BB:CC:DD:EE:GG",
                CONF_NAME: "Karofi",
            },
        )
        assert result2.get("type") is FlowResultType.FORM
        assert result2.get("step_id") == "manual"
        assert result2.get("errors") == {CONF_MAC: "invalid_mac"}

        # 2. Arbitrary string
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {
                CONF_MAC: "invalid-mac-address",
                CONF_NAME: "Karofi",
            },
        )
        assert result3.get("type") is FlowResultType.FORM
        assert result3.get("step_id") == "manual"
        assert result3.get("errors") == {CONF_MAC: "invalid_mac"}


async def test_user_flow_already_configured(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test duplicate configuration is rejected in user flow.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Existing mock entry.
    """
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.karofi.config_flow.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_MAC: "aa:bb:cc:11:22:33",
                CONF_NAME: "Karofi Duplicate",
            },
        )
        assert result2.get("type") is FlowResultType.ABORT
        assert result2.get("reason") == "already_configured"


async def test_user_flow_filters_already_configured_devices(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test user flow filters out already configured devices from dropdown.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Existing mock entry.
    """
    mock_config_entry.add_to_hass(hass)
    mock_discovery = _create_service_info(
        name="Karofi Configured",
        address="AA:BB:CC:11:22:33",
    )
    with patch(
        "custom_components.karofi.config_flow.async_discovered_service_info",
        return_value=[mock_discovery],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )
        # Because the only discovered device is already configured,
        # device_choices is empty -> redirects to manual
        assert result.get("type") is FlowResultType.FORM
        assert result.get("step_id") == "manual"


async def test_bluetooth_discovery_flow(hass: HomeAssistant) -> None:
    """Test automatic discovery via Bluetooth and user confirmation.

    Args:
        hass: HomeAssistant test instance.
    """
    discovery_info = _create_service_info(
        name="karofi_k365",
        address="12:34:56:78:9A:BC",
        service_uuids=[KarofiCharacteristic.SERVICE],
        rssi=-50,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=discovery_info,
    )
    assert result.get("type") is FlowResultType.FORM
    assert result.get("step_id") == "bluetooth_confirm"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {},
    )
    assert result2.get("type") is FlowResultType.CREATE_ENTRY
    data = result2.get("data")
    assert isinstance(data, dict)
    assert data[CONF_MAC] == "12:34:56:78:9a:bc"
    assert data[CONF_NAME] == "karofi_k365"
    assert result2.get("title") == "karofi_k365"


async def test_bluetooth_discovery_already_configured(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test bluetooth discovery aborts when device is already configured.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Preconfigured mock entry.
    """
    mock_config_entry.add_to_hass(hass)

    discovery_info = _create_service_info(
        name="karofi_k365",
        address="AA:BB:CC:11:22:33",
        service_uuids=[KarofiCharacteristic.SERVICE],
        rssi=-50,
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=discovery_info,
    )
    assert result.get("type") is FlowResultType.ABORT
    assert result.get("reason") == "already_configured"
