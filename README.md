# EcoGuard Home Assistant Integration

A Home Assistant custom integration for monitoring utility usage and costs through the EcoGuard Curves platform. EcoGuard delivers complete solutions and services for individual metering and distribution of water, heating, electricity and charging costs for housing companies.

<img width="503" height="763" alt="image" src="https://i.imgur.com/uCekRul.png" />

This integration is currently mostly focused around water consumption metering. Contributions for integrating support for other utilities (heating, electricity) are welcome. I don't have access to test other utilities than water on my personal EcoGuard account.

## Features

This integration provides comprehensive monitoring of your water consumption and costs:

- **Daily Consumption Sensors**: Last known daily consumption (m³) for each meter (cold and hot water)
- **Month-to-Date Consumption**: Running totals for the current month for cold and hot water
- **Month-to-Date Price Sensors**:
  - **Metered**: Actual price data from the API (when available)
  - **Estimated**: Estimated prices using Nord Pool spot prices for hot water heating costs
- **Total Monthly Cost Sensors**:
  - **Metered**: Sum of actual metered costs from the API
  - **Estimated**: Includes estimated hot water costs when actual price data is unavailable
- **Other Items Cost**: General fees and charges from the last billing period
- **End-of-Month Estimate**: Projected monthly bill based on current consumption patterns
- **Latest Reception Sensors**: Timestamp of last data reception per measuring point

### Data Availability

**Important**: The EcoGuard API is not real-time. Consumption data may be delayed by up to a day or more. The integration displays the most recent data available from the API, which may not reflect today's consumption.

The "Latest Reception" sensors show when data was last received by EcoGuard's systems for each measuring point. Note that this timestamp reflects when the data was received by EcoGuard, not when it becomes available through the API for consumption queries.

## Installation

### HACS (Recommended)

Click the button:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=skogsmaskin&repository=ecoguard-home-assistant)

OR

1. Ensure [HACS](https://hacs.xyz/) is installed
2. Go to HACS → Integrations
3. Click the three dots (⋮) in the top right corner
4. Select "Custom repositories"
5. Add this repository URL and select "Integration" as the category
6. Install the integration from HACS

### Manual Installation

1. Clone or download this repository
2. Copy the `custom_components/ecoguard` folder to your Home Assistant `custom_components` directory:
   ```
   <config>/custom_components/ecoguard/
   ```
3. Restart Home Assistant
4. Go to Settings → Devices & Services → Add Integration
5. Search for "EcoGuard" and follow the setup wizard

## Configuration

Config is done in the HA integrations UI.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=ecoguard)

<img width="614" height="571" alt="image" src="https://i.imgur.com/LMbPJtX.png" />

During setup, you will need to provide:

- **Username/Object number**: Your EcoGuard username or object number
- **Password**: Your EcoGuard account password
- **Domain**: Your EcoGuard domain code (e.g., "SkrentenBrl")
- **Nord Pool Area** (optional): Your Nord Pool pricing area (e.g., "NO1" for Oslo, "SE3" for Stockholm)

The integration will automatically:
- Authenticate with the EcoGuard API
- Discover your node and measuring points
- Create sensors for all available data

### Nord Pool Integration

The integration can estimate hot water heating costs using Nord Pool electricity spot prices. This is useful because the EcoGuard API typically doesn't provide actual hot water price data for the current month.

**How it works:**
- When actual hot water price data is unavailable from the API, the integration estimates the heating cost using Nord Pool spot prices
- The estimate is calibrated against historical billing data to improve accuracy
- The estimated cost includes both the cold water component and the electricity cost for heating
- You can choose your Nord Pool area during setup (e.g., NO1, NO2, SE1, SE2, etc.)

**Note**: Estimates are only used when actual API price data is missing. When actual data is available, it takes precedence.

## Sensors

The integration creates the following sensors:

### Daily Consumption Sensors
- **Format**: `Daily Consumption (Utility) - "Measuring Point"`
- **Example**: `Daily Consumption (Cold Water) - "Kaldtvann Bad"`
- Shows the last known daily consumption value for each meter
- **Note**: Data may be delayed by up to a day

