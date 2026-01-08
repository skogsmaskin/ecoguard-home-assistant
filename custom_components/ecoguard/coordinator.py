"""Data update coordinator for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
import logging
import time
import zoneinfo
import asyncio

from homeassistant.core import HomeAssistant, CoreState
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .api import EcoGuardAPI, EcoGuardAPIError
from .const import (
    UPDATE_INTERVAL_DATA,
    UPDATE_INTERVAL_LATEST_RECEPTION,
)
from .helpers import get_timezone, get_month_timestamps
from .nord_pool import NordPoolPriceFetcher, NORD_POOL_AVAILABLE
from .price_calculator import HWPriceCalculator
from .billing_manager import BillingManager

_LOGGER = logging.getLogger(__name__)


class EcoGuardDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching EcoGuard data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: EcoGuardAPI,
        node_id: int,
        domain: str,
        nord_pool_area: str | None = None,
        entry_id: str | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_DATA),
        )
        self.api = api
        self.node_id = node_id
        self.domain = domain
        self.nord_pool_area = nord_pool_area
        self.entry_id = entry_id
        self._measuring_points: list[dict[str, Any]] = []
        self._installations: list[dict[str, Any]] = []
        self._latest_reception: list[dict[str, Any]] = []
        self._node_data: dict[str, Any] | None = None
        self._settings: list[dict[str, Any]] = []
        # Initialize Nord Pool price fetcher
        self._nord_pool_price_cache: dict[str, float] = {}  # Cache for current day prices
        self._nord_pool_fetcher: NordPoolPriceFetcher | None = None
        if nord_pool_area:
            self._nord_pool_fetcher = NordPoolPriceFetcher(price_cache=self._nord_pool_price_cache)
        
        # Initialize HW price calculator
        self._hw_price_calculator: HWPriceCalculator | None = None
        
        # HW calibration ratio for spot price calculations
        self._hw_calibration_ratio: float | None = None
        self._hw_calibration_calculated: bool = False
        self._hw_calibration_lock = asyncio.Lock()
        
        self._billing_results_cache: dict[str, tuple[list[dict[str, Any]], float]] = {}  # Cache for billing results: key -> (data, timestamp)
        self._billing_cache_ttl: float = 86400.0  # Cache billing data for 24 hours (it's historical and doesn't change)
        
        self._data_request_cache: dict[str, tuple[Any, float]] = {}  # Cache for data API requests: key -> (data, timestamp)
        self._data_cache_ttl: float = 60.0  # Cache data requests for 60 seconds to prevent duplicate calls
        self._pending_requests: dict[str, asyncio.Task] = {}  # Track pending requests to deduplicate simultaneous calls
        self._pending_requests_lock = asyncio.Lock()  # Lock to prevent race conditions when checking/adding pending requests
        self._cache_loaded: bool = False  # Track if we've loaded from cache
        
        # Caches for sensor data (populated by batch fetching)
        # Key format: f"{utility_code}_{measuring_point_id or 'all'}"
        self._latest_consumption_cache: dict[str, dict[str, Any]] = {}  # Latest consumption by utility/meter
        self._latest_cost_cache: dict[str, dict[str, Any]] = {}  # Latest cost by utility/meter/cost_type
        
        # Daily data cache - stores ALL daily values for reuse (not just latest)
        # Key format: f"{utility_code}_{measuring_point_id or 'all'}"
        # Value: list of daily values sorted by time: [{"time": timestamp, "value": value, "unit": unit}, ...]
        self._daily_consumption_cache: dict[str, list[dict[str, Any]]] = {}  # All daily consumption values
        self._daily_price_cache: dict[str, list[dict[str, Any]]] = {}  # All daily price values
        
        # Key format: f"{utility_code}_{year}_{month}_{aggregate_type}_{cost_type}"
        self._monthly_aggregate_cache: dict[str, dict[str, Any]] = {}  # Monthly aggregates
        self._cache_timestamp: float = 0.0  # When cache was last updated
        
        # Initialize billing manager after all attributes are set
        self.billing_manager = BillingManager(
            api=self.api,
            node_id=self.node_id,
            hass=self.hass,
            billing_cache=self._billing_results_cache,
            pending_requests=self._pending_requests,
            pending_requests_lock=self._pending_requests_lock,
            get_setting=self.get_setting,
            billing_cache_ttl=self._billing_cache_ttl,
            get_monthly_aggregate=lambda uc, y, m, at, ct: self.get_monthly_aggregate(uc, y, m, at, ct),
            get_hw_price_from_spot_prices=lambda c, y, m, cwp, cwc: self._get_hw_price_from_spot_prices(c, y, m, cwp, cwc),
            nord_pool_area=self.nord_pool_area,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from EcoGuard API.
        
        This method is called:
        - Once on startup (async_config_entry_first_refresh)
        - Periodically based on update_interval (every hour by default)
        """
        from .storage import load_cached_data, save_cached_data
        
        _LOGGER.debug("Coordinator update triggered (cache_loaded=%s)", self._cache_loaded)
        
        try:
            # Load from cache first (only once on startup)
            if not self._cache_loaded and self.entry_id:
                _LOGGER.debug("Attempting to load cached data for entry %s", self.entry_id)
                cached_data = await load_cached_data(self.hass, self.entry_id)
                if cached_data:
                    _LOGGER.info("Loading data from cache for entry %s", self.entry_id)
                    if cached_data.get("installations"):
                        self._installations = cached_data["installations"]
                        _LOGGER.info("Loaded %d installations from cache", len(self._installations))
                    if cached_data.get("measuring_points"):
                        self._measuring_points = cached_data["measuring_points"]
                        _LOGGER.info("Loaded %d measuring points from cache", len(self._measuring_points))
                    if cached_data.get("node_data"):
                        self._node_data = cached_data["node_data"]
                        _LOGGER.info("Loaded node_data from cache")
                    if cached_data.get("settings"):
                        self._settings = cached_data["settings"]
                        _LOGGER.info("Loaded %d settings from cache", len(self._settings))
                else:
                    _LOGGER.debug("No cached data found for entry %s", self.entry_id)
                self._cache_loaded = True

            # Use cached node data and measuring points during startup
            # API updates happen after Home Assistant has fully started (see __init__.py)
            if not self._node_data:
                _LOGGER.debug("No cached node data available")
                self._node_data = None
                self._measuring_points = []
            elif not self._measuring_points:
                _LOGGER.debug("No cached measuring points available")
                self._measuring_points = []
            else:
                _LOGGER.debug("Using cached node data and measuring points")

            # Use cached installations during startup
            # API updates happen after Home Assistant has fully started (see __init__.py)
            if not self._installations:
                _LOGGER.debug("No cached installations available")
                self._installations = []
            else:
                _LOGGER.debug("Using cached installations")

            # Use cached settings during startup
            # API updates happen after Home Assistant has fully started (see __init__.py)
            if not self._settings:
                _LOGGER.debug("No cached settings available")
                self._settings = []
            else:
                _LOGGER.debug("Using cached settings")

            # Note: Latest reception is now handled by a separate coordinator
            # Keep this for backward compatibility, but it won't update frequently
            if not self._latest_reception:
                _LOGGER.debug("No cached latest reception available")
                self._latest_reception = []

            # Log static info summary
            self._log_static_info_summary()

            # Batch fetch is triggered after Home Assistant has fully started
            # (see __init__.py for the startup event listener)
            # This ensures sensors load instantly without blocking startup

            # Return data immediately - don't wait for background API updates
            # This allows sensors to be created quickly using cached data
            # Background updates will happen asynchronously
            return {
                "measuring_points": self._measuring_points,
                "installations": self._installations,
                "latest_reception": self._latest_reception,
                "node_data": self._node_data,
                "settings": self._settings,
                "node_id": self.node_id,
                "domain": self.domain,
                # Include caches so sensors can read from them
                "latest_consumption_cache": self._latest_consumption_cache,
                "latest_cost_cache": self._latest_cost_cache,
                "daily_consumption_cache": self._daily_consumption_cache,  # All daily values for reuse
                "daily_price_cache": self._daily_price_cache,  # All daily prices for reuse
                "monthly_aggregate_cache": self._monthly_aggregate_cache,
            }
        except EcoGuardAPIError as err:
            # If we have cached data, return it even if API calls fail
            # This allows sensors to be created immediately on startup
            if self._cache_loaded and (self._installations or self._measuring_points or self._settings):
                _LOGGER.warning(
                    "API error occurred, but returning cached data: %s", err
                )
                return {
                    "measuring_points": self._measuring_points,
                    "installations": self._installations,
                    "latest_reception": self._latest_reception,
                    "node_data": self._node_data,
                    "settings": self._settings,
                    "node_id": self.node_id,
                    "domain": self.domain,
                }
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    def get_measuring_points(self) -> list[dict[str, Any]]:
        """Get cached measuring points."""
        return self._measuring_points

    def get_installations(self) -> list[dict[str, Any]]:
        """Get cached installations."""
        return self._installations

    def get_latest_reception(self) -> list[dict[str, Any]]:
        """Get latest reception data."""
        return self._latest_reception

    def get_node_data(self) -> dict[str, Any] | None:
        """Get node data."""
        return self._node_data

    def get_settings(self) -> list[dict[str, Any]]:
        """Get settings."""
        return self._settings

    def get_setting(self, name: str) -> str | None:
        """Get a specific setting value by name."""
        for setting in self._settings:
            if setting.get("Name") == name:
                return setting.get("Value")
        return None

    def _log_static_info_summary(self) -> None:
        """Log a summary of all static information."""
        if not self._node_data and not self._settings:
            return

        _LOGGER.debug("=" * 80)
        _LOGGER.debug("ECOGUARD STATIC DATA SUMMARY")
        _LOGGER.debug("=" * 80)

        # Node Information
        if self._node_data:
            _LOGGER.debug("NODE DATA:")
            _LOGGER.debug("  Node ID: %s", self.node_id)
            _LOGGER.debug("  Domain: %s", self.domain)

            # Properties
            properties = self._node_data.get("Properties", [])
            if properties:
                _LOGGER.debug("  Properties:")
                for prop in properties:
                    _LOGGER.debug("    - %s: %s", prop.get("Name", "Unknown"), prop.get("Value", "N/A"))

            # Measuring Points
            measuring_points = self._node_data.get("MeasuringPoints", [])
            if measuring_points:
                _LOGGER.debug("  Measuring Points (%d):", len(measuring_points))
                for mp in measuring_points:
                    _LOGGER.debug("    - ID: %s, Name: %s", mp.get("ID"), mp.get("Name"))

            # SubNodes
            sub_nodes = self._node_data.get("SubNodes", [])
            if sub_nodes:
                _LOGGER.debug("  SubNodes (%d):", len(sub_nodes))
                for sub in sub_nodes:
                    _LOGGER.debug("    - ID: %s, Name: %s", sub.get("ID"), sub.get("Name"))

            # Rental Contracts
            contracts = self._node_data.get("RentalContracts", [])
            if contracts:
                _LOGGER.debug("  Rental Contracts (%d):", len(contracts))
                for contract in contracts:
                    contract_date = contract.get("Date")
                    if contract_date:
                        from datetime import datetime
                        date_str = datetime.fromtimestamp(contract_date).strftime("%Y-%m-%d")
                    else:
                        date_str = "N/A"
                    _LOGGER.debug("    - ID: %s, Date: %s, Code: %s",
                                 contract.get("ID"), date_str, contract.get("ContractCode"))
        else:
            _LOGGER.debug("NODE DATA: Not available")

        # Settings
        if self._settings:
            _LOGGER.debug("SETTINGS (%d):", len(self._settings))
            for setting in self._settings:
                _LOGGER.debug("  - %s: %s", setting.get("Name"), setting.get("Value"))
        else:
            _LOGGER.debug("SETTINGS: Not available")

        # Installations
        if self._installations:
            _LOGGER.debug("INSTALLATIONS (%d):", len(self._installations))
            for inst in self._installations:
                mp_id = inst.get("MeasuringPointID")
                device_type = inst.get("DeviceTypeDisplay", "Unknown")
                external_key = inst.get("ExternalKey", "N/A")

                # Installation lifespan
                from_date = inst.get("From")
                to_date = inst.get("To")
                if from_date:
                    from_str = datetime.fromtimestamp(from_date).strftime("%Y-%m-%d")
                else:
                    from_str = "N/A"
                if to_date:
                    to_str = datetime.fromtimestamp(to_date).strftime("%Y-%m-%d")
                    status = "Ended"
                else:
                    to_str = "Active"
                    status = "Active"

                _LOGGER.debug("  - MeasuringPointID: %s, DeviceType: %s, ExternalKey: %s",
                             mp_id, device_type, external_key)
                _LOGGER.debug("    Status: %s, From: %s, To: %s", status, from_str, to_str)

                # Registers (utility codes)
                registers = inst.get("Registers", [])
                if registers:
                    _LOGGER.debug("    Registers:")
                    for reg in registers:
                        util_code = reg.get("UtilityCode", "Unknown")
                        _LOGGER.debug("      - UtilityCode: %s", util_code)
        else:
            _LOGGER.debug("INSTALLATIONS: Not available")

        # Measuring Points (from cache)
        if self._measuring_points:
            _LOGGER.debug("MEASURING POINTS CACHE (%d):", len(self._measuring_points))
            for mp in self._measuring_points:
                _LOGGER.debug("  - ID: %s, Name: %s", mp.get("ID"), mp.get("Name"))

        # Latest Reception
        if self._latest_reception:
            _LOGGER.debug("LATEST RECEPTION (%d):", len(self._latest_reception))
            for reception in self._latest_reception:
                pos_id = reception.get("PositionID")
                latest = reception.get("LatestReception")
                if latest:
                    from datetime import datetime
                    date_str = datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    date_str = "N/A"
                _LOGGER.debug("  - PositionID: %s, LatestReception: %s", pos_id, date_str)
        else:
            _LOGGER.debug("LATEST RECEPTION: Not available")

        _LOGGER.debug("=" * 80)

    def get_latest_reading(
        self, measuring_point_id: int
    ) -> dict[str, Any] | None:
        """Get the latest reading information for a measuring point."""
        if not self._latest_reception:
            return None

        for reception in self._latest_reception:
            if reception.get("PositionID") == measuring_point_id:
                return reception

        return None

    def _sync_cache_to_data(self) -> None:
        """Sync cache dictionaries to coordinator.data so sensors can read them."""
        if self.data:
            self.data["latest_consumption_cache"] = self._latest_consumption_cache
            self.data["latest_cost_cache"] = self._latest_cost_cache
            self.data["daily_consumption_cache"] = self._daily_consumption_cache
            self.data["daily_price_cache"] = self._daily_price_cache
            self.data["monthly_aggregate_cache"] = self._monthly_aggregate_cache

    async def _batch_fetch_sensor_data(self) -> None:
        """Batch fetch consumption and price data for all utility codes.
        
        This method fetches data for all utility codes at once, then caches it
        so individual sensors can read from the cache instead of making API calls.
        """
        import time
        
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
                _LOGGER.debug("No utility codes found, skipping batch fetch")
                return
            
            _LOGGER.info("Batch fetching consumption and price data for utility codes: %s", sorted(utility_codes))
            
            # Get timezone for date calculations
            timezone_str = self.get_setting("TimeZoneIANA") or "UTC"
            try:
                tz = zoneinfo.ZoneInfo(timezone_str)
            except Exception:
                tz = zoneinfo.ZoneInfo("UTC")
            
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
            # When include_sub_nodes=True, we get node-level aggregates, not per-meter data
            # So we need to fetch per measuring point to get the correct cache keys
            consumption_utilities = [f"{uc}[con]" for uc in utility_codes]
            try:
                # Fetch data per measuring point to get accurate cache keys
                for measuring_point_id, utilities in mp_to_utilities.items():
                    try:
                        consumption_data = await self.api.get_data(
                            node_id=self.node_id,
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
            
            # Fetch price data per measuring point for accurate cache keys
            try:
                # Fetch data per measuring point to get accurate cache keys
                for measuring_point_id, utilities in mp_to_utilities.items():
                    try:
                        price_data = await self.api.get_data(
                            node_id=self.node_id,
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
                                
                                for result in results:
                                    utility_code = result.get("Utl")
                                    if result.get("Func") == "price" and utility_code:
                                        values = result.get("Values", [])
                                        unit = result.get("Unit", "")
                                        
                                        # Cache ALL daily price values (not just latest) for reuse
                                        daily_prices = []
                                        latest_price = None
                                        latest_time = None
                                        
                                        for value_entry in values:
                                            value = value_entry.get("Value")
                                            time_stamp = value_entry.get("Time")
                                            if value is not None and value > 0 and time_stamp is not None:
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
                                            
                                            # Cache keys - use measuring_point_id from our mapping
                                            cache_key = f"{utility_code}_{measuring_point_id}_metered"
                                            
                                            # Store all daily prices for reuse
                                            self._daily_price_cache[cache_key] = daily_prices
                                            
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
                                                self._latest_cost_cache[cache_key] = cache_entry
                                                _LOGGER.debug("Cached price: %s (meter %s) = %s %s", 
                                                             cache_key, measuring_point_id, latest_price, unit)
                    except Exception as err:
                        _LOGGER.warning("Failed to fetch price data for measuring point %s: %s", measuring_point_id, err)
                        continue
                
                _LOGGER.info("Cached price data: %d daily price sets, %d latest values",
                            len(self._daily_price_cache), len(self._latest_cost_cache))
            except Exception as err:
                _LOGGER.warning("Failed to batch fetch price data: %s", err)
            
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
            # Always update data and notify listeners to ensure sensors get updates
            if self.data:
                # Update the existing data dict with new cache
                # Create a completely new dict with new cache dicts to ensure change detection
                updated_data = {
                    "measuring_points": self.data.get("measuring_points"),
                    "installations": self.data.get("installations"),
                    "latest_reception": self.data.get("latest_reception"),
                    "node_data": self.data.get("node_data"),
                    "settings": self.data.get("settings"),
                    "node_id": self.data.get("node_id"),
                    "domain": self.data.get("domain"),
                    # Create new dicts for caches to ensure change detection
                    "latest_consumption_cache": dict(self._latest_consumption_cache),
                    "latest_cost_cache": dict(self._latest_cost_cache),
                    "daily_consumption_cache": {k: list(v) for k, v in self._daily_consumption_cache.items()},  # Deep copy lists
                    "daily_price_cache": {k: list(v) for k, v in self._daily_price_cache.items()},  # Deep copy lists
                    "monthly_aggregate_cache": dict(self._monthly_aggregate_cache),
                }
                
                # Use async_set_updated_data to properly notify sensors
                # This ensures sensors get the update callback
                # IMPORTANT: This must be called from the event loop, which we are (batch fetch runs in background task)
                self.async_set_updated_data(updated_data)
                # Also explicitly notify listeners to ensure all sensors get updated
                # This is a safety net in case async_set_updated_data doesn't trigger for some reason
                self.async_update_listeners()
                
                # Log listener details for debugging
                listener_count = len(self._listeners) if hasattr(self, '_listeners') else 0
                listener_ids = [str(l) for l in (self._listeners if hasattr(self, '_listeners') else [])][:10]
                _LOGGER.info("Notified %d listeners about cache update (consumption: %d keys, cost: %d keys). Listeners: %s", 
                             listener_count, 
                             len(self._latest_consumption_cache),
                             len(self._latest_cost_cache),
                             listener_ids)
                
                # Schedule a delayed update notification to catch sensors that are added after batch fetch completes
                # This ensures sensors get updated even if they're added after the batch fetch finishes
                async def _delayed_notification():
                    await asyncio.sleep(1.0)  # Wait 1 second for sensors to be added
                    if not self.hass.is_stopping:
                        _LOGGER.info("Delayed notification: Notifying %d listeners again (consumption: %d keys, cost: %d keys)", 
                                     len(self._listeners) if hasattr(self, '_listeners') else 0,
                                     len(self._latest_consumption_cache),
                                     len(self._latest_cost_cache))
                        self.async_update_listeners()
                
                self.hass.async_create_task(_delayed_notification())
            else:
                # If no data yet, just sync for when it's created
                self._sync_cache_to_data()
                # Notify listeners manually
                self.async_update_listeners()
                _LOGGER.debug("Notified listeners (no data yet, cache synced for later)")
            
        except Exception as err:
            _LOGGER.warning("Error in batch fetch: %s", err)

    async def get_latest_consumption_value(
        self, utility_code: str, days: int = 7, measuring_point_id: int | None = None, external_key: str | None = None
    ) -> dict[str, Any] | None:
        """Get the latest consumption value for a utility code.

        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            days: Number of days to look back (default: 7)
            measuring_point_id: Optional measuring point ID to filter by specific meter
            external_key: Optional external key to filter by specific meter

        Returns a dict with 'value', 'time', 'unit', and 'utility_code',
        or None if no data is available.
        """
        # First, try to get from cache (populated by batch fetch)
        if measuring_point_id:
            cache_key = f"{utility_code}_{measuring_point_id}"
        else:
            cache_key = f"{utility_code}_all"
        
        if cache_key in self._latest_consumption_cache:
            cached = self._latest_consumption_cache[cache_key]
            _LOGGER.debug("✓ Cache HIT: consumption data for %s (measuring_point_id=%s)", utility_code, measuring_point_id)
            return cached
        
        # Cache miss - fall back to API call (for backward compatibility)
        # This should rarely happen if batch fetch is working
        _LOGGER.debug("✗ Cache MISS: consumption data for %s (measuring_point_id=%s), falling back to API", utility_code, measuring_point_id)
        try:
            # Get timezone from settings
            timezone_str = self.get_setting("TimeZoneIANA")
            if not timezone_str:
                # Fallback to UTC if timezone not available
                timezone_str = "UTC"

            try:
                tz = zoneinfo.ZoneInfo(timezone_str)
            except Exception:
                # Fallback to UTC if timezone is invalid
                _LOGGER.warning("Invalid timezone %s, using UTC", timezone_str)
                tz = zoneinfo.ZoneInfo("UTC")

            # Get current time in the configured timezone
            now_tz = datetime.now(tz)

            # Align to start of tomorrow in the timezone (to include all of today)
            tomorrow_start = datetime.combine(
                (now_tz.date() + timedelta(days=1)), datetime.min.time(), tz
            )
            to_time = int(tomorrow_start.timestamp())

            # Calculate from_time as start of day N days ago
            from_date = (now_tz.date() - timedelta(days=days))
            from_start = datetime.combine(from_date, datetime.min.time(), tz)
            from_time = int(from_start.timestamp())

            _LOGGER.debug(
                "Fetching consumption data for %s: from=%s (%s) to=%s (%s)",
                utility_code,
                from_time,
                from_start.isoformat(),
                to_time,
                tomorrow_start.isoformat(),
            )

            # Query data endpoint for consumption
            # Use measuringpointid in API call if provided for efficiency
            data = await self.api.get_data(
                node_id=self.node_id,
                from_time=from_time,
                to_time=to_time,
                interval="d",
                grouping="apartment",
                utilities=[f"{utility_code}[con]"],
                include_sub_nodes=measuring_point_id is None,  # Only include sub-nodes if not filtering by measuring point
                measuring_point_id=measuring_point_id,
            )

            if not data or not isinstance(data, list):
                return None

            # Find the latest non-null value
            # Data structure: [{"ID": ..., "Name": ..., "Result": [{"Utl": "HW", "Func": "con", "Unit": "m3", "Values": [...]}]}]
            # If measuringpointid was used, the API should have filtered the data already
            # But we still check for compatibility and in case external_key filtering is needed
            for node_data in data:
                # If measuring_point_id was provided but API didn't filter correctly, do client-side filtering
                if measuring_point_id is not None:
                    node_id = node_data.get("ID")
                    # Check if this node_data corresponds to the measuring point
                    if node_id != measuring_point_id:
                        # Try to match via installations (fallback if API filtering didn't work)
                        matched = False
                        for inst in self._installations:
                            if inst.get("MeasuringPointID") == measuring_point_id:
                                # Check if this node_data matches the installation
                                if external_key and inst.get("ExternalKey") == external_key:
                                    matched = True
                                    break
                                elif not external_key:
                                    # If no external_key provided, match by measuring point ID only
                                    matched = True
                                    break
                        if not matched:
                            continue

                results = node_data.get("Result", [])
                for result in results:
                    if result.get("Utl") == utility_code and result.get("Func") == "con":
                        values = result.get("Values", [])
                        unit = result.get("Unit", "")

                        # Find the latest non-null value (values are sorted by time)
                        for value_entry in reversed(values):
                            value = value_entry.get("Value")
                            if value is not None:
                                result_data = {
                                    "value": value,
                                    "time": value_entry.get("Time"),
                                    "unit": unit,
                                    "utility_code": utility_code,
                                }
                                # Update cache for future use
                                if measuring_point_id:
                                    cache_key = f"{utility_code}_{measuring_point_id}"
                                else:
                                    cache_key = f"{utility_code}_all"
                                self._latest_consumption_cache[cache_key] = result_data
                                self._sync_cache_to_data()  # Keep coordinator.data in sync
                                return result_data

            return None
        except Exception as err:
            _LOGGER.warning(
                "Failed to fetch latest consumption for utility %s: %s",
                utility_code,
                err,
            )
            return None

    async def _get_latest_price_data(
        self,
        utility_code: str,
        days: int = 30,
        measuring_point_id: int | None = None,
        external_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Get the latest price data from the API with request deduplication.

        Helper method that extracts common logic for fetching price data.
        Returns price data if found, None otherwise.
        
        This method deduplicates simultaneous requests for the same parameters
        to prevent multiple API calls when multiple sensors request the same data.

        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            days: Number of days to look back (default: 30 to account for API delays)
            measuring_point_id: Optional measuring point ID to filter by specific meter
            external_key: Optional external key to filter by specific meter

        Returns a dict with 'value', 'time', 'unit', and 'utility_code',
        or None if no price data is available.
        """
        try:
            # Get timezone from settings
            timezone_str = self.get_setting("TimeZoneIANA")
            if not timezone_str:
                timezone_str = "UTC"

            try:
                tz = zoneinfo.ZoneInfo(timezone_str)
            except Exception:
                _LOGGER.warning("Invalid timezone %s, using UTC", timezone_str)
                tz = zoneinfo.ZoneInfo("UTC")

            # Get current time in the configured timezone
            now_tz = datetime.now(tz)

            # Align to start of tomorrow in the timezone (to include all of today)
            tomorrow_start = datetime.combine(
                (now_tz.date() + timedelta(days=1)), datetime.min.time(), tz
            )
            to_time = int(tomorrow_start.timestamp())

            # Calculate from_time as start of day N days ago
            # Use longer lookback to account for API delays
            from_date = (now_tz.date() - timedelta(days=days))
            from_start = datetime.combine(from_date, datetime.min.time(), tz)
            from_time = int(from_start.timestamp())

            # Create cache key for request deduplication
            cache_key = f"price_{utility_code}_{from_time}_{to_time}_{measuring_point_id or 'all'}"
            
            # Use lock to prevent race condition when checking/adding pending requests
            async with self._pending_requests_lock:
                # Check if there's already a pending request for this cache key
                if cache_key in self._pending_requests:
                    pending_task = self._pending_requests[cache_key]
                    if not pending_task.done():
                        task_to_await = pending_task
                    else:
                        # Task completed, remove it
                        del self._pending_requests[cache_key]
                        task_to_await = None
                else:
                    task_to_await = None
            
            # Await outside the lock to avoid deadlock
            if task_to_await is not None:
                _LOGGER.debug(
                    "Waiting for pending price data request for %s (measuring_point_id=%s)",
                    utility_code,
                    measuring_point_id,
                )
                try:
                    data = await task_to_await
                    return data
                except Exception as err:
                    _LOGGER.debug(
                        "Pending price request failed for %s: %s",
                        utility_code,
                        err,
                    )
                    # Remove failed task if it's still the same one
                    async with self._pending_requests_lock:
                        if cache_key in self._pending_requests and self._pending_requests[cache_key] is task_to_await:
                            del self._pending_requests[cache_key]
                    # Continue to fetch below

            _LOGGER.debug(
                "Fetching price data for %s: from=%s (%s) to=%s (%s)",
                utility_code,
                from_time,
                from_start.isoformat(),
                to_time,
                tomorrow_start.isoformat(),
            )

            # Create async task for fetching
            async def _fetch_price_data() -> dict[str, Any] | None:
                try:
                    # Query data endpoint for price
                    # Use measuringpointid in API call if provided for efficiency
                    data = await self.api.get_data(
                        node_id=self.node_id,
                        from_time=from_time,
                        to_time=to_time,
                        interval="d",
                        grouping="apartment",
                        utilities=[f"{utility_code}[price]"],
                        include_sub_nodes=measuring_point_id is None,  # Only include sub-nodes if not filtering by measuring point
                        measuring_point_id=measuring_point_id,
                    )

                    # Find the latest non-null value from price data
                    # Data structure: [{"ID": ..., "Name": ..., "Result": [{"Utl": "HW", "Func": "price", "Unit": "NOK", "Values": [...]}]}]
                    # If measuringpointid was used, the API should have filtered the data already
                    # But we still check for compatibility and in case external_key filtering is needed
                    total_value = 0.0
                    latest_time = None
                    unit = ""
                    found_price_value = False

                    if data and isinstance(data, list):
                        for node_data in data:
                            # If measuring_point_id was provided but API didn't filter correctly, do client-side filtering
                            if measuring_point_id is not None:
                                node_id = node_data.get("ID")
                                # Check if this node_data corresponds to the measuring point
                                if node_id != measuring_point_id:
                                    # Try to match via installations (fallback if API filtering didn't work)
                                    matched = False
                                    for inst in self._installations:
                                        if inst.get("MeasuringPointID") == measuring_point_id:
                                            if external_key and inst.get("ExternalKey") == external_key:
                                                matched = True
                                                break
                                            elif not external_key:
                                                matched = True
                                                break
                                    if not matched:
                                        continue

                            results = node_data.get("Result", [])
                            for result in results:
                                if result.get("Utl") == utility_code and result.get("Func") == "price":
                                    values = result.get("Values", [])
                                    if not unit:
                                        unit = result.get("Unit", "")

                                    # Find the latest non-null, non-zero value (values are sorted by time)
                                    # API can be a day behind, so we look through all values to find the last one with data
                                    for value_entry in reversed(values):
                                        value = value_entry.get("Value")
                                        # Check for both None and 0, as price might be 0 if not yet calculated
                                        if value is not None and value != 0:
                                            total_value += value
                                            found_price_value = True
                                            current_time = value_entry.get("Time")
                                            if latest_time is None or (current_time is not None and current_time > latest_time):
                                                latest_time = current_time
                                            break  # Only take the latest value for this meter

                    # If we found price data, return it
                    if found_price_value:
                        _LOGGER.debug(
                            "Found price data for %s: value=%.2f, time=%s",
                            utility_code,
                            total_value,
                            latest_time,
                        )
                        return {
                            "value": total_value,
                            "time": latest_time,
                            "unit": unit,
                            "utility_code": utility_code,
                        }

                    return None
                finally:
                    # Clean up pending request
                    async with self._pending_requests_lock:
                        if cache_key in self._pending_requests and self._pending_requests[cache_key] is task:
                            del self._pending_requests[cache_key]

            # Check one more time for pending request before creating task (with lock)
            async with self._pending_requests_lock:
                if cache_key in self._pending_requests:
                    pending_task = self._pending_requests[cache_key]
                    if not pending_task.done():
                        task_to_await = pending_task
                    else:
                        # Task completed, remove it
                        del self._pending_requests[cache_key]
                        task_to_await = None
                else:
                    task_to_await = None
            
            # If we found a pending task, await it
            if task_to_await is not None:
                _LOGGER.debug(
                    "Found pending price data request for %s (measuring_point_id=%s) (late check)",
                    utility_code,
                    measuring_point_id,
                )
                try:
                    return await task_to_await
                except Exception as err:
                    _LOGGER.debug(
                        "Pending price request failed for %s: %s",
                        utility_code,
                        err,
                    )
                    async with self._pending_requests_lock:
                        if cache_key in self._pending_requests and self._pending_requests[cache_key] is task_to_await:
                            del self._pending_requests[cache_key]
                    # Continue to create new task below
            
            # Create and track the task (with lock protection)
            # We need to add it to _pending_requests BEFORE starting it to prevent races
            async with self._pending_requests_lock:
                # Final check - did another request create a task while we were waiting?
                if cache_key in self._pending_requests:
                    pending_task = self._pending_requests[cache_key]
                    if not pending_task.done():
                        # Another task exists, use that one
                        task = pending_task
                    else:
                        # Task completed, remove it and create new one
                        del self._pending_requests[cache_key]
                        task = asyncio.create_task(_fetch_price_data())
                        self._pending_requests[cache_key] = task
                else:
                    # No pending task, create and add it
                    task = asyncio.create_task(_fetch_price_data())
                    self._pending_requests[cache_key] = task
            
            try:
                return await task
            except Exception as err:
                # Clean up on error
                async with self._pending_requests_lock:
                    if cache_key in self._pending_requests and self._pending_requests[cache_key] is task:
                        del self._pending_requests[cache_key]
                raise
            finally:
                # Clean up pending request if still there and it's done
                async with self._pending_requests_lock:
                    if cache_key in self._pending_requests and self._pending_requests[cache_key].done():
                        if self._pending_requests[cache_key] is task:
                            del self._pending_requests[cache_key]

        except Exception as err:
            _LOGGER.warning(
                "Failed to fetch price data for utility %s: %s",
                utility_code,
                err,
            )
            return None

    async def get_latest_metered_cost(
        self,
        utility_code: str,
        days: int = 30,
        measuring_point_id: int | None = None,
        external_key: str | None = None,
    ) -> dict[str, Any] | None:
        # First, try to get from cache (populated by batch fetch)
        if measuring_point_id:
            cache_key = f"{utility_code}_{measuring_point_id}_metered"
        else:
            cache_key = f"{utility_code}_all_metered"
        
        if cache_key in self._latest_cost_cache:
            cached = self._latest_cost_cache[cache_key]
            _LOGGER.debug("✓ Cache HIT: metered cost data for %s (measuring_point_id=%s)", utility_code, measuring_point_id)
            return cached
        
        # Cache miss - fall back to API call (for backward compatibility)
        _LOGGER.debug("✗ Cache MISS: metered cost data for %s (measuring_point_id=%s), falling back to API", utility_code, measuring_point_id)
        price_data = await self._get_latest_price_data(
            utility_code=utility_code,
            days=days,
            measuring_point_id=measuring_point_id,
            external_key=external_key,
        )

        if price_data:
            price_data["cost_type"] = "actual"
            # Update cache for future use
            if measuring_point_id:
                cache_key = f"{utility_code}_{measuring_point_id}_metered"
            else:
                cache_key = f"{utility_code}_all_metered"
            self._latest_cost_cache[cache_key] = price_data
            self._sync_cache_to_data()  # Keep coordinator.data in sync
            return price_data

        _LOGGER.debug(
            "No metered price data found for %s, returning None",
            utility_code,
        )
        return None

    async def get_latest_estimated_cost(
        self,
        utility_code: str,
        days: int = 30,
        measuring_point_id: int | None = None,
        external_key: str | None = None,
    ) -> dict[str, Any] | None:
        # For estimated cost, we can calculate from cached consumption + rate
        # First try to get consumption from cache
        if measuring_point_id:
            consumption_cache_key = f"{utility_code}_{measuring_point_id}"
        else:
            consumption_cache_key = f"{utility_code}_all"
        
        """Get the latest estimated cost value.

        First attempts to get actual price data from the API. If no price data is available,
        calculates cost from consumption * rate. This is useful for utilities like HW where
        price data might not be available in the daily API.

        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            days: Number of days to look back (default: 30 to account for API delays)
            measuring_point_id: Optional measuring point ID to filter by specific meter
            external_key: Optional external key to filter by specific meter

        Returns a dict with 'value', 'time', 'unit', 'utility_code', and 'cost_type',
        or None if no data is available.
        """
        # For estimated costs, skip API price data check and calculate directly
        # "Estimated" means we calculate from consumption * rate (or spot prices for HW),
        # not fetch from API (which would be "actual" data)
        # This avoids blocking on API requests that may never complete
        _LOGGER.debug(
            "Calculating estimated cost for %s from consumption (spot prices for HW, rate for others)",
            utility_code,
        )

        # Try to get consumption from cache first
        if measuring_point_id:
            consumption_cache_key = f"{utility_code}_{measuring_point_id}"
        else:
            consumption_cache_key = f"{utility_code}_all"
        
        consumption_data = self._latest_consumption_cache.get(consumption_cache_key)
        
        # If not in cache, fetch it
        if not consumption_data:
            consumption_data = await self.get_latest_consumption_value(
                utility_code=utility_code,
                days=days,
                measuring_point_id=measuring_point_id,
                external_key=external_key,
            )

        try:
            if not consumption_data:
                _LOGGER.debug("No consumption data available for %s", utility_code)
                return None

            consumption = consumption_data.get("value")
            if consumption is None or consumption <= 0:
                _LOGGER.debug("Consumption value is None or <= 0 for %s", utility_code)
                return None

            # Get timezone from settings for rate calculation
            timezone_str = self.get_setting("TimeZoneIANA")
            if not timezone_str:
                timezone_str = "UTC"

            try:
                tz = zoneinfo.ZoneInfo(timezone_str)
            except Exception:
                _LOGGER.warning("Invalid timezone %s, using UTC", timezone_str)
                tz = zoneinfo.ZoneInfo("UTC")

            # Get date from consumption data
            consumption_time = consumption_data.get("time")
            if consumption_time:
                consumption_date = datetime.fromtimestamp(consumption_time, tz=tz)
                year = consumption_date.year
                month = consumption_date.month
            else:
                # Fallback to current month if no timestamp
                now = datetime.now(tz)
                year = now.year
                month = now.month

            # For HW, use spot prices and calibration (more accurate than simple rate)
            if utility_code == "HW":
                # Create cache key for this calculation to deduplicate simultaneous requests
                hw_cost_cache_key = f"hw_estimated_cost_{consumption:.3f}_{year}_{month}_{measuring_point_id or 'all'}"
                
                # Use lock to prevent race condition when checking/adding pending requests
                async with self._pending_requests_lock:
                    # Check if there's already a pending request for this calculation
                    if hw_cost_cache_key in self._pending_requests:
                        pending_task = self._pending_requests[hw_cost_cache_key]
                        if not pending_task.done():
                            task_to_await = pending_task
                        else:
                            # Task completed, remove it
                            del self._pending_requests[hw_cost_cache_key]
                            task_to_await = None
                    else:
                        task_to_await = None
                
                # Await outside the lock to avoid deadlock
                if task_to_await is not None:
                    _LOGGER.debug(
                        "Waiting for pending HW estimated cost calculation: consumption=%.3f m3, year=%d, month=%d",
                        consumption, year, month
                    )
                    try:
                        result = await task_to_await
                        return result
                    except Exception as err:
                        _LOGGER.debug(
                            "Pending HW cost calculation failed: %s",
                            err,
                        )
                        # Remove failed task if it's still the same one
                        async with self._pending_requests_lock:
                            if hw_cost_cache_key in self._pending_requests and self._pending_requests[hw_cost_cache_key] is task_to_await:
                                del self._pending_requests[hw_cost_cache_key]
                        # Continue to calculate below

                # No pending task, create a new one
                _LOGGER.debug("Calculating HW estimated cost: consumption=%.3f m3, year=%d, month=%d, measuring_point_id=%s", 
                             consumption, year, month, measuring_point_id)
                
                # Create async task for calculation
                async def _calculate_hw_estimated_cost() -> dict[str, Any] | None:
                    # Try to get CW price and consumption for more accurate calculation
                    cw_price = None
                    cw_consumption = None
                    
                    # Try to get CW price from cache
                    cw_price_cache_key = "CW_all_metered"  # Use aggregate cache key
                    coordinator_data = self.data
                    if coordinator_data:
                        cost_cache = coordinator_data.get("latest_cost_cache", {})
                        cw_price_data = cost_cache.get(cw_price_cache_key)
                        if cw_price_data:
                            cw_price = cw_price_data.get("value")
                            _LOGGER.debug("Got CW price from cache: %.2f NOK", cw_price)
                    
                    # Try to get CW consumption from cache
                    cw_consumption_cache_key = "CW_all"
                    cw_consumption_data = self._latest_consumption_cache.get(cw_consumption_cache_key)
                    if cw_consumption_data:
                        cw_consumption = cw_consumption_data.get("value")
                        _LOGGER.debug("Got CW consumption from cache: %.3f m3", cw_consumption)
                    
                    # Calculate HW price using spot prices
                    hw_price_data = await self._get_hw_price_from_spot_prices(
                        consumption=consumption,
                        year=year,
                        month=month,
                        cold_water_price=cw_price,
                        cold_water_consumption=cw_consumption,
                    )
                    
                    if hw_price_data:
                        daily_cost = hw_price_data.get("value")
                        _LOGGER.info("Calculated HW daily estimated cost: %.2f NOK (consumption: %.3f m3, year: %d, month: %d)", 
                                    daily_cost, consumption, year, month)
                        # Convert to daily cost format
                        return {
                            "value": daily_cost,
                            "time": consumption_time,
                            "unit": hw_price_data.get("unit") or self.get_setting("Currency") or "NOK",
                            "utility_code": utility_code,
                            "cost_type": "estimated",
                        }
                    else:
                        _LOGGER.debug("Spot price calculation failed for HW, falling back to billing rate")
                        return None
                
                # Create and track the task (with lock protection)
                task = asyncio.create_task(_calculate_hw_estimated_cost())
                async with self._pending_requests_lock:
                    # Double-check that no other task was created while we were creating this one
                    if hw_cost_cache_key not in self._pending_requests:
                        self._pending_requests[hw_cost_cache_key] = task
                    else:
                        # Another task was created, await that one instead
                        task.cancel()
                        task = self._pending_requests[hw_cost_cache_key]
                
                try:
                    result = await task
                    if result:
                        return result
                except asyncio.CancelledError:
                    # Task was cancelled because another one was created first
                    # Await the other task instead
                    async with self._pending_requests_lock:
                        if hw_cost_cache_key in self._pending_requests:
                            task = self._pending_requests[hw_cost_cache_key]
                        else:
                            raise
                    result = await task
                    if result:
                        return result
                except Exception as err:
                    # Clean up on error
                    async with self._pending_requests_lock:
                        if hw_cost_cache_key in self._pending_requests and self._pending_requests[hw_cost_cache_key] is task:
                            del self._pending_requests[hw_cost_cache_key]
                    raise
                finally:
                    # Clean up pending request if still there and it's done
                    async with self._pending_requests_lock:
                        if hw_cost_cache_key in self._pending_requests and self._pending_requests[hw_cost_cache_key].done():
                            if self._pending_requests[hw_cost_cache_key] is task:
                                del self._pending_requests[hw_cost_cache_key]

            # For non-HW or if spot price calculation failed, use billing rate
            rate = await self.billing_manager.get_rate_from_billing(utility_code, year, month)

            if rate is None:
                _LOGGER.debug("No rate found for %s", utility_code)
                return None

            # Calculate cost from consumption * rate
            calculated_cost = consumption * rate
            currency = self.get_setting("Currency") or "NOK"

            _LOGGER.debug(
                "Calculated estimated cost for %s: %.2f m3 * %.2f = %.2f %s",
                utility_code,
                consumption,
                rate,
                calculated_cost,
                currency,
            )

            return {
                "value": calculated_cost,
                "time": consumption_time,
                "unit": currency,
                "utility_code": utility_code,
                "cost_type": "estimated",
            }

        except Exception as err:
            _LOGGER.warning(
                "Failed to calculate estimated cost for utility %s: %s",
                utility_code,
                err,
            )
            return None

    def get_active_installations(self) -> list[dict[str, Any]]:
        """Get list of active installations (where To is null)."""
        return [
            inst
            for inst in self._installations
            if inst.get("To") is None
        ]

    def _get_month_timestamps(self, year: int, month: int) -> tuple[int, int]:
        """Get start and end timestamps for a month.
        
        Args:
            year: Year
            month: Month (1-12)
            
        Returns:
            Tuple of (from_time, to_time) as Unix timestamps
        """
        timezone_str = self.get_setting("TimeZoneIANA") or "UTC"
        tz = get_timezone(timezone_str)
        return get_month_timestamps(year, month, tz)

    async def _calculate_monthly_price_from_daily_cache(
        self, utility_code: str, year: int, month: int
    ) -> dict[str, Any] | None:
        """Calculate monthly price from cached daily prices.
        
        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            year: Year
            month: Month (1-12)
            
        Returns:
            Dict with price data or None if not available
        """
        from_time, to_time = self._get_month_timestamps(year, month)
        
        # Try to calculate from cached daily prices (aggregate across all meters)
        total_price = 0.0
        has_cached_data = False
        
        # Sum prices from all meters for this utility
        for cache_key_price, daily_prices in self._daily_price_cache.items():
            if cache_key_price.startswith(f"{utility_code}_") and cache_key_price.endswith("_metered"):
                # Filter daily prices for this month
                month_prices = [
                    p for p in daily_prices
                    if from_time <= p["time"] < to_time and p.get("value") is not None and p.get("value", 0) > 0
                ]
                if month_prices:
                    # Sum prices for this meter
                    meter_total = sum(p["value"] for p in month_prices)
                    total_price += meter_total
                    has_cached_data = True
        
        if has_cached_data:
            currency = self.get_setting("Currency") or ""
            _LOGGER.info(
                "✓ Smart reuse: Calculated monthly price for %s %d-%02d from cached daily prices (no API call!)",
                utility_code, year, month
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
        """Fetch monthly price from API with request deduplication.
        
        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            year: Year
            month: Month (1-12)
            
        Returns:
            Dict with price data or None if not available
        """
        from_time, to_time = self._get_month_timestamps(year, month)
        
        # Defer API calls during startup
        if self.hass.state == CoreState.starting:
            _LOGGER.debug(
                "Deferring API call for monthly price aggregate %s %d-%02d (HA is starting)",
                utility_code, year, month
            )
            return None

        # Create cache key for this request
        api_cache_key = f"data_{self.node_id}_{from_time}_{to_time}_{utility_code}_price"

        # Check API request cache first
        data = None
        if api_cache_key in self._data_request_cache:
            cached_data, cache_timestamp = self._data_request_cache[api_cache_key]
            age = time.time() - cache_timestamp
            if age < self._data_cache_ttl:
                _LOGGER.debug(
                    "Using cached API data for %s price %d-%02d (age: %.1f seconds)",
                    utility_code,
                    year,
                    month,
                    age,
                )
                data = cached_data
            else:
                # Cache expired, remove it
                del self._data_request_cache[api_cache_key]

        # Use lock to prevent race condition when checking/adding pending requests
        async with self._pending_requests_lock:
            # Check if there's already a pending request for this data
            if data is None and api_cache_key in self._pending_requests:
                pending_task = self._pending_requests[api_cache_key]
                if not pending_task.done():
                    task_to_await = pending_task
                else:
                    # Task completed, remove it
                    del self._pending_requests[api_cache_key]
                    task_to_await = None
            else:
                task_to_await = None
        
        # Await outside the lock to avoid deadlock
        if task_to_await is not None:
            _LOGGER.debug(
                "Waiting for pending price API request for %s %d-%02d",
                utility_code,
                year,
                month,
            )
            try:
                data = await task_to_await
            except Exception as err:
                _LOGGER.warning(
                    "Pending price API request failed for %s %d-%02d: %s",
                    utility_code,
                    year,
                    month,
                    err,
                )
                # Remove failed pending request if it's still the same one
                async with self._pending_requests_lock:
                    if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_to_await:
                        del self._pending_requests[api_cache_key]
                data = None

        # If no cached data and no pending request, make the API call
        if data is None:
            # Check one more time for pending request before creating task (with lock)
            async with self._pending_requests_lock:
                if api_cache_key in self._pending_requests:
                    pending_task = self._pending_requests[api_cache_key]
                    if not pending_task.done():
                        task_to_await = pending_task
                    else:
                        # Task completed, remove it
                        del self._pending_requests[api_cache_key]
                        task_to_await = None
                else:
                    task_to_await = None
            
            # If we found a pending task, await it
            if task_to_await is not None:
                _LOGGER.debug(
                    "Found pending price API request for %s %d-%02d (late check)",
                    utility_code,
                    year,
                    month,
                )
                try:
                    data = await task_to_await
                except Exception as err:
                    _LOGGER.warning(
                        "Pending price API request failed for %s %d-%02d: %s",
                        utility_code,
                        year,
                        month,
                        err,
                    )
                    async with self._pending_requests_lock:
                        if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_to_await:
                            del self._pending_requests[api_cache_key]
                    data = None
            
            # Only log and create task if we're actually going to make an API call
            if data is None:
                _LOGGER.debug(
                    "Fetching monthly price for %s %d-%02d from API (cache miss): from=%s to=%s",
                    utility_code,
                    year,
                    month,
                    from_time,
                    to_time,
                )
            
            # If still no data, create a new task
            if data is None:
                # Create a task for this request
                # We'll capture the task reference to use in cleanup
                task_ref = None
                
                async def fetch_price_data():
                    nonlocal task_ref
                    try:
                        result = await self.api.get_data(
                            node_id=self.node_id,
                            from_time=from_time,
                            to_time=to_time,
                            interval="d",
                            grouping="apartment",
                            utilities=[f"{utility_code}[price]"],
                            include_sub_nodes=True,
                        )
                        # Cache the result
                        if result:
                            self._data_request_cache[api_cache_key] = (result, time.time())
                        return result
                    finally:
                        # Remove from pending requests when done
                        async with self._pending_requests_lock:
                            if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_ref:
                                del self._pending_requests[api_cache_key]

                # Create and track the task (with lock protection)
                # We need to add it to _pending_requests BEFORE starting it to prevent races
                async with self._pending_requests_lock:
                    # Final check - did another request create a task while we were waiting?
                    if api_cache_key in self._pending_requests:
                        pending_task = self._pending_requests[api_cache_key]
                        if not pending_task.done():
                            # Another task exists, use that one
                            task = pending_task
                        else:
                            # Task completed, remove it and create new one
                            del self._pending_requests[api_cache_key]
                            task = asyncio.create_task(fetch_price_data())
                            task_ref = task
                            # Add to dict immediately after creation (still inside lock)
                            self._pending_requests[api_cache_key] = task
                    else:
                        # No pending task, create and add it (all inside lock for atomicity)
                        task = asyncio.create_task(fetch_price_data())
                        task_ref = task
                        # Add to dict immediately after creation (still inside lock)
                        self._pending_requests[api_cache_key] = task

                # Wait for the result (outside lock to avoid deadlock)
                try:
                    data = await task
                except Exception as err:
                    # Clean up on error
                    async with self._pending_requests_lock:
                        if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task:
                            del self._pending_requests[api_cache_key]
                    raise

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
            currency = self.get_setting("Currency") or ""
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
        """Get monthly actual price aggregate.
        
        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            year: Year
            month: Month (1-12)
            cache_key: Cache key for storing result
            
        Returns:
            Dict with price data or None if not available
        """
        # Try to calculate from cached daily prices first
        result = await self._calculate_monthly_price_from_daily_cache(utility_code, year, month)
        if result:
            self._monthly_aggregate_cache[cache_key] = result
            self._sync_cache_to_data()
            return result
        
        _LOGGER.debug(
            "Daily price cache exists but no values for %s %d-%02d (date range mismatch?)",
            utility_code, year, month
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
        """Get monthly price for CW utility.
        
        Args:
            utility_code: Utility code (should be "CW")
            year: Year
            month: Month (1-12)
            cost_type: Cost type ("actual" or "estimated")
            
        Returns:
            Dict with price data or None if not available
        """
        if utility_code != "CW":
            return None
        
        from_time, to_time = self._get_month_timestamps(year, month)
        
        # Defer API calls during startup
        if self.hass.state == CoreState.starting:
            _LOGGER.debug(
                "Deferring API call for CW price %d-%02d (HA is starting)",
                year, month
            )
            return None
        
        # Create cache key for this request
        api_cache_key = f"data_{self.node_id}_{from_time}_{to_time}_{utility_code}_price"
        
        # Check API request cache first
        data = None
        if api_cache_key in self._data_request_cache:
            cached_data, cache_timestamp = self._data_request_cache[api_cache_key]
            age = time.time() - cache_timestamp
            if age < self._data_cache_ttl:
                _LOGGER.debug(
                    "Using cached API data for CW price %d-%02d (age: %.1f seconds)",
                    year,
                    month,
                    age,
                )
                data = cached_data
            else:
                # Cache expired, remove it
                del self._data_request_cache[api_cache_key]
        
        # Use lock to prevent race condition when checking/adding pending requests
        async with self._pending_requests_lock:
            # Check if there's already a pending request for this data
            if data is None and api_cache_key in self._pending_requests:
                pending_task = self._pending_requests[api_cache_key]
                if not pending_task.done():
                    task_to_await = pending_task
                else:
                    # Task completed, remove it
                    del self._pending_requests[api_cache_key]
                    task_to_await = None
            else:
                task_to_await = None
        
        # Await outside the lock to avoid deadlock
        if task_to_await is not None:
            _LOGGER.debug(
                "Waiting for pending CW price API request for %d-%02d",
                year,
                month,
            )
            try:
                data = await task_to_await
            except Exception as err:
                _LOGGER.warning(
                    "Pending CW price API request failed for %d-%02d: %s",
                    year,
                    month,
                    err,
                )
                # Remove failed pending request if it's still the same one
                async with self._pending_requests_lock:
                    if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_to_await:
                        del self._pending_requests[api_cache_key]
                data = None
        
        # If no cached data and no pending request, make the API call
        if data is None:
            # Check one more time for pending request before creating task (with lock)
            async with self._pending_requests_lock:
                if api_cache_key in self._pending_requests:
                    pending_task = self._pending_requests[api_cache_key]
                    if not pending_task.done():
                        task_to_await = pending_task
                    else:
                        # Task completed, remove it
                        del self._pending_requests[api_cache_key]
                        task_to_await = None
                else:
                    task_to_await = None
            
            # If we found a pending task, await it
            if task_to_await is not None:
                _LOGGER.debug(
                    "Found pending CW price API request for %d-%02d (late check)",
                    year,
                    month,
                )
                try:
                    data = await task_to_await
                except Exception as err:
                    _LOGGER.warning(
                        "Pending CW price API request failed for %d-%02d: %s",
                        year,
                        month,
                        err,
                    )
                    async with self._pending_requests_lock:
                        if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_to_await:
                            del self._pending_requests[api_cache_key]
                    data = None
            
            # If still no data, create a new task
            if data is None:
                # Only log when we're actually going to make an API call
                _LOGGER.debug(
                    "Fetching CW price for %d-%02d from API (cache miss): from=%s to=%s",
                    year,
                    month,
                    from_time,
                    to_time,
                )
                # Create a task for this request
                # We'll capture the task reference to use in cleanup
                task_ref = None
                
                async def fetch_cw_price_data():
                    nonlocal task_ref
                    try:
                        result = await self.api.get_data(
                            node_id=self.node_id,
                            from_time=from_time,
                            to_time=to_time,
                            interval="d",
                            grouping="apartment",
                            utilities=[f"{utility_code}[price]"],
                            include_sub_nodes=True,
                        )
                        # Cache the result
                        if result:
                            self._data_request_cache[api_cache_key] = (result, time.time())
                        return result
                    finally:
                        # Remove from pending requests when done
                        async with self._pending_requests_lock:
                            if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_ref:
                                del self._pending_requests[api_cache_key]
                
                # Create and track the task (with lock protection)
                # We need to add it to _pending_requests BEFORE starting it to prevent races
                async with self._pending_requests_lock:
                    # Final check - did another request create a task while we were waiting?
                    if api_cache_key in self._pending_requests:
                        pending_task = self._pending_requests[api_cache_key]
                        if not pending_task.done():
                            # Another task exists, use that one
                            task = pending_task
                        else:
                            # Task completed, remove it and create new one
                            del self._pending_requests[api_cache_key]
                            task = asyncio.create_task(fetch_cw_price_data())
                            task_ref = task
                            # Add to dict immediately after creation (still inside lock)
                            self._pending_requests[api_cache_key] = task
                    else:
                        # No pending task, create and add it (all inside lock for atomicity)
                        task = asyncio.create_task(fetch_cw_price_data())
                        task_ref = task
                        # Add to dict immediately after creation (still inside lock)
                        self._pending_requests[api_cache_key] = task

                # Wait for the result (outside lock to avoid deadlock)
                try:
                    data = await task
                except Exception as err:
                    # Clean up on error
                    async with self._pending_requests_lock:
                        if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task:
                            del self._pending_requests[api_cache_key]
                    raise
        
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
            currency = self.get_setting("Currency") or "NOK"
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
        """Get monthly estimated price for HW utility.
        
        Args:
            year: Year
            month: Month (1-12)
            
        Returns:
            Dict with price data or None if not available
        """
        from_time, to_time = self._get_month_timestamps(year, month)
        
        # Defer API calls during startup
        if self.hass.state == CoreState.starting:
            _LOGGER.debug(
                "Deferring API call for HW price check %d-%02d (HA is starting)",
                year, month
            )
            return None
        
        # First check if we have actual price data from API
        # Use the same deduplication pattern as other price fetches
        api_cache_key = f"data_{self.node_id}_{from_time}_{to_time}_HW_price"
        
        # Check API request cache first
        data = None
        if api_cache_key in self._data_request_cache:
            cached_data, cache_timestamp = self._data_request_cache[api_cache_key]
            age = time.time() - cache_timestamp
            if age < self._data_cache_ttl:
                _LOGGER.debug(
                    "Using cached API data for HW price check %d-%02d (age: %.1f seconds)",
                    year,
                    month,
                    age,
                )
                data = cached_data
        
        # Use lock to prevent race condition when checking/adding pending requests
        async with self._pending_requests_lock:
            # Check if there's already a pending request for this data
            if data is None and api_cache_key in self._pending_requests:
                pending_task = self._pending_requests[api_cache_key]
                if not pending_task.done():
                    task_to_await = pending_task
                else:
                    # Task completed, remove it
                    del self._pending_requests[api_cache_key]
                    task_to_await = None
            else:
                task_to_await = None
        
        # Await outside the lock to avoid deadlock
        if task_to_await is not None:
            _LOGGER.debug(
                "Waiting for pending HW price check API request for %d-%02d",
                year,
                month,
            )
            try:
                data = await task_to_await
            except Exception as err:
                _LOGGER.warning(
                    "Pending HW price check API request failed for %d-%02d: %s",
                    year,
                    month,
                    err,
                )
                # Remove failed pending request if it's still the same one
                async with self._pending_requests_lock:
                    if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_to_await:
                        del self._pending_requests[api_cache_key]
                data = None
        
        # If no cached data and no pending request, make the API call
        if data is None:
            # Check one more time for pending request before creating task (with lock)
            async with self._pending_requests_lock:
                if api_cache_key in self._pending_requests:
                    pending_task = self._pending_requests[api_cache_key]
                    if not pending_task.done():
                        task_to_await = pending_task
                    else:
                        # Task completed, remove it
                        del self._pending_requests[api_cache_key]
                        task_to_await = None
                else:
                    task_to_await = None
            
            # If we found a pending task, await it
            if task_to_await is not None:
                _LOGGER.debug(
                    "Found pending HW price check API request for %d-%02d (late check)",
                    year,
                    month,
                )
                try:
                    data = await task_to_await
                except Exception as err:
                    _LOGGER.warning(
                        "Pending HW price check API request failed for %d-%02d: %s",
                        year,
                        month,
                        err,
                    )
                    async with self._pending_requests_lock:
                        if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_to_await:
                            del self._pending_requests[api_cache_key]
                    data = None
            
            # If still no data, create a new task
            if data is None:
                # Create a task for this request
                # We'll capture the task reference to use in cleanup
                task_ref = None
                
                async def fetch_hw_price_check_data():
                    nonlocal task_ref
                    try:
                        result = await self.api.get_data(
                            node_id=self.node_id,
                            from_time=from_time,
                            to_time=to_time,
                            interval="d",
                            grouping="apartment",
                            utilities=["HW[price]"],
                            include_sub_nodes=True,
                        )
                        # Cache the result
                        if result:
                            self._data_request_cache[api_cache_key] = (result, time.time())
                        return result
                    finally:
                        # Remove from pending requests when done
                        async with self._pending_requests_lock:
                            if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_ref:
                                del self._pending_requests[api_cache_key]
                
                # Create and track the task (with lock protection)
                # We need to add it to _pending_requests BEFORE starting it to prevent races
                async with self._pending_requests_lock:
                    # Final check - did another request create a task while we were waiting?
                    if api_cache_key in self._pending_requests:
                        pending_task = self._pending_requests[api_cache_key]
                        if not pending_task.done():
                            # Another task exists, use that one
                            task = pending_task
                        else:
                            # Task completed, remove it and create new one
                            del self._pending_requests[api_cache_key]
                            task = asyncio.create_task(fetch_hw_price_check_data())
                            task_ref = task
                            # Add to dict immediately after creation (still inside lock)
                            self._pending_requests[api_cache_key] = task
                    else:
                        # No pending task, create and add it (all inside lock for atomicity)
                        task = asyncio.create_task(fetch_hw_price_check_data())
                        task_ref = task
                        # Add to dict immediately after creation (still inside lock)
                        self._pending_requests[api_cache_key] = task

                # Wait for the result (outside lock to avoid deadlock)
                try:
                    data = await task
                except Exception as err:
                    _LOGGER.debug(
                        "Failed to check for actual HW price data: %s",
                        err,
                    )
                    # Clean up on error
                    async with self._pending_requests_lock:
                        if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task:
                            del self._pending_requests[api_cache_key]
                    data = None

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
            currency = self.get_setting("Currency") or ""
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
        hw_consumption_data = await self.get_monthly_aggregate(
            utility_code="HW",
            year=year,
            month=month,
            aggregate_type="con",
        )

        if hw_consumption_data:
            hw_consumption = hw_consumption_data.get("value")
            if hw_consumption and hw_consumption > 0:
                # Get CW price and consumption for the estimation
                cw_price_data = await self.get_monthly_aggregate(
                    utility_code="CW",
                    year=year,
                    month=month,
                    aggregate_type="price",
                    cost_type="actual",
                )
                cw_consumption_data = await self.get_monthly_aggregate(
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
                    hw_estimated_data["cost_type"] = "estimated"
                    hw_estimated_data["is_estimated"] = True
                    return hw_estimated_data

        return None

    async def _calculate_monthly_consumption_from_daily_cache(
        self, utility_code: str, year: int, month: int, cache_key: str
    ) -> dict[str, Any] | None:
        """Calculate monthly consumption from cached daily consumption data.
        
        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            year: Year
            month: Month (1-12)
            cache_key: Cache key for storing result
            
        Returns:
            Dict with consumption data or None if not available
        """
        cache_key_daily = f"{utility_code}_all"  # Use aggregate cache key
        daily_values = self._daily_consumption_cache.get(cache_key_daily)
        
        if not daily_values:
            return None
        
        from_time, to_time = self._get_month_timestamps(year, month)
        
        # Filter daily values for this month
        month_values = [
            v for v in daily_values
            if from_time <= v["time"] < to_time and v.get("value") is not None
        ]
        
        if not month_values:
            return None
        
        # Sum all values for the month
        total_value = sum(v["value"] for v in month_values)
        unit = month_values[0].get("unit", "") if month_values else ""
        
        _LOGGER.debug(
            "Calculated monthly consumption for %s %d-%02d from %d cached daily values (reused data!)",
            utility_code, year, month, len(month_values)
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
        self, utility_code: str, year: int, month: int, aggregate_type: str, cache_key: str
    ) -> dict[str, Any] | None:
        """Fetch monthly consumption from API.
        
        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            year: Year
            month: Month (1-12)
            aggregate_type: Aggregate type ("con" for consumption)
            cache_key: Cache key for storing result
            
        Returns:
            Dict with consumption data or None if not available
        """
        from_time, to_time = self._get_month_timestamps(year, month)
        
        # Defer API calls during startup
        if self.hass.state == CoreState.starting:
            _LOGGER.debug(
                "Deferring API call for monthly aggregate %s[%s] %d-%02d (HA is starting)",
                utility_code, aggregate_type, year, month
            )
            return None

        # Query data endpoint for the month
        utilities = [f"{utility_code}[{aggregate_type}]"]

        # Create cache key for this request
        api_cache_key = f"data_{self.node_id}_{from_time}_{to_time}_{utility_code}_{aggregate_type}"

        # Check API request cache first
        data = None
        if api_cache_key in self._data_request_cache:
            cached_data, cache_timestamp = self._data_request_cache[api_cache_key]
            age = time.time() - cache_timestamp
            if age < self._data_cache_ttl:
                _LOGGER.debug(
                    "Using cached API data for %s[%s] %d-%02d (age: %.1f seconds)",
                    utility_code,
                    aggregate_type,
                    year,
                    month,
                    age,
                )
                data = cached_data
            else:
                # Cache expired, remove it
                del self._data_request_cache[api_cache_key]

        # Use lock to prevent race condition when checking/adding pending requests
        async with self._pending_requests_lock:
            # Check if there's already a pending request for this data
            if data is None and api_cache_key in self._pending_requests:
                pending_task = self._pending_requests[api_cache_key]
                if not pending_task.done():
                    task_to_await = pending_task
                else:
                    # Task completed, remove it
                    del self._pending_requests[api_cache_key]
                    task_to_await = None
            else:
                task_to_await = None
        
        # Await outside the lock to avoid deadlock
        if task_to_await is not None:
            _LOGGER.debug(
                "Waiting for pending request for %s[%s] %d-%02d",
                utility_code,
                aggregate_type,
                year,
                month,
            )
            try:
                data = await task_to_await
            except Exception as err:
                _LOGGER.warning(
                    "Pending request failed for %s[%s] %d-%02d: %s",
                    utility_code,
                    aggregate_type,
                    year,
                    month,
                    err,
                )
                # Remove failed pending request if it's still the same one
                async with self._pending_requests_lock:
                    if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_to_await:
                        del self._pending_requests[api_cache_key]
                data = None

        # If no cached data and no pending request, make the API call
        if data is None:
            # Check one more time for pending request before creating task (with lock)
            async with self._pending_requests_lock:
                if api_cache_key in self._pending_requests:
                    pending_task = self._pending_requests[api_cache_key]
                    if not pending_task.done():
                        task_to_await = pending_task
                    else:
                        # Task completed, remove it
                        del self._pending_requests[api_cache_key]
                        task_to_await = None
                else:
                    task_to_await = None
            
            # If we found a pending task, await it
            if task_to_await is not None:
                _LOGGER.debug(
                    "Found pending request for %s[%s] %d-%02d (late check)",
                    utility_code,
                    aggregate_type,
                    year,
                    month,
                )
                try:
                    data = await task_to_await
                except Exception as err:
                    _LOGGER.warning(
                        "Pending request failed for %s[%s] %d-%02d: %s",
                        utility_code,
                        aggregate_type,
                        year,
                        month,
                        err,
                    )
                    async with self._pending_requests_lock:
                        if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_to_await:
                            del self._pending_requests[api_cache_key]
                    data = None
            
            # If still no data, create a new task
            if data is None:
                # Only log when we're actually going to make an API call
                _LOGGER.debug(
                    "Fetching monthly aggregate for %s[%s] %d-%02d from API (cache miss): from=%s to=%s",
                    utility_code,
                    aggregate_type,
                    year,
                    month,
                    from_time,
                    to_time,
                )
                # Create a task for this request
                # We'll capture the task reference to use in cleanup
                task_ref = None
                
                async def fetch_data():
                    nonlocal task_ref
                    try:
                        result = await self.api.get_data(
                            node_id=self.node_id,
                            from_time=from_time,
                            to_time=to_time,
                            interval="d",
                            grouping="apartment",
                            utilities=utilities,
                            include_sub_nodes=True,
                        )
                        # Cache the result
                        if result:
                            self._data_request_cache[api_cache_key] = (result, time.time())
                        return result
                    finally:
                        # Remove from pending requests when done
                        async with self._pending_requests_lock:
                            if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_ref:
                                del self._pending_requests[api_cache_key]
                
                # Create and track the task (with lock protection)
                # We need to add it to _pending_requests BEFORE starting it to prevent races
                async with self._pending_requests_lock:
                    # Final check - did another request create a task while we were waiting?
                    if api_cache_key in self._pending_requests:
                        pending_task = self._pending_requests[api_cache_key]
                        if not pending_task.done():
                            # Another task exists, use that one
                            task = pending_task
                        else:
                            # Task completed, remove it and create new one
                            del self._pending_requests[api_cache_key]
                            task = asyncio.create_task(fetch_data())
                            task_ref = task
                            # Add to dict immediately after creation (still inside lock)
                            self._pending_requests[api_cache_key] = task
                    else:
                        # No pending task, create and add it (all inside lock for atomicity)
                        task = asyncio.create_task(fetch_data())
                        task_ref = task
                        # Add to dict immediately after creation (still inside lock)
                        self._pending_requests[api_cache_key] = task

                # Wait for the result (outside lock to avoid deadlock)
                try:
                    data = await task
                except Exception as err:
                    # Clean up on error
                    async with self._pending_requests_lock:
                        if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task:
                            del self._pending_requests[api_cache_key]
                    raise

        if not data or not isinstance(data, list):
            return None

        # Aggregate values across all nodes and days
        total_value = 0.0
        unit = ""
        has_data = False

        for node_data in data:
            results = node_data.get("Result", [])
            for result in results:
                if result.get("Utl") == utility_code and result.get("Func") == aggregate_type:
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

    async def get_monthly_aggregate(
        self,
        utility_code: str,
        year: int,
        month: int,
        aggregate_type: str = "con",
        cost_type: str = "actual",
    ) -> dict[str, Any] | None:
        """Get monthly aggregate for consumption or price.
        
        First checks the monthly aggregate cache. If not found, makes API call and caches result.

        Args:
            utility_code: Utility code (e.g., "HW", "CW")
            year: Year (e.g., 2025)
            month: Month (1-12)
            aggregate_type: "con" for consumption, "price" for price
            cost_type: "actual" for metered API data, "estimated" for estimated (falls back to metered if available)

        Returns:
            Dict with 'value', 'unit', 'year', 'month', 'utility_code', 'aggregate_type', 'cost_type',
            or None if no data is available.
        """
        # Check monthly aggregate cache first
        cache_key = f"{utility_code}_{year}_{month}_{aggregate_type}_{cost_type}"
        if cache_key in self._monthly_aggregate_cache:
            cached = self._monthly_aggregate_cache[cache_key]
            _LOGGER.debug("✓ Cache HIT: monthly aggregate %s", cache_key)
            return cached
        
        # Cache miss - will try to calculate from daily cache or fetch from API
        _LOGGER.debug("✗ Cache MISS: monthly aggregate %s, will try daily cache or API", cache_key)
        
        # Wrap calculation in a task for deduplication
        async def _calculate_monthly_aggregate() -> dict[str, Any] | None:
            # For price aggregates
            if aggregate_type == "price":
                # Handle actual price
                if cost_type == "actual":
                    result = await self._get_monthly_price_actual(utility_code, year, month, cache_key)
                    if result:
                        return result
                
                # Handle CW price (special case)
                if utility_code == "CW":
                    result = await self._get_monthly_price_cw(utility_code, year, month, cost_type)
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
                result = await self.billing_manager.get_monthly_price_from_billing(
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
        
        # Use lock to prevent race condition when checking/adding pending requests
        async with self._pending_requests_lock:
            # Check if there's already a pending request for this cache key
            if cache_key in self._pending_requests:
                pending_task = self._pending_requests[cache_key]
                if not pending_task.done():
                    task_to_await = pending_task
                else:
                    # Task completed, remove it
                    del self._pending_requests[cache_key]
                    task_to_await = None
            else:
                task_to_await = None
        
        # Await outside the lock to avoid deadlock
        if task_to_await is not None:
            _LOGGER.debug(
                "Waiting for pending monthly aggregate request for %s",
                cache_key,
            )
            try:
                result = await task_to_await
                return result
            except Exception as err:
                _LOGGER.debug(
                    "Pending monthly aggregate request failed for %s: %s",
                    cache_key,
                    err,
                )
                # Remove failed task if it's still the same one
                async with self._pending_requests_lock:
                    if cache_key in self._pending_requests and self._pending_requests[cache_key] is task_to_await:
                        del self._pending_requests[cache_key]
                # Continue to calculate below
        
        # No pending task, create a new one
        # Create and track the task (with lock protection)
        # We need to add it to _pending_requests BEFORE starting it to prevent races
        async with self._pending_requests_lock:
            # Final check - did another request create a task while we were waiting?
            if cache_key in self._pending_requests:
                pending_task = self._pending_requests[cache_key]
                if not pending_task.done():
                    # Another task exists, use that one
                    task = pending_task
                else:
                    # Task completed, remove it and create new one
                    del self._pending_requests[cache_key]
                    task = asyncio.create_task(_calculate_monthly_aggregate())
                    # Add to dict immediately after creation (still inside lock)
                    self._pending_requests[cache_key] = task
            else:
                # No pending task, create and add it (all inside lock for atomicity)
                task = asyncio.create_task(_calculate_monthly_aggregate())
                # Add to dict immediately after creation (still inside lock)
                self._pending_requests[cache_key] = task

        # Wait for the result (outside lock to avoid deadlock)
        try:
            result = await task
            return result
        except Exception as err:
            # Clean up on error
            async with self._pending_requests_lock:
                if cache_key in self._pending_requests and self._pending_requests[cache_key] is task:
                    del self._pending_requests[cache_key]
            _LOGGER.warning(
                "Failed to fetch monthly aggregate for %s[%s] %d-%02d: %s",
                utility_code,
                aggregate_type,
                year,
                month,
                err,
            )
            return None
        finally:
            # Clean up pending request if still there and it's done
            async with self._pending_requests_lock:
                if cache_key in self._pending_requests and self._pending_requests[cache_key].done():
                    if self._pending_requests[cache_key] is task:
                        del self._pending_requests[cache_key]

    async def get_monthly_aggregate_for_meter(
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
            try:
                # Get timezone from settings
                timezone_str = self.get_setting("TimeZoneIANA")
                if not timezone_str:
                    timezone_str = "UTC"

                try:
                    tz = zoneinfo.ZoneInfo(timezone_str)
                except Exception:
                    _LOGGER.warning("Invalid timezone %s, using UTC", timezone_str)
                    tz = zoneinfo.ZoneInfo("UTC")

                # Calculate month boundaries in the configured timezone
                from_date = datetime(year, month, 1, tzinfo=tz)
                if month == 12:
                    to_date = datetime(year + 1, 1, 1, tzinfo=tz)
                else:
                    to_date = datetime(year, month + 1, 1, tzinfo=tz)

                from_time = int(from_date.timestamp())
                to_time = int(to_date.timestamp())

                # Create cache key for this request
                cache_key = f"data_meter_{self.node_id}_{measuring_point_id}_{from_time}_{to_time}_{utility_code}_{aggregate_type}"

                # Check cache first
                if cache_key in self._data_request_cache:
                    cached_data, cache_timestamp = self._data_request_cache[cache_key]
                    age = time.time() - cache_timestamp
                    if age < self._data_cache_ttl:
                        _LOGGER.debug(
                            "Using cached price data for meter %d %s[%s] %d-%02d (age: %.1f seconds)",
                            measuring_point_id,
                            utility_code,
                            aggregate_type,
                            year,
                            month,
                            age,
                        )
                        data = cached_data
                    else:
                        del self._data_request_cache[cache_key]
                        data = None
                else:
                    data = None

                # Check if there's already a pending request
                if data is None and cache_key in self._pending_requests:
                    _LOGGER.debug(
                        "Waiting for pending price request for meter %d %s[%s] %d-%02d",
                        measuring_point_id,
                        utility_code,
                        aggregate_type,
                        year,
                        month,
                    )
                    try:
                        data = await self._pending_requests[cache_key]
                    except Exception as err:
                        _LOGGER.warning(
                            "Pending price request failed for meter %d %s[%s] %d-%02d: %s",
                            measuring_point_id,
                            utility_code,
                            aggregate_type,
                            year,
                            month,
                            err,
                        )
                        if cache_key in self._pending_requests:
                            del self._pending_requests[cache_key]
                        data = None

                # If no cached data and no pending request, make the API call
                if data is None:
                    async def fetch_data():
                        try:
                            utilities = [f"{utility_code}[price]"]
                            # When querying with measuringpointid, ensure the utility matches the measuring point
                            # The utility_code comes from the installation's registers, so it should match
                            _LOGGER.debug(
                                "Fetching price data for measuring_point_id=%d with utility=%s (matching utility for this meter)",
                                measuring_point_id,
                                utility_code,
                            )
                            result = await self.api.get_data(
                                node_id=self.node_id,
                                from_time=from_time,
                                to_time=to_time,
                                interval="d",
                                grouping="apartment",
                                utilities=utilities,
                                include_sub_nodes=False,  # Don't include sub-nodes when filtering by measuring point
                                measuring_point_id=measuring_point_id,
                            )
                            if result:
                                self._data_request_cache[cache_key] = (result, time.time())
                            return result
                        finally:
                            if cache_key in self._pending_requests:
                                del self._pending_requests[cache_key]

                    task = asyncio.create_task(fetch_data())
                    self._pending_requests[cache_key] = task
                    data = await task

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
                        # Skip data processing and go directly to estimated cost calculation
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
                            # Get this meter's consumption for the month
                            meter_consumption_data = await self.get_monthly_aggregate_for_meter(
                                utility_code=utility_code,
                                measuring_point_id=measuring_point_id,
                                external_key=external_key,
                                year=year,
                                month=month,
                                aggregate_type="con",
                                cost_type="actual",
                            )
                            
                            if meter_consumption_data and meter_consumption_data.get("value") is not None:
                                meter_consumption = meter_consumption_data.get("value", 0.0)
                                
                                if meter_consumption > 0:
                                    # Get total HW consumption and estimated cost for the month
                                    total_hw_consumption_data = await self.get_monthly_aggregate(
                                        utility_code="HW",
                                        year=year,
                                        month=month,
                                        aggregate_type="con",
                                    )
                                    total_hw_cost_data = await self.get_monthly_aggregate(
                                        utility_code="HW",
                                        year=year,
                                        month=month,
                                        aggregate_type="price",
                                        cost_type="estimated",
                                    )
                                    
                                    if total_hw_consumption_data and total_hw_cost_data:
                                        total_hw_consumption = total_hw_consumption_data.get("value", 0.0)
                                        total_hw_cost = total_hw_cost_data.get("value", 0.0)
                                        
                                        if total_hw_consumption > 0 and total_hw_cost > 0:
                                            # Calculate this meter's share of total consumption
                                            consumption_share = meter_consumption / total_hw_consumption
                                            
                                            # Allocate cost proportionally
                                            allocated_cost = total_hw_cost * consumption_share
                                            currency = total_hw_cost_data.get("unit") or self.get_setting("Currency") or "NOK"
                                            
                                            _LOGGER.info(
                                                "Allocated HW cost for meter %d %d-%02d (from zero API data): %.2f %s (meter: %.2f m3 / total: %.2f m3 = %.1f%%, total cost: %.2f %s)",
                                                measuring_point_id, year, month,
                                                allocated_cost, currency,
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
                                                "utility_code": utility_code,
                                                "aggregate_type": "price",
                                                "cost_type": "estimated",
                                                "measuring_point_id": measuring_point_id,
                                            }
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
                        _LOGGER.debug(
                            "Calculating estimated cost for meter %d (%s %d-%02d) from consumption × rate",
                            measuring_point_id,
                            utility_code,
                            year,
                            month,
                        )
                        # Get this meter's consumption for the month
                        meter_consumption_data = await self.get_monthly_aggregate_for_meter(
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
                            # (it will return 0 cost if consumption is 0, which is correct)
                            if utility_code == "HW":
                                _LOGGER.debug(
                                    "No consumption data for HW meter %d %d-%02d, trying spot price estimation anyway",
                                    measuring_point_id, year, month
                                )
                                # Get CW price and consumption for the estimation
                                # Use aggregate CW data (like get_monthly_aggregate does) since
                                # the CW meter might be different from the HW meter
                                cw_price_data = await self.get_monthly_aggregate(
                                    utility_code="CW",
                                    year=year,
                                    month=month,
                                    aggregate_type="price",
                                    cost_type="actual",
                                )
                                cw_consumption_data = await self.get_monthly_aggregate(
                                    utility_code="CW",
                                    year=year,
                                    month=month,
                                    aggregate_type="con",
                                )
                                
                                cw_price = cw_price_data.get("value") if cw_price_data else None
                                cw_consumption = cw_consumption_data.get("value") if cw_consumption_data else None
                                
                                # Estimate HW price from spot prices (will return 0 if consumption is 0)
                                hw_estimated_data = await self._get_hw_price_from_spot_prices(
                                    consumption=0.0,
                                    year=year,
                                    month=month,
                                    cold_water_price=cw_price,
                                    cold_water_consumption=cw_consumption,
                                )
                                
                                if hw_estimated_data:
                                    currency = hw_estimated_data.get("unit") or self.get_setting("Currency") or "NOK"
                                    _LOGGER.debug(
                                        "Estimated HW cost for meter %d %d-%02d: %.2f %s (no consumption data, using 0)",
                                        measuring_point_id, year, month,
                                        hw_estimated_data.get("value"), currency
                                    )
                                    return {
                                        "value": hw_estimated_data.get("value"),
                                        "unit": currency,
                                        "year": year,
                                        "month": month,
                                        "utility_code": utility_code,
                                        "aggregate_type": "price",
                                        "cost_type": "estimated",
                                        "measuring_point_id": measuring_point_id,
                                    }
                                else:
                                    _LOGGER.debug(
                                        "Spot price estimation failed for HW meter %d %d-%02d (no consumption data)",
                                        measuring_point_id, year, month
                                    )
                                    return None
                            else:
                                _LOGGER.debug(
                                    "No consumption data available for meter %d (%s), cannot calculate estimated cost",
                                    measuring_point_id, utility_code
                                )
                                return None
                        
                        meter_consumption = meter_consumption_data.get("value", 0.0)
                        
                        # Get rate from billing
                        rate = await self.billing_manager.get_rate_from_billing(utility_code, year, month)
                        
                        if rate is None:
                            # For HW, try proportional allocation from aggregate estimated cost
                            if utility_code == "HW":
                                _LOGGER.debug(
                                    "No rate found for HW %d-%02d, trying proportional allocation from aggregate estimated cost",
                                    year, month
                                )
                                
                                # Get total HW consumption and estimated cost for the month
                                total_hw_consumption_data = await self.get_monthly_aggregate(
                                    utility_code="HW",
                                    year=year,
                                    month=month,
                                    aggregate_type="con",
                                )
                                total_hw_cost_data = await self.get_monthly_aggregate(
                                    utility_code="HW",
                                    year=year,
                                    month=month,
                                    aggregate_type="price",
                                    cost_type="estimated",
                                )
                                
                                if total_hw_consumption_data and total_hw_cost_data:
                                    total_hw_consumption = total_hw_consumption_data.get("value", 0.0)
                                    total_hw_cost = total_hw_cost_data.get("value", 0.0)
                                    
                                    if total_hw_consumption > 0 and total_hw_cost > 0:
                                        # Calculate this meter's share of total consumption
                                        consumption_share = meter_consumption / total_hw_consumption
                                        
                                        # Allocate cost proportionally
                                        allocated_cost = total_hw_cost * consumption_share
                                        currency = total_hw_cost_data.get("unit") or self.get_setting("Currency") or "NOK"
                                        
                                        _LOGGER.info(
                                            "Allocated HW cost for meter %d %d-%02d: %.2f %s (meter: %.2f m3 / total: %.2f m3 = %.1f%%, total cost: %.2f %s)",
                                            measuring_point_id, year, month,
                                            allocated_cost, currency,
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
                                            "utility_code": utility_code,
                                            "aggregate_type": "price",
                                            "cost_type": "estimated",
                                            "measuring_point_id": measuring_point_id,
                                        }
                                    else:
                                        _LOGGER.debug(
                                            "Total HW consumption (%.2f) or cost (%.2f) is 0, cannot allocate proportionally",
                                            total_hw_consumption,
                                            total_hw_cost,
                                        )
                                else:
                                    _LOGGER.debug(
                                        "Could not get total HW consumption or cost for proportional allocation"
                                    )
                                
                                # Fallback to spot price estimation if proportional allocation didn't work
                                _LOGGER.debug(
                                    "Proportional allocation failed, trying spot price estimation for meter %d (HW %d-%02d)",
                                    measuring_point_id, year, month
                                )
                                # Get CW price and consumption for the estimation
                                # Use aggregate CW data (like get_monthly_aggregate does) since
                                # the CW meter might be different from the HW meter
                                cw_price_data = await self.get_monthly_aggregate(
                                    utility_code="CW",
                                    year=year,
                                    month=month,
                                    aggregate_type="price",
                                    cost_type="actual",
                                )
                                cw_consumption_data = await self.get_monthly_aggregate(
                                    utility_code="CW",
                                    year=year,
                                    month=month,
                                    aggregate_type="con",
                                )
                                
                                cw_price = cw_price_data.get("value") if cw_price_data else None
                                cw_consumption = cw_consumption_data.get("value") if cw_consumption_data else None
                                
                                # Estimate HW price from spot prices
                                _LOGGER.debug(
                                    "Calling spot price estimation for meter %d (HW %d-%02d): consumption=%.2f m3, cw_price=%s, cw_consumption=%s",
                                    measuring_point_id, year, month,
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
                                    currency = hw_estimated_data.get("unit") or self.get_setting("Currency") or "NOK"
                                    estimated_value = hw_estimated_data.get("value", 0.0)
                                    _LOGGER.info(
                                        "Estimated HW cost for meter %d %d-%02d: %.2f %s (consumption: %.2f m3, method: %s)",
                                        measuring_point_id, year, month,
                                        estimated_value, currency,
                                        meter_consumption,
                                        hw_estimated_data.get("calculation_method", "unknown")
                                    )
                                    return {
                                        "value": estimated_value,
                                        "unit": currency,
                                        "year": year,
                                        "month": month,
                                        "utility_code": utility_code,
                                        "aggregate_type": "price",
                                        "cost_type": "estimated",
                                        "measuring_point_id": measuring_point_id,
                                    }
                                else:
                                    _LOGGER.warning(
                                        "Spot price estimation returned None for meter %d (HW %d-%02d) with consumption %.2f m3. Check Nord Pool configuration.",
                                        measuring_point_id, year, month, meter_consumption
                                    )
                            
                            _LOGGER.debug(
                                "No rate found for %s %d-%02d, cannot calculate estimated cost",
                                utility_code, year, month
                            )
                            return None
                        
                        # Calculate cost from consumption × rate
                        calculated_cost = meter_consumption * rate
                        currency = self.get_setting("Currency") or "NOK"
                        
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
                    return None
            except Exception as err:
                _LOGGER.warning(
                    "Failed to fetch per-meter price data for meter %d %s[%s] %d-%02d: %s",
                    measuring_point_id,
                    utility_code,
                    aggregate_type,
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
                        # Get this meter's consumption for the month
                        meter_consumption_data = await self.get_monthly_aggregate_for_meter(
                            utility_code=utility_code,
                            measuring_point_id=measuring_point_id,
                            external_key=external_key,
                            year=year,
                            month=month,
                            aggregate_type="con",
                            cost_type="actual",
                        )
                        
                        if meter_consumption_data and meter_consumption_data.get("value") is not None:
                            meter_consumption = meter_consumption_data.get("value", 0.0)
                            rate = await self.billing_manager.get_rate_from_billing(utility_code, year, month)
                            
                            if rate is not None:
                                calculated_cost = meter_consumption * rate
                                currency = self.get_setting("Currency") or "NOK"
                                
                                _LOGGER.debug(
                                    "Calculated estimated cost for meter %d (%s %d-%02d) after API error: %.2f m3 × %.2f = %.2f %s",
                                    measuring_point_id,
                                    utility_code,
                                    year,
                                    month,
                                    meter_consumption,
                                    rate,
                                    calculated_cost,
                                    currency,
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
                            elif utility_code == "HW":
                                # For HW, try proportional allocation from aggregate estimated cost first
                                _LOGGER.debug(
                                    "No rate found for HW %d-%02d after API error, trying proportional allocation",
                                    year, month
                                )
                                
                                if meter_consumption > 0:
                                    # Get total HW consumption and estimated cost for the month
                                    total_hw_consumption_data = await self.get_monthly_aggregate(
                                        utility_code="HW",
                                        year=year,
                                        month=month,
                                        aggregate_type="con",
                                    )
                                    total_hw_cost_data = await self.get_monthly_aggregate(
                                        utility_code="HW",
                                        year=year,
                                        month=month,
                                        aggregate_type="price",
                                        cost_type="estimated",
                                    )
                                    
                                    if total_hw_consumption_data and total_hw_cost_data:
                                        total_hw_consumption = total_hw_consumption_data.get("value", 0.0)
                                        total_hw_cost = total_hw_cost_data.get("value", 0.0)
                                        
                                        if total_hw_consumption > 0 and total_hw_cost > 0:
                                            # Calculate this meter's share of total consumption
                                            consumption_share = meter_consumption / total_hw_consumption
                                            
                                            # Allocate cost proportionally
                                            allocated_cost = total_hw_cost * consumption_share
                                            currency = total_hw_cost_data.get("unit") or self.get_setting("Currency") or "NOK"
                                            
                                            _LOGGER.info(
                                                "Allocated HW cost for meter %d %d-%02d (after API error): %.2f %s (meter: %.2f m3 / total: %.2f m3 = %.1f%%, total cost: %.2f %s)",
                                                measuring_point_id, year, month,
                                                allocated_cost, currency,
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
                                                "utility_code": utility_code,
                                                "aggregate_type": "price",
                                                "cost_type": "estimated",
                                                "measuring_point_id": measuring_point_id,
                                            }
                                
                                # Fallback to spot price estimation if proportional allocation didn't work
                                _LOGGER.debug(
                                    "Proportional allocation failed, trying spot price estimation for HW %d-%02d after API error",
                                    year, month
                                )
                                # Get CW price and consumption for the estimation
                                # Use aggregate CW data (like get_monthly_aggregate does) since
                                # the CW meter might be different from the HW meter
                                cw_price_data = await self.get_monthly_aggregate(
                                    utility_code="CW",
                                    year=year,
                                    month=month,
                                    aggregate_type="price",
                                    cost_type="actual",
                                )
                                cw_consumption_data = await self.get_monthly_aggregate(
                                    utility_code="CW",
                                    year=year,
                                    month=month,
                                    aggregate_type="con",
                                )
                                
                                cw_price = cw_price_data.get("value") if cw_price_data else None
                                cw_consumption = cw_consumption_data.get("value") if cw_consumption_data else None
                                
                                # Estimate HW price from spot prices
                                hw_estimated_data = await self._get_hw_price_from_spot_prices(
                                    consumption=meter_consumption,
                                    year=year,
                                    month=month,
                                    cold_water_price=cw_price,
                                    cold_water_consumption=cw_consumption,
                                )
                                
                                if hw_estimated_data:
                                    currency = hw_estimated_data.get("unit") or self.get_setting("Currency") or "NOK"
                                    return {
                                        "value": hw_estimated_data.get("value"),
                                        "unit": currency,
                                        "year": year,
                                        "month": month,
                                        "utility_code": utility_code,
                                        "aggregate_type": "price",
                                        "cost_type": "estimated",
                                        "measuring_point_id": measuring_point_id,
                                    }
                    except Exception as calc_err:
                        _LOGGER.debug(
                            "Failed to calculate estimated cost from consumption × rate: %s",
                            calc_err,
                        )
                return None

        # For consumption, we can filter by measuring point
        try:
            # Get timezone from settings
            timezone_str = self.get_setting("TimeZoneIANA")
            if not timezone_str:
                timezone_str = "UTC"

            try:
                tz = zoneinfo.ZoneInfo(timezone_str)
            except Exception:
                _LOGGER.warning("Invalid timezone %s, using UTC", timezone_str)
                tz = zoneinfo.ZoneInfo("UTC")

            # Calculate month boundaries in the configured timezone
            from_date = datetime(year, month, 1, tzinfo=tz)
            if month == 12:
                to_date = datetime(year + 1, 1, 1, tzinfo=tz)
            else:
                to_date = datetime(year, month + 1, 1, tzinfo=tz)

            from_time = int(from_date.timestamp())
            to_time = int(to_date.timestamp())

            _LOGGER.debug(
                "Fetching monthly aggregate for meter %d (%s[%s]) %d-%02d: from=%s to=%s",
                measuring_point_id,
                utility_code,
                aggregate_type,
                year,
                month,
                from_time,
                to_time,
            )

            # Query data endpoint for the month
            utilities = [f"{utility_code}[{aggregate_type}]"]

            # Create cache key for this request
            cache_key = f"data_meter_{self.node_id}_{measuring_point_id}_{from_time}_{to_time}_{utility_code}_{aggregate_type}"

            # Check cache first
            if cache_key in self._data_request_cache:
                cached_data, cache_timestamp = self._data_request_cache[cache_key]
                age = time.time() - cache_timestamp
                if age < self._data_cache_ttl:
                    _LOGGER.debug(
                        "Using cached data for meter %d %s[%s] %d-%02d (age: %.1f seconds)",
                        measuring_point_id,
                        utility_code,
                        aggregate_type,
                        year,
                        month,
                        age,
                    )
                    data = cached_data
                else:
                    del self._data_request_cache[cache_key]
                    data = None
            else:
                data = None

            # Check if there's already a pending request
            if data is None and cache_key in self._pending_requests:
                _LOGGER.debug(
                    "Waiting for pending request for meter %d %s[%s] %d-%02d",
                    measuring_point_id,
                    utility_code,
                    aggregate_type,
                    year,
                    month,
                )
                try:
                    data = await self._pending_requests[cache_key]
                except Exception as err:
                    _LOGGER.warning(
                        "Pending request failed for meter %d %s[%s] %d-%02d: %s",
                        measuring_point_id,
                        utility_code,
                        aggregate_type,
                        year,
                        month,
                        err,
                    )
                    if cache_key in self._pending_requests:
                        del self._pending_requests[cache_key]
                    data = None

            # If no cached data and no pending request, make the API call
            if data is None:
                async def fetch_data():
                    try:
                        # When querying with measuringpointid, ensure the utility matches the measuring point
                        # The utility_code comes from the installation's registers, so it should match
                        _LOGGER.debug(
                            "Fetching %s data for measuring_point_id=%d with utility=%s (matching utility for this meter)",
                            aggregate_type,
                            measuring_point_id,
                            utility_code,
                        )
                        result = await self.api.get_data(
                            node_id=self.node_id,
                            from_time=from_time,
                            to_time=to_time,
                            interval="d",
                            grouping="apartment",
                            utilities=utilities,
                            include_sub_nodes=False,  # Don't include sub-nodes when filtering by measuring point
                            measuring_point_id=measuring_point_id,
                        )
                        if result:
                            self._data_request_cache[cache_key] = (result, time.time())
                        return result
                    finally:
                        if cache_key in self._pending_requests:
                            del self._pending_requests[cache_key]

                task = asyncio.create_task(fetch_data())
                self._pending_requests[cache_key] = task
                data = await task

            if not data or not isinstance(data, list):
                return None

            # Filter data to only include this specific meter
            # Match by measuring_point_id or external_key (similar to get_latest_consumption_value)
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
                    # If we have an installation with this measuring_point_id, accept any node_data
                    # (the API might return data aggregated differently)
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
                    if result.get("Utl") == utility_code and result.get("Func") == aggregate_type:
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
                    "No data found for meter %d (%s[%s]) %d-%02d after filtering",
                    measuring_point_id,
                    utility_code,
                    aggregate_type,
                    year,
                    month,
                )
                return None

            _LOGGER.debug(
                "Found monthly aggregate for meter %d (%s[%s]) %d-%02d: %.2f %s",
                measuring_point_id,
                utility_code,
                aggregate_type,
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
                "aggregate_type": aggregate_type,
                "measuring_point_id": measuring_point_id,
            }
        except Exception as err:
            _LOGGER.warning(
                "Failed to fetch monthly aggregate for meter %d %s[%s] %d-%02d: %s",
                measuring_point_id,
                utility_code,
                aggregate_type,
                year,
                month,
                err,
            )
            return None

    async def _get_nord_pool_spot_price(
        self,
        area_code: str,
        currency: str,
    ) -> float | None:
        """Fetch current spot price from Nord Pool using the nordpool library.

        Uses the nordpool Python library (https://github.com/kipe/nordpool) to fetch
        spot prices directly from Nord Pool API.

        Args:
            area_code: Nord Pool area code (e.g., "NO1", "NO2", "SE3")
            currency: Currency code (e.g., "NOK", "SEK", "EUR")

        Returns:
            Current spot price in currency/kWh, or None if unavailable
        """
        if not area_code:
            return None
        
        # Initialize fetcher if not already initialized (lazy initialization)
        if not self._nord_pool_fetcher:
            if not NORD_POOL_AVAILABLE:
                _LOGGER.warning(
                    "nordpool library not installed. Install it with: pip install nordpool"
                )
                return None
            # Initialize fetcher with shared cache
            self._nord_pool_fetcher = NordPoolPriceFetcher(price_cache=self._nord_pool_price_cache)
        
        timezone_str = self.get_setting("TimeZoneIANA") or "UTC"
        return await self._nord_pool_fetcher.get_spot_price(
            area_code=area_code,
            currency=currency,
            timezone_str=timezone_str,
        )

    async def _get_hw_price_from_spot_prices(
        self,
        consumption: float,
        year: int,
        month: int,
        cold_water_price: float | None = None,
        cold_water_consumption: float | None = None,
    ) -> dict[str, Any] | None:
        """Calculate hot water price using electricity spot prices.

        This method fetches spot prices directly from Nord Pool API, then calculates the cost to heat water.

        Formula: cost = consumption (m3) × energy_per_m3 (kWh/m3) × avg_spot_price (NOK/kWh)

        Typical energy needed: ~40-50 kWh per m3 (heating from ~10°C to ~60°C)

        Args:
            consumption: Hot water consumption in m3
            year: Year
            month: Month
            cold_water_price: Optional cold water price for current month
            cold_water_consumption: Optional cold water consumption for current month

        Returns:
            Dict with price data, or None if spot prices unavailable
        """
        try:
            # Default energy factor: kWh needed to heat 1 m3 of water
            # Typical: 40-50 kWh/m3 (heating from ~10°C to ~60°C, ~50°C rise)
            # Using 45 kWh/m3 as a reasonable default
            ENERGY_PER_M3 = 45.0  # kWh per m3

            # Calculate calibration ratio from historical data (once, cached)
            # Use lock to prevent race conditions when multiple sensors call this simultaneously
            async with self._hw_calibration_lock:
                if not self._hw_calibration_calculated:
                    _LOGGER.info("Calculating HW calibration ratio from historical billing data...")
                    self._hw_calibration_ratio = await self.billing_manager.calculate_hw_calibration_ratio(months_back=6)
                    self._hw_calibration_calculated = True
                    if self._hw_calibration_ratio:
                        _LOGGER.info(
                            "✓ Calibration ratio calculated successfully: %.3f (from historical billing data). "
                            "This will be applied to all HW price calculations.",
                            self._hw_calibration_ratio,
                        )
                    else:
                        _LOGGER.warning(
                            "✗ No calibration ratio available! HW prices will be calculated without calibration. "
                            "This may result in lower than expected prices (typically ~50% of actual cost). "
                            "Check if billing data and Nord Pool area are configured correctly. "
                            "The calibration ratio is typically around 1.5-2.5."
                        )

            spot_price = None
            price_sensor_entity_id = None
            currency = None

            # First, try to get spot price from Nord Pool API if area is configured
            if self.nord_pool_area:
                # Get currency from EcoGuard settings
                currency = self.get_setting("Currency") or "NOK"

                spot_price = await self._get_nord_pool_spot_price(
                    area_code=self.nord_pool_area,
                    currency=currency,
                )

                if spot_price is not None:
                    _LOGGER.info(
                        "Using Nord Pool API spot price for %s/%s: %.4f %s/kWh",
                        self.nord_pool_area,
                        currency,
                        spot_price,
                        currency,
                    )
                    price_sensor_entity_id = f"nord_pool_api_{self.nord_pool_area}"

            # If no spot price from API, return None
            if spot_price is None:
                _LOGGER.debug(
                    "No spot price available from Nord Pool API (area: %s, currency: %s)",
                    self.nord_pool_area or "not configured",
                    currency or "unknown",
                )
                return None

            # If we got currency from settings but not from sensor, use it
            if not currency:
                currency = self.get_setting("Currency") or "NOK"

            # Calculate total energy needed to heat the water
            total_energy_kwh = consumption * ENERGY_PER_M3

            # Calculate base heating cost from spot price
            base_heating_cost = total_energy_kwh * spot_price

            # Apply calibration ratio if available (accounts for system efficiency, fixed costs, etc.)
            if self._hw_calibration_ratio is not None:
                heating_cost = base_heating_cost * self._hw_calibration_ratio
                _LOGGER.info(
                    "✓ Applied calibration ratio %.3f: base=%.2f NOK → calibrated=%.2f NOK (consumption=%.3f m3, energy=%.2f kWh, spot=%.4f NOK/kWh)",
                    self._hw_calibration_ratio,
                    base_heating_cost,
                    heating_cost,
                    consumption,
                    total_energy_kwh,
                    spot_price,
                )
            else:
                heating_cost = base_heating_cost
                _LOGGER.warning(
                    "✗ No calibration ratio available! Using base heating cost only: %.2f NOK (consumption=%.3f m3, energy=%.2f kWh, spot=%.4f NOK/kWh). "
                    "This may result in lower than expected prices (~50% of actual cost). "
                    "The calibration ratio should typically be around 1.5-2.5. "
                    "Check logs for calibration ratio calculation errors.",
                    base_heating_cost,
                    consumption,
                    total_energy_kwh,
                    spot_price,
                )

            # Get cold water cost
            # If we have the current month's CW price, use it directly (more accurate)
            # Otherwise, calculate from billing rate
            cold_water_cost = None
            cold_water_rate = None

            if cold_water_price is not None and cold_water_consumption is not None:
                # Use the actual current month CW price and consumption (already fetched)
                if cold_water_consumption > 0:
                    # Calculate effective rate from actual price
                    cold_water_rate = cold_water_price / cold_water_consumption
                    cold_water_cost = consumption * cold_water_rate
                    _LOGGER.debug(
                        "Using current month CW price: %.2f NOK for %.2f m3 = %.2f NOK/m3 rate",
                        cold_water_price,
                        cold_water_consumption,
                        cold_water_rate,
                    )

            # Fallback to billing rate if we don't have current month price
            if cold_water_cost is None:
                cold_water_rate = await self.billing_manager.get_rate_from_billing("CW", year, month)

                if cold_water_rate is None:
                    _LOGGER.debug(
                        "Could not get cold water rate for HW calculation, using heating cost only"
                    )
                    total_cost = heating_cost
                else:
                    # Calculate cold water cost (same volume as hot water)
                    cold_water_cost = consumption * cold_water_rate
                    _LOGGER.debug(
                        "Using billing rate for CW: %.2f NOK/m3",
                        cold_water_rate,
                    )

            if cold_water_cost is not None:
                # Total hot water cost = cold water cost + heating cost
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

            # Ensure currency is set
            if not currency:
                currency = self.get_setting("Currency") or "NOK"

            result = {
                "value": round(total_cost, 2),
                "unit": currency,
                "year": year,
                "month": month,
                "utility_code": "HW",
                "aggregate_type": "price",
                "calculation_method": "spot_price_calibrated" if self._hw_calibration_ratio else "spot_price",
                "energy_per_m3_kwh": ENERGY_PER_M3,
                "total_energy_kwh": round(total_energy_kwh, 2),
                "spot_price_per_kwh": round(spot_price, 4),
                "spot_price_currency": currency,
                "heating_cost": round(heating_cost, 2),
            }

            if self._hw_calibration_ratio is not None:
                result["calibration_ratio"] = round(self._hw_calibration_ratio, 3)
                result["base_heating_cost"] = round(base_heating_cost, 2)

            if price_sensor_entity_id:
                result["price_source"] = price_sensor_entity_id

            if self.nord_pool_area:
                result["nord_pool_area"] = self.nord_pool_area
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

    async def get_current_month_total_cost(
        self,
        include_estimated: bool = True,
    ) -> dict[str, Any] | None:
        """Get total cost for the current month by summing all price values from data API.

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
            timezone_str = self.get_setting("TimeZoneIANA")
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
            active_installations = self.get_active_installations()
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
            data = await self.api.get_data(
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
            currency = self.get_setting("Currency") or "NOK"

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
                hw_consumption_data = await self.get_monthly_aggregate(
                    utility_code="HW",
                    year=year,
                    month=month,
                    aggregate_type="con",
                )

                if hw_consumption_data:
                    hw_consumption = hw_consumption_data.get("value")
                    if hw_consumption and hw_consumption > 0:
                        # Get CW price and consumption for the estimation
                        cw_price_data = await self.get_monthly_aggregate(
                            utility_code="CW",
                            year=year,
                            month=month,
                            aggregate_type="price",
                        )
                        cw_consumption_data = await self.get_monthly_aggregate(
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
            metered_cost + estimated_cost
            all_utilities = metered_utilities | estimated_utilities

            if not has_data and estimated_cost == 0:
                _LOGGER.debug("No price data found for total cost calculation")
                return None

            # Currency is already defined above for logging purposes

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
                billing_results = await self.billing_manager.get_cached_billing_results(
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

                        billing_total_without_vat + billing_vat

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

    async def get_end_of_month_estimate(self) -> dict[str, Any] | None:
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
        import uuid
        call_id = str(uuid.uuid4())[:8]
        _LOGGER.debug("get_end_of_month_estimate[%s]: Method called", call_id)
        try:
            now = datetime.now()
            current_year = now.year
            current_month = now.month
            _LOGGER.debug("get_end_of_month_estimate[%s]: Starting for year=%d, month=%d", call_id, current_year, current_month)

            # Get timezone
            timezone_str = self.get_setting("TimeZoneIANA") or "UTC"
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

            currency = self.get_setting("Currency") or "NOK"

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
                            
                            # Check API request cache first
                            data = None
                            if api_cache_key in self._data_request_cache:
                                cached_data, cache_timestamp = self._data_request_cache[api_cache_key]
                                age = time.time() - cache_timestamp
                                if age < self._data_cache_ttl:
                                    data = cached_data
                                else:
                                    del self._data_request_cache[api_cache_key]
                            
                            # Use lock to prevent race condition when checking/adding pending requests
                            async with self._pending_requests_lock:
                                if data is None and api_cache_key in self._pending_requests:
                                    pending_task = self._pending_requests[api_cache_key]
                                    if not pending_task.done():
                                        task_to_await = pending_task
                                    else:
                                        del self._pending_requests[api_cache_key]
                                        task_to_await = None
                                else:
                                    task_to_await = None
                            
                            # Await outside the lock to avoid deadlock
                            if task_to_await is not None:
                                try:
                                    data = await task_to_await
                                except Exception as err:
                                    _LOGGER.debug(
                                        "Pending request failed for %s %s: %s",
                                        utility_code,
                                        data_type,
                                        err,
                                    )
                                    async with self._pending_requests_lock:
                                        if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_to_await:
                                            del self._pending_requests[api_cache_key]
                                    data = None
                            
                            # If still no data, create a new task
                            if data is None:
                                task_ref = None
                                
                                async def fetch_data():
                                    nonlocal task_ref
                                    try:
                                        result = await self.api.get_data(
                                            node_id=self.node_id,
                                            from_time=from_time,
                                            to_time=to_time,
                                            interval="d",
                                            grouping="apartment",
                                            utilities=[f"{utility_code}[{data_type}]"],
                                            include_sub_nodes=True,
                                        )
                                        if result:
                                            self._data_request_cache[api_cache_key] = (result, time.time())
                                        return result
                                    finally:
                                        async with self._pending_requests_lock:
                                            if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task_ref:
                                                del self._pending_requests[api_cache_key]
                                
                                # Create and track the task (with lock protection)
                                async with self._pending_requests_lock:
                                    if api_cache_key in self._pending_requests:
                                        pending_task = self._pending_requests[api_cache_key]
                                        if not pending_task.done():
                                            task = pending_task
                                        else:
                                            del self._pending_requests[api_cache_key]
                                            task = asyncio.create_task(fetch_data())
                                            task_ref = task
                                            self._pending_requests[api_cache_key] = task
                                    else:
                                        task = asyncio.create_task(fetch_data())
                                        task_ref = task
                                        self._pending_requests[api_cache_key] = task
                                
                                # Wait for the result
                                try:
                                    data = await task
                                except Exception as err:
                                    async with self._pending_requests_lock:
                                        if api_cache_key in self._pending_requests and self._pending_requests[api_cache_key] is task:
                                            del self._pending_requests[api_cache_key]
                                    raise
                            
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

                    spot_price_data = await self._get_hw_price_from_spot_prices(
                        consumption=hw_consumption_estimate,
                        year=current_year,
                        month=current_month,
                        cold_water_price=estimated_cw_price if estimated_cw_price > 0 else None,
                        cold_water_consumption=estimated_cw_consumption if estimated_cw_consumption > 0 else None,
                    )

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
                    cw_price_data = await self.get_monthly_aggregate(
                        utility_code="CW",
                        year=current_year,
                        month=current_month,
                        aggregate_type="price",
                        cost_type="estimated",
                    )
                    
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
                other_items_data = await self.billing_manager.get_monthly_other_items_cost(
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
            
            # Calculate total estimated bill
            try:
                hw_price_est = estimates.get("hw_price_estimate", {}).get("estimated_total", 0)
                cw_price_est = estimates.get("cw_price_estimate", {}).get("estimated_total", 0)
                total_bill_estimate = hw_price_est + cw_price_est + other_items_cost

                _LOGGER.debug(
                    "get_end_of_month_estimate[%s]: Calculating total bill estimate: HW=%.2f, CW=%.2f, Other=%.2f, Total=%.2f",
                    call_id,
                    hw_price_est,
                    cw_price_est,
                    other_items_cost,
                    total_bill_estimate,
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


class EcoGuardLatestReceptionCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Coordinator for latest reception data with faster update interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: EcoGuardAPI,
        node_id: int,
    ) -> None:
        """Initialize the latest reception coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_latest_reception",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_LATEST_RECEPTION),
        )
        self.api = api
        self.node_id = node_id

    async def _async_update_data(self) -> list[dict[str, Any]]:
        """Fetch latest reception data from EcoGuard API.
        
        Note: Latest reception is fetched after Home Assistant has fully started
        (see __init__.py for the startup event listener) to avoid blocking startup.
        During periodic updates, this method will fetch fresh data.
        """
        try:
            latest_reception = await self.api.get_latest_reception(self.node_id)
            _LOGGER.debug("Fetched latest reception data: %d entries", len(latest_reception))
            return latest_reception
        except EcoGuardAPIError as err:
            raise UpdateFailed(f"Error fetching latest reception: {err}") from err
        except Exception as err:
            _LOGGER.warning("Failed to fetch latest reception: %s", err)
            return []
