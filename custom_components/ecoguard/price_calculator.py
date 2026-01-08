"""Price calculation logic for EcoGuard utilities."""

from __future__ import annotations

from typing import Any, Callable, Awaitable
import asyncio
import logging

from .nord_pool import NordPoolPriceFetcher

_LOGGER = logging.getLogger(__name__)


class HWPriceCalculator:
    """Calculates hot water prices using spot prices and calibration."""

    # Default energy factor: kWh needed to heat 1 m3 of water
    # Typical: 40-50 kWh/m3 (heating from ~10°C to ~60°C, ~50°C rise)
    ENERGY_PER_M3 = 45.0  # kWh per m3

    def __init__(
        self,
        calculate_calibration_ratio: Callable[[int], Awaitable[float | None]],
        nord_pool_fetcher: NordPoolPriceFetcher | None = None,
        get_rate_from_billing: Any | None = None,
        get_setting: Any | None = None,
    ) -> None:
        """Initialize the HW price calculator.

        Args:
            nord_pool_fetcher: Nord Pool price fetcher instance
            get_rate_from_billing: Function to get rate from billing
            get_setting: Function to get setting value
            calculate_calibration_ratio: Async function to calculate calibration ratio.
                Should accept months_back (int) and return float | None
        """
        self._nord_pool_fetcher = nord_pool_fetcher
        self._get_rate_from_billing = get_rate_from_billing
        self._get_setting = get_setting
        self._calculate_calibration_ratio_callback = calculate_calibration_ratio
        self._calibration_ratio: float | None = None
        self._calibration_calculated: bool = False
        self._calibration_lock = asyncio.Lock()

    async def calculate_price(
        self,
        consumption: float,
        year: int,
        month: int,
        cold_water_price: float | None = None,
        cold_water_consumption: float | None = None,
        nord_pool_area: str | None = None,
    ) -> dict[str, Any] | None:
        """Calculate hot water price using electricity spot prices.

        Formula: cost = consumption (m3) × energy_per_m3 (kWh/m3) × avg_spot_price (NOK/kWh)

        Args:
            consumption: Hot water consumption in m3
            year: Year
            month: Month
            cold_water_price: Optional cold water price for current month
            cold_water_consumption: Optional cold water consumption for current month
            nord_pool_area: Nord Pool area code

        Returns:
            Dict with price data, or None if spot prices unavailable
        """
        try:
            # Calculate calibration ratio from historical data (once, cached)
            async with self._calibration_lock:
                if not self._calibration_calculated:
                    self._calibration_ratio = await self._calculate_calibration_ratio()
                    self._calibration_calculated = True
                    if self._calibration_ratio:
                        _LOGGER.info(
                            "Using calibrated HW heating cost ratio: %.3f (from historical billing data)",
                            self._calibration_ratio,
                        )
                    else:
                        _LOGGER.debug(
                            "No calibration ratio available, using default calculation"
                        )

            spot_price = None
            price_sensor_entity_id = None
            currency = None

            # First, try to get spot price from Nord Pool API if area is configured
            if nord_pool_area and self._nord_pool_fetcher:
                currency = (
                    self._get_setting("Currency") if self._get_setting else None
                ) or "NOK"
                timezone_str = (
                    self._get_setting("TimeZoneIANA") if self._get_setting else None
                ) or "UTC"

                spot_price = await self._nord_pool_fetcher.get_spot_price(
                    area_code=nord_pool_area,
                    currency=currency,
                    timezone_str=timezone_str,
                )

                if spot_price is not None:
                    _LOGGER.info(
                        "Using Nord Pool API spot price for %s/%s: %.4f %s/kWh",
                        nord_pool_area,
                        currency,
                        spot_price,
                        currency,
                    )
                    price_sensor_entity_id = f"nord_pool_api_{nord_pool_area}"

            # If no spot price from API, return None
            if spot_price is None:
                _LOGGER.debug(
                    "No spot price available from Nord Pool API (area: %s, currency: %s)",
                    nord_pool_area or "not configured",
                    currency or "unknown",
                )
                return None

            # If we got currency from settings but not from sensor, use it
            if not currency:
                currency = (
                    self._get_setting("Currency") if self._get_setting else None
                ) or "NOK"

            # Calculate total energy needed to heat the water
            total_energy_kwh = consumption * self.ENERGY_PER_M3

            # Calculate base heating cost from spot price
            base_heating_cost = total_energy_kwh * spot_price

            # Apply calibration ratio if available
            if self._calibration_ratio is not None:
                heating_cost = base_heating_cost * self._calibration_ratio
                _LOGGER.debug(
                    "Applied calibration ratio %.3f: base=%.2f NOK → calibrated=%.2f NOK",
                    self._calibration_ratio,
                    base_heating_cost,
                    heating_cost,
                )
            else:
                heating_cost = base_heating_cost

            # Get cold water cost
            cold_water_cost = None
            cold_water_rate = None

            if cold_water_price is not None and cold_water_consumption is not None:
                if cold_water_consumption > 0:
                    cold_water_rate = cold_water_price / cold_water_consumption
                    cold_water_cost = consumption * cold_water_rate
                    _LOGGER.debug(
                        "Using current month CW price: %.2f NOK for %.2f m3 = %.2f NOK/m3 rate",
                        cold_water_price,
                        cold_water_consumption,
                        cold_water_rate,
                    )

            # Fallback to billing rate if we don't have current month price
            if cold_water_cost is None and self._get_rate_from_billing:
                cold_water_rate = await self._get_rate_from_billing("CW", year, month)

                if cold_water_rate is None:
                    _LOGGER.debug(
                        "Could not get cold water rate for HW calculation, using heating cost only"
                    )
                    total_cost = heating_cost
                else:
                    cold_water_cost = consumption * cold_water_rate
                    _LOGGER.debug(
                        "Using billing rate for CW: %.2f NOK/m3",
                        cold_water_rate,
                    )

            if cold_water_cost is not None:
                total_cost = cold_water_cost + heating_cost
                _LOGGER.debug(
                    "HW cost breakdown: %.2f m3 × %.2f NOK/m3 (CW) + %.2f kWh × %.4f NOK/kWh (heating) = %.2f + %.2f = %.2f NOK",
                    consumption,
                    cold_water_rate,
                    total_energy_kwh,
                    spot_price,
                    cold_water_cost,
                    heating_cost,
                    total_cost,
                )
            else:
                total_cost = heating_cost

            _LOGGER.debug(
                "Calculated HW price from spot: %.2f m3, heating: %.2f kWh × %.4f NOK/kWh = %.2f NOK (sensor: %s)",
                consumption,
                total_energy_kwh,
                spot_price,
                heating_cost if cold_water_rate is None else total_cost,
                price_sensor_entity_id,
            )

            if not currency:
                currency = (
                    self._get_setting("Currency") if self._get_setting else None
                ) or "NOK"

            result = {
                "value": round(total_cost, 2),
                "unit": currency,
                "year": year,
                "month": month,
                "utility_code": "HW",
                "aggregate_type": "price",
                "calculation_method": (
                    "spot_price_calibrated" if self._calibration_ratio else "spot_price"
                ),
                "energy_per_m3_kwh": self.ENERGY_PER_M3,
                "total_energy_kwh": round(total_energy_kwh, 2),
                "spot_price_per_kwh": round(spot_price, 4),
                "spot_price_currency": currency,
                "heating_cost": round(heating_cost, 2),
            }

            if self._calibration_ratio is not None:
                result["calibration_ratio"] = round(self._calibration_ratio, 3)
                result["base_heating_cost"] = round(base_heating_cost, 2)

            if price_sensor_entity_id:
                result["price_source"] = price_sensor_entity_id

            if nord_pool_area:
                result["nord_pool_area"] = nord_pool_area
                result["price_source"] = "nord_pool_api"

            if cold_water_rate is not None:
                result["cold_water_rate_nok_per_m3"] = round(cold_water_rate, 2)
                result["cold_water_cost"] = round(consumption * cold_water_rate, 2)

            return result
        except Exception as err:
            _LOGGER.debug(
                "Failed to calculate HW price from spot prices: %s",
                err,
            )
            return None

    async def _calculate_calibration_ratio(
        self,
        months_back: int = 6,
    ) -> float | None:
        """Calculate calibration ratio by comparing historical billing data with spot prices.

        Uses the provided callback function to calculate the calibration ratio.
        The calibration ratio accounts for system efficiency, fixed costs, and other factors
        that aren't captured by simple spot price calculations.

        Args:
            months_back: Number of months to look back for historical data (default: 6)

        Returns:
            Calibration ratio (typically 1.5-2.5), or None if unavailable
        """
        try:
            ratio = await self._calculate_calibration_ratio_callback(months_back)
            if ratio is not None:
                _LOGGER.debug(
                    "Calculated calibration ratio: %.3f (from %d months of historical data)",
                    ratio,
                    months_back,
                )
            else:
                _LOGGER.debug(
                    "Calibration ratio calculation returned None (insufficient historical data)"
                )
            return ratio
        except Exception as err:
            _LOGGER.warning(
                "Failed to calculate calibration ratio: %s",
                err,
                exc_info=True,
            )
            return None
