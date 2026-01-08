"""Tests for the billing manager."""

from unittest.mock import AsyncMock, MagicMock
import pytest
import time

from homeassistant.core import HomeAssistant

from custom_components.ecoguard.billing_manager import BillingManager
from custom_components.ecoguard.api import EcoGuardAPI


@pytest.fixture
def mock_api() -> MagicMock:
    """Create a mock EcoGuard API."""
    api = MagicMock(spec=EcoGuardAPI)
    api.get_billing_results = AsyncMock(return_value=[])
    return api


@pytest.fixture
async def billing_manager(mock_api: MagicMock, hass: HomeAssistant) -> BillingManager:
    """Create a billing manager instance for testing."""
    import asyncio

    billing_cache = {}
    pending_requests = {}
    pending_requests_lock = asyncio.Lock()

    def get_setting(name: str) -> str | None:
        if name == "Currency":
            return "NOK"
        return None

    manager = BillingManager(
        api=mock_api,
        node_id=123,
        hass=hass,
        billing_cache=billing_cache,
        pending_requests=pending_requests,
        pending_requests_lock=pending_requests_lock,
        get_setting=get_setting,
    )
    return manager


async def test_get_cached_billing_results_first_call(
    billing_manager: BillingManager, mock_api: MagicMock
):
    """Test getting billing results on first call (should fetch from API)."""
    mock_billing_data = [
        {"ID": 1, "Amount": 100.0, "Year": 2024, "Month": 1},
        {"ID": 2, "Amount": 200.0, "Year": 2024, "Month": 2},
    ]
    mock_api.get_billing_results = AsyncMock(return_value=mock_billing_data)

    result = await billing_manager.get_cached_billing_results()

    assert len(result) == 2
    assert mock_api.get_billing_results.call_count == 1


async def test_get_cached_billing_results_cached(
    billing_manager: BillingManager, mock_api: MagicMock
):
    """Test that billing results are cached on second call."""
    mock_billing_data = [{"ID": 1, "Amount": 100.0}]
    mock_api.get_billing_results = AsyncMock(return_value=mock_billing_data)

    # First call
    result1 = await billing_manager.get_cached_billing_results()
    assert mock_api.get_billing_results.call_count == 1

    # Second call should use cache
    result2 = await billing_manager.get_cached_billing_results()
    assert mock_api.get_billing_results.call_count == 1  # Still 1
    assert len(result2) == len(result1)


async def test_get_cached_billing_results_cache_expiry(
    billing_manager: BillingManager, mock_api: MagicMock
):
    """Test that cache expires after TTL."""
    mock_billing_data = [{"ID": 1, "Amount": 100.0}]
    mock_api.get_billing_results = AsyncMock(return_value=mock_billing_data)

    # First call
    await billing_manager.get_cached_billing_results()
    assert mock_api.get_billing_results.call_count == 1

    # Manually expire cache by setting old timestamp
    cache_key = list(billing_manager._billing_cache.keys())[0]
    old_timestamp = time.time() - (billing_manager._billing_cache_ttl + 1)
    billing_manager._billing_cache[cache_key] = (
        billing_manager._billing_cache[cache_key][0],
        old_timestamp,
    )

    # Second call should fetch again
    await billing_manager.get_cached_billing_results()
    assert mock_api.get_billing_results.call_count == 2


async def test_get_monthly_other_items_cost(
    billing_manager: BillingManager, mock_api: MagicMock
):
    """Test getting monthly other items cost."""
    from custom_components.ecoguard.helpers import get_timezone, get_month_timestamps

    # Create billing data with the expected structure
    tz = get_timezone("Europe/Oslo")
    from_time, to_time = get_month_timestamps(2024, 1, tz)

    mock_billing_data = [
        {
            "ID": 1,
            "Start": from_time,
            "End": to_time,
            "Parts": [
                {
                    "Code": None,  # Other items have Code=None
                    "Name": "Øvrig",
                    "Items": [
                        {
                            "Total": 25.0,
                            "PriceComponent": {"Name": "Fee 1"},
                            "Rate": 0.0,
                        },
                        {
                            "Total": 15.0,
                            "PriceComponent": {"Name": "Fee 2"},
                            "Rate": 0.0,
                        },
                    ],
                    "Rounding": 0.0,
                }
            ],
        }
    ]
    mock_api.get_billing_results = AsyncMock(return_value=mock_billing_data)

    result = await billing_manager.get_monthly_other_items_cost(2024, 1)

    assert result is not None
    assert result["value"] == 40.0
    assert result["item_count"] == 2
    assert len(result["items"]) == 2


async def test_get_monthly_other_items_cost_no_data(
    billing_manager: BillingManager, mock_api: MagicMock
):
    """Test getting monthly other items cost when no data available."""
    mock_api.get_billing_results = AsyncMock(return_value=[])

    result = await billing_manager.get_monthly_other_items_cost(2024, 1)

    assert result is None


async def test_get_rate_from_billing(
    billing_manager: BillingManager, mock_api: MagicMock
):
    """Test getting rate from billing data."""
    from custom_components.ecoguard.helpers import get_timezone, get_month_timestamps

    # Create billing data with the expected structure
    tz = get_timezone("Europe/Oslo")
    from_time, to_time = get_month_timestamps(2024, 1, tz)

    # The billing manager looks for Parts with Code matching utility_code
    # and Items with PriceComponent Type="C1" or "C2" and RateUnit="m3"
    mock_billing_data = [
        {
            "ID": 1,
            "Start": from_time,
            "End": to_time,
            "Parts": [
                {
                    "Code": "CW",
                    "Items": [
                        {
                            "Rate": 5.0,
                            "RateUnit": "m3",
                            "PriceComponent": {
                                "Type": "C1",
                                "Name": "Variable Charge",
                            },
                        }
                    ],
                }
            ],
        }
    ]
    mock_api.get_billing_results = AsyncMock(return_value=mock_billing_data)

    rate = await billing_manager.get_rate_from_billing("CW", 2024, 1)

    assert rate is not None
    assert rate == 5.0


async def test_get_rate_from_billing_no_consumption(
    billing_manager: BillingManager, mock_api: MagicMock
):
    """Test getting rate when consumption is zero."""
    mock_billing_data = [
        {
            "ID": 1,
            "Year": 2024,
            "Month": 1,
            "UtilityCode": "CW",
            "Consumption": 0.0,
            "Amount": 500.0,
        }
    ]
    mock_api.get_billing_results = AsyncMock(return_value=mock_billing_data)

    rate = await billing_manager.get_rate_from_billing("CW", 2024, 1)

    assert rate is None
