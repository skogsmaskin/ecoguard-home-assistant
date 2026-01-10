# API and Architecture Guidelines

## EcoGuard API Overview

### Base Information
- **Base URL**: `https://integration.ecoguard.se`
- **Authentication**: Tenant-based (username, password, domain)
- **Response Format**: JSON
- **Rate Limiting**: Yes (implement exponential backoff)

### Authentication Flow
```python
# 1. Authenticate to get access token
POST /token
Body: {
    "username": "user",
    "password": "pass",
    "domain": "domain_code"
}
Response: {
    "Authentication Tenant": "access_token",
    "RefreshToken": "refresh_token"  # Optional
}

# 2. Use Bearer token in subsequent requests
GET /api/{domaincode}/nodes
Headers: {
    "Authorization": "Bearer access_token"
}
```

### Key Endpoints
```python
# Get user information
GET /api/users/self

# Get nodes (properties/buildings)
GET /api/{domaincode}/nodes

# Get measuring points (water meters, etc.)
GET /api/{domaincode}/measuringpoints?nodeId={node_id}

# Get consumption data
GET /api/{domaincode}/data?nodeId={node_id}&from={timestamp}&to={timestamp}&interval=d

# Get latest reception timestamps
GET /api/{domaincode}/latestReception?nodeId={node_id}

# Get billing results
GET /api/{domaincode}/billingresults?nodeId={node_id}&from={timestamp}&to={timestamp}

# Get installations
GET /api/{domaincode}/installations?nodeId={node_id}
```

## Data Models

### Installation Data Structure
```python
{
    "ID": "installation_id",
    "Name": "Measuring Point Name",
    "UtilityCode": "HW",  # HW=Hot Water, CW=Cold Water
    "Active": True,
    "NodeID": "node_id"
}
```

### Consumption Data Structure
```python
{
    "Value": 150.5,  # Consumption value
    "TimeStamp": 1609459200,  # Unix timestamp
    "Function": "con",  # con=consumption, price=price
    "UtilityCode": "HW"
}
```

### Billing Data Structure
```python
{
    "UtilityCode": "HW",
    "Consumption": 100.5,
    "Price": 250.0,
    "Currency": "NOK",
    "PeriodFrom": 1609459200,
    "PeriodTo": 1612137600
}
```

## Architecture Patterns

### Data Update Flow
```
ConfigEntry Setup
    ↓
Create API Client
    ↓
Create Coordinators
    ↓
Coordinators Fetch Data (with caching)
    ↓
Sensors Update from Coordinator Data
```

### Coordinator Architecture
Two separate coordinators for different update intervals:

1. **EcoGuardDataUpdateCoordinator** (1 hour interval)
   - Fetches consumption data
   - Fetches billing data (cached 24h)
   - Manages installations
   - Calculates monthly totals

2. **EcoGuardLatestReceptionCoordinator** (5 minute interval)
   - Fetches latest reception timestamps
   - Independent from main data coordinator
   - Lighter weight updates

### Caching Strategy

#### API Response Caching
```python
# Short-term cache (60 seconds) - prevents duplicate requests
cache_key = f"{endpoint}_{params}"
if cache_key in cache and not expired:
    return cached_data

# Long-term cache (24 hours) - for historical data
if is_historical_data:
    cache_duration = 86400

# Per-day cache - for daily prices
cache_key = f"nordpool_{area}_{date}"
```

#### Request Deduplication
Multiple sensors requesting same data share single API call:
```python
class RequestDeduplicator:
    """Prevent duplicate concurrent requests."""
    
    async def get_or_fetch(self, key: str, fetch_func):
        """Get cached or fetch data once."""
        if key in pending_requests:
            return await pending_requests[key]
        
        task = asyncio.create_task(fetch_func())
        pending_requests[key] = task
        try:
            return await task
        finally:
            del pending_requests[key]
```

### Sensor Factory Pattern
Use factory functions to create sensors:
```python
def create_daily_aggregate_sensors(
    hass: HomeAssistant,
    coordinator: EcoGuardDataUpdateCoordinator,
    utility_codes: set[str],
) -> list[SensorEntity]:
    """Create daily aggregate sensors."""
    sensors = []
    for utility_code in utility_codes:
        sensors.append(
            EcoGuardDailyAggregateSensor(
                coordinator=coordinator,
                utility_code=utility_code,
            )
        )
    return sensors
```

