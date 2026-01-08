"""Tests for the price calculator."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from homeassistant.core import HomeAssistant

from custom_components.ecoguard.price_calculator import HWPriceCalculator
from custom_components.ecoguard.nord_pool import NordPoolPriceFetcher


@pytest.fixture
def mock_nord_pool_fetcher():
    """Create a mock Nord Pool price fetcher."""
    fetcher = MagicMock(spec=NordPoolPriceFetcher)
    fetcher.get_spot_price = AsyncMock(return_value=0.5)  # 0.5 NOK/kWh
    return fetcher


@pytest.fixture
def mock_calculate_calibration_ratio():
    """Create a mock calibration ratio calculator."""

    async def calc_ratio(months_back: int) -> float | None:
        return 1.5  # 50% overhead

    return calc_ratio


@pytest.fixture
def price_calculator(
    hass: HomeAssistant,
    mock_nord_pool_fetcher: MagicMock,
    mock_calculate_calibration_ratio,
) -> HWPriceCalculator:
    """Create a price calculator instance for testing."""

    def get_setting(name: str) -> str | None:
        if name == "Currency":
            return "NOK"
        if name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    def get_rate_from_billing(utility_code: str, year: int, month: int) -> float | None:
        if utility_code == "CW":
            return 10.0  # 10 NOK/m3
        return None

    calculator = HWPriceCalculator(
        calculate_calibration_ratio=mock_calculate_calibration_ratio,
        nord_pool_fetcher=mock_nord_pool_fetcher,
        get_rate_from_billing=get_rate_from_billing,
        get_setting=get_setting,
    )
    return calculator


async def test_calculate_price_with_spot_price(
    price_calculator: HWPriceCalculator,
    mock_nord_pool_fetcher: MagicMock,
):
    """Test calculating HW price using spot prices."""
    # Consumption: 10 m3
    # Energy needed: 10 m3 × 45 kWh/m3 = 450 kWh
    # Base cost: 450 kWh × 0.5 NOK/kWh = 225 NOK
    # With calibration (1.5): 225 × 1.5 = 337.5 NOK
    # Cold water: 10 m3 × 10 NOK/m3 = 100 NOK
    # Total: 337.5 + 100 = 437.5 NOK

    result = await price_calculator.calculate_price(
        consumption=10.0,
        year=2024,
        month=1,
        cold_water_price=100.0,
        cold_water_consumption=10.0,
        nord_pool_area="NO1",
    )

    assert result is not None
    assert result["value"] == 437.5  # 337.5 (heating) + 100 (CW)
    assert result["unit"] == "NOK"
    assert result["utility_code"] == "HW"
    assert result["calculation_method"] == "spot_price_calibrated"
    assert result["calibration_ratio"] == 1.5
    assert result["heating_cost"] == 337.5
    assert result["cold_water_cost"] == 100.0
    assert result["cold_water_rate_nok_per_m3"] == 10.0


async def test_calculate_price_without_calibration(
    hass: HomeAssistant,
    mock_nord_pool_fetcher: MagicMock,
):
    """Test calculating HW price without calibration ratio."""

    # Create a new calculator without calibration
    async def no_calibration(months_back: int) -> float | None:
        return None

    def get_setting(name: str) -> str | None:
        if name == "Currency":
            return "NOK"
        if name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    calculator = HWPriceCalculator(
        calculate_calibration_ratio=no_calibration,
        nord_pool_fetcher=mock_nord_pool_fetcher,
        get_setting=get_setting,
    )

    result = await calculator.calculate_price(
        consumption=10.0,
        year=2024,
        month=1,
        nord_pool_area="NO1",
    )

    assert result is not None
    assert result["value"] == 225.0  # 10 m3 × 45 kWh/m3 × 0.5 NOK/kWh (no calibration)
    assert result["calculation_method"] == "spot_price"
    assert "calibration_ratio" not in result


async def test_calculate_price_with_billing_rate_fallback(
    hass: HomeAssistant,
    mock_nord_pool_fetcher: MagicMock,
):
    """Test calculating HW price with billing rate fallback for cold water."""

    async def mock_calibration(months_back: int) -> float | None:
        return 1.5

    def get_setting(name: str) -> str | None:
        if name == "Currency":
            return "NOK"
        if name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    async def get_rate_from_billing(
        utility_code: str, year: int, month: int
    ) -> float | None:
        if utility_code == "CW":
            return 10.0  # 10 NOK/m3
        return None

    calculator = HWPriceCalculator(
        calculate_calibration_ratio=mock_calibration,
        nord_pool_fetcher=mock_nord_pool_fetcher,
        get_rate_from_billing=get_rate_from_billing,
        get_setting=get_setting,
    )

    result = await calculator.calculate_price(
        consumption=10.0,
        year=2024,
        month=1,
        cold_water_price=None,  # No current month price
        cold_water_consumption=None,
        nord_pool_area="NO1",
    )

    assert result is not None
    # Should use billing rate (10 NOK/m3) for cold water
    assert result["cold_water_rate_nok_per_m3"] == 10.0
    assert result["cold_water_cost"] == 100.0  # 10 m3 × 10 NOK/m3


async def test_calculate_price_no_spot_price(
    price_calculator: HWPriceCalculator,
    mock_nord_pool_fetcher: MagicMock,
):
    """Test that None is returned when spot price is unavailable."""
    mock_nord_pool_fetcher.get_spot_price = AsyncMock(return_value=None)

    result = await price_calculator.calculate_price(
        consumption=10.0,
        year=2024,
        month=1,
        nord_pool_area="NO1",
    )

    assert result is None


async def test_calculate_price_no_nord_pool_area(
    price_calculator: HWPriceCalculator,
):
    """Test that None is returned when Nord Pool area is not configured."""
    result = await price_calculator.calculate_price(
        consumption=10.0,
        year=2024,
        month=1,
        nord_pool_area=None,
    )

    assert result is None


async def test_calibration_ratio_calculation(
    hass: HomeAssistant,
    mock_nord_pool_fetcher: MagicMock,
    mock_calculate_calibration_ratio,
):
    """Test that calibration ratio is calculated and cached."""

    def get_setting(name: str) -> str | None:
        if name == "Currency":
            return "NOK"
        if name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    calculator = HWPriceCalculator(
        calculate_calibration_ratio=mock_calculate_calibration_ratio,
        nord_pool_fetcher=mock_nord_pool_fetcher,
        get_setting=get_setting,
    )

    # First call should calculate calibration
    await calculator.calculate_price(
        consumption=10.0,
        year=2024,
        month=1,
        nord_pool_area="NO1",
    )

    assert calculator._calibration_calculated is True
    assert calculator._calibration_ratio == 1.5

    # Second call should use cached calibration
    result2 = await calculator.calculate_price(
        consumption=5.0,
        year=2024,
        month=1,
        nord_pool_area="NO1",
    )

    # Calibration should still be 1.5 (cached)
    assert result2["calibration_ratio"] == 1.5
