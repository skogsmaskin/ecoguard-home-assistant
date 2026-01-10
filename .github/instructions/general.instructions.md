# EcoGuard Home Assistant Integration - General Instructions

## Project Overview
This is a Home Assistant custom integration for monitoring utility usage and costs through the EcoGuard Curves platform. The integration primarily focuses on water consumption metering (hot water and cold water) and price estimation using Nord Pool spot prices.

## Repository Structure
```
custom_components/ecoguard/     # Main integration code
├── __init__.py                  # Integration setup and entry points
├── api.py                       # EcoGuard API client
├── config_flow.py               # Configuration flow for user setup
├── coordinator.py               # Data update coordinators
├── sensor.py                    # Main sensor platform
├── sensor_factory.py            # Sensor creation logic
├── sensors/                     # Individual sensor implementations
├── const.py                     # Constants (API URLs, sensor types, etc.)
├── translations/                # Localization files (nb, nn, sv, en)
└── manifest.json                # Integration metadata

tests/                           # Pytest test suite
.github/                         # GitHub workflows and instructions
```

## Code Style and Conventions

### Python Style
- Follow PEP 8 conventions
- Use type hints for function parameters and return values
- Use docstrings for classes and public functions
- Import `from __future__ import annotations` for forward references
- Use descriptive variable names

### Import Organization
1. Standard library imports
2. Third-party imports (homeassistant, aiohttp, etc.)
3. Local imports (relative imports from this integration)
4. Use `if TYPE_CHECKING:` for type-only imports to avoid circular dependencies

### Logging
- Use the module-level logger: `_LOGGER = logging.getLogger(__name__)`
- Log levels:
  - `debug`: Detailed diagnostic information
  - `info`: Important milestones (setup, initialization)
  - `warning`: Unexpected situations that don't prevent operation
  - `error`: Errors that prevent specific operations

### Naming Conventions
- Classes: PascalCase (e.g., `EcoGuardAPI`, `EcoGuardDataUpdateCoordinator`)
- Functions/methods: snake_case (e.g., `async_setup_entry`, `get_active_installations`)
- Constants: UPPER_SNAKE_CASE (e.g., `DOMAIN`, `UPDATE_INTERVAL_DATA`)
- Private methods: prefix with underscore (e.g., `_async_update_data`)

## Commit Messages
Follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) format:
- `feat:` - New features
- `fix:` - Bug fixes
- `docs:` - Documentation changes
- `test:` - Test additions or modifications
- `refactor:` - Code refactoring
- `chore:` - Maintenance tasks
- `ci:` - CI/CD changes

Examples:
- `feat: add support for electricity sensors`
- `fix: handle missing price data gracefully`
- `docs: update README with new sensor types`

## Dependencies
- Core: `aiohttp>=3.8.0` for async HTTP requests
- Optional: `nordpool>=0.5.0` for Nord Pool price integration
- Test: pytest, pytest-homeassistant-custom-component, pytest-asyncio

## Common Patterns

### Async Operations
All API calls and Home Assistant operations are async:
```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from config entry."""
    # ... async operations
```

### Error Handling
Use specific exception types:
- `EcoGuardAuthenticationError` for authentication failures
- `EcoGuardAPIError` for API errors
- `ConfigEntryNotReady` for setup failures

### Data Coordinators
Use `DataUpdateCoordinator` for managing data updates:
- `EcoGuardDataUpdateCoordinator` - Main consumption data (1 hour interval)
- `EcoGuardLatestReceptionCoordinator` - Latest reception timestamps (5 minute interval)

## Testing
- Run tests with: `pytest`
- Run with coverage: `pytest --cov=custom_components.ecoguard`
- All new features should include tests
- Mock external API calls using `aioresponses` or `pytest-mock`

## Documentation
- Update README.md for user-facing changes
- Update CHANGELOG.md following Keep a Changelog format
- Use semantic versioning (MAJOR.MINOR.PATCH)
- Translations are in `translations/` directory (nb, nn, sv, en)
