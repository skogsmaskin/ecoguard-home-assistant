"""Tests for the EcoGuard coordinator."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ecoguard.coordinator import (
    EcoGuardDataUpdateCoordinator,
    EcoGuardLatestReceptionCoordinator,
)
from custom_components.ecoguard.api import EcoGuardAPIError

# Import pytest-homeassistant-custom-component fixtures
pytest_plugins = ("pytest_homeassistant_custom_component",)

async def test_coordinator_update_data_success(
    hass: HomeAssistant, mock_api: MagicMock
):
    """Test successful coordinator data update."""
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
        domain="test_domain",
    )

    data = await coordinator._async_update_data()

    assert "measuring_points" in data
    assert "installations" in data
    assert "node_id" in data
    assert data["node_id"] == 123


async def test_coordinator_update_data_api_error(
    hass: HomeAssistant, mock_api: MagicMock
):
    """Test coordinator update with API error - coordinator handles errors gracefully."""
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
        domain="test_domain",
    )

    # Mock all endpoints to fail - coordinator should handle gracefully
    mock_api.get_node = AsyncMock(side_effect=EcoGuardAPIError("API Error"))
    mock_api.get_measuring_points = AsyncMock(side_effect=EcoGuardAPIError("API Error"))
    mock_api.get_installations = AsyncMock(side_effect=EcoGuardAPIError("API Error"))
    mock_api.get_settings = AsyncMock(side_effect=EcoGuardAPIError("API Error"))
    mock_api.get_latest_reception = AsyncMock(side_effect=EcoGuardAPIError("API Error"))

    # Coordinator is designed to be resilient and return data even when endpoints fail
    data = await coordinator._async_update_data()
    
    # Should return data structure with empty lists
    assert data is not None
    assert data["measuring_points"] == []
    assert data["installations"] == []
    assert data["settings"] == []
    assert data["latest_reception"] == []
    assert data["node_data"] is None


async def test_coordinator_get_measuring_points(
    hass: HomeAssistant, mock_api: MagicMock, mock_coordinator_data: dict
):
    """Test getting measuring points from coordinator."""
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
        domain="test_domain",
    )

    # Set the data directly
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]

    points = coordinator.get_measuring_points()

    assert len(points) == 1
    assert points[0]["ID"] == 1


async def test_coordinator_get_installations(
    hass: HomeAssistant, mock_api: MagicMock, mock_coordinator_data: dict
):
    """Test getting installations from coordinator."""
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
        domain="test_domain",
    )

    coordinator._installations = mock_coordinator_data["installations"]

    installations = coordinator.get_installations()

    assert len(installations) == 1
    assert installations[0]["MeasuringPointID"] == 1


async def test_coordinator_get_setting(
    hass: HomeAssistant, mock_api: MagicMock, mock_coordinator_data: dict
):
    """Test getting a specific setting."""
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
        domain="test_domain",
    )

    coordinator._settings = mock_coordinator_data["settings"]

    currency = coordinator.get_setting("Currency")

    assert currency == "NOK"


async def test_coordinator_get_setting_not_found(
    hass: HomeAssistant, mock_api: MagicMock
):
    """Test getting a setting that doesn't exist."""
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
        domain="test_domain",
    )

    coordinator._settings = []

    result = coordinator.get_setting("NonExistent")

    assert result is None


async def test_coordinator_get_active_installations(
    hass: HomeAssistant, mock_api: MagicMock
):
    """Test getting active installations."""
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
        domain="test_domain",
    )

    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "Registers": [{"UtilityCode": "CW"}],
            "To": None,  # Active installation (To is None)
        },
        {
            "MeasuringPointID": 2,
            "Registers": [{"UtilityCode": "HW"}],
            "To": "2024-01-01",  # Inactive installation (To is set)
        },
    ]

    active = coordinator.get_active_installations()

    assert len(active) == 1
    assert active[0]["MeasuringPointID"] == 1


async def test_coordinator_get_active_installations_no_isactive_field(
    hass: HomeAssistant, mock_api: MagicMock
):
    """Test getting active installations when IsActive field is missing."""
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
        domain="test_domain",
    )

    # When IsActive is missing, all installations should be considered active
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "Registers": [{"UtilityCode": "CW"}],
        },
        {
            "MeasuringPointID": 2,
            "Registers": [{"UtilityCode": "HW"}],
        },
    ]

    active = coordinator.get_active_installations()

    assert len(active) == 2


async def test_latest_reception_coordinator_update(
    hass: HomeAssistant, mock_api: MagicMock
):
    """Test latest reception coordinator update."""
    coordinator = EcoGuardLatestReceptionCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
    )

    mock_api.get_latest_reception = AsyncMock(
        return_value=[
            {"PositionID": 1, "LatestReception": 1234567890}
        ]
    )

    data = await coordinator._async_update_data()

    assert len(data) == 1
    assert data[0]["PositionID"] == 1


async def test_latest_reception_coordinator_error(
    hass: HomeAssistant, mock_api: MagicMock
):
    """Test latest reception coordinator with error."""
    coordinator = EcoGuardLatestReceptionCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
    )

    mock_api.get_latest_reception = AsyncMock(
        side_effect=EcoGuardAPIError("API Error")
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_coordinator_billing_cache(
    hass: HomeAssistant, mock_api: MagicMock
):
    """Test billing results caching."""
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
        domain="test_domain",
    )

    mock_billing_data = [{"ID": 1, "Amount": 100.0}]
    mock_api.get_billing_results = AsyncMock(return_value=mock_billing_data)

    # First call should fetch from API
    result1 = await coordinator._get_cached_billing_results(123)
    assert mock_api.get_billing_results.call_count == 1
    assert len(result1) == 1

    # Second call should use cache
    result2 = await coordinator._get_cached_billing_results(123)
    assert mock_api.get_billing_results.call_count == 1  # Still 1
    assert len(result2) == 1


async def test_coordinator_get_latest_consumption_value(
    hass: HomeAssistant, mock_api: MagicMock
):
    """Test getting latest consumption value."""
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
        domain="test_domain",
    )

    # Set up settings for timezone
    coordinator._settings = [{"Name": "TimeZoneIANA", "Value": "Europe/Oslo"}]
    
    # Set up installations for filtering
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
        }
    ]

    mock_data = [
        {
            "ID": 1,
            "Result": [
                {
                    "Utl": "CW",
                    "Func": "con",
                    "Unit": "m³",
                    "Values": [
                        {"Time": 1234567890, "Value": 10.5},
                        {"Time": 1234567900, "Value": 11.0},
                    ],
                }
            ],
        }
    ]
    mock_api.get_data = AsyncMock(return_value=mock_data)

    result = await coordinator.get_latest_consumption_value(
        utility_code="CW",
        measuring_point_id=1,
    )

    assert result is not None
    assert result["value"] == 11.0
    assert result["unit"] == "m³"
    assert result["utility_code"] == "CW"
