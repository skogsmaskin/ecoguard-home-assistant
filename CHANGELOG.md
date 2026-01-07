# Changelog

All notable changes to the EcoGuard Home Assistant integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-01-07

Major bump because breaking changes. Strictly following semantic versioning, though v. 1.0.0 was
released yesterday :)

### Added

#### New Sensor Types
- **Daily Cost Sensors**: Daily cost tracking for individual meters and aggregated by utility
  - Separate sensors for metered (actual API data) and estimated (calculated) costs
  - Format: `Cost Daily Metered - Meter "Measuring Point" (Utility)` or `Cost Daily Metered - Utility`
- **Monthly Meter Sensors**: Monthly consumption and cost per individual meter
  - Format: `Consumption Monthly Aggregated - Meter "Measuring Point" (Utility)`
  - Format: `Cost Monthly Aggregated Metered - Meter "Measuring Point" (Utility)`
- **Combined Water Sensors**: Combined hot and cold water consumption and costs
  - Daily consumption: `Consumption Daily - Combined Water`
  - Daily cost: `Cost Daily Metered - Combined Water` / `Cost Daily Estimated - Combined Water`

#### Translation Support
- Added Norwegian (Nynorsk) translations (`translations/nn.json`)
- Added Swedish translations (`translations/sv.json`)
- Improved translation system with better entity registry name updates
- Sensor names now properly translate in both list view and modal dialogs

### Changed

#### Sensor Naming Conventions
- **Consumption sensors**: Renamed from "Daily Consumption" to "Consumption Daily" format
  - Individual meters: `Consumption Daily - Meter "Measuring Point" (Utility)`
  - Aggregated: `Consumption Daily - Utility`
- **Cost sensors**: Renamed to "Cost Daily" and "Cost Monthly Aggregated" format
  - Individual meters: `Cost Daily Metered - Meter "Measuring Point" (Utility)`
  - Aggregated: `Cost Monthly Aggregated Metered - Utility`
- **Reception sensors**: Renamed from "Latest Measurement" to "Reception Last Update"
  - Format: `Reception Last Update - Meter "Measuring Point" (Utility)`
- **Total cost sensors**: Updated to "Cost Monthly Aggregated Metered/Estimated - All Utilities"
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
- Updated entity ID format to follow pattern: `{purpose}_{group}_{utility}_{sensor_name}`
- Removed node ID prefix from entity IDs for cleaner naming
- Example: `sensor.consumption_daily_cold_water_kaldtvann_bad`

#### Translation Keys
- Updated translation keys to match new naming conventions:
  - `name.daily_consumption` → `name.consumption_daily`
  - `name.last_update` → `name.reception_last_update`
  - Added `name.cost_daily` for daily cost sensors
  - Added `name.meter` for meter prefix
  - Added `name.all_utilities` for total cost sensors
  - Added `name.combined` and `name.combined_water` for combined sensors

### Fixed

#### Translation Issues
- Fixed sensor titles not translating in modal dialogs
- Improved entity registry name updates to ensure translations are applied correctly
- Fixed timing issues where entity registry updates happened before entities were registered

#### Data Handling
- Fixed daily cost sensors to properly use last available day with data (accounting for API delays)
- Fixed "Cost Daily Metered" sensors to strictly use actual price data (no fallback to estimated)
- Improved lookback period for daily cost data (increased to 30 days)

### Technical Improvements

#### Code Quality
- Standardized entity registry updates to use `unique_id` instead of `entity_id` for better reliability
- Improved translation system with consistent fallback handling
- Added comprehensive test coverage for new sensor types
- Updated all tests to reflect new naming conventions and entity formats

#### Entity Registry Management
- Improved entity registry update logic to handle timing issues
- Added background task scheduling for entity registry updates after entity creation
- Better handling of existing vs. new entities when disabling individual meter sensors

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

[1.0.0]: https://github.com/skogsmaskin/ecoguard-home-assistant/releases/tag/v1.0.0
