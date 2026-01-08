"""Monthly cost calculator for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Awaitable
import zoneinfo
import logging

_LOGGER = logging.getLogger(__name__)


class MonthlyCostCalculator:
    """Calculates total monthly cost by summing all price values from data API."""

    def __init__(
        self,
        node_id: int,
        api: Any,  # EcoGuardAPI
        get_setting: Callable[[str], str | None],
        get_active_installations: Callable[[], list[dict[str, Any]]],
        get_monthly_aggregate: Callable[[str, int, int, str, str], Awaitable[dict[str, Any] | None]],
        get_hw_price_from_spot_prices: Callable[[float, int, int, float | None, float | None], Awaitable[dict[str, Any] | None]],
        billing_manager: Any,  # BillingManager
    ) -> None:
        """Initialize the monthly cost calculator.

        Args:
            node_id: Node ID for API calls
            api: EcoGuard API instance
            get_setting: Function to get settings
            get_active_installations: Function to get active installations
            get_monthly_aggregate: Function to get monthly aggregate data
            get_hw_price_from_spot_prices: Function to get HW price from spot prices
            billing_manager: Billing manager instance
        """
        self.node_id = node_id
        self._api = api
        self._get_setting = get_setting
        self._get_active_installations = get_active_installations
        self._get_monthly_aggregate = get_monthly_aggregate
        self._get_hw_price_from_spot_prices = get_hw_price_from_spot_prices
        self._billing_manager = billing_manager

    async def calculate(
        self,
        include_estimated: bool = True,
    ) -> dict[str, Any] | None:
        """Calculate total cost for the current month by summing all price values from data API.

        This matches the approach used in the React app - fetches all price utilities
        for the current month and sums all price values (like getSumOfDataset).

        Args:
            include_estimated: If True, includes estimated HW costs when price data is missing.
                              If False, only includes metered costs from API.

        Returns:
            Dict with 'value', 'unit', 'year', 'month', 'currency', 'utilities',
            'metered_utilities', 'estimated_utilities', 'is_estimated',
            or None if no data is available.
        """
        try:
            # Get timezone from settings
            timezone_str = self._get_setting("TimeZoneIANA")
            if not timezone_str:
                timezone_str = "UTC"

            try:
                tz = zoneinfo.ZoneInfo(timezone_str)
            except Exception:
                _LOGGER.warning("Invalid timezone %s, using UTC", timezone_str)
                tz = zoneinfo.ZoneInfo("UTC")

            # Get current month boundaries in the configured timezone
            now = datetime.now(tz)
            year = now.year
            month = now.month

            from_date = datetime(year, month, 1, tzinfo=tz)
            # Get first day of next month
            if month == 12:
                to_date = datetime(year + 1, 1, 1, tzinfo=tz)
            else:
                to_date = datetime(year, month + 1, 1, tzinfo=tz)

            from_time = int(from_date.timestamp())
            to_time = int(to_date.timestamp())

            # Get all active installations to determine which utilities to fetch
            active_installations = self._get_active_installations()
            utility_codes = set()

            for installation in active_installations:
                registers = installation.get("Registers", [])
                for register in registers:
                    utility_code = register.get("UtilityCode")
                    if utility_code and utility_code in ("HW", "CW", "E", "HE"):
                        utility_codes.add(utility_code)

            if not utility_codes:
                _LOGGER.debug("No utility codes found for total cost calculation")
                return None

            # Build utilities list with price aggregates for all utilities
            utilities = [f"{util}[price]" for util in sorted(utility_codes)]

            _LOGGER.debug(
                "Fetching total monthly cost for %d-%02d: utilities=%s, from=%s to=%s",
                year,
                month,
                utilities,
                from_time,
                to_time,
            )

            # Fetch data for all price utilities at once
            data = await self._api.get_data(
                node_id=self.node_id,
                from_time=from_time,
                to_time=to_time,
                interval="d",
                grouping="apartment",
                utilities=utilities,
                include_sub_nodes=True,
            )

            if not data or not isinstance(data, list):
                _LOGGER.debug("No data returned for total cost calculation")
                return None

            # Sum all price values across all utilities and nodes
            # This matches the React app's getSumOfDataset approach
            metered_cost = 0.0
            has_data = False
            metered_utilities = set()
            estimated_utilities = set()

            # Get currency from settings (needed for logging)
            currency = self._get_setting("Currency") or "NOK"

            for node_data in data:
                results = node_data.get("Result", [])
                for result in results:
                    if result.get("Func") == "price":
                        utility_code = result.get("Utl")
                        values = result.get("Values", [])

                        # Sum all non-null price values for this utility
                        utility_has_data = False
                        for value_entry in values:
                            value = value_entry.get("Value")
                            if value is not None:
                                metered_cost += value
                                utility_has_data = True
                                has_data = True

                        if utility_has_data:
                            metered_utilities.add(utility_code)

            # Check if HW is missing and needs estimation
            estimated_cost = 0.0
            if include_estimated and "HW" in utility_codes and "HW" not in metered_utilities:
                # HW price data is missing, try to estimate it
                _LOGGER.debug("HW price data missing, attempting to estimate from spot prices")

                # Get HW consumption for current month
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
                        )
                        cw_consumption_data = await self._get_monthly_aggregate(
                            utility_code="CW",
                            year=year,
                            month=month,
                            aggregate_type="con",
                        )

                        cw_price = cw_price_data.get("value") if cw_price_data else None
                        cw_consumption = cw_consumption_data.get("value") if cw_consumption_data else None

                        # Estimate HW price from spot prices
                        hw_estimated_data = await self._get_hw_price_from_spot_prices(
                            consumption=hw_consumption,
                            year=year,
                            month=month,
                            cold_water_price=cw_price,
                            cold_water_consumption=cw_consumption,
                        )

                        if hw_estimated_data:
                            estimated_cost = hw_estimated_data.get("value", 0.0)
                            estimated_utilities.add("HW")
                            _LOGGER.debug(
                                "Estimated HW cost for %d-%02d: %.2f %s",
                                year,
                                month,
                                estimated_cost,
                                currency,
                            )

            # Total cost is metered + estimated
            all_utilities = metered_utilities | estimated_utilities

            if not has_data and estimated_cost == 0:
                _LOGGER.debug("No price data found for total cost calculation")
                return None

            # Always ensure we output the pure (pre-VAT) value
            # Check billing results to determine if data API prices include VAT
            # If they do, remove VAT to get pure value
            # Note: VAT only applies to metered costs (from API), not estimated costs
            pure_metered_cost = metered_cost  # Start with what we got from API
            total_vat = 0.0
            vat_rate = None
            prices_include_vat = False

            try:
                # Fetch most recent billing results to get VAT structure
                # Look back up to 6 months to find a billing result
                lookback_time = from_time - (180 * 24 * 60 * 60)  # 6 months
                cache_key = f"vat_{year}_{month}"
                billing_results = await self._billing_manager.get_cached_billing_results(
                    start_from=lookback_time,
                    start_to=to_time,
                    cache_key=cache_key,
                )

                if billing_results and isinstance(billing_results, list):
                    # Sort by end date descending to get most recent first
                    sorted_results = sorted(
                        billing_results,
                        key=lambda x: x.get("End", 0),
                        reverse=True
                    )

                    # Find VAT information from the most recent billing result
                    for billing_result in sorted_results:
                        parts = billing_result.get("Parts", [])
                        if not parts:
                            continue

                        # Calculate total VAT from all items (use item-level TotalVat for accuracy)
                        billing_vat = 0.0
                        billing_total_without_vat = 0.0

                        for part in parts:
                            items = part.get("Items", [])
                            for item in items:
                                item_total = item.get("Total", 0)
                                item_vat = item.get("TotalVat", 0)

                                if isinstance(item_total, (int, float)):
                                    billing_total_without_vat += item_total
                                if isinstance(item_vat, (int, float)):
                                    billing_vat += item_vat

                        # If we found VAT data, calculate the effective VAT rate
                        if billing_total_without_vat > 0 and billing_vat > 0:
                            vat_rate = billing_vat / billing_total_without_vat

                            # Assume data API prices might include VAT if billing shows VAT exists
                            # Remove VAT from metered cost to get pure value
                            # Formula: price_with_vat = price_without_vat * (1 + vat_rate)
                            # So: price_without_vat = price_with_vat / (1 + vat_rate)
                            pure_metered_cost = metered_cost / (1 + vat_rate)
                            metered_vat = metered_cost - pure_metered_cost
                            total_vat = metered_vat  # VAT only on metered costs
                            prices_include_vat = True

                            _LOGGER.debug(
                                "Found VAT in billing (%.2f%%). Removing VAT from metered costs: %.2f -> %.2f (VAT removed: %.2f)",
                                vat_rate * 100,
                                metered_cost,
                                pure_metered_cost,
                                metered_vat,
                            )
                            break
                        elif billing_total_without_vat > 0:
                            # No VAT in billing, so prices are already pure (without VAT)
                            _LOGGER.debug(
                                "No VAT found in billing results. Using metered costs as-is (already pure): %.2f",
                                metered_cost,
                            )
                            pure_metered_cost = metered_cost
                            break
            except Exception as err:
                _LOGGER.debug(
                    "Failed to fetch VAT information from billing results, using prices as-is: %s",
                    err,
                )
                # Continue with prices as-is (assume they're already pure)
                pure_metered_cost = metered_cost

            # Calculate total pure cost (metered without VAT + estimated)
            pure_total_cost = pure_metered_cost + estimated_cost
            total_with_vat = metered_cost + estimated_cost if prices_include_vat else pure_total_cost

            if prices_include_vat and vat_rate:
                _LOGGER.debug(
                    "Total monthly cost for %d-%02d: %.2f %s (pure, without VAT), "
                    "metered: %.2f %s (with VAT: %.2f %s), estimated: %.2f %s "
                    "(VAT removed: %.2f %s, rate: %.2f%%) "
                    "(metered utilities: %s, estimated utilities: %s)",
                    year,
                    month,
                    pure_total_cost,
                    currency,
                    pure_metered_cost,
                    currency,
                    metered_cost,
                    currency,
                    estimated_cost,
                    currency,
                    total_vat,
                    currency,
                    vat_rate * 100,
                    sorted(metered_utilities),
                    sorted(estimated_utilities),
                )
            else:
                _LOGGER.debug(
                    "Total monthly cost for %d-%02d: %.2f %s (pure, without VAT) "
                    "(metered: %.2f %s, estimated: %.2f %s) "
                    "(metered utilities: %s, estimated utilities: %s)",
                    year,
                    month,
                    pure_total_cost,
                    currency,
                    pure_metered_cost,
                    currency,
                    estimated_cost,
                    currency,
                    sorted(metered_utilities),
                    sorted(estimated_utilities),
                )

            result = {
                "value": round(pure_total_cost, 2),  # Total pure value (metered without VAT + estimated)
                "unit": currency,
                "year": year,
                "month": month,
                "currency": currency,
                "utilities": sorted(all_utilities),
                "metered_utilities": sorted(metered_utilities),
                "estimated_utilities": sorted(estimated_utilities),
                "metered_cost": round(pure_metered_cost, 2),  # Metered cost without VAT
                "metered_cost_with_vat": round(metered_cost, 2) if prices_include_vat else round(pure_metered_cost, 2),
                "estimated_cost": round(estimated_cost, 2),
                "is_estimated": len(estimated_utilities) > 0,
                "cost_without_vat": round(pure_total_cost, 2),  # Same as value (always pure)
            }

            # Include VAT information if VAT was found and removed
            if prices_include_vat and total_vat > 0:
                result["cost_with_vat"] = round(total_with_vat, 2)  # Total with VAT (metered with VAT + estimated)
                result["vat_amount"] = round(total_vat, 2)
                if vat_rate:
                    result["vat_rate_percent"] = round(vat_rate * 100, 2)
                result["prices_included_vat"] = True

            return result
        except Exception as err:
            _LOGGER.warning(
                "Failed to fetch current month total cost: %s",
                err,
            )
            return None
