"""Tests for the meter aggregate calculator."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from datetime import datetime

from homeassistant.core import HomeAssistant

from custom_components.ecoguard.meter_aggregate_calculator import MeterAggregateCalculator
from custom_components.ecoguard.request_deduplicator import RequestDeduplicator


@pytest.fixture
def mock_request_deduplicator():
    """Create a mock request deduplicator."""
    deduplicator = MagicMock(spec=RequestDeduplicator)
    deduplicator.get_or_fetch = AsyncMock(return_value=[])
    return deduplicator


@pytest.fixture
def mock_api():
    """Create a mock API."""
    api = MagicMock()
    api.get_data = AsyncMock(return_value=[])
    return api


@pytest.fixture
def meter_aggregate_calculator(
    hass: HomeAssistant,
    mock_request_deduplicator: MagicMock,
    mock_api: MagicMock,
) -> MeterAggregateCalculator:
    """Create a meter aggregate calculator instance for testing."""
    installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key-1",
            "Registers": [{"UtilityCode": "CW"}],
        },
        {
            "MeasuringPointID": 2,
            "ExternalKey": "test-key-2",
            "Registers": [{"UtilityCode": "HW"}],
        },
    ]
    
    def get_setting(name: str) -> str | None:
        if name == "Currency":
            return "NOK"
        if name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None
    
    async def get_monthly_aggregate(utility_code, year, month, aggregate_type, cost_type):
        if utility_code == "HW" and aggregate_type == "con":
            return {"value": 100.0, "unit": "m³", "year": year, "month": month}
        if utility_code == "HW" and aggregate_type == "price" and cost_type == "estimated":
            return {"value": 500.0, "unit": "NOK", "year": year, "month": month}
        return None
    
    async def get_hw_price_from_spot_prices(consumption, year, month, cw_price, cw_consumption):
        return {
            "value": consumption * 50.0,
            "unit": "NOK",
        }
    
    mock_billing_manager = MagicMock()
    mock_billing_manager.get_rate_from_billing = AsyncMock(return_value=10.0)  # 10 NOK/m3
    
    calculator = MeterAggregateCalculator(
        node_id=123,
        request_deduplicator=mock_request_deduplicator,
        api=mock_api,
        get_setting=get_setting,
        get_monthly_aggregate=get_monthly_aggregate,
        get_hw_price_from_spot_prices=get_hw_price_from_spot_prices,
        billing_manager=mock_billing_manager,
        installations=installations,
    )
    return calculator


async def test_calculate_consumption_aggregate(
    meter_aggregate_calculator: MeterAggregateCalculator,
    mock_request_deduplicator: MagicMock,
):
    """Test calculating consumption aggregate for a specific meter."""
    # Mock request deduplicator to return consumption data
    mock_request_deduplicator.get_or_fetch = AsyncMock(return_value=[
        {
            "ID": 1,  # Matching measuring_point_id
            "Result": [
                {
                    "Utl": "CW",
                    "Func": "con",
                    "Unit": "m³",
                    "Values": [
                        {"Time": int(datetime.now().timestamp()), "Value": 10.0},
                        {"Time": int(datetime.now().timestamp()) - 86400, "Value": 9.0},
                    ],
                }
            ],
        }
    ])
    
    result = await meter_aggregate_calculator.calculate(
        utility_code="CW",
        measuring_point_id=1,
        external_key="test-key-1",
        year=2024,
        month=1,
        aggregate_type="con",
    )
    
    assert result is not None
    assert result["value"] == 19.0  # 10.0 + 9.0
    assert result["unit"] == "m³"
    assert result["utility_code"] == "CW"
    assert result["aggregate_type"] == "con"
    assert result["measuring_point_id"] == 1


async def test_calculate_price_aggregate_metered(
    meter_aggregate_calculator: MeterAggregateCalculator,
    mock_request_deduplicator: MagicMock,
):
    """Test calculating price aggregate (metered) for a specific meter."""
    # Mock request deduplicator to return price data
    mock_request_deduplicator.get_or_fetch = AsyncMock(return_value=[
        {
            "ID": 1,
            "Result": [
                {
                    "Utl": "CW",
                    "Func": "price",
                    "Unit": "NOK",
                    "Values": [
                        {"Time": int(datetime.now().timestamp()), "Value": 50.0},
                        {"Time": int(datetime.now().timestamp()) - 86400, "Value": 45.0},
                    ],
                }
            ],
        }
    ])
    
    result = await meter_aggregate_calculator.calculate(
        utility_code="CW",
        measuring_point_id=1,
        external_key="test-key-1",
        year=2024,
        month=1,
        aggregate_type="price",
        cost_type="actual",
    )
    
    assert result is not None
    assert result["value"] == 95.0  # 50.0 + 45.0
    assert result["unit"] == "NOK"
    assert result["cost_type"] == "actual"


async def test_calculate_price_aggregate_estimated_fallback(
    meter_aggregate_calculator: MeterAggregateCalculator,
    mock_api: MagicMock,
):
    """Test calculating estimated price when API returns no data."""
    # Mock API to return no data
    mock_api.get_data = AsyncMock(return_value=[])
    
    # Mock consumption calculation to return data
    with patch.object(
        meter_aggregate_calculator,
        "_calculate_consumption_aggregate",
        new_callable=AsyncMock,
        return_value={"value": 10.0, "unit": "m³", "year": 2024, "month": 1},
    ):
        result = await meter_aggregate_calculator.calculate(
            utility_code="CW",
            measuring_point_id=1,
            external_key="test-key-1",
            year=2024,
            month=1,
            aggregate_type="price",
            cost_type="estimated",
        )
        
        # Should calculate from consumption × rate
        assert result is not None
        assert result["value"] == 100.0  # 10.0 m³ × 10 NOK/m³
        assert result["cost_type"] == "estimated"


async def test_calculate_hw_proportional_allocation(
    meter_aggregate_calculator: MeterAggregateCalculator,
):
    """Test proportional allocation for HW estimated costs."""
    # Mock consumption calculation
    with patch.object(
        meter_aggregate_calculator,
        "calculate",
        new_callable=AsyncMock,
        side_effect=lambda **kwargs: (
            {"value": 20.0, "unit": "m³"} if kwargs.get("aggregate_type") == "con"
            else None
        ),
    ):
        result = await meter_aggregate_calculator._try_hw_proportional_allocation(
            measuring_point_id=2,
            external_key="test-key-2",
            year=2024,
            month=1,
        )
        
        # Meter consumption: 20 m³
        # Total consumption: 100 m³
        # Total cost: 500 NOK
        # Allocation: 20/100 = 20%, so 500 × 0.2 = 100 NOK
        assert result is not None
        assert result["value"] == 100.0
        assert result["cost_type"] == "estimated"