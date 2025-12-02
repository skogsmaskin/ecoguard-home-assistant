"""Tests for the EcoGuard integration initialization."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from custom_components.ecoguard import DOMAIN, async_setup, async_setup_entry, async_unload_entry

# Import pytest-homeassistant-custom-component fixtures
pytest_plugins = ("pytest_homeassistant_custom_component",)

async def test_async_setup(hass: HomeAssistant):
    """Test async_setup."""
    result = await async_setup(hass, {})

    assert result is True
    assert DOMAIN in hass.data


async def test_async_setup_entry(hass: HomeAssistant, mock_config_entry: ConfigEntry):
    """Test async_setup_entry."""
    with patch(
        "custom_components.ecoguard.EcoGuardAPI"
    ) as mock_api_class, patch(
        "custom_components.ecoguard.EcoGuardDataUpdateCoordinator"
    ) as mock_coord_class, patch(
        "custom_components.ecoguard.EcoGuardLatestReceptionCoordinator"
    ) as mock_latest_coord_class, patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups"
    ) as mock_forward:
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api

        mock_coord = MagicMock()
        mock_coord_class.return_value = mock_coord

        mock_latest_coord = MagicMock()
        mock_latest_coord_class.return_value = mock_latest_coord

        mock_forward.return_value = True

        result = await async_setup_entry(hass, mock_config_entry)
        await hass.async_block_till_done()

        assert result is True
        assert mock_config_entry.entry_id in hass.data[DOMAIN]
        assert "coordinator" in hass.data[DOMAIN][mock_config_entry.entry_id]
        assert "latest_reception_coordinator" in hass.data[DOMAIN][mock_config_entry.entry_id]
        assert "api" in hass.data[DOMAIN][mock_config_entry.entry_id]
        mock_forward.assert_called_once_with(mock_config_entry, ["sensor"])


async def test_async_unload_entry(hass: HomeAssistant, mock_config_entry: ConfigEntry):
    """Test async_unload_entry."""
    # Set up the entry first
    with patch(
        "custom_components.ecoguard.EcoGuardAPI"
    ) as mock_api_class, patch(
        "custom_components.ecoguard.EcoGuardDataUpdateCoordinator"
    ) as mock_coord_class, patch(
        "custom_components.ecoguard.EcoGuardLatestReceptionCoordinator"
    ) as mock_latest_coord_class, patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups"
    ) as mock_forward, patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms"
    ) as mock_unload:
        mock_api = MagicMock()
        mock_api.async_close = AsyncMock()
        mock_api_class.return_value = mock_api

        mock_coord = MagicMock()
        mock_coord_class.return_value = mock_coord

        mock_latest_coord = MagicMock()
        mock_latest_coord_class.return_value = mock_latest_coord

        mock_forward.return_value = True
        mock_unload.return_value = True

        await async_setup_entry(hass, mock_config_entry)
        await hass.async_block_till_done()

        # Now test unloading
        result = await async_unload_entry(hass, mock_config_entry)
        await hass.async_block_till_done()

        assert result is True
        mock_unload.assert_called_once_with(mock_config_entry, ["sensor"])
        mock_api.async_close.assert_called_once()
        assert mock_config_entry.entry_id not in hass.data[DOMAIN]


async def test_async_unload_entry_failure(hass: HomeAssistant, mock_config_entry: ConfigEntry):
    """Test async_unload_entry when unload fails."""
    # Set up the entry first
    with patch(
        "custom_components.ecoguard.EcoGuardAPI"
    ) as mock_api_class, patch(
        "custom_components.ecoguard.EcoGuardDataUpdateCoordinator"
    ) as mock_coord_class, patch(
        "custom_components.ecoguard.EcoGuardLatestReceptionCoordinator"
    ) as mock_latest_coord_class, patch(
        "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups"
    ) as mock_forward, patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms"
    ) as mock_unload:
        mock_api = MagicMock()
        mock_api.async_close = AsyncMock()
        mock_api_class.return_value = mock_api

        mock_coord = MagicMock()
        mock_coord_class.return_value = mock_coord

        mock_latest_coord = MagicMock()
        mock_latest_coord_class.return_value = mock_latest_coord

        mock_forward.return_value = True
        mock_unload.return_value = False  # Unload fails

        await async_setup_entry(hass, mock_config_entry)
        await hass.async_block_till_done()

        # Now test unloading
        result = await async_unload_entry(hass, mock_config_entry)
        await hass.async_block_till_done()

        assert result is False
        # API should not be closed if unload failed
        mock_api.async_close.assert_not_called()
        # Entry should still be in hass.data
        assert mock_config_entry.entry_id in hass.data[DOMAIN]
