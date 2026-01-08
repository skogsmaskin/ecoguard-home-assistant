"""Monthly aggregate calculator for EcoGuard integration (aggregate/all meters)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable
import logging

from .helpers import get_timezone, get_month_timestamps, format_cache_key

_LOGGER = logging.getLogger(__name__)


class MonthlyAggregateCalculator:
    """Calculates monthly aggregates for all meters combined (aggregate)."""

    def __init__(
        self,
        node_id: int,
        request_deduplicator: Any,  # RequestDeduplicator
        api: Any,  # EcoGuardAPI
        get_setting: Callable[[str], str | None],
        get_monthly_aggregate: Callable[
            [str, int, int, str, str], Awaitable[dict[str, Any] | None]
        ],
        get_hw_price_from_spot_prices: Callable[
            [float, int, int, float | None, float | None],
            Awaitable[dict[str, Any] | None],
        ],
        billing_manager: Any,  # BillingManager
        daily_consumption_cache: dict[str, list[dict[str, Any]]],
        daily_price_cache: dict[str, list[dict[str, Any]]],
        monthly_aggregate_cache: dict[str, dict[str, Any]],
        sync_cache_to_data: Callable[[], None],
    ) -> None:
        """Initialize the monthly aggregate calculator.

        Args:
            node_id: Node ID
            request_deduplicator: Request deduplicator instance
            api: EcoGuard API instance
            get_setting: Function to get setting value
            get_monthly_aggregate: Function to get monthly aggregate (for recursive calls)
            get_hw_price_from_spot_prices: Function to get HW price from spot prices
            billing_manager: Billing manager instance
            daily_consumption_cache: Cache of daily consumption data
            daily_price_cache: Cache of daily price data
            monthly_aggregate_cache: Cache of monthly aggregate data
            sync_cache_to_data: Function to sync cache to coordinator data
        """
        self.node_id = node_id
        self._request_deduplicator = request_deduplicator
        self._api = api
        self._get_setting = get_setting
        self._get_monthly_aggregate = get_monthly_aggregate
        self._get_hw_price_from_spot_prices = get_hw_price_from_spot_prices
        self._billing_manager = billing_manager
        self._daily_consumption_cache = daily_consumption_cache
        self._daily_price_cache = daily_price_cache
        self._monthly_aggregate_cache = monthly_aggregate_cache
        self._sync_cache_to_data = sync_cache_to_data

    def _get_month_timestamps(self, year: int, month: int) -> tuple[int, int]:
        """Get start and end timestamps for a month."""
        return get_month_timestamps(
            year, month, get_timezone(self._get_setting("TimeZoneIANA"))
        )

    async def _calculate_monthly_price_from_daily_cache(
        self, utility_code: str, year: int, month: int
    ) -> dict[str, Any] | None:
        """Calculate monthly price from cached daily prices."""
        from_time, to_time = self._get_month_timestamps(year, month)

        # Try to calculate from cached daily prices (aggregate across all meters)
        total_price = 0.0
        has_cached_data = False

        # Sum prices from all meters for this utility
        for cache_key_price, daily_prices in self._daily_price_cache.items():
            if cache_key_price.startswith(
                f"{utility_code}_"
            ) and cache_key_price.endswith("_metered"):
                # Filter daily prices for this month
                month_prices = [
                    p
                    for p in daily_prices
                    if from_time <= p["time"] < to_time
                    and p.get("value") is not None
                    and p.get("value", 0) > 0
                ]
                if month_prices:
                    # Sum prices for this meter
                    meter_total = sum(p["value"] for p in month_prices)
                    total_price += meter_total
                    has_cached_data = True

        if has_cached_data:
            currency = self._get_setting("Currency") or ""
            _LOGGER.info(
                "✓ Smart reuse: Calculated monthly price for %s %d-%02d from cached daily prices (no API call!)",
                utility_code,
                year,
                month,
            )
            return {
                "value": total_price,
                "unit": currency,
                "year": year,
                "month": month,
                "utility_code": utility_code,
                "aggregate_type": "price",
                "cost_type": "actual",
                "is_estimated": False,
            }

        return None

    async def _fetch_monthly_price_from_api(
        self, utility_code: str, year: int, month: int
    ) -> dict[str, Any] | None:
        """Fetch monthly price from API with request deduplication."""
        from_time, to_time = self._get_month_timestamps(year, month)

        # Create cache key for this request
        api_cache_key = format_cache_key(
            "data",
            utility_code=utility_code,
            from_time=from_time,
            to_time=to_time,
            node_id=self.node_id,
            aggregate_type="price",
        )

        async def fetch_price_data() -> list[dict[str, Any]] | None:
            """Fetch price data from API."""
            _LOGGER.debug(
                "Fetching monthly price for %s %d-%02d from API: from=%s to=%s",
                utility_code,
                year,
                month,
                from_time,
                to_time,
            )
            return await self._api.get_data(
                node_id=self.node_id,
                from_time=from_time,
                to_time=to_time,
                interval="d",
                grouping="apartment",
                utilities=[f"{utility_code}[price]"],
                include_sub_nodes=True,
            )

        # Use request deduplicator to handle caching and deduplication
        data = await self._request_deduplicator.get_or_fetch(
            cache_key=api_cache_key,
            fetch_func=fetch_price_data,
            use_cache=True,
        )

        if not data or not isinstance(data, list):
            return None

        # Process the data
        has_actual_api_data = False
        total_price = 0.0
        for node_data in data:
            results = node_data.get("Result", [])
            for result in results:
                if result.get("Utl") == utility_code and result.get("Func") == "price":
                    values = result.get("Values", [])
                    for value_entry in values:
                        value = value_entry.get("Value")
                        if value is not None and value > 0:
                            total_price += value
                            has_actual_api_data = True

        if has_actual_api_data:
            currency = self._get_setting("Currency") or ""
            return {
                "value": total_price,
                "unit": currency,
                "year": year,
                "month": month,
                "utility_code": utility_code,
                "aggregate_type": "price",
                "cost_type": "actual",
                "is_estimated": False,
            }

        return None

    async def _get_monthly_price_actual(
        self, utility_code: str, year: int, month: int, cache_key: str
    ) -> dict[str, Any] | None:
        """Get monthly actual price aggregate."""
        # Try to calculate from cached daily prices first
        result = await self._calculate_monthly_price_from_daily_cache(
            utility_code, year, month
        )
        if result:
            self._monthly_aggregate_cache[cache_key] = result
            self._sync_cache_to_data()
            return result

        _LOGGER.debug(
            "Daily price cache exists but no values for %s %d-%02d (date range mismatch?)",
            utility_code,
            year,
            month,
        )

        # Fall back to API call
        result = await self._fetch_monthly_price_from_api(utility_code, year, month)
        if result:
            self._monthly_aggregate_cache[cache_key] = result
            self._sync_cache_to_data()

        return result

    async def _get_monthly_price_cw(
        self, utility_code: str, year: int, month: int, cost_type: str
    ) -> dict[str, Any] | None:
        """Get monthly price for CW utility."""
        if utility_code != "CW":
            return None

        from_time, to_time = self._get_month_timestamps(year, month)

        # Create cache key for this request
        api_cache_key = format_cache_key(
            "data",
            utility_code=utility_code,
            from_time=from_time,
            to_time=to_time,
            node_id=self.node_id,
            aggregate_type="price",
        )

        async def fetch_cw_price_data() -> list[dict[str, Any]] | None:
            """Fetch CW price data from API."""
            _LOGGER.debug(
                "Fetching CW price for %d-%02d from API: from=%s to=%s",
                year,
                month,
                from_time,
                to_time,
            )
            return await self._api.get_data(
                node_id=self.node_id,
                from_time=from_time,
                to_time=to_time,
                interval="d",
                grouping="apartment",
                utilities=[f"{utility_code}[price]"],
                include_sub_nodes=True,
            )

        # Use request deduplicator to handle caching and deduplication
        data = await self._request_deduplicator.get_or_fetch(
            cache_key=api_cache_key,
            fetch_func=fetch_cw_price_data,
            use_cache=True,
        )

        if not data or not isinstance(data, list):
            return None

        # Process the data
        total_price = 0.0
        has_data = False

        for node_data in data:
            results = node_data.get("Result", [])
            for result in results:
                if result.get("Utl") == utility_code and result.get("Func") == "price":
                    values = result.get("Values", [])
                    for value_entry in values:
                        value = value_entry.get("Value")
                        if value is not None and value > 0:  # Only use non-zero values
                            total_price += value
                            has_data = True

        if has_data:
            _LOGGER.debug(
                "Got CW price from consumption endpoint: %.2f for %d-%02d",
                total_price,
                year,
                month,
            )
            currency = self._get_setting("Currency") or "NOK"
            return {
                "value": total_price,
                "unit": currency,
                "year": year,
                "month": month,
                "utility_code": utility_code,
                "aggregate_type": "price",
                "cost_type": cost_type,
                "is_estimated": False,
            }

        return None

    async def _get_monthly_price_hw_estimated(
        self, year: int, month: int
    ) -> dict[str, Any] | None:
        """Get monthly estimated price for HW utility."""
        from_time, to_time = self._get_month_timestamps(year, month)

        # First check if we have actual price data from API
        # Use the same deduplication pattern as other price fetches
        api_cache_key = format_cache_key(
            "data",
            utility_code="HW",
            from_time=from_time,
            to_time=to_time,
            node_id=self.node_id,
            aggregate_type="price",
        )

        async def fetch_hw_price_check_data() -> list[dict[str, Any]] | None:
            """Fetch HW price check data from API."""
            _LOGGER.debug(
                "Fetching HW price check for %d-%02d from API: from=%s to=%s",
                year,
                month,
                from_time,
                to_time,
            )
            return await self._api.get_data(
                node_id=self.node_id,
                from_time=from_time,
                to_time=to_time,
                interval="d",
                grouping="apartment",
                utilities=["HW[price]"],
                include_sub_nodes=True,
            )

        # Use request deduplicator to handle caching and deduplication
        data = await self._request_deduplicator.get_or_fetch(
            cache_key=api_cache_key,
            fetch_func=fetch_hw_price_check_data,
            use_cache=True,
        )

        # Process the data
        has_actual_api_data = False
        total_price = 0.0
        if data and isinstance(data, list):
            for node_data in data:
                results = node_data.get("Result", [])
                for result in results:
                    if result.get("Utl") == "HW" and result.get("Func") == "price":
                        values = result.get("Values", [])
                        for value_entry in values:
                            value = value_entry.get("Value")
                            if value is not None and value > 0:
                                total_price += value
                                has_actual_api_data = True

        # If we have actual API data, return it (estimated = actual when actual exists)
        if has_actual_api_data:
            currency = self._get_setting("Currency") or ""
            return {
                "value": total_price,
                "unit": currency,
                "year": year,
                "month": month,
                "utility_code": "HW",
                "aggregate_type": "price",
                "cost_type": "actual",
                "is_estimated": False,
            }

        # No actual price data from API, estimate from spot prices
        # Note: This calls back to coordinator's get_monthly_aggregate (recursive)
        hw_consumption_data = await self._get_monthly_aggregate(
            utility_code="HW",
            year=year,
            month=month,
            aggregate_type="con",
        )

        if hw_consumption_data:
            hw_consumption = hw_consumption_data.get("value")
            if hw_consumption and hw_consumption > 0:
                # Get CW price and consumption for the estimation
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
                )

                cw_price = cw_price_data.get("value") if cw_price_data else None
                cw_consumption = (
                    cw_consumption_data.get("value") if cw_consumption_data else None
                )

                # Estimate HW price from spot prices
                hw_estimated_data = await self._get_hw_price_from_spot_prices(
                    consumption=hw_consumption,
                    year=year,
                    month=month,
                    cold_water_price=cw_price,
                    cold_water_consumption=cw_consumption,
                )

                if hw_estimated_data:
                    hw_estimated_data["cost_type"] = "estimated"
                    hw_estimated_data["is_estimated"] = True
                    return hw_estimated_data

        return None

    async def _calculate_monthly_consumption_from_daily_cache(
        self, utility_code: str, year: int, month: int, cache_key: str
    ) -> dict[str, Any] | None:
        """Calculate monthly consumption from cached daily consumption data."""
        cache_key_daily = f"{utility_code}_all"  # Use aggregate cache key
        daily_values = self._daily_consumption_cache.get(cache_key_daily)

        if not daily_values:
            return None

        from_time, to_time = self._get_month_timestamps(year, month)

        # Filter daily values for this month
        month_values = [
            v
            for v in daily_values
            if from_time <= v["time"] < to_time and v.get("value") is not None
        ]

        if not month_values:
            return None

        # Sum all values for the month
        total_value = sum(v["value"] for v in month_values)
        unit = month_values[0].get("unit", "") if month_values else ""

        _LOGGER.debug(
            "Calculated monthly consumption for %s %d-%02d from %d cached daily values (reused data!)",
            utility_code,
            year,
            month,
            len(month_values),
        )

        result = {
            "value": total_value,
            "unit": unit,
            "year": year,
            "month": month,
            "utility_code": utility_code,
            "aggregate_type": "con",
        }
        # Cache the result
        self._monthly_aggregate_cache[cache_key] = result
        self._sync_cache_to_data()
        return result

    async def _fetch_monthly_consumption_from_api(
        self,
        utility_code: str,
        year: int,
        month: int,
        aggregate_type: str,
        cache_key: str,
    ) -> dict[str, Any] | None:
        """Fetch monthly consumption from API."""
        from_time, to_time = self._get_month_timestamps(year, month)

        # Query data endpoint for the month
        utilities = [f"{utility_code}[{aggregate_type}]"]

        # Create cache key for this request
        api_cache_key = format_cache_key(
            "data",
            utility_code=utility_code,
            from_time=from_time,
            to_time=to_time,
            node_id=self.node_id,
            aggregate_type=aggregate_type,
        )

        async def fetch_data() -> list[dict[str, Any]] | None:
            """Fetch consumption data from API."""
            _LOGGER.debug(
                "Fetching monthly aggregate for %s[%s] %d-%02d from API: from=%s to=%s",
                utility_code,
                aggregate_type,
                year,
                month,
                from_time,
                to_time,
            )
            return await self._api.get_data(
                node_id=self.node_id,
                from_time=from_time,
                to_time=to_time,
                interval="d",
                grouping="apartment",
                utilities=utilities,
                include_sub_nodes=True,
            )

        # Use request deduplicator to handle caching and deduplication
        data = await self._request_deduplicator.get_or_fetch(
            cache_key=api_cache_key,
            fetch_func=fetch_data,
            use_cache=True,
        )

        if not data or not isinstance(data, list):
            return None

        # Aggregate values across all nodes and days
        total_value = 0.0
        unit = ""
        has_data = False

        for node_data in data:
            results = node_data.get("Result", [])
            for result in results:
                if (
                    result.get("Utl") == utility_code
                    and result.get("Func") == aggregate_type
                ):
                    values = result.get("Values", [])
                    unit = result.get("Unit", "")

                    # Sum all non-null values for the month
                    for value_entry in values:
                        value = value_entry.get("Value")
                        if value is not None:
                            total_value += value
                            has_data = True

        if not has_data:
            return None

        result = {
            "value": total_value,
            "unit": unit,
            "year": year,
            "month": month,
            "utility_code": utility_code,
            "aggregate_type": aggregate_type,
        }
        # Cache the result
        self._monthly_aggregate_cache[cache_key] = result
        self._sync_cache_to_data()
        return result

    async def calculate(
        self,
        utility_code: str,
        year: int,
        month: int,
        aggregate_type: str = "con",
        cost_type: str = "actual",
        cache_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Calculate monthly aggregate for consumption or price (aggregate/all meters).

        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            year: Year (e.g., 2025)
            month: Month (1-12)
            aggregate_type: "con" for consumption, "price" for price
            cost_type: "actual" for metered API data, "estimated" for estimated
            cache_key: Cache key for storing result (if None, will be generated)

        Returns:
            Dict with 'value', 'unit', 'year', 'month', 'utility_code', 'aggregate_type', 'cost_type',
            or None if no data is available.
        """
        if cache_key is None:
            cache_key = f"{utility_code}_{year}_{month}_{aggregate_type}_{cost_type}"

        # Wrap calculation in a task for deduplication
        async def _calculate_monthly_aggregate() -> dict[str, Any] | None:
            # For price aggregates
            if aggregate_type == "price":
                # Handle actual price
                if cost_type == "actual":
                    result = await self._get_monthly_price_actual(
                        utility_code, year, month, cache_key
                    )
                    if result:
                        return result

                # Handle CW price (special case)
                if utility_code == "CW":
                    result = await self._get_monthly_price_cw(
                        utility_code, year, month, cost_type
                    )
                    if result:
                        self._monthly_aggregate_cache[cache_key] = result
                        self._sync_cache_to_data()
                        return result

                # Handle HW estimated price
                if utility_code == "HW" and cost_type == "estimated":
                    result = await self._get_monthly_price_hw_estimated(year, month)
                    if result:
                        self._monthly_aggregate_cache[cache_key] = result
                        self._sync_cache_to_data()
                        return result

                # For HW actual: if we got here, we already checked for actual API data and didn't find it
                # Don't fall back to billing/spot prices for "actual" - that would be an estimate
                if utility_code == "HW" and cost_type == "actual":
                    _LOGGER.debug(
                        "No actual API price data for HW %d-%02d, returning None for actual cost",
                        year,
                        month,
                    )
                    return None

                # For CW or HW estimated (fallback), use billing results
                result = await self._billing_manager.get_monthly_price_from_billing(
                    utility_code=utility_code,
                    year=year,
                    month=month,
                )

                if result:
                    result["cost_type"] = cost_type
                    # Mark as estimated if it came from spot prices or billing calculation
                    if utility_code == "HW":
                        result["is_estimated"] = True
                    else:
                        result["is_estimated"] = False
                    self._monthly_aggregate_cache[cache_key] = result
                    self._sync_cache_to_data()

                return result

            # For consumption aggregates
            if aggregate_type == "con":
                # Try to calculate from cached daily consumption data first
                result = await self._calculate_monthly_consumption_from_daily_cache(
                    utility_code, year, month, cache_key
                )
                if result:
                    return result

                # Fall back to API call
                return await self._fetch_monthly_consumption_from_api(
                    utility_code, year, month, aggregate_type, cache_key
                )

            return None

        # Use request deduplicator for calculation task (not API call, so use_cache=False)
        return await self._request_deduplicator.get_or_fetch(
            cache_key=cache_key,
            fetch_func=_calculate_monthly_aggregate,
            use_cache=False,  # Don't cache calculation results, only deduplicate
        )