### Month-to-Date Sensors

#### Consumption
- **Format**: `Month-to-Date Consumption - Utility`
- **Examples**: 
  - `Month-to-Date Consumption - Hot Water`
  - `Month-to-Date Consumption - Cold Water`
- Total consumption for the current month (m³)

#### Price (Metered)
- **Format**: `Month-to-Date Price (Metered) - Utility`
- **Examples**:
  - `Month-to-Date Price (Metered) - Hot Water`
  - `Month-to-Date Price (Metered) - Cold Water`
- Actual price from API data (when available)

#### Price (Estimated)
- **Format**: `Month-to-Date Price (Estimated) - Utility`
- **Examples**:
  - `Month-to-Date Price (Estimated) - Hot Water`
  - `Month-to-Date Price (Estimated) - Cold Water`
- Estimated price using Nord Pool spot prices (used when actual data is unavailable)
- Note: Cold water prices are typically available from the API, so estimated and metered are usually the same

### Total Cost Sensors
- **Month-to-Date Total Cost (Metered)**: Sum of all metered costs (only actual API data)
- **Month-to-Date Total Cost (Estimated)**: Sum including estimated hot water costs when price data is missing

### Other Sensors
- **Other Items Monthly Cost (Last Bill)**: General fees and charges from the most recent billing period
- **End-of-Month Bill Estimate**: Projected total monthly bill based on current consumption patterns and mean daily values
- **Latest Measurement**: Timestamp of last data reception for each measuring point
  - **Format**: `Latest Measurement (Utility) - "Measuring Point"` or `Latest Measurement - "Measuring Point"`
  - **Examples**:
    - `Latest Measurement (Cold Water) - "Kaldtvann Bad"`
    - `Latest Measurement (Hot Water) - "Varmtvann Bad"`


## Development

### Prerequisites

- Python 3.9 or higher
- Home Assistant development environment
- Access to an EcoGuard account for testing

### Project Structure

```
custom_components/ecoguard/
├── __init__.py          # Integration setup and entry points
├── manifest.json        # Integration metadata
├── config_flow.py       # Configuration flow for user setup
├── const.py             # Constants (API URLs, sensor types, etc.)
├── coordinator.py       # Data update coordinator
├── api.py               # EcoGuard API client
├── sensor.py            # Sensor entity definitions
├── strings.json         # Translation strings (base/default)
└── translations/*       # Localization files
```

### Setting Up Development Environment

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd ecoguard-home-assistant
   ```

2. Set up a Home Assistant development environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install homeassistant
   ```

3. Copy the integration to your Home Assistant config:
   ```bash
   cp -r custom_components/ecoguard ~/.homeassistant/custom_components/
   ```

### Running Tests

#### Automated Tests

The integration includes a comprehensive test suite using pytest:

1. Install test dependencies:
   ```bash
   pip install -r tests/requirements.txt
   ```

2. Run all tests:
   ```bash
   pytest
   ```

3. Run with coverage report:
   ```bash
   pytest --cov=custom_components.ecoguard --cov-report=html
   ```

4. Run specific test files:
   ```bash
   pytest tests/test_config_flow.py  # Test config flow
   pytest tests/test_api.py           # Test API client
   pytest tests/test_coordinator.py   # Test coordinators
   pytest tests/test_sensor.py        # Test sensors
   ```

See `tests/README.md` for more details on running tests.

#### Manual Testing

1. Start Home Assistant in development mode:
   ```bash
   hass --script check_config
   ```

2. Test the integration:
   - Add the integration through the UI
   - Verify sensors are created
   - Check logs for any errors:
     ```bash
     tail -f ~/.homeassistant/home-assistant.log
     ```

#### Testing API Client Directly

You can test the API client independently:

