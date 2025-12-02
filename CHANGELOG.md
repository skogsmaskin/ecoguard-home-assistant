# Changelog

All notable changes to the EcoGuard Home Assistant integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- **Month-to-Date Price Sensors**:
  - **Metered**: Actual price data from API (when available)
  - **Estimated**: Estimated prices using Nord Pool spot prices for hot water heating
- **Total Monthly Cost Sensors**:
  - **Metered**: Sum of actual metered costs from API
  - **Estimated**: Includes estimated hot water costs when actual data unavailable
- **Other Items Cost Sensor**: General fees and charges from last billing period
- **End-of-Month Estimate Sensor**: Projected monthly bill based on current consumption patterns

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
