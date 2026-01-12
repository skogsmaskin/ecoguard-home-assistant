# Changelog

All notable changes to the EcoGuard Home Assistant integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.3.0] - 2026-01-12

### Added

#### Meter Count for Aggregate Sensors
- **Meter count for monthly sensors**: All monthly accumulated sensors now expose meter count information
  - Added `meter_count` and `meters` attributes to monthly accumulated sensors
  - Meters collected from monthly cache or calculated from daily cache when needed
  - Provides visibility into how many meters contribute to aggregate values
- **Meter count for combined water sensors**: Combined water sensors now show separate meter counts for hot and cold water
  - Added `hw_meter_count`, `cw_meter_count`, `hw_meters`, and `cw_meters` attributes
  - Helps identify which meters contribute to combined totals
- **Meter count for total monthly cost sensor**: `EcoGuardTotalMonthlyCostSensor` now includes meter information
  - Collects meters from all water utilities (HW + CW)
  - Shows meter details with `utility_code` for clarity
  - Provides complete visibility into cost calculation sources

### Fixed

#### Meter Count Display for "Unknown" Values
- **Daily cost sensors**: Meter count now displays even when sensor value is "Unknown"
  - Fixed `cost_daily_metered_hot_water` showing `meter_count: 0` when value was "Unknown"
  - Fixed `cost_daily_metered_combined_water` showing `meter_count: 0` when value was "Unknown"
  - Solution: Write state directly when value is `None` but meters exist (bypasses `_async_write_ha_state_if_changed` which skips `None` values)
- **Monthly cost sensors**: Meter count now displays even when sensor value is "Unknown"
  - Ensures meter information is available regardless of data availability
- **Combined water sensors**: Meter counts now display even when sensor value is "Unknown"
  - Both hot and cold water meter counts remain visible during data gaps

#### Estimated Cost Sensor Meter Count
- **Fixed `cost_monthly_accumulated_estimated_combined_water`**: Now properly shows meter count instead of `meter_count: 0`
  - Updated `create_monthly_meter_data_getter` to handle `estimated` cost_type
  - Returns `0.0` for estimated costs when no data exists to ensure meters are counted
- **Fixed `cost_monthly_total_estimated`**: Added missing meter count entirely
  - Now includes meter information for all water utilities

#### Monthly Cost Sensor Meter Count Fallback
- **Fixed `cost_monthly_accumulated_metered_cold_water`**: Now properly shows meter count instead of `meter_count: 0`
  - Added fallback to calculate monthly cost meter_count from daily price cache when per-meter monthly cache entries are missing
  - Handles edge cases where monthly cache data is incomplete but daily data is available

### Technical Improvements

#### Code Refactoring
- **Extracted `create_monthly_meter_data_getter` helper function**: Centralized meter data collection logic
  - Reduces code duplication by ~65 lines in monthly sensors
  - Ensures consistent meter count handling across all sensor types
- **Extracted `get_month_timestamps` usage**: Standardized month boundary calculations
  - All sensors now use shared helpers for consistency
- **Shared `collect_meters_with_data` helper**: All sensors now use unified meter collection logic
  - Improved code maintainability and consistency
  - Easier to update meter collection logic in the future

#### Testing
- **Added comprehensive tests for monthly sensor meter_count functionality**: 15 meter_count/cost related tests
  - Tests cover meter count collection from monthly cache
  - Tests cover fallback to daily cache when monthly cache is incomplete
  - Updated tests to reflect real-world scenarios (missing per-meter monthly cache entries)
  - All tests passing

### Impact
- All aggregate sensors now consistently show meter count
- Meter count visible even when sensor value is "Unknown" (improves debugging and monitoring)
- Estimated cost sensors properly include meters in count
- Total monthly cost sensor now includes meter information
- Improved code maintainability through shared helpers
- Better visibility into data sources for all aggregate calculations

## [3.2.0] - 2026-01-11

### Added

