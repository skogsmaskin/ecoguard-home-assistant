"""Tests for the data processor."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from datetime import datetime

from homeassistant.core import HomeAssistant

from custom_components.ecoguard.data_processor import DataProcessor


@pytest.fixture
def mock_api():
    """Create a mock API."""
    api = MagicMock()
    api.get_data = AsyncMock(return_value=[])
    return api


@pytest.fixture
def data_processor(
    hass: HomeAssistant,
    mock_api: MagicMock,
) -> DataProcessor:
    """Create a data processor instance for testing."""
    installations = [
        {
            "MeasuringPointID": 1,
            "Registers": [{"UtilityCode": "CW"}],
        },
        {
            "MeasuringPointID": 2,
            "Registers": [{"UtilityCode": "HW"}],
        },
    ]
    
    def get_setting(name: str) -> str | None:
        if name == "Currency":
            return "NOK"
        if name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None
    
    latest_consumption_cache = {}
    latest_cost_cache = {}
    daily_consumption_cache = {}
    daily_price_cache = {}
    monthly_aggregate_cache = {}
    
    data = {
        "measuring_points": [],
        "installations": installations,
        "latest_reception": [],
        "node_data": None,
        "settings": [],
        "node_id": 123,
        "domain": "test",
    }
    
    def async_set_updated_data(updated_data: dict) -> None:
        data.update(updated_data)
    
    def async_update_listeners() -> None:
        pass
    
    def get_listeners() -> list:
        return []
    
    processor = DataProcessor(
        api=mock_api,
        node_id=123,
        installations=installations,
        get_setting=get_setting,
        latest_consumption_cache=latest_consumption_cache,
        latest_cost_cache=latest_cost_cache,
        daily_consumption_cache=daily_consumption_cache,
        daily_price_cache=daily_price_cache,
        monthly_aggregate_cache=monthly_aggregate_cache,
        async_set_updated_data=async_set_updated_data,
        async_update_listeners=async_update_listeners,
        get_listeners=get_listeners,
        hass=hass,
        data=data,
    )
    return processor


async def test_batch_fetch_consumption_data(
    data_processor: DataProcessor,
    mock_api: MagicMock,
):
    """Test batch fetching consumption data."""
    # Mock API response
    mock_api.get_data = AsyncMock(return_value=[
        {
            "ID": 1,
            "Result": [
                {
                    "Utl": "CW",
                    "Func": "con",
                    "Unit": "m³",
                    "Values": [
                        {"Time": int(datetime.now().timestamp()), "Value": 10.0},
                    ],
                }
            ],
        }
    ])
    
    await data_processor.batch_fetch_sensor_data()
    
    # Verify cache was populated
    assert len(data_processor._latest_consumption_cache) > 0
    assert len(data_processor._daily_consumption_cache) > 0
    assert "CW_1" in data_processor._latest_consumption_cache
    assert "CW_1" in data_processor._daily_consumption_cache


async def test_batch_fetch_price_data(
    data_processor: DataProcessor,
    mock_api: MagicMock,
):
    """Test batch fetching price data."""
    # Mock API response with price data
    mock_api.get_data = AsyncMock(return_value=[
        {
            "ID": 1,
            "Result": [
                {
                    "Utl": "CW",
                    "Func": "price",
                    "Unit": "NOK",
                    "Values": [
                        {"Time": int(datetime.now().timestamp()), "Value": 50.0},
                    ],
                }
            ],
        }
    ])
    
    await data_processor.batch_fetch_sensor_data()
    
    # Verify cache was populated
    assert len(data_processor._latest_cost_cache) > 0
    assert len(data_processor._daily_price_cache) > 0
    assert "CW_1_metered" in data_processor._latest_cost_cache


async def test_batch_fetch_aggregates_all_meters(
    data_processor: DataProcessor,
    mock_api: MagicMock,
):
    """Test that batch fetch aggregates data across all meters."""
    # Mock API response for multiple meters
    mock_api.get_data = AsyncMock(side_effect=[
        # First call (meter 1)
        [{
            "ID": 1,
            "Result": [
                {
                    "Utl": "CW",
                    "Func": "con",
                    "Unit": "m³",
                    "Values": [
                        {"Time": int(datetime.now().timestamp()), "Value": 10.0},
                    ],
                }
            ],
        }],
        # Second call (meter 2)
        [{
            "ID": 2,
            "Result": [
                {
                    "Utl": "HW",
                    "Func": "con",
                    "Unit": "m³",
                    "Values": [
                        {"Time": int(datetime.now().timestamp()), "Value": 5.0},
                    ],
                }
            ],
        }],
        # Price calls
        [],
        [],
    ])
    
    await data_processor.batch_fetch_sensor_data()
    
    # Verify "all" cache entries exist
    assert "CW_all" in data_processor._latest_consumption_cache
    assert "CW_all" in data_processor._daily_consumption_cache


async def test_batch_fetch_handles_hw_zero_prices(
    data_processor: DataProcessor,
    mock_api: MagicMock,
):
    """Test that HW zero prices are handled correctly (treated as Unknown)."""
    # Mock API response with all-zero HW prices
    mock_api.get_data = AsyncMock(return_value=[
        {
            "ID": 2,
            "Result": [
                {
                    "Utl": "HW",
                    "Func": "price",
                    "Unit": "NOK",
                    "Values": [
                        {"Time": int(datetime.now().timestamp()), "Value": 0.0},
                        {"Time": int(datetime.now().timestamp()) - 86400, "Value": 0.0},
                    ],
                }
            ],
        }
    ])
    
    await data_processor.batch_fetch_sensor_data()
    
    # HW with all zeros should not be cached (treated as Unknown)
    # Verify that HW price is not in cache (or is None)
    hw_cache_key = "HW_2_metered"
    # The implementation skips caching when all prices are 0 for HW
    assert hw_cache_key not in data_processor._latest_cost_cache or \
           data_processor._latest_cost_cache.get(hw_cache_key) is None


async def test_batch_fetch_no_installations(
    data_processor: DataProcessor,
):
    """Test that batch fetch handles empty installations gracefully."""
    data_processor._installations = []
    
    # Should not raise an error
    await data_processor.batch_fetch_sensor_data()
    
    # Caches should remain empty
    assert len(data_processor._latest_consumption_cache) == 0
    assert len(data_processor._latest_cost_cache) == 0