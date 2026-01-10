# Changelog

All notable changes to the EcoGuard Home Assistant integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[2.0.0]: https://github.com/skogsmaskin/ecoguard-home-assistant/releases/tag/v2.0.0
[1.0.0]: https://github.com/skogsmaskin/ecoguard-home-assistant/releases/tag/v1.0.0
