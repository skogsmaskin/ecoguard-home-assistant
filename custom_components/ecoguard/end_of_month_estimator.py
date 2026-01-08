"""End-of-month bill estimate calculator for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Awaitable
import uuid
import zoneinfo
import logging

_LOGGER = logging.getLogger(__name__)


class EndOfMonthEstimator:
    """Calculates end-of-month bill estimates based on current month's data."""

    def __init__(
        self,
        node_id: int,
        request_deduplicator: Any,  # RequestDeduplicator
        api: Any,  # EcoGuardAPI
        get_setting: Callable[[str], str | None],
        daily_consumption_cache: dict[str, list[dict[str, Any]]],
        get_hw_price_from_spot_prices: Callable[[float, int, int, float | None, float | None], Awaitable[dict[str, Any] | None]],
        get_monthly_aggregate: Callable[[str, int, int, str, str], Awaitable[dict[str, Any] | None]],
        billing_manager: Any,  # BillingManager
    ) -> None:
        """Initialize the end-of-month estimator.

        Args:
            node_id: Node ID for API calls
            request_deduplicator: Request deduplicator for API calls
            api: EcoGuard API instance
            get_setting: Function to get settings
            daily_consumption_cache: Cache of daily consumption data
            get_hw_price_from_spot_prices: Function to get HW price from spot prices
            get_monthly_aggregate: Function to get monthly aggregate data
            billing_manager: Billing manager instance
        """
        self.node_id = node_id
        self._request_deduplicator = request_deduplicator
        self._api = api
        self._get_setting = get_setting
        self._daily_consumption_cache = daily_consumption_cache
        self._get_hw_price_from_spot_prices = get_hw_price_from_spot_prices
        self._get_monthly_aggregate = get_monthly_aggregate
        self._billing_manager = billing_manager

    async def calculate(self) -> dict[str, Any] | None:
        """Calculate end-of-month bill estimate based on current month's data.

        Estimates are calculated using mean daily consumption and price so far this month,
        projected to the end of the month.

        Returns:
            Dict with estimated values for:
            - hw_consumption_estimate: Estimated HW consumption by end of month
            - hw_price_estimate: Estimated HW price by end of month
            - cw_consumption_estimate: Estimated CW consumption by end of month
            - cw_price_estimate: Estimated CW price by end of month
            - other_items_cost: Other items cost from last bill
            - total_bill_estimate: Total estimated bill (CW + HW + other fees)
            - currency: Currency code
            - year: Current year
            - month: Current month
            - days_elapsed: Number of days with data so far
            - days_remaining: Number of days remaining in month
            - total_days_in_month: Total days in current month
        """
        call_id = str(uuid.uuid4())[:8]
        _LOGGER.debug("get_end_of_month_estimate[%s]: Method called", call_id)
        try:
            # Verify dependencies are available
            if not self._request_deduplicator:
                _LOGGER.error("get_end_of_month_estimate[%s]: Request deduplicator not available", call_id)
                return None
            if not self._api:
                _LOGGER.error("get_end_of_month_estimate[%s]: API not available", call_id)
                return None
            if not self._billing_manager:
                _LOGGER.error("get_end_of_month_estimate[%s]: Billing manager not available", call_id)
                return None
            now = datetime.now()
            current_year = now.year
            current_month = now.month
            _LOGGER.debug("get_end_of_month_estimate[%s]: Starting for year=%d, month=%d", call_id, current_year, current_month)

            # Get timezone
            timezone_str = self._get_setting("TimeZoneIANA") or "UTC"
            try:
                tz = zoneinfo.ZoneInfo(timezone_str)
            except Exception:
                tz = zoneinfo.ZoneInfo("UTC")

            now_tz = datetime.now(tz)
            current_year = now_tz.year
            current_month = now_tz.month

            # Calculate month boundaries
            from_date = datetime(current_year, current_month, 1, tzinfo=tz)
            if current_month == 12:
                to_date = datetime(current_year + 1, 1, 1, tzinfo=tz)
            else:
                to_date = datetime(current_year, current_month + 1, 1, tzinfo=tz)

            total_days_in_month = (to_date - from_date).days
            days_elapsed = (now_tz.date() - from_date.date()).days + 1  # +1 to include today
            days_remaining = total_days_in_month - days_elapsed

            if days_elapsed <= 0:
                _LOGGER.debug("No days elapsed yet in current month, cannot estimate")
                return None

            from_time = int(from_date.timestamp())
            to_time = int(to_date.timestamp())

            currency = self._get_setting("Currency") or "NOK"

            # Fetch daily data for consumption and price for both HW and CW
            # Use cached data first to avoid unnecessary API calls
            estimates = {}

            for utility_code in ["HW", "CW"]:
                for data_type in ["con", "price"]:
                    try:
                        # Try to get data from cache first
                        daily_values = []
                        latest_data_time = None

                        if data_type == "con":
                            # Check daily consumption cache
                            cache_key = f"{utility_code}_all"
                            cached_values = self._daily_consumption_cache.get(cache_key, [])
                            if cached_values:
                                # Filter for current month
                                for v in cached_values:
                                    if from_time <= v.get("time", 0) < to_time and v.get("value") is not None and v.get("value") > 0:
                                        daily_values.append(v.get("value"))
                                        time_stamp = v.get("time")
                                        if time_stamp and (latest_data_time is None or time_stamp > latest_data_time):
                                            latest_data_time = time_stamp
                        elif data_type == "price":
                            # Check daily price cache
                            # Price cache uses different keys per measuring point, but we can check aggregate
                            # For now, we'll need to make API call for price data
                            # TODO: Add price cache checking similar to consumption
                            pass

                        # If we don't have enough cached data, make API call with deduplication
                        if not daily_values:
                            # Create cache key for deduplication
                            api_cache_key = f"data_{self.node_id}_{from_time}_{to_time}_{utility_code}_{data_type}"

                            async def fetch_data() -> list[dict[str, Any]] | None:
                                """Fetch data from API."""
                                return await self._api.get_data(
                                    node_id=self.node_id,
                                    from_time=from_time,
                                    to_time=to_time,
                                    interval="d",
                                    grouping="apartment",
                                    utilities=[f"{utility_code}[{data_type}]"],
                                    include_sub_nodes=True,
                                )

                            # Use request deduplicator to handle caching and deduplication
                            data = await self._request_deduplicator.get_or_fetch(
                                cache_key=api_cache_key,
                                fetch_func=fetch_data,
                                use_cache=True,
                            )

                            if not data or not isinstance(data, list):
                                continue

                            # Extract daily values with timestamps
                            for node_data in data:
                                results = node_data.get("Result", [])
                                for result in results:
                                    if result.get("Utl") == utility_code and result.get("Func") == data_type:
                                        values = result.get("Values", [])
                                        for value_entry in values:
                                            value = value_entry.get("Value")
                                            time_stamp = value_entry.get("Time")
                                            if value is not None and value > 0:
                                                daily_values.append(value)
                                                # Track the latest timestamp with data
                                                if time_stamp and (latest_data_time is None or time_stamp > latest_data_time):
                                                    latest_data_time = time_stamp

                        if daily_values:
                            # Calculate mean daily value based on actual days with data
                            # This is the key: we only use days where we have actual data
                            days_with_data = len(daily_values)
                            mean_daily = sum(daily_values) / days_with_data

                            # Project to end of month using the mean daily rate
                            # Note: This assumes the mean daily rate continues for the rest of the month
                            total_so_far = sum(daily_values)
                            estimated_total = mean_daily * total_days_in_month

                            key = f"{utility_code.lower()}_{data_type}_estimate"
                            estimates[key] = {
                                "mean_daily": mean_daily,
                                "total_so_far": total_so_far,
                                "estimated_total": estimated_total,
                                "days_with_data": days_with_data,
                                "latest_data_time": latest_data_time,
                            }

                            _LOGGER.debug(
                                "%s %s: mean daily=%.2f, so far=%.2f, estimated=%.2f (days with data: %d)",
                                utility_code,
                                data_type,
                                mean_daily,
                                total_so_far,
                                estimated_total,
                                len(daily_values),
                            )
                    except Exception as err:
                        _LOGGER.debug(
                            "Failed to fetch %s %s data for estimate: %s",
                            utility_code,
                            data_type,
                            err,
                        )

            # If we don't have HW price data, try to estimate it
            hw_price_has_data = False
            if "hw_price_estimate" in estimates:
                hw_price_est_data = estimates["hw_price_estimate"]
                if isinstance(hw_price_est_data, dict):
                    hw_price_has_data = hw_price_est_data.get("days_with_data", 0) > 0

            if not hw_price_has_data:
                # Try to estimate HW price using spot prices
                if "hw_con_estimate" in estimates:
                    hw_consumption_estimate = estimates["hw_con_estimate"]["estimated_total"]

                    # Get CW price and consumption estimates (use mean daily if available, otherwise calculate from so_far)
                    cw_price_estimate_data = estimates.get("cw_price_estimate", {})
                    cw_con_estimate_data = estimates.get("cw_con_estimate", {})

                    # Use mean daily from estimates if available
                    # Note: We only use actual data days, not calendar days elapsed
                    if cw_price_estimate_data.get("mean_daily", 0) > 0:
                        estimated_cw_price = cw_price_estimate_data["mean_daily"] * total_days_in_month
                    else:
                        # Fallback: if no mean daily, we can't reliably estimate
                        _LOGGER.debug("No CW price mean daily available for HW price estimation")
                        estimated_cw_price = 0

                    if cw_con_estimate_data.get("mean_daily", 0) > 0:
                        estimated_cw_consumption = cw_con_estimate_data["mean_daily"] * total_days_in_month
                    else:
                        # Fallback: if no mean daily, we can't reliably estimate
                        _LOGGER.debug("No CW consumption mean daily available for HW price estimation")
                        estimated_cw_consumption = 0

                    try:
                        spot_price_data = await self._get_hw_price_from_spot_prices(
                            consumption=hw_consumption_estimate,
                            year=current_year,
                            month=current_month,
                            cold_water_price=estimated_cw_price if estimated_cw_price > 0 else None,
                            cold_water_consumption=estimated_cw_consumption if estimated_cw_consumption > 0 else None,
                        )
                    except Exception as err:
                        _LOGGER.warning(
                            "get_end_of_month_estimate[%s]: Failed to get HW price from spot prices: %s",
                            call_id,
                            err,
                            exc_info=True,
                        )
                        spot_price_data = None

                    if spot_price_data:
                        hw_price_estimate_value = spot_price_data.get("value", 0)
                        estimates["hw_price_estimate"] = {
                            "mean_daily": hw_price_estimate_value / total_days_in_month,
                            "total_so_far": 0,  # Not available from API
                            "estimated_total": hw_price_estimate_value,
                            "days_with_data": 0,  # Estimated, not from actual data
                            "is_estimated": True,
                        }
                        _LOGGER.debug(
                            "Estimated HW price using spot prices: %.2f",
                            hw_price_estimate_value,
                        )

            # If we don't have CW price data, try to estimate it from monthly aggregate
            cw_price_has_data = False
            if "cw_price_estimate" in estimates:
                cw_price_est_data = estimates["cw_price_estimate"]
                if isinstance(cw_price_est_data, dict):
                    cw_price_has_data = cw_price_est_data.get("days_with_data", 0) > 0

            if not cw_price_has_data:
                # Try to get CW price estimate from monthly aggregate
                if "cw_con_estimate" in estimates:
                    cw_consumption_estimate = estimates["cw_con_estimate"]["estimated_total"]

                    # Try to get estimated price from monthly aggregate
                    try:
                        cw_price_data = await self._get_monthly_aggregate(
                            utility_code="CW",
                            year=current_year,
                            month=current_month,
                            aggregate_type="price",
                            cost_type="estimated",
                        )
                    except Exception as err:
                        _LOGGER.warning(
                            "get_end_of_month_estimate[%s]: Failed to get CW price from monthly aggregate: %s",
                            call_id,
                            err,
                            exc_info=True,
                        )
                        cw_price_data = None

                    if cw_price_data and cw_price_data.get("value") is not None:
                        cw_price_estimate_value = cw_price_data.get("value", 0)
                        estimates["cw_price_estimate"] = {
                            "mean_daily": cw_price_estimate_value / total_days_in_month,
                            "total_so_far": 0,  # Not available from daily data
                            "estimated_total": cw_price_estimate_value,
                            "days_with_data": 0,  # Estimated, not from actual daily data
                            "is_estimated": True,
                        }
                        _LOGGER.debug(
                            "Estimated CW price from monthly aggregate: %.2f",
                            cw_price_estimate_value,
                        )

            _LOGGER.debug("get_end_of_month_estimate[%s]: Finished calculating estimates, now getting other items cost", call_id)

            # Get other items cost from last bill
            _LOGGER.debug("get_end_of_month_estimate[%s]: Getting other items cost for year=%d, month=%d", call_id, current_year, current_month)
            other_items_cost = 0
            try:
                other_items_data = await self._billing_manager.get_monthly_other_items_cost(
                    year=current_year,
                    month=current_month,
                )
                if other_items_data:
                    other_items_cost = other_items_data.get("value", 0)
                else:
                    other_items_cost = 0
                _LOGGER.debug("get_end_of_month_estimate[%s]: Got other items cost: %.2f", call_id, other_items_cost)
            except Exception as err:
                _LOGGER.error("get_end_of_month_estimate[%s]: EXCEPTION in get_monthly_other_items_cost: %s", call_id, err, exc_info=True)
                other_items_cost = 0

            _LOGGER.debug("get_end_of_month_estimate[%s]: About to calculate total bill estimate", call_id)

            # Safely extract values from estimates dictionary
            def safe_get(key: str, attr: str, default: Any = 0) -> Any:
                """Safely get nested dictionary value."""
                try:
                    data = estimates.get(key, {})
                    if isinstance(data, dict):
                        return data.get(attr, default)
                    return default
                except (AttributeError, TypeError):
                    return default

            # Calculate total estimated bill
            try:
                # Use safe_get helper to extract values, which handles missing keys properly
                hw_price_est = safe_get("hw_price_estimate", "estimated_total", 0)
                cw_price_est = safe_get("cw_price_estimate", "estimated_total", 0)
                total_bill_estimate = hw_price_est + cw_price_est + other_items_cost

                _LOGGER.debug(
                    "get_end_of_month_estimate[%s]: Calculating total bill estimate: HW=%.2f, CW=%.2f, Other=%.2f, Total=%.2f",
                    call_id,
                    hw_price_est,
                    cw_price_est,
                    other_items_cost,
                    total_bill_estimate,
                )
                _LOGGER.debug(
                    "get_end_of_month_estimate[%s]: Estimates dict keys: %s",
                    call_id,
                    list(estimates.keys()),
                )
            except Exception as err:
                _LOGGER.warning("Failed to calculate total bill estimate: %s", err, exc_info=True)
                # Use safe defaults
                hw_price_est = 0
                cw_price_est = 0
                total_bill_estimate = other_items_cost

            # Calculate maximum days with data across all metrics to show data freshness
            max_days_with_data = 0
            latest_data_timestamp = None
            for key in ["hw_con_estimate", "hw_price_estimate", "cw_con_estimate", "cw_price_estimate"]:
                estimate_data = estimates.get(key, {})
                days_with_data = estimate_data.get("days_with_data", 0)
                if days_with_data > max_days_with_data:
                    max_days_with_data = days_with_data
                data_time = estimate_data.get("latest_data_time")
                if data_time and (latest_data_timestamp is None or data_time > latest_data_timestamp):
                    latest_data_timestamp = data_time

            result = {
                "hw_consumption_estimate": safe_get("hw_con_estimate", "estimated_total", 0),
                "hw_price_estimate": hw_price_est,
                "cw_consumption_estimate": safe_get("cw_con_estimate", "estimated_total", 0),
                "cw_price_estimate": cw_price_est,
                "other_items_cost": other_items_cost,
                "total_bill_estimate": total_bill_estimate,
                "currency": currency,
                "year": current_year,
                "month": current_month,
                "days_elapsed_calendar": days_elapsed,  # Calendar days since month start
                "days_with_data": max_days_with_data,  # Actual days with data
                "days_remaining": days_remaining,
                "total_days_in_month": total_days_in_month,
                "latest_data_timestamp": latest_data_timestamp,  # Latest timestamp with data
                "hw_mean_daily_consumption": safe_get("hw_con_estimate", "mean_daily", 0),
                "hw_mean_daily_price": safe_get("hw_price_estimate", "mean_daily", 0),
                "cw_mean_daily_consumption": safe_get("cw_con_estimate", "mean_daily", 0),
                "cw_mean_daily_price": safe_get("cw_price_estimate", "mean_daily", 0),
                "hw_consumption_so_far": safe_get("hw_con_estimate", "total_so_far", 0),
                "hw_price_so_far": safe_get("hw_price_estimate", "total_so_far", 0),
                "cw_consumption_so_far": safe_get("cw_con_estimate", "total_so_far", 0),
                "cw_price_so_far": safe_get("cw_price_estimate", "total_so_far", 0),
                "hw_consumption_days_with_data": safe_get("hw_con_estimate", "days_with_data", 0),
                "hw_price_days_with_data": safe_get("hw_price_estimate", "days_with_data", 0),
                "cw_consumption_days_with_data": safe_get("cw_con_estimate", "days_with_data", 0),
                "cw_price_days_with_data": safe_get("cw_price_estimate", "days_with_data", 0),
                "hw_price_is_estimated": safe_get("hw_price_estimate", "is_estimated", False),
            }

            _LOGGER.info(
                "get_end_of_month_estimate[%s]: End-of-month estimate for %d-%02d: Total=%.2f %s (HW: %.2f, CW: %.2f, Other: %.2f) "
                "(%d days with data out of %d calendar days elapsed, %d days in month)",
                call_id,
                current_year,
                current_month,
                total_bill_estimate,
                currency,
                hw_price_est,
                cw_price_est,
                other_items_cost,
                max_days_with_data,
                days_elapsed,
                total_days_in_month,
            )

            _LOGGER.debug("get_end_of_month_estimate[%s]: Returning result with %d keys", call_id, len(result))
            return result
        except Exception as err:
            _LOGGER.warning(
                "get_end_of_month_estimate[%s]: Failed to calculate end-of-month estimate: %s",
                call_id,
                err,
                exc_info=True,
            )
            return None
