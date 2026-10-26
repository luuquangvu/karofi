"""Tests for the Karofi component initialization and unloading."""

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.karofi import async_remove_entry
from custom_components.karofi.const import (
    DOMAIN,
    PENDING_RESET_STORAGE_SUFFIX,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)


async def test_setup_and_unload_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Test successful setup and unload of a config entry.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        hass_storage: Fixture providing mock storage from test harness.
    """
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.karofi.coordinator.KarofiDataCoordinator._async_update_data",
        return_value=None,
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
    hass_storage: dict[str, Any],
) -> None:
    """Test removing a config entry cleans up persistent storage.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        hass_storage: Fixture providing mock storage from test harness.
    """
    mock_config_entry.add_to_hass(hass)
    storage_key = f"{STORAGE_KEY_PREFIX}_{mock_config_entry.entry_id}"
    pending_storage_key = f"{storage_key}_{PENDING_RESET_STORAGE_SUFFIX}"
    hass_storage[storage_key] = {
        "version": STORAGE_VERSION,
        "data": {"filter_1": "2026-01-01T00:00:00"},
    }
    hass_storage[pending_storage_key] = {
        "version": STORAGE_VERSION,
        "data": {"filter_1": 4320},
    }
    await async_remove_entry(hass, mock_config_entry)
    assert storage_key not in hass_storage
    assert pending_storage_key not in hass_storage


async def test_setup_entry_schedules_background_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test setup entry runs coordinator setup and schedules background refresh.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.karofi.coordinator.KarofiDataCoordinator.async_setup",
            new_callable=AsyncMock,
        ) as mock_setup,
        patch(
            "custom_components.karofi.coordinator.KarofiDataCoordinator.async_refresh",
            new_callable=AsyncMock,
        ) as mock_refresh,
    ):
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
        assert result is True
        mock_setup.assert_awaited_once()
        await hass.async_block_till_done()
        mock_refresh.assert_awaited_once()


async def test_setup_entry_fails_on_coordinator_setup_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test setup entry fails when coordinator async_setup raises an error.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.karofi.coordinator.KarofiDataCoordinator.async_setup",
            side_effect=RuntimeError("Storage failure"),
        ),
        patch(
            "custom_components.karofi.coordinator.KarofiDataCoordinator.async_refresh",
            new_callable=AsyncMock,
        ) as mock_refresh,
    ):
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
        assert result is False
        mock_refresh.assert_not_called()
        assert mock_config_entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_unload_entry_cancels_background_refresh(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test unloading an entry cancels background refresh tasks.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
    """
    mock_config_entry.add_to_hass(hass)

    task_cancelled = asyncio.Event()

    async def _mock_slow_refresh() -> None:
        """Simulate slow refresh waiting until cancelled."""
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            task_cancelled.set()
            raise

    with (
        patch(
            "custom_components.karofi.coordinator.KarofiDataCoordinator.async_setup",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.karofi.coordinator.KarofiDataCoordinator.async_refresh",
            side_effect=_mock_slow_refresh,
        ),
    ):
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
        assert result is True

        unload_result = await hass.config_entries.async_unload(mock_config_entry.entry_id)
        assert unload_result is True
        await hass.async_block_till_done()

        assert task_cancelled.is_set()
        assert mock_config_entry.entry_id not in hass.data[DOMAIN]


async def test_unload_entry_cancels_pending_unknown_payload_flush(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test unloading an entry cancels queued unknown payload diagnostics.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
        caplog: Fixture capturing log messages.
    """
    caplog.set_level(logging.DEBUG, logger="custom_components.karofi.coordinator")
    mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.karofi.coordinator.KarofiDataCoordinator.async_setup",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.karofi.coordinator.KarofiDataCoordinator.async_refresh",
            new_callable=AsyncMock,
        ),
    ):
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
        coordinator._process_tds_payload(b'{"first_unknown":1}')
        coordinator._process_tds_payload(b'{"second_unknown":2}')
        assert "second_unknown=2" not in caplog.text
        assert coordinator._unknown_payload_flush_handle is not None

        unload_result = await hass.config_entries.async_unload(mock_config_entry.entry_id)
        assert unload_result is True
        await hass.async_block_till_done()

        assert coordinator._unknown_payload_flush_handle is None
        coordinator._handle_unknown_payload_flush()
        assert "second_unknown=2" not in caplog.text


async def test_unload_entry_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test unload failure when platforms fail to unload.

    Args:
        hass: HomeAssistant test instance.
        mock_config_entry: Fixture providing mock config entry.
    """
    mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.karofi.coordinator.KarofiDataCoordinator.async_setup",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.karofi.coordinator.KarofiDataCoordinator.async_refresh",
            new_callable=AsyncMock,
        ),
    ):
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
        assert result is True
        await hass.async_block_till_done()

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=False),
        ):
            unload_result = await hass.config_entries.async_unload(mock_config_entry.entry_id)
            assert unload_result is False
            assert mock_config_entry.entry_id in hass.data[DOMAIN]
