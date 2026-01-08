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
from .helpers import (
    get_timezone,
    get_month_timestamps,
    get_date_range_timestamps,
    format_cache_key,
    log_static_info_summary,
)
from .nord_pool import NordPoolPriceFetcher, NORD_POOL_AVAILABLE
from .price_calculator import HWPriceCalculator
from .billing_manager import BillingManager
from .request_deduplicator import RequestDeduplicator
from .meter_aggregate_calculator import MeterAggregateCalculator
from .data_processor import DataProcessor
from .end_of_month_estimator import EndOfMonthEstimator
from .monthly_cost_calculator import MonthlyCostCalculator

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
        
        # Initialize HW price calculator (will be set up after billing_manager is created)
        self._hw_price_calculator: HWPriceCalculator | None = None
        
        # Initialize data processor (will be set up after all attributes are set)
        self._data_processor: DataProcessor | None = None
        
        self._billing_results_cache: dict[str, tuple[list[dict[str, Any]], float]] = {}  # Cache for billing results: key -> (data, timestamp)
        self._billing_cache_ttl: float = 86400.0  # Cache billing data for 24 hours (it's historical and doesn't change)
        
        self._data_request_cache: dict[str, tuple[Any, float]] = {}  # Cache for data API requests: key -> (data, timestamp)
        self._data_cache_ttl: float = 60.0  # Cache data requests for 60 seconds to prevent duplicate calls
        self._pending_requests: dict[str, asyncio.Task] = {}  # Track pending requests to deduplicate simultaneous calls
        self._pending_requests_lock = asyncio.Lock()  # Lock to prevent race conditions when checking/adding pending requests
        self._cache_loaded: bool = False  # Track if we've loaded from cache
        
        # Debounce listener updates to prevent excessive sensor updates
        self._listener_update_task: asyncio.Task | None = None  # Pending listener update task
        self._listener_update_lock = asyncio.Lock()  # Lock for listener update debouncing
        self._listener_update_debounce_delay = 0.05  # Debounce delay in seconds (50ms) - reduced for responsiveness
        
        # Initialize request deduplicator for API data requests
        # Shares cache and pending_requests with coordinator for compatibility
        self._request_deduplicator = RequestDeduplicator(
            hass=self.hass,
            cache_ttl=self._data_cache_ttl,
            defer_during_startup=True,
            cache=self._data_request_cache,
            pending_requests=self._pending_requests,
            lock=self._pending_requests_lock,
        )
        
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

        # Initialize HW price calculator now that billing_manager is available
        if nord_pool_area:
            self._hw_price_calculator = HWPriceCalculator(
                calculate_calibration_ratio=lambda months_back: self.billing_manager.calculate_hw_calibration_ratio(months_back=months_back),
                nord_pool_fetcher=self._nord_pool_fetcher,
                get_rate_from_billing=lambda uc, y, m: self.billing_manager.get_rate_from_billing(uc, y, m),
                get_setting=self.get_setting,
            )
        
        # Initialize meter aggregate calculator
        self._meter_aggregate_calculator = MeterAggregateCalculator(
            node_id=self.node_id,
            request_deduplicator=self._request_deduplicator,
            api=self.api,
            get_setting=self.get_setting,
            get_monthly_aggregate=lambda uc, y, m, at, ct: self.get_monthly_aggregate(uc, y, m, at, ct),
            get_hw_price_from_spot_prices=lambda c, y, m, cwp, cwc: self._get_hw_price_from_spot_prices(c, y, m, cwp, cwc),
            billing_manager=self.billing_manager,
            installations=self._installations,
        )
        
        # Initialize data processor
        self._data_processor = DataProcessor(
            api=self.api,
            node_id=self.node_id,
            installations=self._installations,
            get_setting=self.get_setting,
            latest_consumption_cache=self._latest_consumption_cache,
            latest_cost_cache=self._latest_cost_cache,
            daily_consumption_cache=self._daily_consumption_cache,
            daily_price_cache=self._daily_price_cache,
            monthly_aggregate_cache=self._monthly_aggregate_cache,
            async_set_updated_data=self.async_set_updated_data,
            async_update_listeners=self.async_update_listeners,
            get_listeners=lambda: list(self._listeners) if hasattr(self, '_listeners') else [],
            hass=self.hass,
            data=None,  # Will be updated when data is available
        )
        
        # Initialize end-of-month estimator
        self._end_of_month_estimator = EndOfMonthEstimator(
            node_id=self.node_id,
            request_deduplicator=self._request_deduplicator,
            api=self.api,
            get_setting=self.get_setting,
            daily_consumption_cache=self._daily_consumption_cache,
            get_hw_price_from_spot_prices=lambda consumption, year, month, cold_water_price=None, cold_water_consumption=None: self._get_hw_price_from_spot_prices(consumption, year, month, cold_water_price, cold_water_consumption),
            get_monthly_aggregate=lambda uc, y, m, at, ct: self.get_monthly_aggregate(uc, y, m, at, ct),
            billing_manager=self.billing_manager,
        )
        
        # Initialize monthly cost calculator
        self._monthly_cost_calculator = MonthlyCostCalculator(
            node_id=self.node_id,
            api=self.api,
            get_setting=self.get_setting,
            get_active_installations=self.get_active_installations,
            get_monthly_aggregate=lambda uc, y, m, at, ct: self.get_monthly_aggregate(uc, y, m, at, ct),
            get_hw_price_from_spot_prices=lambda c, y, m, cwp, cwc: self._get_hw_price_from_spot_prices(c, y, m, cwp, cwc),
            billing_manager=self.billing_manager,
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
            log_static_info_summary(
                node_data=self._node_data,
                settings=self._settings,
                installations=self._installations,
                measuring_points=self._measuring_points,
                latest_reception=self._latest_reception,
                node_id=self.node_id,
                domain=self.domain,
            )

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

    def async_update_listeners(self) -> None:
        """Override async_update_listeners with debounced version to prevent excessive sensor updates.
        
        This method batches multiple listener update calls together, preventing
        a feedback loop where sensor updates trigger more coordinator updates.
        """
        # Store reference to current task
        current_task = self._listener_update_task
        
        # Cancel any pending update task
        if current_task is not None:
            current_task.cancel()
        
        # Schedule a new update after debounce delay
        async def _delayed_update(task_ref: list) -> None:
            """Delayed update that checks if it's still the current task."""
            try:
                await asyncio.sleep(self._listener_update_debounce_delay)
                # Only update if this task is still the current one (not cancelled/replaced)
                if self._listener_update_task is task_ref[0]:
                    _LOGGER.debug("Executing debounced listener update (after %.1fms delay)", self._listener_update_debounce_delay * 1000)
                    # Call parent class method directly (super() doesn't work in nested functions)
                    DataUpdateCoordinator.async_update_listeners(self)
                    self._listener_update_task = None
                    _LOGGER.debug("Debounced listener update completed")
                else:
                    _LOGGER.debug("Skipping debounced listener update (task was replaced)")
            except asyncio.CancelledError:
                _LOGGER.debug("Debounced listener update was cancelled")
                pass
            except Exception as err:
                _LOGGER.error("Error in debounced listener update: %s", err, exc_info=True)
                # Ensure task is cleared even on error
                if self._listener_update_task is task_ref[0]:
                    self._listener_update_task = None
        
        # Use a list to store task reference that can be checked later
        task_ref = [None]
        new_task = self.hass.async_create_task(_delayed_update(task_ref))
        task_ref[0] = new_task
        self._listener_update_task = new_task
        _LOGGER.debug("Scheduled debounced listener update (delay: %.1fms)", self._listener_update_debounce_delay * 1000)

    async def _batch_fetch_sensor_data(self) -> None:
        """Batch fetch consumption and price data for all utility codes.
        
        This method fetches data for all utility codes at once, then caches it
        so individual sensors can read from the cache instead of making API calls.
        """
        if not self._data_processor:
            _LOGGER.warning("DataProcessor not initialized, cannot batch fetch sensor data.")
            return
        
        # Update installations in processor (they might have been loaded from cache after processor was created)
        self._data_processor._installations = self._installations
        _LOGGER.debug("Batch fetch: Using %d installations", len(self._installations))
        
        # Update data reference in processor - use current data or create initial structure
        if self.data:
            self._data_processor._data = self.data
        else:
            # Create initial data structure if coordinator hasn't been refreshed yet
            # This ensures the processor can update it properly
            initial_data = {
                "measuring_points": self._measuring_points,
                "installations": self._installations,
                "latest_reception": self._latest_reception,
                "node_data": self._node_data,
                "settings": self._settings,
                "node_id": self.node_id,
                "domain": self.domain,
                "latest_consumption_cache": self._latest_consumption_cache,
                "latest_cost_cache": self._latest_cost_cache,
                "daily_consumption_cache": self._daily_consumption_cache,
                "daily_price_cache": self._daily_price_cache,
                "monthly_aggregate_cache": self._monthly_aggregate_cache,
            }
            self._data_processor._data = initial_data
        
        await self._data_processor.batch_fetch_sensor_data()
        
        # The processor calls async_set_updated_data which updates self.data
        # Since we're using references to cache dictionaries, the caches are already in sync
        # But we call _sync_cache_to_data to ensure references are correct
        self._sync_cache_to_data()
        
        # Force another notification to ensure sensors get the update
        # This is important because sensors might have been added after the processor's notification
        # The debounced version is now the default, so just call async_update_listeners()
        self.async_update_listeners()
        _LOGGER.debug("Coordinator data synced and listeners notified: %d consumption keys, %d cost keys",
                     len(self._latest_consumption_cache), len(self._latest_cost_cache))

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
            # Get date range timestamps
            from_time, to_time = get_date_range_timestamps(days, self.get_setting)

            # Create cache key for request deduplication
            cache_key = format_cache_key(
                "price",
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                from_time=from_time,
                to_time=to_time,
            )

            # Format timestamps for logging
            tz = get_timezone(self.get_setting("TimeZoneIANA"))
            from_start_dt = datetime.fromtimestamp(from_time, tz=tz)
            to_start_dt = datetime.fromtimestamp(to_time, tz=tz)
            _LOGGER.debug(
                "Fetching price data for %s: from=%s (%s) to=%s (%s)",
                utility_code,
                from_time,
                from_start_dt.isoformat(),
                to_time,
                to_start_dt.isoformat(),
            )

            # Create async function for fetching and processing price data
            async def _fetch_price_data() -> dict[str, Any] | None:
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

            # Use request deduplicator to handle caching and deduplication
            # Note: This doesn't use the data_request_cache since it processes the data
            return await self._request_deduplicator.get_or_fetch(
                cache_key=cache_key,
                fetch_func=_fetch_price_data,
                use_cache=False,  # Don't cache processed results, only deduplicate requests
            )

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
                hw_cost_cache_key = format_cache_key(
                    "hw_estimated_cost",
                    consumption=consumption,
                    year=year,
                    month=month,
                    measuring_point_id=measuring_point_id,
                )
                
                # Create async task for calculation
                async def _calculate_hw_estimated_cost() -> dict[str, Any] | None:
                    _LOGGER.debug("Calculating HW estimated cost: consumption=%.3f m3, year=%d, month=%d, measuring_point_id=%s",
                                 consumption, year, month, measuring_point_id)
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
                
                # Use request deduplicator for calculation task (not API call, so use_cache=False)
                result = await self._request_deduplicator.get_or_fetch(
                    cache_key=hw_cost_cache_key,
                    fetch_func=_calculate_hw_estimated_cost,
                    use_cache=False,  # Don't cache calculation results, only deduplicate
                )
                if result:
                    return result

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
        return get_month_timestamps(year, month, get_timezone(self.get_setting("TimeZoneIANA")))

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
            return await self.api.get_data(
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
            return await self.api.get_data(
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
            return await self.api.get_data(
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
            return await self.api.get_data(
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
        
        # Use request deduplicator for calculation task (not API call, so use_cache=False)
        try:
            # Check if data was already in cache before fetching
            was_cached = cache_key in self._monthly_aggregate_cache
            
            result = await self._request_deduplicator.get_or_fetch(
                cache_key=cache_key,
                fetch_func=_calculate_monthly_aggregate,
                use_cache=False,  # Don't cache calculation results, only deduplicate
            )
            
            # Only notify listeners if new data was cached (not if it was already there)
            if result and not was_cached and cache_key in self._monthly_aggregate_cache:
                self._sync_cache_to_data()
                self.async_update_listeners()
            return result
        except Exception as err:
            _LOGGER.warning(
                "Failed to fetch monthly aggregate for %s[%s] %d-%02d: %s",
                utility_code,
                aggregate_type,
                year,
                month,
                err,
            )
            return None

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

        First checks the monthly aggregate cache. If not found, calculates and caches result.

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
        if not self._meter_aggregate_calculator:
            _LOGGER.warning("MeterAggregateCalculator not initialized, cannot calculate meter aggregate.")
            return None
        
        # Check monthly aggregate cache first (per-meter cache key includes measuring_point_id)
        cache_key = f"{utility_code}_{measuring_point_id}_{year}_{month}_{aggregate_type}_{cost_type}"
        if cache_key in self._monthly_aggregate_cache:
            cached = self._monthly_aggregate_cache[cache_key]
            _LOGGER.debug("✓ Cache HIT: per-meter monthly aggregate %s", cache_key)
            return cached
        
        # Cache miss - will calculate
        _LOGGER.debug("✗ Cache MISS: per-meter monthly aggregate %s, will calculate", cache_key)
        
        # Use request deduplicator to prevent multiple simultaneous calculations
        async def _calculate_meter_aggregate() -> dict[str, Any] | None:
            """Calculate the meter aggregate."""
            # Double-check cache inside the deduplication function
            # (another call might have cached it while we were waiting)
            if cache_key in self._monthly_aggregate_cache:
                cached = self._monthly_aggregate_cache[cache_key]
                _LOGGER.debug("✓ Cache HIT (during dedup): per-meter monthly aggregate %s", cache_key)
                return cached
            
            result = await self._meter_aggregate_calculator.calculate(
                                utility_code=utility_code,
                                measuring_point_id=measuring_point_id,
                                external_key=external_key,
                                year=year,
                                month=month,
                aggregate_type=aggregate_type,
                cost_type=cost_type,
            )
            
            # Cache the result (including None) to prevent repeated calculations
            self._monthly_aggregate_cache[cache_key] = result
            self._sync_cache_to_data()
            
            return result
        
        try:
            # Check if data was already in cache before fetching
            was_cached = cache_key in self._monthly_aggregate_cache
            
            result = await self._request_deduplicator.get_or_fetch(
                cache_key=f"meter_agg_{cache_key}",
                fetch_func=_calculate_meter_aggregate,
                use_cache=False,  # Don't cache calculation results, only deduplicate
            )
            
            # Only notify listeners if new data was cached (not if it was already there)
            if result and not was_cached and cache_key in self._monthly_aggregate_cache:
                self.async_update_listeners()
            
            return result
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

    async def _get_hw_price_from_spot_prices(
        self,
        consumption: float,
        year: int,
        month: int,
        cold_water_price: float | None = None,
        cold_water_consumption: float | None = None,
    ) -> dict[str, Any] | None:
        """Calculate hot water price using electricity spot prices.

        Delegates to HWPriceCalculator for the actual calculation.

        Args:
            consumption: Hot water consumption in m3
            year: Year
            month: Month
            cold_water_price: Optional cold water price for current month
            cold_water_consumption: Optional cold water consumption for current month

        Returns:
            Dict with price data, or None if spot prices unavailable
        """
        if not self._hw_price_calculator:
            _LOGGER.debug("HW price calculator not available (Nord Pool area not configured)")
            return None

        return await self._hw_price_calculator.calculate_price(
            consumption=consumption,
            year=year,
            month=month,
            cold_water_price=cold_water_price,
            cold_water_consumption=cold_water_consumption,
            nord_pool_area=self.nord_pool_area,
        )

    async def get_current_month_total_cost(
        self,
        include_estimated: bool = True,
    ) -> dict[str, Any] | None:
        """Get total cost for the current month by summing all price values from data API.

        This matches the approach used in the React app - fetches all price utilities
        for the current month and sums all price values (like getSumOfDataset).

        Delegates to MonthlyCostCalculator for the actual calculation.

        Args:
            include_estimated: If True, includes estimated HW costs when price data is missing.
                              If False, only includes metered costs from API.

        Returns:
            Dict with 'value', 'unit', 'year', 'month', 'currency', 'utilities',
            'metered_utilities', 'estimated_utilities', 'is_estimated',
            or None if no data is available.
        """
        if not self._monthly_cost_calculator:
            _LOGGER.error("Monthly cost calculator not initialized - this should not happen")
            return None
        
        return await self._monthly_cost_calculator.calculate(include_estimated=include_estimated)

    async def get_end_of_month_estimate(self) -> dict[str, Any] | None:
        """Calculate end-of-month bill estimate based on current month's data.

        Delegates to EndOfMonthEstimator for the actual calculation.
        Uses request deduplication to prevent multiple simultaneous calculations.
        """
        if not self._end_of_month_estimator:
            _LOGGER.error("End-of-month estimator not initialized - this should not happen")
            return None
        
        # Use request deduplication to prevent multiple simultaneous calculations
        # Cache key includes current year and month so estimates refresh when month changes
        from datetime import datetime
        from .helpers import get_timezone
        
        timezone_str = self.get_setting("TimeZoneIANA") or "UTC"
        tz = get_timezone(timezone_str)
        now_tz = datetime.now(tz)
        cache_key = f"end_of_month_estimate_{now_tz.year}_{now_tz.month}"
        
        async def calculate_estimate() -> dict[str, Any] | None:
            """Calculate the estimate."""
            try:
                return await self._end_of_month_estimator.calculate()
            except Exception as err:
                _LOGGER.error(
                    "Error in get_end_of_month_estimate: %s",
                err,
                exc_info=True,
            )
            return None
        
        return await self._request_deduplicator.get_or_fetch(
            cache_key=cache_key,
            fetch_func=calculate_estimate,
            use_cache=True,
        )


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
