"""Data processor for batch fetching and caching sensor data."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
import logging
import time
import zoneinfo
import asyncio

from .helpers import get_timezone

_LOGGER = logging.getLogger(__name__)


class DataProcessor:
    """Processes and caches batch-fetched sensor data."""

    def __init__(
        self,
        api: Any,  # EcoGuardAPI
        node_id: int,
        installations: list[dict[str, Any]],
        get_setting: Any,  # Callable[[str], str | None]
        latest_consumption_cache: dict[str, dict[str, Any]],
        latest_cost_cache: dict[str, dict[str, Any]],
        daily_consumption_cache: dict[str, list[dict[str, Any]]],
        daily_price_cache: dict[str, list[dict[str, Any]]],
        monthly_aggregate_cache: dict[str, dict[str, Any]],
        async_set_updated_data: Any,  # Callable[[dict[str, Any]], None]
        async_update_listeners: Any,  # Callable[[], None]
        get_listeners: Any,  # Callable[[], list]
        hass: Any,  # HomeAssistant
        data: dict[str, Any] | None,
    ) -> None:
        """Initialize the data processor.

        Args:
            api: EcoGuard API instance
            node_id: Node ID
            installations: List of installations
            get_setting: Function to get setting value
            latest_consumption_cache: Cache for latest consumption values
            latest_cost_cache: Cache for latest cost values
            daily_consumption_cache: Cache for daily consumption values
            daily_price_cache: Cache for daily price values
            monthly_aggregate_cache: Cache for monthly aggregates
            async_set_updated_data: Function to update coordinator data
            async_update_listeners: Function to notify listeners
            hass: Home Assistant instance
            data: Coordinator data dict
        """
        self._api = api
        self._node_id = node_id
        self._installations = installations
        self._get_setting = get_setting
        self._latest_consumption_cache = latest_consumption_cache
        self._latest_cost_cache = latest_cost_cache
        self._daily_consumption_cache = daily_consumption_cache
        self._daily_price_cache = daily_price_cache
        self._monthly_aggregate_cache = monthly_aggregate_cache
        self._async_set_updated_data = async_set_updated_data
        self._async_update_listeners = async_update_listeners
        self._get_listeners = get_listeners
        self._hass = hass
        self._data = data
        _LOGGER.debug("DataProcessor initialized with %d installations, node_id=%d", len(installations), node_id)

    async def batch_fetch_sensor_data(self) -> None:
        """Batch fetch consumption and price data for all utility codes.

        This method fetches data for all utility codes at once, then caches it
        so individual sensors can read from the cache instead of making API calls.
        """
        _LOGGER.info("Starting batch fetch sensor data (installations: %d)", len(self._installations))
        try:
            # Collect all unique utility codes from installations
            utility_codes = set()
            for installation in self._installations:
                registers = installation.get("Registers", [])
                for register in registers:
                    utility_code = register.get("UtilityCode")
                    if utility_code and utility_code in ("HW", "CW", "E", "HE"):
                        utility_codes.add(utility_code)

            if not utility_codes:
                _LOGGER.warning("No utility codes found in %d installations, skipping batch fetch", len(self._installations))
                return

            _LOGGER.info("Batch fetching consumption and price data for utility codes: %s (from %d installations)", 
                        sorted(utility_codes), len(self._installations))

            # Get timezone for date calculations
            timezone_str = self._get_setting("TimeZoneIANA") or "UTC"
            tz = get_timezone(timezone_str)

            now_tz = datetime.now(tz)
            tomorrow_start = datetime.combine(
                (now_tz.date() + timedelta(days=1)), datetime.min.time(), tz
            )
            to_time = int(tomorrow_start.timestamp())

            # Fetch 30 days of data for comprehensive cache coverage
            # Since this runs asynchronously in the background, it doesn't block startup
            initial_days = 30
            from_time = int((datetime.combine(now_tz.date() - timedelta(days=initial_days), datetime.min.time(), tz)).timestamp())

            _LOGGER.debug("Batch fetch: Fetching %d days of data (from %s to %s)",
                         initial_days, datetime.fromtimestamp(from_time, tz=tz).date(),
                         datetime.fromtimestamp(to_time, tz=tz).date())

            # Create a mapping of measuring_point_id -> utility_codes for this installation
            # This helps us correctly map API responses to cache keys
            mp_to_utilities: dict[int, set[str]] = {}
            for installation in self._installations:
                mp_id = installation.get("MeasuringPointID")
                if mp_id:
                    registers = installation.get("Registers", [])
                    utilities = {r.get("UtilityCode") for r in registers if r.get("UtilityCode") in utility_codes}
                    if utilities:
                        mp_to_utilities[mp_id] = utilities

            # Fetch consumption data per measuring point for accurate cache keys
            await self._fetch_consumption_data(mp_to_utilities, from_time, to_time)

            # Fetch price data per measuring point for accurate cache keys
            await self._fetch_price_data(mp_to_utilities, from_time, to_time)

            # Update cache timestamp
            self._cache_timestamp = time.time()

            # Log cache statistics
            _LOGGER.info(
                "Batch fetch complete: %d daily consumption sets, %d daily price sets, %d latest consumption, %d latest prices",
                len(self._daily_consumption_cache),
                len(self._daily_price_cache),
                len(self._latest_consumption_cache),
                len(self._latest_cost_cache)
            )

            # Sync cache to coordinator.data and notify sensors
            self._sync_and_notify()

        except Exception as err:
            _LOGGER.warning("Error in batch fetch: %s", err, exc_info=True)
            # Even if there was an error, sync what we have so sensors can at least see partial data
            self._sync_and_notify()

    async def _fetch_consumption_data(
        self,
        mp_to_utilities: dict[int, set[str]],
        from_time: int,
        to_time: int,
    ) -> None:
        """Fetch and cache consumption data."""
        try:
            # Fetch data per measuring point to get accurate cache keys
            for measuring_point_id, utilities in mp_to_utilities.items():
                try:
                    consumption_data = await self._api.get_data(
                        node_id=self._node_id,
                        from_time=from_time,
                        to_time=to_time,
                        interval="d",
                        grouping="apartment",
                        utilities=[f"{uc}[con]" for uc in utilities],
                        include_sub_nodes=False,
                        measuring_point_id=measuring_point_id,
                    )

                    # Process and cache consumption data for this measuring point
                    if consumption_data and isinstance(consumption_data, list):
                        for node_data in consumption_data:
                            results = node_data.get("Result", [])

                            if not results:
                                continue

                            for result in results:
                                utility_code = result.get("Utl")
                                if result.get("Func") == "con" and utility_code:
                                    values = result.get("Values", [])
                                    unit = result.get("Unit", "")

                                    if not values:
                                        continue

                                    # Cache ALL daily values (not just latest) for reuse
                                    daily_values = []
                                    latest_value = None
                                    latest_time = None

                                    for value_entry in values:
                                        value = value_entry.get("Value")
                                        time_stamp = value_entry.get("Time")
                                        if value is not None and time_stamp is not None:
                                            daily_values.append({
                                                "time": time_stamp,
                                                "value": value,
                                                "unit": unit,
                                            })
                                            # Track latest for quick access
                                            if latest_time is None or time_stamp > latest_time:
                                                latest_value = value
                                                latest_time = time_stamp

                                    if daily_values:
                                        # Sort by time
                                        daily_values.sort(key=lambda x: x["time"])

                                        # Cache keys - use measuring_point_id from our mapping
                                        cache_key_all = f"{utility_code}_all"
                                        cache_key_meter = f"{utility_code}_{measuring_point_id}"

                                        # Aggregate into "all" cache (sum values across all meters)
                                        if cache_key_all not in self._daily_consumption_cache:
                                            self._daily_consumption_cache[cache_key_all] = []

                                        # Merge daily values into "all" cache (deduplicate by time)
                                        existing_all = self._daily_consumption_cache[cache_key_all]
                                        existing_times = {v["time"] for v in existing_all}

                                        # Aggregate values by time for "all" cache
                                        for daily_val in daily_values:
                                            time_stamp = daily_val["time"]
                                            if time_stamp in existing_times:
                                                # Sum with existing value for this time
                                                for existing in existing_all:
                                                    if existing["time"] == time_stamp:
                                                        existing["value"] += daily_val["value"]
                                                        break
                                            else:
                                                # New time, add it
                                                existing_all.append(daily_val.copy())

                                        # Sort "all" cache by time
                                        existing_all.sort(key=lambda x: x["time"])

                                        # Store per-meter daily values
                                        self._daily_consumption_cache[cache_key_meter] = daily_values

                                        # Also store latest for quick access
                                        if latest_value is not None:
                                            cache_entry = {
                                                "value": latest_value,
                                                "time": latest_time,
                                                "unit": unit,
                                                "utility_code": utility_code,
                                                "measuring_point_id": measuring_point_id,
                                            }

                                            # Update "all" latest (sum across all meters)
                                            if cache_key_all in self._latest_consumption_cache:
                                                existing_all_entry = self._latest_consumption_cache[cache_key_all]
                                                # Use the latest time and sum values
                                                if latest_time >= existing_all_entry.get("time", 0):
                                                    existing_all_entry["value"] = existing_all_entry.get("value", 0) + latest_value
                                                    existing_all_entry["time"] = latest_time
                                            else:
                                                self._latest_consumption_cache[cache_key_all] = cache_entry.copy()

                                            # Store per-meter latest
                                            self._latest_consumption_cache[cache_key_meter] = cache_entry
                                            _LOGGER.debug("Cached consumption: %s (meter %s) = %s %s",
                                                         cache_key_meter, measuring_point_id, latest_value, unit)
                except Exception as err:
                    _LOGGER.warning("Failed to fetch consumption data for measuring point %s: %s", measuring_point_id, err)
                    continue

            if not self._latest_consumption_cache:
                _LOGGER.warning("Batch fetch: No consumption data was cached")

            _LOGGER.info("Cached consumption data: %d daily value sets, %d latest values",
                        len(self._daily_consumption_cache), len(self._latest_consumption_cache))
        except Exception as err:
            _LOGGER.warning("Failed to batch fetch consumption data: %s", err)

    async def _fetch_price_data(
        self,
        mp_to_utilities: dict[int, set[str]],
        from_time: int,
        to_time: int,
    ) -> None:
        """Fetch and cache price data."""
        try:
            # Fetch data per measuring point to get accurate cache keys
            for measuring_point_id, utilities in mp_to_utilities.items():
                try:
                    price_data = await self._api.get_data(
                        node_id=self._node_id,
                        from_time=from_time,
                        to_time=to_time,
                        interval="d",
                        grouping="apartment",
                        utilities=[f"{uc}[price]" for uc in utilities],
                        include_sub_nodes=False,
                        measuring_point_id=measuring_point_id,
                    )

                    # Process and cache price data for this measuring point
                    if price_data and isinstance(price_data, list):
                        for node_data in price_data:
                            results = node_data.get("Result", [])

                            if not results:
                                _LOGGER.debug("No price results for measuring point %s", measuring_point_id)
                                continue

                            for result in results:
                                utility_code = result.get("Utl")
                                func = result.get("Func")
                                _LOGGER.debug("Processing price result: utility=%s, func=%s, measuring_point=%s",
                                             utility_code, func, measuring_point_id)
                                
                                if func == "price" and utility_code:
                                    values = result.get("Values", [])
                                    unit = result.get("Unit", "")

                                    if not values:
                                        _LOGGER.debug("No price values for %s (meter %s)", utility_code, measuring_point_id)
                                        continue

                                    # Cache ALL daily price values (not just latest) for reuse
                                    daily_prices = []
                                    latest_price = None
                                    latest_time = None

                                    for value_entry in values:
                                        value = value_entry.get("Value")
                                        time_stamp = value_entry.get("Time")
                                        # Allow 0 values (they're valid, just means no cost for that day)
                                        if value is not None and value >= 0 and time_stamp is not None:
                                            daily_prices.append({
                                                "time": time_stamp,
                                                "value": value,
                                                "unit": unit,
                                            })
                                            # Track latest for quick access
                                            if latest_time is None or time_stamp > latest_time:
                                                latest_price = value
                                                latest_time = time_stamp

                                    if daily_prices:
                                        # Sort by time
                                        daily_prices.sort(key=lambda x: x["time"])

                                        # Find the most recent non-zero price (data is delayed by 1 day)
                                        # Iterate backwards to find the last entry with a non-zero value
                                        latest_price = None
                                        latest_time = None
                                        for price_entry in reversed(daily_prices):
                                            if price_entry["value"] > 0:
                                                latest_price = price_entry["value"]
                                                latest_time = price_entry["time"]
                                                break
                                        
                                        # For hot water: if all prices are 0, treat as "Unknown" (no metered price data)
                                        # HW prices are typically calculated from spot prices, not from API metered data
                                        # For other utilities: if all prices are 0, use 0 (might be valid - no cost for those days)
                                        if latest_price is None and daily_prices:
                                            if utility_code == "HW":
                                                # Don't cache anything for HW if all prices are 0
                                                # This will make sensors show "Unknown" instead of 0.0 NOK
                                                _LOGGER.debug("All HW price entries are 0 for meter %s, treating as Unknown (no metered price data)", measuring_point_id)
                                                continue
                                            else:
                                                # For other utilities, 0 might be valid
                                                last_price_entry = daily_prices[-1]
                                                latest_price = last_price_entry["value"]
                                                latest_time = last_price_entry["time"]

                                        # Cache keys - use measuring_point_id from our mapping
                                        cache_key_all = f"{utility_code}_all_metered"
                                        cache_key_meter = f"{utility_code}_{measuring_point_id}_metered"

                                        # Store all daily prices for reuse (per meter)
                                        self._daily_price_cache[cache_key_meter] = daily_prices
                                        
                                        # Only cache latest price if we found a non-zero value (or non-HW with 0)
                                        if latest_price is not None:
                                            _LOGGER.info("Cached %d daily prices for %s (meter %s), latest non-zero: %s %s (time: %s)",
                                                       len(daily_prices), cache_key_meter, measuring_point_id,
                                                       latest_price, unit, datetime.fromtimestamp(latest_time).strftime("%Y-%m-%d") if latest_time else "N/A")
                                        else:
                                            _LOGGER.debug("Cached %d daily prices for %s (meter %s), but no valid latest price to cache",
                                                         len(daily_prices), cache_key_meter, measuring_point_id)

                                        # Also store latest for quick access
                                        if latest_price is not None:
                                            cache_entry = {
                                                "value": latest_price,
                                                "time": latest_time,
                                                "unit": unit,
                                                "utility_code": utility_code,
                                                "cost_type": "metered",
                                                "measuring_point_id": measuring_point_id,
                                            }
                                            # Store per-meter latest cost
                                            self._latest_cost_cache[cache_key_meter] = cache_entry
                                            _LOGGER.info("Cached latest price: %s (meter %s) = %s %s",
                                                       cache_key_meter, measuring_point_id, latest_price, unit)
                                            
                                            # Update "all" latest (sum across all meters)
                                            if cache_key_all in self._latest_cost_cache:
                                                existing_all_entry = self._latest_cost_cache[cache_key_all]
                                                # Sum values and use the latest time
                                                existing_all_entry["value"] = existing_all_entry.get("value", 0) + latest_price
                                                if latest_time > existing_all_entry.get("time", 0):
                                                    existing_all_entry["time"] = latest_time
                                                _LOGGER.info("Updated aggregate price: %s = %s %s (summed from %d meters)",
                                                           cache_key_all, existing_all_entry["value"], unit, 
                                                           len([k for k in self._latest_cost_cache.keys() if k.startswith(f"{utility_code}_") and k.endswith("_metered")]))
                                            else:
                                                # Create aggregate entry (first meter for this utility)
                                                self._latest_cost_cache[cache_key_all] = {
                                                    "value": latest_price,
                                                    "time": latest_time,
                                                    "unit": unit,
                                                    "utility_code": utility_code,
                                                    "cost_type": "metered",
                                                    "measuring_point_id": None,  # Aggregate across all meters
                                                }
                                                _LOGGER.info("Created aggregate price: %s = %s %s",
                                                           cache_key_all, latest_price, unit)
                                    else:
                                        _LOGGER.debug("No valid daily prices for %s (meter %s) - all values were None or <= 0",
                                                     utility_code, measuring_point_id)
                                else:
                                    _LOGGER.debug("Skipping result: func=%s, utility=%s (not a price result)",
                                                func, utility_code)
                    else:
                        _LOGGER.debug("No price data returned for measuring point %s (data=%s)",
                                    measuring_point_id, type(price_data).__name__ if price_data else "None")
                except Exception as err:
                    _LOGGER.warning("Failed to fetch price data for measuring point %s: %s", measuring_point_id, err)
                    continue

            _LOGGER.info("Cached price data: %d daily price sets, %d latest values",
                        len(self._daily_price_cache), len(self._latest_cost_cache))
        except Exception as err:
            _LOGGER.warning("Failed to batch fetch price data: %s", err)

    def _sync_and_notify(self) -> None:
        """Sync cache to coordinator.data and notify sensors."""
        # Always update data and notify listeners to ensure sensors get updates
        # Use references to cache dictionaries (not copies) so updates are immediately visible
        if self._data:
            # Update the existing data dict with references to cache dictionaries
            # This ensures that when caches are updated, sensors can see the changes immediately
            self._data["latest_consumption_cache"] = self._latest_consumption_cache
            self._data["latest_cost_cache"] = self._latest_cost_cache
            self._data["daily_consumption_cache"] = self._daily_consumption_cache
            self._data["daily_price_cache"] = self._daily_price_cache
            self._data["monthly_aggregate_cache"] = self._monthly_aggregate_cache
            updated_data = self._data
        else:
            # Create initial data structure if coordinator hasn't been refreshed yet
            updated_data = {
                "measuring_points": [],
                "installations": [],
                "latest_reception": [],
                "node_data": None,
                "settings": [],
                "node_id": self._node_id,
                "domain": "",
                # Use references to cache dictionaries so updates are immediately visible
                "latest_consumption_cache": self._latest_consumption_cache,
                "latest_cost_cache": self._latest_cost_cache,
                "daily_consumption_cache": self._daily_consumption_cache,
                "daily_price_cache": self._daily_price_cache,
                "monthly_aggregate_cache": self._monthly_aggregate_cache,
            }
            self._data = updated_data
        
        # Always update data and notify, even if updated_data is empty
        # This ensures sensors get notified about cache updates
        _LOGGER.debug("Syncing and notifying: %d consumption keys, %d cost keys, %d daily consumption sets, %d daily price sets",
                     len(self._latest_consumption_cache), len(self._latest_cost_cache),
                     len(self._daily_consumption_cache), len(self._daily_price_cache))
        
        # Use async_set_updated_data to properly notify sensors
        # This ensures sensors get the update callback
        # IMPORTANT: This must be called from the event loop, which we are (batch fetch runs in background task)
        self._async_set_updated_data(updated_data)
        # Also explicitly notify listeners to ensure all sensors get updated
        # This is a safety net in case async_set_updated_data doesn't trigger for some reason
        self._async_update_listeners()
        _LOGGER.debug("Called async_set_updated_data and async_update_listeners")

        # Log listener details for debugging
        listeners = self._get_listeners()
        listener_count = len(listeners)
        listener_ids = [str(l) for l in listeners][:10]
        _LOGGER.info("Notified %d listeners about cache update (consumption: %d keys, cost: %d keys). Listeners: %s",
                     listener_count,
                     len(self._latest_consumption_cache),
                     len(self._latest_cost_cache),
                     listener_ids)

        # Schedule a delayed update notification to catch sensors that are added after batch fetch completes
        # This ensures sensors get updated even if they're added after the batch fetch finishes
        async def _delayed_notification():
            await asyncio.sleep(1.0)  # Wait 1 second for sensors to be added
            if not self._hass.is_stopping:
                listeners = self._get_listeners()
                _LOGGER.info("Delayed notification: Notifying %d listeners again (consumption: %d keys, cost: %d keys)",
                             len(listeners),
                             len(self._latest_consumption_cache),
                             len(self._latest_cost_cache))
                self._async_update_listeners()

        self._hass.async_create_task(_delayed_notification())
