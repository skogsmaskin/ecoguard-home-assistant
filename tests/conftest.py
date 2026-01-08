"""Test configuration and fixtures for EcoGuard integration tests."""

from unittest.mock import AsyncMock, MagicMock
import inspect
import pytest
from aiohttp import ClientSession

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from custom_components.ecoguard import DOMAIN
from custom_components.ecoguard.api import EcoGuardAPI
from custom_components.ecoguard.coordinator import (
    EcoGuardDataUpdateCoordinator,
    EcoGuardLatestReceptionCoordinator,
)

# Import pytest-homeassistant-custom-component fixtures
pytest_plugins = ("pytest_homeassistant_custom_component",)


def _create_config_entry(**kwargs) -> ConfigEntry:
    """Create a ConfigEntry that works with different Home Assistant versions.

    Some versions require discovery_keys and subentries_data, others don't accept them.
    This function inspects the signature and conditionally includes them.
    """
    # Get the ConfigEntry signature
    sig = inspect.signature(ConfigEntry.__init__)
    params = sig.parameters

    # Check if discovery_keys and subentries_data are in the signature
    needs_discovery_keys = "discovery_keys" in params
    needs_subentries_data = "subentries_data" in params

    # Add optional parameters if they're required by this version
    if needs_discovery_keys and "discovery_keys" not in kwargs:
        kwargs["discovery_keys"] = None
    if needs_subentries_data and "subentries_data" not in kwargs:
        kwargs["subentries_data"] = None

    return ConfigEntry(**kwargs)


@pytest.fixture(autouse=True)
async def setup_integration(hass: HomeAssistant):
    """Set up the integration for testing."""
    # Ensure the integration is set up
    from custom_components.ecoguard import async_setup

    await async_setup(hass, {})


@pytest.fixture
def mock_api() -> MagicMock:
    """Create a mock EcoGuard API."""
    api = MagicMock(spec=EcoGuardAPI)
    api.authenticate = AsyncMock(return_value={"access_token": "test_token"})
    api.get_user_info = AsyncMock(return_value={"ID": 1, "Name": "Test User"})
    api.get_nodes = AsyncMock(return_value=[{"ID": 123, "Name": "Test Node"}])
    api.get_node = AsyncMock(
        return_value={
            "ID": 123,
            "Name": "Test Node",
            "MeasuringPoints": [{"ID": 1, "Name": "Test Measuring Point"}],
        }
    )
    api.get_measuring_points = AsyncMock(
        return_value=[{"ID": 1, "Name": "Test Measuring Point"}]
    )
    api.get_installations = AsyncMock(
        return_value=[
            {
                "MeasuringPointID": 1,
                "ExternalKey": "test-key",
                "DeviceTypeDisplay": "Test Device",
                "Registers": [
                    {"UtilityCode": "CW"},
                    {"UtilityCode": "HW"},
                ],
            }
        ]
    )
    api.get_settings = AsyncMock(
        return_value=[
            {"Name": "Currency", "Value": "NOK"},
        ]
    )
    api.get_latest_reception = AsyncMock(
        return_value=[{"PositionID": 1, "LatestReception": 1234567890}]
    )
    api.get_data = AsyncMock(return_value=[])
    api.get_billing_results = AsyncMock(return_value=[])
    api.async_close = AsyncMock()
    return api


@pytest.fixture
def mock_config_entry() -> ConfigEntry:
    """Create a mock config entry."""
    return _create_config_entry(
        version=1,
        domain=DOMAIN,
        title="EcoGuard - Test Node",
        data={
            "username": "test_user",
            "password": "test_password",
            "domain": "test_domain",
            "node_id": 123,
            "nord_pool_area": "NO1",
        },
        source="user",
        entry_id="test_entry_id",
        unique_id="test_domain",
        options={},
        minor_version=1,
    )


@pytest.fixture
def mock_coordinator_data() -> dict:
    """Create mock coordinator data."""
    return {
        "measuring_points": [{"ID": 1, "Name": "Test Measuring Point"}],
        "installations": [
            {
                "MeasuringPointID": 1,
                "ExternalKey": "test-key",
                "DeviceTypeDisplay": "Test Device",
                "Registers": [
                    {"UtilityCode": "CW"},
                    {"UtilityCode": "HW"},
                ],
            }
        ],
        "latest_reception": [{"PositionID": 1, "LatestReception": 1234567890}],
        "node_data": {
            "ID": 123,
            "Name": "Test Node",
            "MeasuringPoints": [{"ID": 1, "Name": "Test Measuring Point"}],
        },
        "settings": [{"Name": "Currency", "Value": "NOK"}],
        "node_id": 123,
        "domain": "test_domain",
    }


@pytest.fixture
async def coordinator(
    hass: HomeAssistant, mock_api: MagicMock
) -> EcoGuardDataUpdateCoordinator:
    """Create a coordinator instance for testing."""
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
        domain="test_domain",
        nord_pool_area="NO1",
    )
    return coordinator


@pytest.fixture
async def latest_reception_coordinator(
    hass: HomeAssistant, mock_api: MagicMock
) -> EcoGuardLatestReceptionCoordinator:
    """Create a latest reception coordinator instance for testing."""
    coordinator = EcoGuardLatestReceptionCoordinator(
        hass=hass,
        api=mock_api,
        node_id=123,
    )
    return coordinator


@pytest.fixture
def mock_aiohttp_session():
    """Create a mock aiohttp session."""
    session = MagicMock(spec=ClientSession)
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value={"access_token": "test_token"})
    response.text = AsyncMock(return_value="")
    session.post = AsyncMock(return_value=response)
    session.request = AsyncMock(return_value=response)
    session.close = AsyncMock()
    return session