### Sensor Base Classes
Hierarchy of sensor classes:
```
SensorEntity (Home Assistant)
    ↓
CoordinatorEntity (Home Assistant)
    ↓
EcoGuardSensorBase (Our base class)
    ↓
├── EcoGuardDailyConsumptionSensor
├── EcoGuardDailyCostSensor
├── EcoGuardMonthlyMeterSensor
├── EcoGuardLatestReceptionSensor
└── ... (other sensor types)
```

## Error Handling Strategy

### Exception Hierarchy
```python
class EcoGuardAPIError(Exception):
    """Base exception for API errors."""

class EcoGuardAuthenticationError(EcoGuardAPIError):
    """Authentication failed."""

class EcoGuardConnectionError(EcoGuardAPIError):
    """Connection failed."""
```

### Coordinator Error Handling
```python
async def _async_update_data(self):
    """Update data."""
    try:
        return await self._fetch_data()
    except EcoGuardAuthenticationError:
        # Authentication issue - trigger reauth flow
        raise ConfigEntryAuthFailed
    except EcoGuardAPIError as err:
        # API error - retry on next update
        raise UpdateFailed(f"API error: {err}")
    except Exception as err:
        # Unexpected error - log and retry
        _LOGGER.exception("Unexpected error: %s", err)
        raise UpdateFailed(f"Unexpected error: {err}")
```

### Retry Logic
```python
async def _fetch_with_retry(self, url: str, max_retries: int = 3):
    """Fetch with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return await self._fetch(url)
        except ClientError as err:
            if attempt == max_retries - 1:
                raise
            wait_time = 2 ** attempt  # Exponential backoff
            await asyncio.sleep(wait_time)
```

## Data Processing Patterns

### Aggregation
Calculate totals across multiple meters:
```python
def calculate_utility_total(installations: list, utility_code: str) -> float:
    """Calculate total for a utility type."""
    return sum(
        inst.get("consumption", 0)
        for inst in installations
        if inst.get("utility_code") == utility_code
    )
```

### Monthly Accumulation
Track running totals for current month:
```python
def calculate_monthly_accumulated(data_points: list) -> float:
    """Calculate accumulated value for current month."""
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0)
    
    return sum(
        point["value"]
        for point in data_points
        if point["timestamp"] >= month_start.timestamp()
    )
```

### Price Estimation
Estimate hot water costs using Nord Pool:
```python
async def estimate_hot_water_cost(
    consumption_m3: float,
    cold_water_price: float,
    electricity_price_kwh: float,
    heating_efficiency: float = 0.95,
) -> float:
    """Estimate hot water heating cost."""
    # Cold water component
    cold_water_cost = consumption_m3 * cold_water_price
    
    # Heating energy (kWh) = m³ × 1000 kg/m³ × 4.18 kJ/(kg·K) × ΔT / 3600
    delta_temp = 50  # Assume 50°C temperature rise
    heating_kwh = consumption_m3 * 1000 * 4.18 * delta_temp / 3600
    
    # Heating cost
    heating_cost = (heating_kwh / heating_efficiency) * electricity_price_kwh
    
    return cold_water_cost + heating_cost
```

## Translation and Localization

### Translation Loading
```python
async def get_translated_name(
    hass: HomeAssistant,
    key: str,
    default: str,
) -> str:
    """Get translated name from translation files."""
    translations = await async_get_translations(
        hass,
        hass.config.language,
        "entity_component",
        "ecoguard"
    )
    return translations.get(f"component.ecoguard.entity.sensor.{key}.name", default)
```

### Dynamic Translation Updates
```python
# Clear cache when language changes
clear_translation_cache()

# Update entity registry names
await update_entity_registry_with_timeout(
    hass=hass,
    coordinator=coordinator,
    entity_id=entity_id,
)
```

## Performance Optimization

### Concurrent Requests
```python
# Fetch multiple endpoints concurrently
results = await asyncio.gather(
    api.get_nodes(),
    api.get_measuring_points(node_id),
    api.get_installations(node_id),
    return_exceptions=True,
)
```

### Lazy Loading
```python
# Only fetch billing data when needed
@property
def billing_data(self):
    """Get billing data (lazy loaded)."""
    if self._billing_data is None:
        # Fetch on first access
        self._billing_data = await self._fetch_billing()
    return self._billing_data
```

### Memory Management
```python
# Clear old cache entries periodically
def cleanup_cache(self, max_age_seconds: int = 3600):
    """Remove expired cache entries."""
    now = time.time()
    self._cache = {
        k: v for k, v in self._cache.items()
        if now - v["timestamp"] < max_age_seconds
    }
```
