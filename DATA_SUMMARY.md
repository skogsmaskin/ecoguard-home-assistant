# EcoGuard Integration - Meta Information Summary

This document summarizes all the meta information available from the EcoGuard API through the integration coordinator. This focuses on static configuration and metadata, not consumption or billing data.

## Available Meta Information

### Node Data (`get_node_data()`)
Complete node information including:

- **Properties**: Node properties like area (Areal), etc.
  - Example: `{"Code": 0, "Name": "Areal", "Value": "72"}`

- **MeasuringPoints**: List of measuring points for this node
  - Example: `{"ID": 201, "Name": "Varmtvann Bad"}`
  - Example: `{"ID": 572, "Name": "Kaldtvann Bad"}`

- **SubNodes**: Child nodes (if any)

- **RentalContracts**: Rental contract information
  - Contains: ID, Date (timestamp), ContractCode

### Settings (`get_settings()` / `get_setting(name)`)
Domain-level settings returned as an array of name-value pairs:

- **TimeZone**: Windows timezone name (e.g., "W. Europe Standard Time")
- **TimeZoneIANA**: IANA timezone identifier (e.g., "Europe/Berlin")
- **DefaultCulture**: Locale (e.g., "nb-NO")
- **Currency**: Currency code (e.g., "NOK")
- **TenantShowTemperature**: Boolean flag
- **TenantShowHours**: Boolean flag
- **TenantShowBillingResults**: Boolean flag
- **MobileApp**: Boolean flag
- **SecurityApp**: Boolean flag

**Helper method**: `get_setting("Currency")` returns `"NOK"` directly

### Installations (`get_installations()`)
Device installation information for each measuring point:

- **MeasuringPointID**: Links to a measuring point
- **DeviceTypeDisplay**: Human-readable device type
- **ExternalKey**: External identifier/key
- **From**: Installation start date (Unix timestamp in seconds)
- **To**: Installation end date (Unix timestamp, or `null` if installation is still active)
- **By**: Installation provider/company name
- **MeasuringDeviceID**: Device identifier
- **Registers**: Array of register information
  - **UtilityCode**: The utility code (e.g., "HW", "CW") used for data queries

**Note**: The `From` and `To` fields indicate the lifespan of the installation. If `To` is `null`, the installation is currently active.

### Measuring Points (`get_measuring_points()`)
List of measuring points (also available in node_data):

- **ID**: Unique identifier
- **Name**: Human-readable name (e.g., "Varmtvann Bad", "Kaldtvann Bad")

### Latest Reception (`get_latest_reception()`)
Updated on each coordinator refresh (every hour). Contains metadata about data reception:

- **PositionID**: Measuring point ID
- **LatestReception**: Timestamp of last data reception
- **MeasuringDeviceID**: Device identifier
- **LatestReceptionAlarm**: Alarm information (if any)

**Helper method**: `get_latest_reading(measuring_point_id)` returns the reception data for a specific measuring point

## Data Access Patterns

### In Sensor Code

```python
# Get static node data
node_data = coordinator.get_node_data()
properties = node_data.get("Properties", [])
measuring_points = node_data.get("MeasuringPoints", [])
rental_contracts = node_data.get("RentalContracts", [])

# Get settings
settings = coordinator.get_settings()
currency = coordinator.get_setting("Currency")  # "NOK"
timezone = coordinator.get_setting("TimeZoneIANA")  # "Europe/Berlin"

# Get installations to find utility codes
installations = coordinator.get_installations()
for inst in installations:
    mp_id = inst.get("MeasuringPointID")
    device_type = inst.get("DeviceTypeDisplay")
    external_key = inst.get("ExternalKey")
    from_date = inst.get("From")  # Unix timestamp
    to_date = inst.get("To")  # Unix timestamp or None if active
    is_active = to_date is None
    for register in inst.get("Registers", []):
        util_code = register.get("UtilityCode")  # "HW" or "CW"

# Get measuring points
measuring_points = coordinator.get_measuring_points()

# Get latest reception data
latest_reception = coordinator.get_latest_reception()
reading = coordinator.get_latest_reading(measuring_point_id=201)
```

## Debug Logging

When the coordinator updates, it automatically logs a comprehensive summary of all meta information to the debug log. Enable debug logging for `custom_components.ecoguard` to see:

- Complete node information (properties, measuring points, subnodes, contracts)
- All settings with values
- All installations with utility codes
- All measuring points
- Latest reception timestamps

Example log output:
```
================================================================================
ECOGUARD STATIC DATA SUMMARY
================================================================================
NODE DATA:
  Node ID: 395
  Domain: BjerkeBrl
  Properties:
    - Areal: 72
  Measuring Points (2):
    - ID: 201, Name: Varmtvann Bad
    - ID: 572, Name: Kaldtvann Bad
SETTINGS (9):
  - TimeZone: W. Europe Standard Time
  - Currency: NOK
  - TimeZoneIANA: Europe/Berlin
  ...
INSTALLATIONS (2):
  - MeasuringPointID: 201, DeviceType: Warm water, ExternalKey: 60004910
    Status: Active, From: 2025-06-08, To: Active
    Registers:
      - UtilityCode: HW
  - MeasuringPointID: 572, DeviceType: Water, ExternalKey: 52998001
    Status: Active, From: 2025-06-08, To: Active
    Registers:
      - UtilityCode: CW
LATEST RECEPTION (2):
  - PositionID: 201, LatestReception: 2024-12-15 14:30:00
  ...
================================================================================
```

## Utility Codes

Common utility codes found in the system:
- **HW**: Hot Water (Varmtvann)
- **CW**: Cold Water (Kaldtvann)

These codes are found in installation registers and can be used to identify which utility type each measuring point monitors.

## Notes

- All meta information is cached and only fetched once during initialization
- Latest reception is updated on each coordinator refresh (every hour)
- The coordinator update interval is 1 hour
- All timestamps are Unix timestamps (seconds since epoch)
- The coordinator handles errors gracefully - if one API call fails, others continue
- This integration currently focuses on metadata only - consumption and billing data are not fetched
