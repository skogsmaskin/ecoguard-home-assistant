"""Nord Pool spot price integration for Home Assistant EcoGuard."""

from __future__ import annotations

from datetime import datetime, timedelta, date as date_class
from typing import Any
import asyncio
import logging
import requests

# Try to import nordpool library (optional dependency)
try:
    from nordpool import elspot
    NORD_POOL_AVAILABLE = True
except ImportError:
    NORD_POOL_AVAILABLE = False
    elspot = None

_LOGGER = logging.getLogger(__name__)


class NordPoolPriceFetcher:
    """Fetches spot prices from Nord Pool API."""

    def __init__(self, price_cache: dict[str, float] | None = None) -> None:
        """Initialize the price fetcher.

        Args:
            price_cache: Optional shared cache dict for prices
        """
        self._price_cache: dict[str, float] = price_cache if price_cache is not None else {}
        self._pending_requests: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def get_spot_price(
        self,
        area_code: str,
        currency: str,
        timezone_str: str = "UTC",
    ) -> float | None:
        """Fetch current spot price from Nord Pool using the nordpool library.

        Uses the nordpool Python library to fetch spot prices directly from Nord Pool API.

        Args:
            area_code: Nord Pool area code (e.g., "NO1", "NO2", "SE3")
            currency: Currency code (e.g., "NOK", "SEK", "EUR")
            timezone_str: Timezone string for date calculations

        Returns:
            Current spot price in currency/kWh, or None if unavailable
        """
        if not area_code:
            return None

        if not NORD_POOL_AVAILABLE:
            _LOGGER.warning(
                "nordpool library not installed. Install it with: pip install nordpool"
            )
            return None

        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo(timezone_str)
        except Exception:
            tz = zoneinfo.ZoneInfo("UTC")

        # Get current date/time in the configured timezone
        now = datetime.now(tz)
        today = now.date()
        current_hour = now.hour

        # Check cache first (prices are updated daily)
        cache_key = f"{area_code}_{currency}_{today.isoformat()}"
        if cache_key in self._price_cache:
            _LOGGER.debug(
                "Using cached Nord Pool price for %s/%s: %.4f",
                area_code,
                currency,
                self._price_cache[cache_key],
            )
            return self._price_cache[cache_key]

        # Also check if we have yesterday's price as fallback
        yesterday = (today - timedelta(days=1)).isoformat()
        fallback_cache_key = f"{area_code}_{currency}_{yesterday}"
        fallback_price = self._price_cache.get(fallback_cache_key)

        # Check if there's already a pending request
        async with self._lock:
            if cache_key in self._pending_requests:
                pending_task = self._pending_requests[cache_key]
                if not pending_task.done():
                    _LOGGER.debug(
                        "Waiting for pending Nord Pool spot price request for %s/%s",
                        area_code,
                        currency,
                    )
                    try:
                        price = await pending_task
                        return price
                    except Exception as err:
                        _LOGGER.debug(
                            "Pending Nord Pool request failed for %s/%s: %s",
                            area_code,
                            currency,
                            err,
                        )
                        if cache_key in self._pending_requests:
                            del self._pending_requests[cache_key]
                else:
                    del self._pending_requests[cache_key]

        _LOGGER.debug(
            "Fetching Nord Pool spot price for area %s, currency %s",
            area_code,
            currency,
        )

        # Create async task for fetching
        async def _fetch_nord_pool_price() -> float | None:
            # Patch requests to use a longer timeout
            original_session_request = requests.Session.request
            original_get = requests.get
            original_post = requests.post

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
                prices_spot = elspot.Prices(currency)
                loop = asyncio.get_event_loop()

                def fetch_prices():
                    try:
                        _LOGGER.debug("Calling nordpool fetch for area %s, date %s", area_code, today)
                        result = prices_spot.fetch(
                            areas=[area_code],
                            end_date=date_class(today.year, today.month, today.day)
                        )
                        _LOGGER.debug(
                            "nordpool fetch returned: %s (type: %s)",
                            result if result is None else f"{type(result).__name__}",
                            type(result).__name__ if result else "None",
                        )

                        if result is None:
                            _LOGGER.debug("No data for today, trying yesterday's data")
                            yesterday_date = today - timedelta(days=1)
                            result = prices_spot.fetch(
                                areas=[area_code],
                                end_date=date_class(yesterday_date.year, yesterday_date.month, yesterday_date.day)
                            )
                            _LOGGER.debug(
                                "nordpool fetch for yesterday returned: %s",
                                type(result).__name__ if result else "None",
                            )

                        return result
                    except Exception as e:
                        _LOGGER.warning("Exception during nordpool fetch: %s", e, exc_info=True)
                        return None

                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(None, fetch_prices),
                        timeout=45.0,
                    )
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "Nord Pool API request timed out after 45 seconds for area %s/%s",
                        area_code,
                        currency,
                    )
                    result = None
                except Exception as fetch_exception:
                    _LOGGER.warning(
                        "Exception while fetching Nord Pool prices: %s",
                        fetch_exception,
                        exc_info=True,
                    )
                    result = None
            finally:
                requests.Session.request = original_session_request
                requests.get = original_get
                requests.post = original_post

            if not result:
                return None

            # Process result and return price
            if not isinstance(result, dict) or "areas" not in result:
                _LOGGER.debug(
                    "Nord Pool API: unexpected result structure. Type: %s, Keys: %s",
                    type(result).__name__,
                    list(result.keys()) if isinstance(result, dict) else "not a dict",
                )
                return None

            area_data = result.get("areas", {}).get(area_code)
            if not area_data:
                _LOGGER.debug(
                    "Nord Pool API: area %s not found in areas dict. Available areas: %s",
                    area_code,
                    list(result.get("areas", {}).keys()),
                )
                return None

            values = area_data.get("values")
            if not values:
                _LOGGER.debug("Nord Pool API returned empty values array for area %s", area_code)
                return None

            # Find the current hour's price, or use the most recent available price
            current_price = None
            prices_today = []

            for price_entry in values:
                start_time = price_entry.get("start")
                value = price_entry.get("value")

                if value is None:
                    continue

                if start_time and isinstance(start_time, datetime):
                    price_date = start_time.date()
                    if price_date == today:
                        # nordpool library returns prices in currency/MWh
                        # Convert to currency/kWh
                        price_per_kwh = value / 1000.0
                        prices_today.append(price_per_kwh)

                        if start_time.hour == current_hour:
                            current_price = price_per_kwh
                            _LOGGER.debug(
                                "Found current hour price for %s: %.2f %s/MWh = %.4f %s/kWh (hour %d)",
                                area_code,
                                value,
                                currency,
                                price_per_kwh,
                                currency,
                                current_hour,
                            )

            if not prices_today:
                _LOGGER.debug("No prices found for today (%s) for area %s", today, area_code)
                return None

            # Use current hour price if available, otherwise use average of today's prices
            if current_price is not None:
                _LOGGER.debug("Using current hour price: %.4f %s/kWh", current_price, currency)
                spot_price = current_price
            else:
                avg_price = sum(prices_today) / len(prices_today)
                _LOGGER.debug(
                    "Using average of today's prices: %.4f %s/kWh (%d price points)",
                    avg_price,
                    currency,
                    len(prices_today),
                )
                spot_price = avg_price

            # Cache the result
            self._price_cache[cache_key] = spot_price
            return spot_price

        # Create and track the task
        async with self._lock:
            if cache_key in self._pending_requests:
                pending_task = self._pending_requests[cache_key]
                if not pending_task.done():
                    task = pending_task
                else:
                    del self._pending_requests[cache_key]
                    task = asyncio.create_task(_fetch_nord_pool_price())
                    self._pending_requests[cache_key] = task
            else:
                task = asyncio.create_task(_fetch_nord_pool_price())
                self._pending_requests[cache_key] = task

        try:
            spot_price = await task
            if spot_price is not None:
                return spot_price

            # If fetch failed, try fallback
            _LOGGER.debug("Nord Pool API returned no data for area %s", area_code)
            if fallback_price is not None:
                _LOGGER.warning(
                    "Using fallback Nord Pool price from yesterday: %.4f",
                    fallback_price,
                )
                return fallback_price
            return None
        except Exception as err:
            if cache_key in self._pending_requests:
                del self._pending_requests[cache_key]
            if fallback_price is not None:
                _LOGGER.warning(
                    "Error fetching Nord Pool price, using fallback from yesterday: %.4f",
                    fallback_price,
                )
                return fallback_price
            raise
        finally:
            if cache_key in self._pending_requests and self._pending_requests[cache_key].done():
                del self._pending_requests[cache_key]
