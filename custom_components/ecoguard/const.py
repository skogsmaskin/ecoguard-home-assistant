"""Constants for the EcoGuard integration."""

DOMAIN = "ecoguard"

# API Configuration
API_BASE_URL = "https://integration.ecoguard.se"
API_TOKEN_ENDPOINT = "/token"
API_USERS_SELF = "/api/users/self"
API_NODES = "/api/{domaincode}/nodes"
API_MEASURING_POINTS = "/api/{domaincode}/measuringpoints"
API_DATA = "/api/{domaincode}/data"
API_LATEST_RECEPTION = "/api/{domaincode}/latestReception"
API_BILLING_RESULTS = "/api/{domaincode}/billingresults"
API_INSTALLATIONS = "/api/{domaincode}/installations"
API_SETTINGS = "/api/{domaincode}/settings"

# Update Intervals
UPDATE_INTERVAL_DATA = 3600  # 1 hour for consumption data
UPDATE_INTERVAL_LATEST_RECEPTION = 300  # 5 minutes for latest reception (meters update frequently)
UPDATE_INTERVAL_BILLING = 86400  # 24 hours for billing data

# Data Query Defaults
DEFAULT_INTERVAL = "d"  # daily
DEFAULT_GROUPING = "apartment"
DEFAULT_DATA_DAYS = 30  # Query last 30 days by default

# Sensor Types
SENSOR_TYPE_CURRENT_USAGE = "current_usage"
SENSOR_TYPE_DAILY_TOTAL = "daily_total"
SENSOR_TYPE_LATEST_RECEPTION = "latest_reception"
SENSOR_TYPE_BILLING = "billing"
SENSOR_TYPE_MEASURING_POINT = "measuring_point"

# Data Function Types
FUNC_CONSUMPTION = "con"
FUNC_PRICE = "price"
FUNC_CO2 = "co2"

# Nord Pool API
# Using the new Data API endpoint
NORD_POOL_API_URL = "https://data-api.nordpoolgroup.com/marketdata/page/10"
NORD_POOL_MAP_URL = "https://data.nordpoolgroup.com/map"
NORD_POOL_AREA_CODES = {
    "NO1": "Oslo",
    "NO2": "Kristiansand",
    "NO3": "Trondheim",
    "NO4": "Tromsø",
    "NO5": "Bergen",
    "SE1": "Luleå",
    "SE2": "Sundsvall",
    "SE3": "Stockholm",
    "SE4": "Malmö",
    "DK1": "West Denmark",
    "DK2": "East Denmark",
    "FI": "Finland",
    "EE": "Estonia",
    "LV": "Latvia",
    "LT": "Lithuania",
}