```python
import asyncio
from custom_components.ecoguard.api import EcoGuardAPI

async def test_api():
    api = EcoGuardAPI(
        username="your_username",
        password="your_password",
        domain="your_domain"
    )

    try:
        # Test authentication
        await api.authenticate()
        print("Authentication successful!")

        # Test getting nodes
        nodes = await api.get_nodes()
        print(f"Found {len(nodes)} nodes")

        # Test getting data
        if nodes:
            node_id = nodes[0]["ID"]
            from_time = int((datetime.now() - timedelta(days=30)).timestamp())
            to_time = int(datetime.now().timestamp())
            data = await api.get_data(node_id, from_time, to_time)
            print(f"Retrieved data: {data}")

    finally:
        await api.async_close()

asyncio.run(test_api())
```

### Code Style

This integration follows Home Assistant's code style guidelines:
- Use `black` for code formatting
- Follow PEP 8 conventions
- Use type hints where appropriate
- Document functions and classes with docstrings

### Debugging

Enable debug logging in Home Assistant by adding to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.ecoguard: debug
```

This will provide detailed logs of API calls, authentication, and data updates.

## API Information

The integration uses the EcoGuard Integration API documented at:
https://integration.ecoguard.se/

### Authentication

The integration uses tenant authentication:
- Endpoint: `POST /token`
- Requires: Username/Object number, Password, Domain
- Returns: Bearer token and optional refresh token

### Key Endpoints Used

- `/api/{domaincode}/nodes` - Get node information
- `/api/{domaincode}/measuringpoints` - Get measuring points
- `/api/{domaincode}/data` - Get consumption data
- `/api/{domaincode}/latestReception` - Get latest reception timestamps
- `/api/{domaincode}/billingresults` - Get billing information
- `/api/{domaincode}/installations` - Get installation details

### Update Intervals

- Consumption data: 1 hour
- Latest reception: 15 minutes
- Billing data: 24 hours (cached)
- Nord Pool prices: Daily (cached)

### Data Caching

The integration implements intelligent caching to reduce API calls and handle rate limits:
- **Data requests**: Cached for 60 seconds to prevent duplicate calls when multiple sensors update simultaneously
- **Billing results**: Cached for 24 hours (historical data doesn't change)
- **Nord Pool prices**: Cached per day (prices update daily)
- **Request deduplication**: Multiple sensors requesting the same data share a single API call

### Rate Limiting

The integration includes automatic rate limiting and retry logic:
- Limits concurrent API requests to prevent overwhelming the server
- Automatically retries failed requests with exponential backoff
- Handles 429 (Too Many Requests) errors gracefully

## Troubleshooting

### Integration won't load

- Check that all files are in `custom_components/ecoguard/`
- Verify `manifest.json` is valid JSON
- Check Home Assistant logs for errors
- Ensure you're using a supported Home Assistant version

### Authentication fails

- Verify your username, password, and domain are correct
- Check that your account has API access
- Review logs for specific error messages

### Sensors not updating

- Check the coordinator logs for API errors
- Verify your network connection
- Check if the API token has expired (should auto-refresh)
- Review update intervals in `const.py`

### Missing sensors

- Ensure your account has measuring points configured
- Check that installations are properly set up in EcoGuard
- Review logs for data retrieval errors

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## Disclaimer

This is an unofficial integration and is not affiliated with, endorsed by, or connected to EcoGuard. This integration is developed independently and uses the publicly available EcoGuard Integration API. Use at your own risk.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a detailed list of changes and version history.

## Translation Files

The integration uses the following translation files:
- `strings.json` - Base English translations (used by Home Assistant's config flow system)
- `translations/en.json` - English translations (required for config flow to work properly)
- `translations/nb.json` - Norwegian (Bokmål) translations

**Note**: `strings.json` and `translations/en.json` should be kept in sync. If you modify `strings.json`, run `./sync-translations.sh` to update `en.json`, or manually copy the changes.

## License

This project is licensed under the MIT License.

## Support

For issues, questions, or feature requests, please open an issue on the GitHub repository.

## Acknowledgments

- EcoGuard for providing the API
- Home Assistant community for integration patterns and examples