#### Value-Based State Writes
- **Automatic recorder optimization**: Sensors now implement intelligent value-based state writes that automatically reduce recorder database entries while maintaining accurate historical data
  - Sensors only write state (and thus get recorded) when values or context (date/month) meaningfully change
  - **Daily sensors**: Record once per day (when date changes), even though they update hourly internally
  - **Monthly sensors**: Record daily to track progression of running totals throughout the month
  - **Combined sensors**: Only record when all dependencies (e.g., HW + CW) have data available
  - Works automatically without any user configuration
  - UI remains responsive as sensors update internally, but only meaningful state changes are recorded

#### Recording Configuration Metadata
- **Sensor attributes**: All sensors now expose recording configuration metadata in state attributes
  - `recording_enabled`: Recommended recording setting
  - `recording_interval_seconds`: Recommended interval in seconds (if set)
  - `recording_interval`: Human-readable interval (e.g., "1 day(s)", "1 hour(s)")

#### Comprehensive Test Suite
- **Value-based state write tests**: Added comprehensive test suite with 21 tests covering:
  - Core logic (`_should_write_state`, `_async_write_ha_state_if_changed`)
  - Daily sensor integration (date change detection)
  - Monthly sensor integration (date/month change detection)
  - Combined sensor data completeness (dependency waiting)
  - Verification that "unknown" states are never written programmatically

### Changed

