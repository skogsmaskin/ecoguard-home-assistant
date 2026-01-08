"""Billing data manager for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime, timedelta, date as date_class
from typing import Any, Callable, Coroutine
import logging
import time
import asyncio
import zoneinfo
import requests

from homeassistant.core import HomeAssistant, CoreState

from .api import EcoGuardAPI
from .helpers import get_timezone, get_month_timestamps
from .nord_pool import NORD_POOL_AVAILABLE

_LOGGER = logging.getLogger(__name__)


class BillingManager:
    """Manages billing data fetching, caching, and extraction."""

    def __init__(
        self,
        api: EcoGuardAPI,
        node_id: int,
        hass: HomeAssistant,
        billing_cache: dict[str, tuple[list[dict[str, Any]], float]],
        pending_requests: dict[str, asyncio.Task],
        pending_requests_lock: asyncio.Lock,
        get_setting: Callable[[str], str | None],
        billing_cache_ttl: float = 86400.0,
        get_monthly_aggregate: Callable[[str, int, int, str, str], Coroutine[Any, Any, dict[str, Any] | None]] | None = None,
        get_hw_price_from_spot_prices: Callable[[float, int, int, float | None, float | None], Coroutine[Any, Any, dict[str, Any] | None]] | None = None,
        nord_pool_area: str | None = None,
    ) -> None:
        """Initialize the billing manager.

        Args:
            api: EcoGuard API instance
            node_id: Node ID
            hass: Home Assistant instance
            billing_cache: Shared billing results cache dict
            pending_requests: Shared pending requests dict for deduplication
            pending_requests_lock: Lock for pending requests
            get_setting: Function to get setting value
            billing_cache_ttl: Cache TTL in seconds (default: 24 hours)
            get_monthly_aggregate: Optional callback to get monthly aggregates
            get_hw_price_from_spot_prices: Optional callback to get HW price from spot prices
            nord_pool_area: Optional Nord Pool area code for calibration
        """
        self.api = api
        self.node_id = node_id
        self.hass = hass
        self._billing_cache = billing_cache
        self._pending_requests = pending_requests
        self._pending_requests_lock = pending_requests_lock
        self._get_setting = get_setting
        self._billing_cache_ttl = billing_cache_ttl
        self._get_monthly_aggregate = get_monthly_aggregate
        self._get_hw_price_from_spot_prices = get_hw_price_from_spot_prices
        self.nord_pool_area = nord_pool_area

    async def get_cached_billing_results(
        self,
        start_from: int | None = None,
        start_to: int | None = None,
        cache_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get billing results with caching and request deduplication.

        Billing data is historical and doesn't change, so we cache it for 24 hours
        to avoid unnecessary API calls and improve reliability.

        This method also deduplicates simultaneous requests for the same cache key
        to prevent multiple API calls when multiple sensors request the same data.

        Args:
            start_from: Start timestamp (optional)
            start_to: End timestamp (optional)
            cache_key: Optional cache key (if not provided, will be generated)

        Returns:
            List of billing results
        """
        # Generate cache key if not provided
        if cache_key is None:
            cache_key = f"billing_{self.node_id}_{start_from}_{start_to}"

        # Check cache first
        if cache_key in self._billing_cache:
            cached_data, cache_timestamp = self._billing_cache[cache_key]
            age = time.time() - cache_timestamp

            if age < self._billing_cache_ttl:
                _LOGGER.debug(
                    "Cached billing results for key %s (age: %.1f seconds)",
                    cache_key,
                    age,
                )
                return cached_data
            else:
                _LOGGER.debug(
                    "Billing cache expired for key %s (age: %.1f seconds, TTL: %.1f seconds)",
                    cache_key,
                    age,
                    self._billing_cache_ttl,
                )
                # Remove expired cache entry
                del self._billing_cache[cache_key]

        # Check if there's already a pending request for this cache key
        pending_task = None
        async with self._pending_requests_lock:
            if cache_key in self._pending_requests:
                pending_task = self._pending_requests[cache_key]
                if not pending_task.done():
                    _LOGGER.debug(
                        "Waiting for pending billing results request for key %s",
                        cache_key,
                    )
                else:
                    # Task completed, remove it
                    del self._pending_requests[cache_key]
                    pending_task = None

        # Await outside the lock to avoid blocking
        if pending_task is not None:
            try:
                data = await pending_task
                return data
            except Exception as err:
                _LOGGER.debug(
                    "Pending billing request failed for key %s: %s",
                    cache_key,
                    err,
                )
                # Remove failed task and continue to fetch
                async with self._pending_requests_lock:
                    if cache_key in self._pending_requests:
                        del self._pending_requests[cache_key]

        # Defer API calls during HA startup to avoid blocking initialization
        if self.hass.state == CoreState.starting:
            _LOGGER.debug(
                "Deferring billing results API call for key %s (HA is starting, using cached data if available)",
                cache_key
            )
            # Return expired cached data if available, or empty list
            if cache_key in self._billing_cache:
                cached_data, _ = self._billing_cache[cache_key]
                _LOGGER.debug("Using expired cached billing data during startup")
                return cached_data
            return []

        # Create async task for fetching
        async def _fetch_billing_results() -> list[dict[str, Any]]:
            try:
                _LOGGER.debug("Fetching billing results from API for key %s", cache_key)
                billing_results = await self.api.get_billing_results(
                    node_id=self.node_id,
                    start_from=start_from,
                    start_to=start_to,
                )

                # Cache the results
                if billing_results:
                    self._billing_cache[cache_key] = (billing_results, time.time())
                    _LOGGER.debug(
                        "Cached billing results for key %s (%d results)",
                        cache_key,
                        len(billing_results),
                    )

                return billing_results if billing_results else []
            except Exception as err:
                _LOGGER.warning(
                    "Failed to fetch billing results for key %s: %s. Using cached data if available.",
                    cache_key,
                    err,
                )
                # Return cached data even if expired, as fallback
                if cache_key in self._billing_cache:
                    cached_data, _ = self._billing_cache[cache_key]
                    _LOGGER.debug("Using expired cached billing data as fallback")
                    return cached_data
                return []
            finally:
                # Clean up pending request
                async with self._pending_requests_lock:
                    if cache_key in self._pending_requests:
                        del self._pending_requests[cache_key]

        # Create and track the task
        async with self._pending_requests_lock:
            task = asyncio.create_task(_fetch_billing_results())
            self._pending_requests[cache_key] = task

        try:
            return await task
        except Exception as err:
            # Clean up on error
            async with self._pending_requests_lock:
                if cache_key in self._pending_requests:
                    del self._pending_requests[cache_key]
            raise

    async def get_rate_from_billing(
        self,
        utility_code: str,
        year: int,
        month: int,
    ) -> float | None:
        """Get the rate (price per m3) from billing results for a utility.

        Since billing is lagging 2-3 months behind, looks back at least 3 months
        to find the most recent billing period with rate information.

        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            year: Year (e.g., 2025)
            month: Month (1-12)

        Returns:
            Rate per m3 from the most recent billing period found, or None if not found.
        """
        try:
            # Get timezone from settings
            timezone_str = self._get_setting("TimeZoneIANA")
            if not timezone_str:
                timezone_str = "UTC"

            tz = get_timezone(timezone_str)

            # Calculate month boundaries in the configured timezone
            from_time, to_time = get_month_timestamps(year, month, tz)

            # Fetch billing results - look back at least 4 months (120 days) to account for
            # billing lag of 2-3 months. We want to find the most recent billing period available.
            lookback_days = 120  # 4 months to be safe
            lookback_time = from_time - (lookback_days * 24 * 60 * 60)

            _LOGGER.debug(
                "Fetching billing results for rate lookup: from %s (lookback %d days) to %s",
                lookback_time,
                lookback_days,
                to_time,
            )

            billing_results = await self.api.get_billing_results(
                node_id=self.node_id,
                start_from=lookback_time,
                start_to=to_time,
            )

            if not billing_results or not isinstance(billing_results, list):
                _LOGGER.debug("No billing results found for rate lookup")
                return None

            # Find the most recent billing result that has rate information for this utility
            # Sort by end time descending to get most recent billing period first
            sorted_results = sorted(
                billing_results,
                key=lambda x: x.get("End", 0),
                reverse=True
            )

            for billing_result in sorted_results:
                billing_start = billing_result.get("Start")
                billing_end = billing_result.get("End")

                if not billing_start or not billing_end:
                    continue

                parts = billing_result.get("Parts", [])
                for part in parts:
                    part_code = part.get("Code")
                    if part_code == utility_code:
                        # Look for variable charge items (Type C1 typically)
                        items = part.get("Items", [])
                        for item in items:
                            price_component = item.get("PriceComponent", {})
                            component_type = price_component.get("Type", "")
                            rate = item.get("Rate")
                            rate_unit = item.get("RateUnit", "")

                            # Look for variable charges (C1 type) with m3 unit
                            if component_type in ("C1", "C2") and rate_unit == "m3" and rate is not None:
                                # Convert billing period timestamps to readable dates for logging
                                billing_start_date = datetime.fromtimestamp(billing_start, tz=tz).strftime("%Y-%m-%d")
                                billing_end_date = datetime.fromtimestamp(billing_end, tz=tz).strftime("%Y-%m-%d")

                                _LOGGER.debug(
                                    "Found rate for %s: %.2f %s (from billing period %s to %s)",
                                    utility_code,
                                    rate,
                                    rate_unit,
                                    billing_start_date,
                                    billing_end_date,
                                )
                                return float(rate)

            _LOGGER.debug("No rate found for %s in billing results", utility_code)
            return None
        except Exception as err:
            _LOGGER.warning(
                "Failed to get rate from billing for %s %d-%02d: %s",
                utility_code,
                year,
                month,
                err,
            )
            return None

    async def get_monthly_other_items_cost(
        self,
        year: int,
        month: int,
    ) -> dict[str, Any] | None:
        """Get monthly cost for other items (general fees) from billing results.

        Looks for parts with Code=null and Name="Øvrig" (or similar) in billing results.
        Uses the most recent billing data as the source of truth.

        Args:
            year: Year (e.g., 2025)
            month: Month (1-12)

        Returns:
            Dict with 'value', 'unit', 'year', 'month', 'utility_code', 'aggregate_type',
            or None if no data is available.
        """
        try:
            # Get timezone from settings
            timezone_str = self._get_setting("TimeZoneIANA")
            if not timezone_str:
                timezone_str = "UTC"

            tz = get_timezone(timezone_str)

            # Calculate month boundaries in the configured timezone
            from_time, to_time = get_month_timestamps(year, month, tz)

            # Look back up to 6 months to find billing results
            lookback_time = from_time - (180 * 24 * 60 * 60)  # 6 months
            cache_key = f"monthly_other_items_{year}_{month}"
            billing_results = await self.get_cached_billing_results(
                start_from=lookback_time,
                start_to=to_time,
                cache_key=cache_key,
            )

            if not billing_results or not isinstance(billing_results, list):
                _LOGGER.debug("No billing results found for other items cost")
                return None

            # Sort by end date descending to get most recent first
            sorted_results = sorted(
                billing_results,
                key=lambda x: x.get("End", 0),
                reverse=True
            )

            # Find the most recent billing result with "other items" (Øvrig)
            for billing_result in sorted_results:
                parts = billing_result.get("Parts", [])
                if not parts:
                    continue

                # Look for part with Code=null and Name="Øvrig" (or similar variations)
                for part in parts:
                    part_code = part.get("Code")
                    part_name = part.get("Name", "")

                    # Check if this is the "other items" part
                    # Code should be null/None, and Name should be "Øvrig" or similar
                    if part_code is None or part_code == "":
                        # Check if name matches common variations
                        part_name_lower = part_name.lower()
                        if (
                            "øvrig" in part_name_lower
                            or "other" in part_name_lower
                            or "andre" in part_name_lower
                            or "misc" in part_name_lower
                            or "generelle" in part_name_lower
                        ):
                            # Found the other items part, sum all items
                            items = part.get("Items", [])
                            total_cost = 0.0
                            item_details = []

                            for item in items:
                                item_total = item.get("Total", 0)
                                if isinstance(item_total, (int, float)) and item_total > 0:
                                    total_cost += item_total

                                    # Collect item details for logging
                                    item_name = item.get("PriceComponent", {}).get("Name", "Unknown")
                                    item_rate = item.get("Rate", 0)
                                    item_details.append({
                                        "name": item_name,
                                        "rate": item_rate,
                                        "total": item_total,
                                    })

                            if total_cost > 0:
                                # Apply rounding from the part to match the actual bill
                                rounding = part.get("Rounding", 0.0)
                                if isinstance(rounding, (int, float)):
                                    total_cost += rounding

                                currency = self._get_setting("Currency") or "NOK"

                                billing_start = billing_result.get("Start")
                                billing_end = billing_result.get("End")

                                _LOGGER.debug(
                                    "Found other items cost: %.2f %s for %d-%02d (from billing period %s to %s, %d items, rounding: %.2f)",
                                    total_cost,
                                    currency,
                                    year,
                                    month,
                                    datetime.fromtimestamp(billing_start, tz=tz).strftime("%Y-%m-%d") if billing_start else "unknown",
                                    datetime.fromtimestamp(billing_end, tz=tz).strftime("%Y-%m-%d") if billing_end else "unknown",
                                    len(item_details),
                                    rounding,
                                )

                                return {
                                    "value": total_cost,
                                    "unit": currency,
                                    "year": year,
                                    "month": month,
                                    "utility_code": "OTHER",
                                    "aggregate_type": "price",
                                    "cost_type": "actual",
                                    "is_estimated": False,
                                    "billing_period_start": billing_start,
                                    "billing_period_end": billing_end,
                                    "item_count": len(item_details),
                                    "items": item_details,
                                    "rounding": rounding,
                                }

            _LOGGER.debug("No other items found in billing results for %d-%02d", year, month)
            return None

        except Exception as err:
            _LOGGER.warning(
                "Failed to get other items cost for %d-%02d: %s",
                year,
                month,
                err,
            )
            return None

    async def get_monthly_price_from_billing(
        self,
        utility_code: str,
        year: int,
        month: int,
    ) -> dict[str, Any] | None:
        """Get monthly price from billing results or calculate from consumption × rate.

        For HW: Calculates price from consumption × rate (since HW price is often 0 in daily data)
        For CW: Tries to get price from billing results, falls back to calculation if needed

        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            year: Year (e.g., 2025)
            month: Month (1-12)

        Returns:
            Dict with 'value', 'unit', 'year', 'month', 'utility_code', 'aggregate_type',
            or None if no data is available.
        """
        try:
            # Get timezone from settings
            timezone_str = self._get_setting("TimeZoneIANA")
            if not timezone_str:
                timezone_str = "UTC"

            tz = get_timezone(timezone_str)

            # Calculate month boundaries in the configured timezone
            from_time, to_time = get_month_timestamps(year, month, tz)

            _LOGGER.debug(
                "Fetching monthly price from billing for %s %d-%02d: from=%s to=%s",
                utility_code,
                year,
                month,
                from_time,
                to_time,
            )

            # For HW, try spot prices first (for current month), then fall back to billing rates
            # For CW, try billing results first, then fall back to calculation
            if utility_code == "HW":
                if not self._get_monthly_aggregate:
                    _LOGGER.warning("get_monthly_aggregate callback not provided, cannot calculate HW price")
                    return None

                # Get monthly consumption
                consumption_data = await self._get_monthly_aggregate(
                    utility_code=utility_code,
                    year=year,
                    month=month,
                    aggregate_type="con",
                    cost_type="actual",
                )

                if not consumption_data:
                    _LOGGER.debug("No consumption data for %s %d-%02d", utility_code, year, month)
                    return None

                consumption = consumption_data.get("value")
                if consumption is None:
                    return None

                # Try to calculate using spot prices for current month
                # This gives more accurate pricing for recent consumption
                now = datetime.now(tz)
                is_current_month = (year == now.year and month == now.month)

                if is_current_month and self._get_hw_price_from_spot_prices:
                    _LOGGER.debug(
                        "Current month detected for %s %d-%02d, attempting to use spot prices",
                        utility_code,
                        year,
                        month,
                    )

                    # Get current month's CW price and consumption if available (more accurate than billing rate)
                    cw_price_data = await self._get_monthly_aggregate(
                        utility_code="CW",
                        year=year,
                        month=month,
                        aggregate_type="price",
                        cost_type="actual",
                    )
                    cold_water_price = cw_price_data.get("value") if cw_price_data else None

                    # Also get CW consumption to avoid fetching it again in the spot price function
                    cw_consumption_data = await self._get_monthly_aggregate(
                        utility_code="CW",
                        year=year,
                        month=month,
                        aggregate_type="con",
                        cost_type="actual",
                    )
                    cw_consumption = cw_consumption_data.get("value") if cw_consumption_data else None

                    spot_price_data = await self._get_hw_price_from_spot_prices(
                        consumption=consumption,
                        year=year,
                        month=month,
                        cold_water_price=cold_water_price,
                        cold_water_consumption=cw_consumption,
                    )

                    if spot_price_data:
                        _LOGGER.info(
                            "Calculated %s price for %d-%02d using spot prices: %.2f m3 = %.2f NOK (sensor: %s)",
                            utility_code,
                            year,
                            month,
                            consumption,
                            spot_price_data.get("value"),
                            spot_price_data.get("price_sensor", "unknown"),
                        )
                        return spot_price_data
                    else:
                        _LOGGER.debug(
                            "Spot price calculation failed for %s %d-%02d, falling back to billing rate",
                            utility_code,
                            year,
                            month,
                        )

                # Fall back to billing rate (for historical months or if spot prices unavailable)
                rate = await self.get_rate_from_billing(utility_code, year, month)
                if rate is None:
                    _LOGGER.debug("No rate found for %s %d-%02d", utility_code, year, month)
                    return None

                # Calculate price using billing rate
                price = consumption * rate

                _LOGGER.debug(
                    "Calculated %s price for %d-%02d using billing rate: %.2f m3 × %.2f = %.2f",
                    utility_code,
                    year,
                    month,
                    consumption,
                    rate,
                    price,
                )

                currency = self._get_setting("Currency") or "NOK"
                return {
                    "value": price,
                    "unit": currency,
                    "year": year,
                    "month": month,
                    "utility_code": utility_code,
                    "aggregate_type": "price",
                    "calculation_method": "billing_rate",
                }

            # For CW, try to get price from billing results first
            cache_key = f"cw_price_{year}_{month}"
            billing_results = await self.get_cached_billing_results(
                start_from=from_time,
                start_to=to_time,
                cache_key=cache_key,
            )

            if billing_results and isinstance(billing_results, list):
                # Find billing results that overlap with the requested month
                total_price = 0.0
                has_data = False

                for billing_result in billing_results:
                    billing_start = billing_result.get("Start")
                    billing_end = billing_result.get("End")

                    # Check if billing period overlaps with requested month
                    if billing_start and billing_end:
                        # Billing period overlaps if it starts before month ends and ends after month starts
                        if billing_start < to_time and billing_end > from_time:
                            parts = billing_result.get("Parts", [])
                            for part in parts:
                                part_code = part.get("Code")
                                if part_code == utility_code:
                                    # Sum all items in this part
                                    items = part.get("Items", [])
                                    part_rounding = part.get("Rounding", 0.0)
                                    for item in items:
                                        total = item.get("Total")
                                        if total is not None:
                                            total_price += total
                                            has_data = True

                                    # Apply rounding from the part to match the actual bill
                                    if isinstance(part_rounding, (int, float)) and has_data:
                                        total_price += part_rounding

                if has_data:
                    currency = self._get_setting("Currency") or "NOK"
                    return {
                        "value": total_price,
                        "unit": currency,
                        "year": year,
                        "month": month,
                        "utility_code": utility_code,
                        "aggregate_type": "price",
                    }

            # Fallback for CW: calculate from consumption × rate
            _LOGGER.debug("No billing price found for %s, calculating from consumption × rate", utility_code)
            if not self._get_monthly_aggregate:
                _LOGGER.warning("get_monthly_aggregate callback not provided, cannot calculate CW price")
                return None

            consumption_data = await self._get_monthly_aggregate(
                utility_code=utility_code,
                year=year,
                month=month,
                aggregate_type="con",
                cost_type="actual",
            )

            if not consumption_data:
                return None

            consumption = consumption_data.get("value")
            if consumption is None:
                return None

            # Get rate from billing results
            rate = await self.get_rate_from_billing(utility_code, year, month)
            if rate is None:
                return None

            # Calculate price
            price = consumption * rate

            _LOGGER.debug(
                "Calculated %s price for %d-%02d: %.2f m3 × %.2f = %.2f",
                utility_code,
                year,
                month,
                consumption,
                rate,
                price,
            )

            currency = self._get_setting("Currency") or "NOK"
            return {
                "value": price,
                "unit": currency,
                "year": year,
                "month": month,
                "utility_code": utility_code,
                "aggregate_type": "price",
            }
        except Exception as err:
            _LOGGER.warning(
                "Failed to fetch monthly price from billing for %s %d-%02d: %s",
                utility_code,
                year,
                month,
                err,
            )
            return None

    async def calculate_hw_calibration_ratio(
        self,
        months_back: int = 6,
    ) -> float | None:
        """Calculate calibration ratio by comparing historical billing data with spot prices.

        This method analyzes historical billing periods to find the relationship between
        actual HW prices and Nord Pool spot prices, accounting for system efficiency,
        fixed costs, and other factors.

        Formula: ratio = (HW_price_per_m3 - CW_price_per_m3) / (avg_spot_price × energy_factor)

        Args:
            months_back: Number of months to look back (default: 6)

        Returns:
            Calibration ratio (typically 0.5-2.0), or None if insufficient data
        """
        if not self.nord_pool_area:
            _LOGGER.debug("Nord Pool area not configured, cannot calculate calibration ratio")
            return None

        if not NORD_POOL_AVAILABLE:
            _LOGGER.debug("nordpool library not available, cannot calculate calibration ratio")
            return None

        try:
            # Get timezone and currency
            timezone_str = self._get_setting("TimeZoneIANA") or "UTC"
            tz = get_timezone(timezone_str)
            currency = self._get_setting("Currency") or "NOK"

            # Calculate date range
            now = datetime.now(tz)
            end_date = now.date()
            start_date = (now - timedelta(days=months_back * 30)).date()

            # Fetch billing results for the period
            start_timestamp = int(datetime.combine(start_date, datetime.min.time()).replace(tzinfo=tz).timestamp())
            end_timestamp = int(datetime.combine(end_date, datetime.max.time()).replace(tzinfo=tz).timestamp())

            _LOGGER.debug(
                "Calculating HW calibration ratio from billing data: %s to %s",
                start_date,
                end_date,
            )

            # Use cached billing data if available
            cache_key = f"calibration_{start_timestamp}_{end_timestamp}"
            billing_results = await self.get_cached_billing_results(
                start_from=start_timestamp,
                start_to=end_timestamp,
                cache_key=cache_key,
            )

            if not billing_results or not isinstance(billing_results, list):
                _LOGGER.debug("No billing results found for calibration")
                return None

            # Sort by end time descending (most recent first)
            sorted_results = sorted(
                billing_results,
                key=lambda x: x.get("End", 0),
                reverse=True,
            )

            ratios = []
            ENERGY_PER_M3 = 45.0  # Same as in _get_hw_price_from_spot_prices

            for billing_result in sorted_results:
                billing_start = billing_result.get("Start")
                billing_end = billing_result.get("End")

                if not billing_start or not billing_end:
                    continue

                # Get HW and CW rates from billing
                hw_rate = None
                cw_rate = None

                parts = billing_result.get("Parts", [])
                for part in parts:
                    part_code = part.get("Code")
                    items = part.get("Items", [])

                    for item in items:
                        price_component = item.get("PriceComponent", {})
                        component_type = price_component.get("Type", "")
                        rate = item.get("Rate")
                        rate_unit = item.get("RateUnit", "")

                        if component_type in ("C1", "C2") and rate_unit == "m3" and rate is not None:
                            if part_code == "HW":
                                hw_rate = float(rate)
                            elif part_code == "CW":
                                cw_rate = float(rate)

                if hw_rate is None or cw_rate is None:
                    continue

                # Calculate average spot price for the billing period
                # Use the middle of the period
                period_start = datetime.fromtimestamp(billing_start, tz=tz)
                period_end = datetime.fromtimestamp(billing_end, tz=tz)
                period_middle = period_start + (period_end - period_start) / 2
                period_date = period_middle.date()

                # Fetch average spot price for this period
                # We'll use the nordpool library to get prices for the period
                # Store original methods
                original_session_request = requests.Session.request
                original_get = requests.get
                original_post = requests.post

                # Patch requests for timeout
                def patched_session_request(self, method, url, **kwargs):
                    if "timeout" not in kwargs:
                        kwargs["timeout"] = 30.0
                    return original_session_request(self, method, url, **kwargs)

                def patched_get(url, **kwargs):
                    if "timeout" not in kwargs:
                        kwargs["timeout"] = 30.0
                    return original_get(url, **kwargs)

                def patched_post(url, **kwargs):
                    if "timeout" not in kwargs:
                        kwargs["timeout"] = 30.0
                    return original_post(url, **kwargs)

                requests.Session.request = patched_session_request
                requests.get = patched_get
                requests.post = patched_post

                try:
                    # Import elspot locally since it's optional
                    if not NORD_POOL_AVAILABLE:
                        return None
                    from nordpool import elspot
                    prices_spot = elspot.Prices(currency)
                    loop = asyncio.get_event_loop()

                    def fetch_historical_price():
                        try:
                            result = prices_spot.fetch(
                                areas=[self.nord_pool_area],
                                end_date=date_class(period_date.year, period_date.month, period_date.day),
                            )
                            return result
                        except Exception as e:
                            _LOGGER.debug("Error fetching historical spot price: %s", e)
                            return None

                    result = await loop.run_in_executor(None, fetch_historical_price)
                finally:
                    requests.Session.request = original_session_request
                    requests.get = original_get
                    requests.post = original_post

                if not result or not isinstance(result, dict) or "areas" not in result:
                    _LOGGER.debug("No spot price data for period %s", period_date)
                    continue

                area_data = result.get("areas", {}).get(self.nord_pool_area)
                if not area_data:
                    continue

                values = area_data.get("values", [])
                if not values:
                    continue

                # Calculate average spot price for the period (convert from MWh to kWh)
                spot_prices = []
                for price_entry in values:
                    value = price_entry.get("value")
                    if value is not None:
                        # nordpool returns prices in currency/MWh, convert to kWh
                        price_per_kwh = value / 1000.0
                        spot_prices.append(price_per_kwh)

                if not spot_prices:
                    continue

                avg_spot_price = sum(spot_prices) / len(spot_prices)

                # Calculate ratio: (HW_price - CW_price) / (spot_price × energy_factor)
                # This accounts for the actual system efficiency and fixed costs
                heating_cost_per_m3 = avg_spot_price * ENERGY_PER_M3
                actual_heating_cost_per_m3 = hw_rate - cw_rate

                if heating_cost_per_m3 > 0:
                    ratio = actual_heating_cost_per_m3 / heating_cost_per_m3
                    ratios.append(ratio)

                    _LOGGER.debug(
                        "Calibration data point: HW=%.2f, CW=%.2f, Spot=%.4f, Ratio=%.3f (period: %s to %s)",
                        hw_rate,
                        cw_rate,
                        avg_spot_price,
                        ratio,
                        period_start.strftime("%Y-%m-%d"),
                        period_end.strftime("%Y-%m-%d"),
                    )

            if not ratios:
                _LOGGER.debug("No valid calibration data points found")
                return None

            # Calculate average ratio (could also use median for robustness)
            avg_ratio = sum(ratios) / len(ratios)

            _LOGGER.info(
                "Calculated HW calibration ratio: %.3f (from %d billing periods, range: %.3f - %.3f)",
                avg_ratio,
                len(ratios),
                min(ratios),
                max(ratios),
            )

            return avg_ratio

        except Exception as err:
            _LOGGER.warning(
                "Failed to calculate HW calibration ratio: %s",
                err,
                exc_info=True,
            )
            return None
