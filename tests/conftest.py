"""Fixtures and common test utilities for Karofi tests."""

from unittest.mock import MagicMock

import pytest
from bleak.backends.device import BLEDevice
from homeassistant.const import CONF_MAC, CONF_NAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.karofi.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations for all tests.

    Args:
        enable_custom_integrations: HA test fixture enabling custom components.
    """


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry.

    Returns:
        MockConfigEntry configured with test MAC and name.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Máy lọc nước Karofi",
        data={
            CONF_MAC: "aa:bb:cc:11:22:33",
            CONF_NAME: "Máy lọc nước Karofi",
        },
        unique_id="aa:bb:cc:11:22:33",
        entry_id="test_karofi_entry_id_1",
    )


@pytest.fixture
def mock_ble_device() -> BLEDevice:
    """Return a mock BLEDevice instance.

    Returns:
        Mock BLEDevice with valid address and name.
    """
    device = MagicMock(spec=BLEDevice)
    device.address = "AA:BB:CC:11:22:33"
    device.name = "karofi-365"
    return device
