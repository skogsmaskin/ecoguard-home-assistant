"""Meter aggregate calculator for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable
import logging
import zoneinfo

from .helpers import get_timezone, format_cache_key

_LOGGER = logging.getLogger(__name__)


class MeterAggregateCalculator:
    """Calculates monthly aggregates for specific meters."""

    def __init__(
        self,
        node_id: int,
        request_deduplicator: Any,  # RequestDeduplicator
        api: Any,  # EcoGuardAPI
        get_setting: Callable[[str], str | None],
        get_monthly_aggregate: Callable[[str, int, int, str, str], Awaitable[dict[str, Any] | None]],
        get_hw_price_from_spot_prices: Callable[[float, int, int, float | None, float | None], Awaitable[dict[str, Any] | None]],
        billing_manager: Any,  # BillingManager
        installations: list[dict[str, Any]],
    ) -> None:
        """Initialize the meter aggregate calculator.

        Args:
            node_id: Node ID
            request_deduplicator: Request deduplicator instance
            api: EcoGuard API instance
            get_setting: Function to get setting value
            get_monthly_aggregate: Function to get monthly aggregate
            get_hw_price_from_spot_prices: Function to get HW price from spot prices
            billing_manager: Billing manager instance
            installations: List of installations
        """
        self.node_id = node_id
        self._request_deduplicator = request_deduplicator
        self._api = api
        self._get_setting = get_setting
        self._get_monthly_aggregate = get_monthly_aggregate
        self._get_hw_price_from_spot_prices = get_hw_price_from_spot_prices
        self._billing_manager = billing_manager
        self._installations = installations

    async def calculate(
        self,
        utility_code: str,
        measuring_point_id: int,
        external_key: str | None,
        year: int,
        month: int,
        aggregate_type: str = "con",
        cost_type: str = "actual",
    ) -> dict[str, Any] | None:
        """Get monthly aggregate for consumption or price for a specific meter.

        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            measuring_point_id: Measuring point ID
            external_key: External key for the installation
            year: Year (e.g., 2025)
            month: Month (1-12)
            aggregate_type: "con" for consumption, "price" for price
            cost_type: "actual" for metered API data, "estimated" for estimated (only for price)

        Returns:
            Dict with 'value', 'unit', 'year', 'month', 'utility_code', 'aggregate_type', 'cost_type',
            or None if no data is available.

        Note:
            For price aggregates, this method fetches per-meter price data directly from the API
            using measuringpointid. The API provides accurate per-meter cost data without requiring
            proportional allocation.
        """
        # For price, fetch directly using measuringpointid
        if aggregate_type == "price":
            return await self._calculate_price_aggregate(
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                external_key=external_key,
                year=year,
                month=month,
                cost_type=cost_type,
            )

        # For consumption, we can filter by measuring point
        return await self._calculate_consumption_aggregate(
            utility_code=utility_code,
            measuring_point_id=measuring_point_id,
            external_key=external_key,
            year=year,
            month=month,
        )

    async def _calculate_price_aggregate(
        self,
        utility_code: str,
        measuring_point_id: int,
        external_key: str | None,
        year: int,
        month: int,
        cost_type: str,
    ) -> dict[str, Any] | None:
        """Calculate price aggregate for a specific meter."""
        try:
            # Get timezone from settings
            timezone_str = self._get_setting("TimeZoneIANA")
            if not timezone_str:
                timezone_str = "UTC"

            tz = get_timezone(timezone_str)

            # Calculate month boundaries in the configured timezone
            from_date = datetime(year, month, 1, tzinfo=tz)
            if month == 12:
                to_date = datetime(year + 1, 1, 1, tzinfo=tz)
            else:
                to_date = datetime(year, month + 1, 1, tzinfo=tz)

            from_time = int(from_date.timestamp())
            to_time = int(to_date.timestamp())

            # Create cache key for this request
            cache_key = format_cache_key(
                "data_meter",
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                from_time=from_time,
                to_time=to_time,
                node_id=self.node_id,
                aggregate_type="price",
            )

            async def fetch_data() -> list[dict[str, Any]] | None:
                """Fetch price data for specific meter from API."""
                utilities = [f"{utility_code}[price]"]
                _LOGGER.debug(
                    "Fetching price data for measuring_point_id=%d with utility=%s (matching utility for this meter)",
                    measuring_point_id,
                    utility_code,
                )
                return await self._api.get_data(
                    node_id=self.node_id,
                    from_time=from_time,
                    to_time=to_time,
                    interval="d",
                    grouping="apartment",
                    utilities=utilities,
                    include_sub_nodes=False,
                    measuring_point_id=measuring_point_id,
                )

            # Use request deduplicator to handle caching and deduplication
            data = await self._request_deduplicator.get_or_fetch(
                cache_key=cache_key,
                fetch_func=fetch_data,
                use_cache=True,
            )

            if not data or not isinstance(data, list):
                _LOGGER.debug(
                    "No price data returned for meter %d (%s %d-%02d)",
                    measuring_point_id,
                    utility_code,
                    year,
                    month,
                )
                # For estimated costs, try to calculate from consumption × rate even if API returned no data
                if cost_type == "estimated":
                    _LOGGER.debug(
                        "No API price data for meter %d (%s %d-%02d), trying estimated cost calculation",
                        measuring_point_id,
                        utility_code,
                        year,
                        month,
                    )
                    has_data = False
                else:
                    return None
            else:
                # Extract price values from the response
                total_value = 0.0
                unit = ""
                has_data = False

                for node_data in data:
                    results = node_data.get("Result", [])
                    for result in results:
                        if result.get("Utl") == utility_code and result.get("Func") == "price":
                            values = result.get("Values", [])
                            unit = result.get("Unit", "")

                            _LOGGER.debug(
                                "Processing price data for meter %d (%s %d-%02d): found %d value entries",
                                measuring_point_id,
                                utility_code,
                                year,
                                month,
                                len(values),
                            )

                            # Sum all non-null values for the month
                            non_null_count = 0
                            null_count = 0
                            zero_count = 0
                            for value_entry in values:
                                value = value_entry.get("Value")
                                if value is not None:
                                    total_value += value
                                    has_data = True
                                    non_null_count += 1
                                    if value == 0:
                                        zero_count += 1
                                else:
                                    null_count += 1

                            _LOGGER.debug(
                                "Price data summary for meter %d (%s %d-%02d): %d non-null values (%d zeros), %d null values, total=%.2f %s",
                                measuring_point_id,
                                utility_code,
                                year,
                                month,
                                non_null_count,
                                zero_count,
                                null_count,
                                total_value,
                                unit,
                            )

            if has_data:
                # For hot water: if all values are 0, treat as "Unknown" (no metered price data)
                # HW prices are typically calculated from spot prices, not from API metered data
                if total_value == 0.0 and utility_code == "HW" and cost_type == "actual":
                    _LOGGER.debug(
                        "All HW price entries are 0 for meter %d (%s %d-%02d), treating as Unknown (no metered price data)",
                        measuring_point_id,
                        utility_code,
                        year,
                        month,
                    )
                    return None

                # If we have data but total is 0, and this is an estimated cost request,
                # fall back to calculation instead of returning 0
                if total_value == 0.0 and cost_type == "estimated":
                    _LOGGER.debug(
                        "Found price data for meter %d (%s %d-%02d) but total is 0.00. For estimated costs, falling back to calculation.",
                        measuring_point_id,
                        utility_code,
                        year,
                        month,
                    )
                    # For HW, try proportional allocation first (more accurate than spot price estimation)
                    if utility_code == "HW":
                        result = await self._try_hw_proportional_allocation(
                            measuring_point_id=measuring_point_id,
                            external_key=external_key,
                            year=year,
                            month=month,
                        )
                        if result:
                            return result
                    # Fall through to estimated cost calculation below
                else:
                    _LOGGER.debug(
                        "Found per-meter price data for meter %d (%s %d-%02d): %.2f %s",
                        measuring_point_id,
                        utility_code,
                        year,
                        month,
                        total_value,
                        unit,
                    )
                    return {
                        "value": total_value,
                        "unit": unit,
                        "year": year,
                        "month": month,
                        "utility_code": utility_code,
                        "aggregate_type": "price",
                        "cost_type": cost_type,
                        "measuring_point_id": measuring_point_id,
                    }
            else:
                _LOGGER.debug(
                    "No price data found for meter %d (%s %d-%02d)",
                    measuring_point_id,
                    utility_code,
                    year,
                    month,
                )
                # For estimated costs, fall back to calculating from consumption × rate
                if cost_type == "estimated":
                    return await self._calculate_estimated_cost(
                        utility_code=utility_code,
                        measuring_point_id=measuring_point_id,
                        external_key=external_key,
                        year=year,
                        month=month,
                    )
                return None
        except Exception as err:
            _LOGGER.warning(
                "Failed to fetch per-meter price data for meter %d %s[price] %d-%02d: %s",
                measuring_point_id,
                utility_code,
                year,
                month,
                err,
            )
            # For estimated costs, try to calculate from consumption × rate even if API call failed
            if cost_type == "estimated":
                try:
                    _LOGGER.debug(
                        "Attempting to calculate estimated cost for meter %d (%s %d-%02d) from consumption × rate after API error",
                        measuring_point_id,
                        utility_code,
                        year,
                        month,
                    )
                    return await self._calculate_estimated_cost(
                        utility_code=utility_code,
                        measuring_point_id=measuring_point_id,
                        external_key=external_key,
                        year=year,
                        month=month,
                    )
                except Exception as calc_err:
                    _LOGGER.debug(
                        "Failed to calculate estimated cost from consumption × rate: %s",
                        calc_err,
                    )
            return None

    async def _calculate_consumption_aggregate(
        self,
        utility_code: str,
        measuring_point_id: int,
        external_key: str | None,
        year: int,
        month: int,
    ) -> dict[str, Any] | None:
        """Calculate consumption aggregate for a specific meter."""
        try:
            # Get timezone from settings
            timezone_str = self._get_setting("TimeZoneIANA")
            if not timezone_str:
                timezone_str = "UTC"

            tz = get_timezone(timezone_str)

            # Calculate month boundaries in the configured timezone
            from_date = datetime(year, month, 1, tzinfo=tz)
            if month == 12:
                to_date = datetime(year + 1, 1, 1, tzinfo=tz)
            else:
                to_date = datetime(year, month + 1, 1, tzinfo=tz)

            from_time = int(from_date.timestamp())
            to_time = int(to_date.timestamp())

            _LOGGER.debug(
                "Fetching monthly aggregate for meter %d (%s[con]) %d-%02d: from=%s to=%s",
                measuring_point_id,
                utility_code,
                year,
                month,
                from_time,
                to_time,
            )

            # Query data endpoint for the month
            utilities = [f"{utility_code}[con]"]

            # Create cache key for this request
            cache_key = format_cache_key(
                "data_meter",
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                from_time=from_time,
                to_time=to_time,
                node_id=self.node_id,
                aggregate_type="con",
            )

            async def fetch_data() -> list[dict[str, Any]] | None:
                """Fetch consumption data for specific meter from API."""
                _LOGGER.debug(
                    "Fetching con data for measuring_point_id=%d with utility=%s (matching utility for this meter)",
                    measuring_point_id,
                    utility_code,
                )
                return await self._api.get_data(
                    node_id=self.node_id,
                    from_time=from_time,
                    to_time=to_time,
                    interval="d",
                    grouping="apartment",
                    utilities=utilities,
                    include_sub_nodes=False,
                    measuring_point_id=measuring_point_id,
                )

            # Use request deduplicator to handle caching and deduplication
            data = await self._request_deduplicator.get_or_fetch(
                cache_key=cache_key,
                fetch_func=fetch_data,
                use_cache=True,
            )

            if not data or not isinstance(data, list):
                return None

            # Filter data to only include this specific meter
            # Match by measuring_point_id or external_key
            total_value = 0.0
            unit = ""
            has_data = False

            for node_data in data:
                node_id = node_data.get("ID")

                # Try to match this node to our measuring point
                matched = False
                if node_id == measuring_point_id:
                    # Direct match by node ID
                    matched = True
                else:
                    # Try to match via installations
                    for inst in self._installations:
                        if inst.get("MeasuringPointID") == measuring_point_id:
                            # If external_key is provided, verify it matches
                            if external_key:
                                inst_external_key = inst.get("ExternalKey")
                                if inst_external_key == external_key:
                                    matched = True
                                    break
                            else:
                                # No external_key provided, match by measuring_point_id only
                                matched = True
                                break

                if not matched:
                    continue

                # Aggregate values for this meter
                results = node_data.get("Result", [])
                for result in results:
                    if result.get("Utl") == utility_code and result.get("Func") == "con":
                        values = result.get("Values", [])
                        unit = result.get("Unit", "")

                        # Sum all non-null values for the month
                        for value_entry in values:
                            value = value_entry.get("Value")
                            if value is not None:
                                total_value += value
                                has_data = True

            if not has_data:
                _LOGGER.debug(
                    "No data found for meter %d (%s[con]) %d-%02d after filtering",
                    measuring_point_id,
                    utility_code,
                    year,
                    month,
                )
                return None

            _LOGGER.debug(
                "Found monthly aggregate for meter %d (%s[con]) %d-%02d: %.2f %s",
                measuring_point_id,
                utility_code,
                year,
                month,
                total_value,
                unit,
            )

            return {
                "value": total_value,
                "unit": unit,
                "year": year,
                "month": month,
                "utility_code": utility_code,
                "aggregate_type": "con",
                "measuring_point_id": measuring_point_id,
            }
        except Exception as err:
            _LOGGER.warning(
                "Failed to fetch monthly aggregate for meter %d %s[con] %d-%02d: %s",
                measuring_point_id,
                utility_code,
                year,
                month,
                err,
            )
            return None

    async def _try_hw_proportional_allocation(
        self,
        measuring_point_id: int,
        external_key: str | None,
        year: int,
        month: int,
    ) -> dict[str, Any] | None:
        """Try to allocate HW cost proportionally from aggregate data."""
        # Get this meter's consumption for the month
        meter_consumption_data = await self.calculate(
            utility_code="HW",
            measuring_point_id=measuring_point_id,
            external_key=external_key,
            year=year,
            month=month,
            aggregate_type="con",
            cost_type="actual",
        )

        if not meter_consumption_data or meter_consumption_data.get("value") is None:
            return None

        meter_consumption = meter_consumption_data.get("value", 0.0)

        if meter_consumption <= 0:
            return None

        # Get total HW consumption and estimated cost for the month
        total_hw_consumption_data = await self._get_monthly_aggregate(
            utility_code="HW",
            year=year,
            month=month,
            aggregate_type="con",
            cost_type="actual",
        )
        total_hw_cost_data = await self._get_monthly_aggregate(
            utility_code="HW",
            year=year,
            month=month,
            aggregate_type="price",
            cost_type="estimated",
        )

        if not total_hw_consumption_data or not total_hw_cost_data:
            return None

        total_hw_consumption = total_hw_consumption_data.get("value", 0.0)
        total_hw_cost = total_hw_cost_data.get("value", 0.0)

        if total_hw_consumption > 0 and total_hw_cost > 0:
            # Calculate this meter's share of total consumption
            consumption_share = meter_consumption / total_hw_consumption

            # Allocate cost proportionally
            allocated_cost = total_hw_cost * consumption_share
            currency = total_hw_cost_data.get("unit") or self._get_setting("Currency") or "NOK"

            _LOGGER.info(
                "Allocated HW cost for meter %d %d-%02d: %.2f %s (meter: %.2f m3 / total: %.2f m3 = %.1f%%, total cost: %.2f %s)",
                measuring_point_id,
                year,
                month,
                allocated_cost,
                currency,
                meter_consumption,
                total_hw_consumption,
                consumption_share * 100,
                total_hw_cost,
                currency,
            )

            return {
                "value": allocated_cost,
                "unit": currency,
                "year": year,
                "month": month,
                "utility_code": "HW",
                "aggregate_type": "price",
                "cost_type": "estimated",
                "measuring_point_id": measuring_point_id,
            }

        return None

    async def _calculate_estimated_cost(
        self,
        utility_code: str,
        measuring_point_id: int,
        external_key: str | None,
        year: int,
        month: int,
    ) -> dict[str, Any] | None:
        """Calculate estimated cost from consumption × rate or other methods."""
        _LOGGER.debug(
            "Calculating estimated cost for meter %d (%s %d-%02d) from consumption × rate",
            measuring_point_id,
            utility_code,
            year,
            month,
        )

        # Get this meter's consumption for the month
        meter_consumption_data = await self.calculate(
            utility_code=utility_code,
            measuring_point_id=measuring_point_id,
            external_key=external_key,
            year=year,
            month=month,
            aggregate_type="con",
            cost_type="actual",
        )

        # Check if we have consumption data
        if not meter_consumption_data or meter_consumption_data.get("value") is None:
            # For HW, still try spot price estimation even without consumption data
            if utility_code == "HW":
                _LOGGER.debug(
                    "No consumption data for HW meter %d %d-%02d, trying spot price estimation anyway",
                    measuring_point_id,
                    year,
                    month,
                )
                return await self._try_hw_spot_price_estimation(
                    measuring_point_id=measuring_point_id,
                    year=year,
                    month=month,
                    meter_consumption=0.0,
                )
            else:
                _LOGGER.debug(
                    "No consumption data available for meter %d (%s), cannot calculate estimated cost",
                    measuring_point_id,
                    utility_code,
                )
                return None

        meter_consumption = meter_consumption_data.get("value", 0.0)

        # Get rate from billing
        rate = await self._billing_manager.get_rate_from_billing(utility_code, year, month)

        if rate is None:
            # For HW, try proportional allocation from aggregate estimated cost
            if utility_code == "HW":
                _LOGGER.debug(
                    "No rate found for HW %d-%02d, trying proportional allocation from aggregate estimated cost",
                    year,
                    month,
                )

                result = await self._try_hw_proportional_allocation(
                    measuring_point_id=measuring_point_id,
                    external_key=external_key,
                    year=year,
                    month=month,
                )
                if result:
                    return result

                # Fallback to spot price estimation if proportional allocation didn't work
                _LOGGER.debug(
                    "Proportional allocation failed, trying spot price estimation for meter %d (HW %d-%02d)",
                    measuring_point_id,
                    year,
                    month,
                )
                return await self._try_hw_spot_price_estimation(
                    measuring_point_id=measuring_point_id,
                    year=year,
                    month=month,
                    meter_consumption=meter_consumption,
                )

            _LOGGER.debug(
                "No rate found for %s %d-%02d, cannot calculate estimated cost",
                utility_code,
                year,
                month,
            )
            return None

        # Calculate cost from consumption × rate
        calculated_cost = meter_consumption * rate
        currency = self._get_setting("Currency") or "NOK"

        _LOGGER.info(
            "Calculated estimated cost for meter %d (%s %d-%02d): %.2f m3 × %.2f = %.2f %s",
            measuring_point_id,
            utility_code,
            year,
            month,
            meter_consumption,
            rate,
            calculated_cost,
            currency,
        )

        # Warn if we got 0 cost but consumption > 0 (might indicate an issue)
        if calculated_cost == 0 and meter_consumption > 0:
            _LOGGER.warning(
                "Estimated cost is 0 for meter %d (%s %d-%02d) despite consumption %.2f m3 and rate %.2f. This might indicate a calculation issue.",
                measuring_point_id,
                utility_code,
                year,
                month,
                meter_consumption,
                rate,
            )

        return {
            "value": calculated_cost,
            "unit": currency,
            "year": year,
            "month": month,
            "utility_code": utility_code,
            "aggregate_type": "price",
            "cost_type": "estimated",
            "measuring_point_id": measuring_point_id,
        }

    async def _try_hw_spot_price_estimation(
        self,
        measuring_point_id: int,
        year: int,
        month: int,
        meter_consumption: float,
    ) -> dict[str, Any] | None:
        """Try to estimate HW cost using spot prices."""
        # Get CW price and consumption for the estimation
        # Use aggregate CW data since the CW meter might be different from the HW meter
        cw_price_data = await self._get_monthly_aggregate(
            utility_code="CW",
            year=year,
            month=month,
            aggregate_type="price",
            cost_type="actual",
        )
        cw_consumption_data = await self._get_monthly_aggregate(
            utility_code="CW",
            year=year,
            month=month,
            aggregate_type="con",
            cost_type="actual",
        )

        cw_price = cw_price_data.get("value") if cw_price_data else None
        cw_consumption = cw_consumption_data.get("value") if cw_consumption_data else None

        # Estimate HW price from spot prices
        _LOGGER.debug(
            "Calling spot price estimation for meter %d (HW %d-%02d): consumption=%.2f m3, cw_price=%s, cw_consumption=%s",
            measuring_point_id,
            year,
            month,
            meter_consumption,
            cw_price,
            cw_consumption,
        )
        hw_estimated_data = await self._get_hw_price_from_spot_prices(
            consumption=meter_consumption,
            year=year,
            month=month,
            cold_water_price=cw_price,
            cold_water_consumption=cw_consumption,
        )

        if hw_estimated_data:
            currency = hw_estimated_data.get("unit") or self._get_setting("Currency") or "NOK"
            estimated_value = hw_estimated_data.get("value", 0.0)
            _LOGGER.info(
                "Estimated HW cost for meter %d %d-%02d: %.2f %s (consumption: %.2f m3, method: %s)",
                measuring_point_id,
                year,
                month,
                estimated_value,
                currency,
                meter_consumption,
                hw_estimated_data.get("calculation_method", "unknown"),
            )
            return {
                "value": estimated_value,
                "unit": currency,
                "year": year,
                "month": month,
                "utility_code": "HW",
                "aggregate_type": "price",
                "cost_type": "estimated",
                "measuring_point_id": measuring_point_id,
            }
        else:
            _LOGGER.warning(
                "Spot price estimation returned None for meter %d (HW %d-%02d) with consumption %.2f m3. Check Nord Pool configuration.",
                measuring_point_id,
                year,
                month,
                meter_consumption,
            )
            return None
