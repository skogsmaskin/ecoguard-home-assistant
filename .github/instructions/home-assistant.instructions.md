# Home Assistant Integration Specific Instructions

## Integration Type
- **Integration Type**: `service` (defined in manifest.json)
- **IoT Class**: `cloud_polling` (polls EcoGuard cloud API)
- **Config Flow**: Yes - UI-based configuration only (no YAML configuration)

## Home Assistant Patterns

### Config Entry Setup
Use the modern `runtime_data` pattern (not `hass.data[DOMAIN]`):
```python
@dataclass
class EcoGuardRuntimeData:
    """Runtime data for EcoGuard config entry."""
    coordinator: EcoGuardDataUpdateCoordinator
    latest_reception_coordinator: EcoGuardLatestReceptionCoordinator
    api: EcoGuardAPI
    entity_registry_update_task: Task | None = None
```

### Entry Points
- `async_setup()` - Component-level setup (register services here, not in async_setup_entry)
- `async_setup_entry()` - Config entry setup (create coordinators, platforms)
- `async_unload_entry()` - Cleanup when entry is removed

### Platform Setup
Only the sensor platform is supported:
```python
PLATFORMS: list[Platform] = [Platform.SENSOR]
```

## Sensor Architecture

### Sensor Types
The integration creates several sensor types:

1. **Daily Consumption Sensors**
   - Individual meters: `EcoGuardDailyConsumptionSensor` (disabled by default)
   - Aggregated by utility: `EcoGuardDailyAggregateSensor`
   - Combined water: `EcoGuardCombinedWaterDailyAggregateSensor`

2. **Daily Cost Sensors**
   - Individual meters: `EcoGuardDailyCostSensor` (disabled by default)
   - Aggregated by utility: `EcoGuardDailyCostAggregateSensor`

3. **Monthly Consumption Sensors**
   - Individual meters: `EcoGuardMonthlyMeterSensor` (disabled by default)
   - Accumulated by utility: `EcoGuardMonthlyAccumulatedSensor`
   - Combined water: `EcoGuardCombinedWaterMonthlySensor`

4. **Monthly Cost Sensors**
   - Individual meters: Separate metered/estimated variants (disabled by default)
   - Accumulated by utility: `EcoGuardMonthlyCostAccumulatedSensor`
   - Total all utilities: `EcoGuardMonthlyCostAllUtilitiesSensor`

5. **Special Sensors**
   - Other items cost: `EcoGuardOtherItemsCostSensor`
   - Estimated final settlement: `EcoGuardEstimatedFinalSettlementSensor`

6. **Reception Sensors**
   - Latest reception timestamp: `EcoGuardLatestReceptionSensor` (disabled by default)

### Entity Naming Convention
- **Individual meter sensors**: Include `_meter_` in entity_id (disabled by default)
  - Example: `sensor.consumption_daily_metered_cold_water_meter_kaldtvann_bad`
- **Aggregate/accumulated sensors**: No `_meter_` in entity_id
  - Example: `sensor.consumption_daily_metered_cold_water`

### Translation Keys
Sensors use translation keys for localization:
- Terminology: Use "Accumulated" for monthly running totals, "Aggregated" for combining multiple meters
- Sensor names follow pattern: `name.consumption_daily`, `name.cost_monthly_accumulated`, etc.
- Utility codes: `utility.hw` (Hot Water), `utility.cw` (Cold Water)

### Entity Registry
- Use `entity_registry_enabled_default=False` for individual meter sensors to reduce clutter
- All aggregate/accumulated sensors are enabled by default
- Use `update_entity_registry_with_timeout()` to update entity names when language changes

## Data Coordinators

### Update Intervals
```python
UPDATE_INTERVAL_DATA = 3600  # 1 hour for consumption data
UPDATE_INTERVAL_LATEST_RECEPTION = 300  # 5 minutes for reception timestamps
UPDATE_INTERVAL_BILLING = 86400  # 24 hours for billing data
```

### Coordinator Pattern
```python
class EcoGuardDataUpdateCoordinator(DataUpdateCoordinator):
    """Handle fetching EcoGuard data."""
    
    async def _async_update_data(self):
        """Fetch data from API."""
        try:
            # Fetch data with error handling
            return data
        except AuthenticationError:
            raise ConfigEntryAuthFailed
        except Exception as err:
            raise UpdateFailed(f"Error: {err}")
```

## API Integration

### Authentication
- Uses tenant authentication with username, password, and domain
- Access tokens are stored and refreshed automatically
- Authentication failures should raise `EcoGuardAuthenticationError`

### API Caching
The integration implements intelligent caching:
- Data requests cached for 60 seconds
- Billing results cached for 24 hours
- Nord Pool prices cached per day
- Request deduplication to prevent duplicate API calls

### Rate Limiting
- Limits concurrent API requests
- Automatic retry with exponential backoff
- Handles 429 (Too Many Requests) gracefully

## Quality Scale Requirements

### Current Status
Follow Home Assistant Integration Quality Scale rules:
- Bronze tier minimum requirements
- Configuration via UI only (no YAML)
- Test coverage for core functionality
- Follow Home Assistant code style

### Best Practices
- Use `async_get_entity_registry()` instead of deprecated patterns
- Clear translation cache when sensors are setup: `clear_translation_cache()`
- Use `ConfigEntry.runtime_data` for storing runtime state
- Implement proper cleanup in `async_unload_entry()`

## Utility Codes
```python
UTILITY_HOT_WATER = "HW"
UTILITY_COLD_WATER = "CW"
UTILITY_ELECTRICITY = "E"
UTILITY_HEAT = "HE"
WATER_UTILITIES = (UTILITY_HOT_WATER, UTILITY_COLD_WATER)
```

## Nord Pool Integration
- Optional feature for estimating hot water heating costs
- Uses Nord Pool spot prices (e.g., NO1, SE3, DK1)
- Calibrates estimates against historical billing data
- Only used when actual API price data is unavailable

## Device and Entity Information
- Sensors are grouped by integration (no device grouping currently)
- Each sensor has appropriate device_class, state_class, and unit_of_measurement
- Use `SensorDeviceClass.MONETARY` for cost sensors with currency from config
- Use `SensorDeviceClass.WATER` for consumption sensors with m³ unit