#### Recording Behavior (Minor Breaking Change)
- **Historical data granularity**: Sensors will no longer record every update
  - **Daily sensors**: One entry per day (instead of hourly)
  - **Monthly accumulated sensors**: Daily progression entries (instead of every update)
  - This provides more meaningful historical data with significantly reduced database size
  - The UI always shows current values (sensors update internally, just don't record every update)

### Fixed

#### Unknown State Prevention
- **Prevented programmatic "unknown" state recording**: Integration code now never writes "unknown" states to the database
  - Sensors wait for valid data before writing state
  - Combined sensors wait for all dependencies before recording
  - Note: Home Assistant automatically records an initial "unknown" state when entities are first registered (on startup/restart). This is expected Home Assistant core behavior and cannot be prevented.

#### Data Completeness
- **Combined sensors**: Fixed to only record when all dependencies have data
  - Prevents recording partial/incomplete totals during startup
  - Ensures accurate combined values (e.g., HW + CW) are only recorded when both utilities have data
- **Zero consumption handling**: Fixed combined water sensor to allow zero consumption values (changed `> 0` to `>= 0`)

#### Code Quality
- **Removed redundant state writes**: Fixed issue where `_async_write_ha_state_if_changed()` was called with `None` values
  - Method returns early for `None` values, so these calls were no-ops
  - Removed unnecessary calls to prevent confusion and improve code clarity
  - Availability status will be updated when sensors next write state with valid values

### Technical Improvements

#### Code Refactoring
- **Centralized value-based write logic**: Added `_should_write_state()` and `_async_write_ha_state_if_changed()` methods to base sensor class
  - Tracks last written value and context (date/month) to determine when state should be written
  - Reduces code duplication across sensor types
  - Follows Home Assistant best practices (doesn't override core methods)

#### Documentation
- **Updated README**: Added concise recorder configuration section explaining value-based state writes
- **Clarified "unknown" state behavior**: Documented that Home Assistant automatically records initial "unknown" states on entity registration

### Minor Breaking Changes
- **Historical data format**: Sensors will now show fewer entries in historical data
  - Daily sensors: One entry per day instead of hourly
  - Monthly accumulated sensors: Daily progression instead of every update
  - This is intentional and provides more meaningful data with reduced database size
  - The UI always shows current values, but historical data has appropriate granularity

## [3.1.0] - 2026-01-10

### Added

#### Lag Detection for Daily Sensors
- **Data lag detection and tracking**: All daily sensors now expose lag detection attributes to identify when API data is outdated
  - `data_lagging` (boolean): Indicates whether the sensor data is lagging behind the expected date
  - `data_lag_days` (integer or None): Number of days the data is lagging, or None when data is unavailable
  - Sensors are marked as lagging when data is older than expected (typically 1 day behind current date)
  - Example use case: Identify when hot water data hasn't been updated for several days while cold water data is current

#### Helper Functions (`helpers.py`)
- **`find_last_data_date()`**: Finds the most recent date with valid consumption data from daily cache
  - Skips None values (no data returned by API)
  - Skips negative values (invalid consumption data)
  - Returns the most recent timestamp with actual valid data
- **`find_last_price_date()`**: Finds the most recent date with valid price data from daily cache
  - Prefers non-zero values (since 0 might indicate missing price data for some utilities)
  - Falls back to zero values if no non-zero values are found
  - Uses optimized single-pass algorithm for efficiency
- **`detect_data_lag()`**: Detects whether data is lagging behind expected date
  - Compares actual last data date to expected date (default: yesterday, accounting for 1-day API delay)
  - Returns tuple of (is_lagging: bool, lag_days: int | None)
  - Handles None values (returns lagging=True, lag_days=None)
  - Handles future dates (logs warning, returns not lagging)
  - Configurable expected delay parameter (default: 1 day)

#### Sensor Updates
- **Daily consumption sensors**: Now track and expose lag detection attributes
  - Individual meter sensors use `find_last_data_date()` from daily consumption cache
  - Aggregate sensors combine data from multiple meters
  - Combined water sensors use the earliest date from hot/cold water for conservative lag detection
- **Daily cost sensors**: Extended lag detection to all cost tracking
  - Metered cost sensors use `find_last_price_date()` from daily price cache
  - Estimated cost sensors use `find_last_data_date()` from daily consumption cache (since calculated from consumption)
  - Aggregate and combined sensors properly track lag across multiple data sources

#### Logging Improvements
- **Enhanced log messages**: Sensor updates now include lag information in logs
  - When `data_lag_days` is None: "data unavailable"
  - When lagging: "lagging X days"
  - Example: `Updated sensor.consumption_daily_metered_hot_water: 2.5 -> 2.7 m³ (lagging 4 days)`

### Changed

#### Sensor Attributes
- **Renamed `last_data_date_readable` to `last_data_date`**: For consistency and clarity
  - Both attributes contain ISO 8601 formatted datetime strings
  - Old attribute name removed (minor breaking change)
  - `last_data_date` now reflects the actual last date where the API returned valid data (not just latest cache timestamp)

#### Lag Detection Consistency
- **Improved consistency across all daily sensors**:
  - All sensors set `_data_lagging = True` when coordinator data is unavailable (previously inconsistent)
  - Lag detection performed consistently after determining last data date
  - Log message formatting handles None values properly (shows "data unavailable" instead of "lagging None days")

### Fixed

#### Edge Case Handling
- **Negative value handling**: `find_last_data_date()` now skips negative consumption values (invalid data)
- **Future date handling**: `detect_data_lag()` now properly handles future dates with warning log
- **UnboundLocalError prevention**: Fixed potential error in `EcoGuardDailyCostSensor` when timezone variable was undefined

#### Algorithm Optimization
- **`find_last_price_date()` optimization**: Refactored to use single-pass algorithm instead of dual-loop pattern
  - More efficient iteration through cache
  - Clearer logic for preferring non-zero values with zero fallback

### Technical Improvements

#### Testing
- **Comprehensive test coverage added** in `test_helpers.py`:
  - `test_find_last_data_date()`: Tests with empty cache, valid data, None values, negative values, unsorted data
  - `test_find_last_price_date()`: Tests with empty cache, non-zero prices, zero-only prices, None values
  - `test_detect_data_lag()`: Tests with None values, past dates, future dates, custom expected delays
  - All edge cases covered including negative values and future dates

#### Code Quality
- **Improved documentation**: Enhanced docstrings for all helper functions with clear parameter and return descriptions
- **Better error messages**: Lag-related log messages now provide more context and handle edge cases gracefully

### Breaking Changes (Minor)
- **`last_data_date_readable` attribute renamed to `last_data_date`**: This affects any automations or dashboards using the old attribute name. Both attributes contain ISO 8601 formatted datetime strings, so migration is straightforward. This is considered a minor breaking change requiring only a minor version bump.

## [3.0.0] - 2025-01-09

### Breaking Changes
- **Renamed monthly sensors from "Aggregated" to "Accumulated"**: Monthly sensors that track running totals for the current month have been renamed to use "Accumulated" terminology instead of "Aggregated"
  - `Consumption Monthly Aggregated` → `Consumption Monthly Accumulated`
  - `Cost Monthly Aggregated` → `Cost Monthly Accumulated`
  - Entity IDs have changed: `consumption_monthly_aggregated_*` → `consumption_monthly_accumulated_*`
  - Entity IDs have changed: `cost_monthly_aggregated_*` → `cost_monthly_accumulated_*`
  - **Note**: "Aggregated" is now reserved for sensors that combine multiple meters (daily aggregate sensors). "Accumulated" is used for monthly running totals that build up over time.

- **Added `_meter_` indicator to individual meter sensor entity IDs**: All individual meter sensors now include `_meter_` in their entity IDs for clarity. This is a breaking change as entity IDs have changed.
  - Daily consumption: `consumption_daily_metered_{utility}_{meter_name}` → `consumption_daily_metered_{utility}_meter_{meter_name}`
  - Daily cost: `cost_daily_metered_{utility}_{meter_name}` → `cost_daily_metered_{utility}_meter_{meter_name}`
  - Daily cost (estimated): `cost_daily_estimated_{utility}_{meter_name}` → `cost_daily_estimated_{utility}_meter_{meter_name}`
  - Monthly consumption: `consumption_monthly_accumulated_{utility}_{meter_name}` → `consumption_monthly_accumulated_{utility}_meter_{meter_name}`
  - Monthly cost: `cost_monthly_accumulated_metered_{utility}_{meter_name}` → `cost_monthly_accumulated_metered_{utility}_meter_{meter_name}`
  - Monthly cost (estimated): `cost_monthly_accumulated_estimated_{utility}_{meter_name}` → `cost_monthly_accumulated_estimated_{utility}_meter_{meter_name}`
  - Reception: `reception_last_update_{utility}_{meter_name}` → `reception_last_update_{utility}_meter_{meter_name}`
  - **Note**: Aggregate/accumulated sensors (combining multiple meters) do NOT include `_meter_` in their entity IDs.

### Changed
- Updated all translation keys: `consumption_monthly_aggregated` → `consumption_monthly_accumulated`, `cost_monthly_aggregated` → `cost_monthly_accumulated`
- Updated class names: `EcoGuardMonthlyAggregateSensor` → `EcoGuardMonthlyAccumulatedSensor`
- Updated function names: `create_monthly_aggregate_sensors` → `create_monthly_accumulated_sensors`
- Updated base translation file (`strings.json`) to use "accumulated" terminology
- Updated entity registry updater to use new translation keys
- Updated documentation

## [2.0.0] - 2026-01-07

Major bump because breaking changes. Strictly following semantic versioning.

### Added

#### New Sensor Types
- **Daily Cost Sensors**: Daily cost tracking for individual meters and aggregated by utility
  - Separate sensors for metered (actual API data) and estimated (calculated) costs
  - Format: `Cost Daily Metered - Meter "Measuring Point" (Utility)` or `Cost Daily Metered - Utility`
- **Monthly Meter Sensors**: Monthly consumption and cost per individual meter
  - Format: `Consumption Monthly Accumulated - Meter "Measuring Point" (Utility)`
  - Format: `Cost Monthly Accumulated Metered - Meter "Measuring Point" (Utility)`
- **Combined Water Sensors**: Combined hot and cold water consumption and costs
  - Daily consumption: `Consumption Daily Metered - Combined Water`
  - Daily cost: `Cost Daily Metered - Combined Water` / `Cost Daily Estimated - Combined Water`

#### Translation Support
- Added Norwegian (Nynorsk) translations (`translations/nn.json`)
- Added Swedish translations (`translations/sv.json`)
- Improved translation system with better entity registry name updates
- Sensor names now properly translate in both list view and modal dialogs

#### Sensor Descriptions
- **Added translated descriptions for all sensors**: Each sensor now includes a description explaining its purpose
  - Descriptions are available in entity attributes for easy reference
  - Descriptions are translated based on Home Assistant language setting
  - Available in English, Norwegian (Bokmål), Norwegian (Nynorsk), and Swedish
  - Descriptions explain what each sensor measures, data sources, and any important notes
  - View descriptions in Developer Tools → States or in entity attributes

#### Sensor Icons
- **Added icons to all sensors**: All sensors now display appropriate icons in the Home Assistant UI
  - **Consumption sensors**: Utility-specific icons
    - Hot Water (HW): `mdi:water-thermometer`
    - Cold Water (CW): `mdi:water`
    - Electricity (E): `mdi:lightning-bolt`
    - Heat (HE): `mdi:radiator`
  - **Cost/Price sensors**: Dollar icon (`mdi:currency-usd`) for all money-related sensors
    - Daily cost sensors (metered and estimated)
    - Monthly cost sensors (metered and estimated)
    - Combined water cost sensors
    - Total monthly cost sensors
    - Other items cost sensor
    - End-of-month estimate sensor
  - **Latest reception sensors**: Clock icon (`mdi:clock-outline`)
  - **Combined water consumption**: Water circle icon (`mdi:water-circle`)

### Changed

#### Sensor Naming Conventions

Sensor names have been restructured to improve grouping and sorting in lists. While some names may now feel less natural, this change makes it easier to locate sensors by their purpose and category.

- **Consumption sensors**: Renamed from "Daily Consumption" to "Consumption Daily" format
  - Individual meters: `Consumption Daily - Meter "Measuring Point" (Utility)`
  - Aggregated: `Consumption Daily Metered - Utility`
- **Cost sensors**: Renamed to "Cost Daily" and "Cost Monthly Accumulated" format
  - Individual meters: `Cost Daily Metered - Meter "Measuring Point" (Utility)`
  - Accumulated: `Cost Monthly Accumulated Metered - Utility`
- **Reception sensors**: Renamed from "Latest Measurement" to "Reception Last Update"
  - Format: `Reception Last Update - Meter "Measuring Point" (Utility)`
- **Total cost sensors**: Updated to "Cost Monthly Accumulated Metered/Estimated - All Utilities"
- All sensor names now use consistent hyphen separators for better readability
- "Meter" prefix added to all individual meter sensor names for clarity

#### Entity Management
- **Individual meter sensors disabled by default**: To reduce clutter, the following sensors are now disabled by default:
  - Daily consumption sensors for individual meters
  - Daily cost sensors for individual meters
  - Monthly meter sensors (consumption and cost)
  - Latest reception sensors
- Users can enable these sensors in Settings → Devices & Services → EcoGuard → Entities if needed
- Existing enabled sensors remain enabled (only new installations are affected)

#### Entity ID Format
- Updated entity ID format to follow pattern: `{purpose}_{group}_{utility}_{sensor_name}` for aggregate/accumulated sensors
- Individual meter sensors include `_meter_` indicator: `{purpose}_{group}_{utility}_meter_{sensor_name}`
- Removed node ID prefix from entity IDs for cleaner naming
- Examples:
  - Aggregate: `sensor.consumption_daily_metered_cold_water`
  - Individual meter: `sensor.consumption_daily_metered_cold_water_meter_kaldtvann_bad`
  - Monthly accumulated: `sensor.consumption_monthly_accumulated_cold_water`
  - Monthly meter: `sensor.consumption_monthly_accumulated_cold_water_meter_kaldtvann_bad`

#### Translation Keys
- Updated translation keys to match new naming conventions:
  - `name.daily_consumption` → `name.consumption_daily`
  - `name.last_update` → `name.reception_last_update`
  - Added `name.cost_daily` for daily cost sensors
  - Added `name.meter` for meter prefix
  - Added `name.all_utilities` for total cost sensors
  - Added `name.combined` and `name.combined_water` for combined sensors

#### Request Deduplication
- **All API-calling methods now use consistent deduplication pattern**:
  - `_fetch_monthly_price_from_api`: Added atomic task creation with lock protection
  - `_get_monthly_price_cw`: Added atomic task creation with lock protection
  - `_get_monthly_price_hw_estimated`: Added atomic task creation with lock protection
  - `_fetch_monthly_consumption_from_api`: Added atomic task creation with lock protection
  - `_get_latest_price_data`: Added atomic task creation with lock protection
  - `get_monthly_aggregate`: Added atomic task creation with lock protection
  - `get_end_of_month_estimate`: Now uses daily cache first and deduplicates API calls
- Tasks are now created and added to `_pending_requests` atomically inside locks
- Final check for pending tasks before creating new ones to prevent race conditions
- Moved log messages to only print when API calls are actually made (not when deduplicated)

### Fixed

#### Translation Issues
- Fixed sensor titles not translating in modal dialogs
- Improved entity registry name updates to ensure translations are applied correctly
- Fixed timing issues where entity registry updates happened before entities were registered

#### Data Handling
- Fixed daily cost sensors to properly use last available day with data (accounting for API delays)
- Fixed "Cost Daily Metered" sensors to strictly use actual price data (no fallback to estimated)
- Improved lookback period for daily cost data (increased to 30 days)
- Fixed estimated cost sensors to properly calculate hot water costs using Nord Pool spot prices
  - Daily estimated cost sensors now trigger async fetch when metered data unavailable
  - Monthly estimated aggregate sensors now trigger async fetch when cache data missing
  - Hot water estimated costs calculated using spot prices, calibration ratio, and cold water rates
- Fixed combined water cost sensors to only show value when both hot and cold water data available
- Fixed estimated daily cost sensors to use metered cost when available (estimated = metered when metered exists)
- Fixed monthly combined water sensors to show "Unknown" when hot water data is missing (consistent with daily sensors)
- Fixed `consumption_monthly_combined_water` sensor to display "m³" unit correctly
- Fixed per-meter estimated cost sensors to be self-sufficient (fetch aggregate data directly when needed)
  - Sensors now work independently regardless of which other sensors are enabled/disabled
  - Proportional allocation calculated automatically when aggregate data becomes available

#### Rate Limit Errors
- **Eliminated all API rate limit errors** by implementing comprehensive request deduplication
- Fixed race conditions in task creation that allowed duplicate API calls
- Added atomic task creation with lock protection to ensure only one request per unique data
- Fixed `get_end_of_month_estimate` to use daily cache first and add deduplication for API calls

### Technical Improvements

#### Performance & Startup
- **Fast startup**: Implemented cache-first approach for instant sensor creation
  - Sensors created immediately with "Unknown" state, no API calls during startup
  - Data fetching deferred until after Home Assistant fully starts
  - Post-setup data fetching triggered after config flow completion
  - Background batch fetching prevents blocking UI during startup
- **Optimized data fetching**: 
  - Batch fetching consolidates multiple API calls into efficient requests
  - Smart cache reuse: monthly aggregates calculated from daily cache data
  - Non-blocking coordinator refresh prevents startup delays
  - Initial 30-day fetch for comprehensive data coverage (async, non-blocking)
  - **Request deduplication**: Ensures only one API call per unique request, eliminating duplicate requests
  - **Better cache utilization**: `get_end_of_month_estimate` now uses daily cache before making API calls
  - **Reduced API calls**: All concurrent requests for the same data now share a single API call

#### Code Quality
- Standardized entity registry updates to use `unique_id` instead of `entity_id` for better reliability
- Improved translation system with consistent fallback handling
- Added comprehensive test coverage for new sensor types
- Updated all tests to reflect new naming conventions and entity formats
- Migrated to `ConfigEntry.runtime_data` for type-safe runtime data storage
- Improved error handling with proper resource cleanup (API session closing)
- Refactored `get_monthly_aggregate` into smaller, focused helper methods for better maintainability:
  - `_get_month_timestamps`: Calculates month start/end timestamps
  - `_calculate_monthly_price_from_daily_cache`: Calculates monthly price from cached daily prices
  - `_fetch_monthly_price_from_api`: Fetches monthly price from API with deduplication
  - `_get_monthly_price_actual`: Handles fetching/calculating "actual" monthly price
  - `_get_monthly_price_cw`: Handles CW monthly price with deduplication
  - `_get_monthly_price_hw_estimated`: Handles HW estimated monthly price with deduplication
  - `_calculate_monthly_consumption_from_daily_cache`: Calculates monthly consumption from cached daily data
  - `_fetch_monthly_consumption_from_api`: Fetches monthly consumption from API with deduplication

#### Code Refactoring
- **Extracted code from `coordinator.py` into separate modules** to improve maintainability:
  - `billing_manager.py`: Billing data fetching, caching, and extraction logic
    - `get_cached_billing_results`: Billing results with caching and request deduplication
    - `get_rate_from_billing`: Extract rates from billing data
    - `get_monthly_other_items_cost`: Extract other items cost from billing data
    - `get_monthly_price_from_billing`: Get monthly price from billing or calculate from consumption × rate
    - `calculate_hw_calibration_ratio`: Calculate HW calibration ratio from historical billing data
  - `helpers.py`: Common helper functions
    - `get_timezone`: Centralized timezone handling
    - `get_month_timestamps`: Month boundary timestamp calculations
    - `get_date_range_timestamps`: Date range timestamp calculations
    - `format_cache_key`: Cache key formatting utility
    - `log_static_info_summary`: Static info logging utility
    - `round_to_max_digits`: Rounding utility for sensor values
  - `nord_pool.py`: Nord Pool spot price fetching
    - `NordPoolPriceFetcher`: Encapsulates Nord Pool API interaction with caching
  - `price_calculator.py`: Hot water price calculation logic
    - `HWPriceCalculator`: Encapsulates complex HW price calculation with calibration
  - `request_deduplicator.py`: Request deduplication pattern
    - `RequestDeduplicator`: Reusable class for deduplicating async requests
  - `meter_aggregate_calculator.py`: Per-meter aggregate calculations
    - `MeterAggregateCalculator`: Encapsulates per-meter monthly aggregate calculation logic
  - `data_processor.py`: Batch sensor data processing
    - `DataProcessor`: Handles batch fetching, processing, and caching of sensor data
- Updated `sensor.py` to use `billing_manager` directly instead of delegation methods
- Added call_id tracking to `get_end_of_month_estimate` for debugging concurrent calls
- **Self-sufficient sensors**: Per-meter estimated cost sensors now fetch aggregate data directly
  - No dependency on other sensors being enabled or finishing first
  - Proportional allocation calculated automatically when aggregate data is available
  - Each sensor operates independently for optimal data loading

#### Entity Registry Management
- Improved entity registry update logic to handle timing issues
- Added background task scheduling for entity registry updates after entity creation
- Better handling of existing vs. new entities when disabling individual meter sensors

#### Data Layer Improvements
- **Centralized API calls**: All HTTP requests moved to coordinator, sensors read from cache
- **Smart caching**: Daily consumption and price data cached for reuse in monthly calculations
- **Estimated cost calculations**: 
  - Hot water estimated costs use Nord Pool spot prices with calibration
  - Cold water estimated costs fall back to metered when available
  - Combined water sensors wait for both utilities before showing value
- **Cache management**: Deep copy data when updating coordinator to ensure HA change detection
- **Explicit listener notifications**: Proper update mechanisms to ensure sensors receive data

### Documentation
- Updated README.md with comprehensive sensor documentation
- Added examples of all new sensor types and formats
- Documented which sensors are disabled by default
- Added troubleshooting section for translation issues
- Updated translation files section with all supported languages

## [1.0.0] - 2026-01-06

### Added

#### Core Features
- Initial release of the EcoGuard Home Assistant integration
- Config flow for easy setup via Home Assistant UI
- Support for EcoGuard Curves platform API integration
- Automatic sensor discovery and creation based on available data

#### Sensors
- **Daily Consumption Sensors**: Last known daily consumption (m³) for each meter
  - Separate sensors for hot water (HW) and cold water (CW)
  - Named with measuring point and utility type for easy identification
- **Latest Reception Sensors**: Timestamp of last data reception per measuring point
  - Shows when data was last received by EcoGuard systems
  - Includes utility type in sensor name
- **Month-to-Date Consumption Sensors**: Running totals for current month
  - Separate sensors for hot water and cold water
- **Month-to-Date Cost Sensors**:
  - **Metered**: Actual price data from API (when available)
  - **Estimated**: Estimated prices using Nord Pool spot prices for hot water heating
- **Total Monthly Cost Sensors**:
  - **Metered**: Sum of actual metered costs from API
  - **Estimated**: Includes estimated hot water costs when actual data unavailable
- **Other Items Cost Sensor**: General fees and charges from last billing period
- **Cost Monthly Estimated Final Settlement Sensor**: Estimated final monthly bill settlement based on current consumption patterns

#### Nord Pool Integration
- Optional Nord Pool area configuration during setup
- Automatic hot water cost estimation using Nord Pool electricity spot prices
- Support for all Nord Pool pricing areas (NO1-NO5, SE1-SE4, DK1-DK2, FI, EE, LV, LT)
- Link to Nord Pool area map in configuration form

#### Internationalization
- Full translation support for Norwegian (Bokmål)
- English as default language
- Translated sensor names and labels
- Translated configuration form with helpful descriptions
- Field-level help text in configuration form
- Automatic language detection based on Home Assistant system language

#### User Experience
- User-friendly configuration form with:
  - Clear field labels and descriptions
  - Help text for each configuration field
  - Link to Nord Pool area map for easy area identification
  - Validation and error messages
- Optimized sensor naming for alphabetical sorting:
  - Sensors grouped by type (Daily Consumption, Last Update, etc.)
  - Then by utility type (Hot Water, Cold Water)
  - Finally by measuring point name
  - Format: `Sensor Type (Utility) - "Measuring Point"`
- Measuring point names displayed in quotation marks for clarity
- Utility types shown in parentheses for easy identification

#### Technical Features
- Asynchronous API communication
- Coordinated data updates with appropriate intervals:
  - Consumption data: 1 hour
  - Latest reception: 5 minutes
  - Billing data: 24 hours
- Automatic VAT detection and removal from prices
- Smart price estimation when API data unavailable
- Comprehensive error handling and logging
- Support for multiple measuring points and utilities
- Device information and attributes for each sensor

### Technical Details
- Built for Home Assistant 2025.12.5 and later
- Uses Home Assistant's config flow framework
- Implements coordinator pattern for efficient data updates
- Non-blocking async file I/O for translations
- Translation caching for performance
- Proper entity state management

### Documentation
- Comprehensive README with installation instructions
- Feature documentation
- Sensor documentation
- Configuration guide
- Nord Pool integration explanation

[3.3.0]: https://github.com/skogsmaskin/ecoguard-home-assistant/releases/tag/v3.3.0
[3.2.0]: https://github.com/skogsmaskin/ecoguard-home-assistant/releases/tag/v3.2.0
[3.1.0]: https://github.com/skogsmaskin/ecoguard-home-assistant/releases/tag/v3.1.0
[3.0.0]: https://github.com/skogsmaskin/ecoguard-home-assistant/releases/tag/v3.0.0
[2.0.0]: https://github.com/skogsmaskin/ecoguard-home-assistant/releases/tag/v2.0.0
[1.0.0]: https://github.com/skogsmaskin/ecoguard-home-assistant/releases/tag/v1.0.0
