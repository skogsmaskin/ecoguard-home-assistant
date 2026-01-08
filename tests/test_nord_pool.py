"""Tests for Nord Pool price fetcher."""

from unittest.mock import MagicMock, patch
import pytest
from datetime import datetime, date

from custom_components.ecoguard.nord_pool import (
    NordPoolPriceFetcher,
    NORD_POOL_AVAILABLE,
)


@pytest.fixture
def nord_pool_fetcher() -> NordPoolPriceFetcher:
    """Create a Nord Pool price fetcher instance for testing."""
    price_cache = {}
    return NordPoolPriceFetcher(price_cache=price_cache)


@pytest.mark.skipif(not NORD_POOL_AVAILABLE, reason="nordpool library not installed")
async def test_get_spot_price_cached(nord_pool_fetcher: NordPoolPriceFetcher):
    """Test that cached spot prices are returned."""
    # Set up cache
    today = date.today()
    cache_key = f"NO1_NOK_{today.isoformat()}"
    nord_pool_fetcher._price_cache[cache_key] = 0.5

    result = await nord_pool_fetcher.get_spot_price(
        area_code="NO1",
        currency="NOK",
        timezone_str="Europe/Oslo",
    )

    assert result == 0.5


@pytest.mark.skipif(not NORD_POOL_AVAILABLE, reason="nordpool library not installed")
async def test_get_spot_price_no_area(nord_pool_fetcher: NordPoolPriceFetcher):
    """Test that None is returned when area code is empty."""
    result = await nord_pool_fetcher.get_spot_price(
        area_code="",
        currency="NOK",
        timezone_str="Europe/Oslo",
    )

    assert result is None


@pytest.mark.skipif(
    NORD_POOL_AVAILABLE, reason="nordpool library is installed, skipping mock test"
)
async def test_get_spot_price_library_not_available(
    nord_pool_fetcher: NordPoolPriceFetcher,
):
    """Test that None is returned when nordpool library is not available."""
    result = await nord_pool_fetcher.get_spot_price(
        area_code="NO1",
        currency="NOK",
        timezone_str="Europe/Oslo",
    )

    assert result is None


@pytest.mark.skipif(not NORD_POOL_AVAILABLE, reason="nordpool library not installed")
async def test_get_spot_price_deduplication(nord_pool_fetcher: NordPoolPriceFetcher):
    """Test that concurrent requests for the same area return the same value."""
    import asyncio

    # Mock the elspot library to return a price
    with patch("custom_components.ecoguard.nord_pool.elspot") as mock_elspot:
        mock_prices = MagicMock()
        mock_prices.fetch.return_value = {
            "areas": {
                "NO1": {
                    "values": [
                        {
                            "start": datetime.now(),
                            "value": 500.0,  # 500 NOK/MWh = 0.5 NOK/kWh
                        }
                    ]
                }
            }
        }
        mock_elspot.Prices.return_value = mock_prices

        # Make 3 concurrent requests
        results = await asyncio.gather(
            nord_pool_fetcher.get_spot_price("NO1", "NOK", "Europe/Oslo"),
            nord_pool_fetcher.get_spot_price("NO1", "NOK", "Europe/Oslo"),
            nord_pool_fetcher.get_spot_price("NO1", "NOK", "Europe/Oslo"),
        )

        # All results should be the same (deduplicated or cached)
        assert all(r == results[0] for r in results)
        assert results[0] == 0.5  # 500 NOK/MWh = 0.5 NOK/kWh
