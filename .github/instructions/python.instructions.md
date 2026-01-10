# Python Development Guidelines

## Python Version
- Minimum: Python 3.9
- Target: Python 3.11+ (Home Assistant 2024+)

## Type Hints
Always use type hints for better code quality and IDE support:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
```

## Async/Await
All I/O operations must be async:
- Use `aiohttp.ClientSession` for HTTP requests (never `requests` library)
- Use `async def` for functions that perform I/O
- Use `await` for async calls
- Use `asyncio` utilities for concurrent operations

### Example
```python
async def fetch_data(self) -> dict:
    """Fetch data from API."""
    async with self.session.get(url) as response:
        if response.status == 200:
            return await response.json()
        raise APIError(f"Failed: {response.status}")
```

## Error Handling

### Custom Exceptions
Define integration-specific exceptions:
```python
class EcoGuardAPIError(Exception):
    """Base exception for EcoGuard API errors."""

class EcoGuardAuthenticationError(EcoGuardAPIError):
    """Authentication failed."""
```

### Exception Handling Pattern
```python
try:
    result = await api.authenticate()
except EcoGuardAuthenticationError:
    _LOGGER.error("Authentication failed")
    raise ConfigEntryAuthFailed
except Exception as err:
    _LOGGER.error("Unexpected error: %s", err)
    raise UpdateFailed(f"Error: {err}")
```

## Logging Best Practices

### Logger Setup
```python
import logging
_LOGGER = logging.getLogger(__name__)
```

### Log Levels
- `debug`: Detailed diagnostic (API requests/responses, state changes)
- `info`: Important events (setup complete, discovery results)
- `warning`: Recoverable issues (missing optional data)
- `error`: Errors preventing operation (API failures, invalid data)

### Examples
```python
_LOGGER.debug("Fetching data for node %s", node_id)
_LOGGER.info("Found %d active installations", len(installations))
_LOGGER.warning("Missing price data, using estimate")
_LOGGER.error("Failed to authenticate: %s", err)
```

## Data Classes
Use `@dataclass` for structured data:
```python
from dataclasses import dataclass

@dataclass
class EcoGuardRuntimeData:
    """Runtime data for config entry."""
    coordinator: EcoGuardDataUpdateCoordinator
    api: EcoGuardAPI
    entity_registry_update_task: Task | None = None
```

## Context Managers
Use context managers for resource cleanup:
```python
async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
        data = await response.json()
```

## List Comprehensions
Prefer list comprehensions over map/filter:
```python
# Good
active = [inst for inst in installations if inst.get("is_active")]

# Avoid
active = list(filter(lambda x: x.get("is_active"), installations))
```

## Dictionary Operations
Use modern dictionary patterns:
```python
# Get with default
value = data.get("key", default_value)

# Merge dictionaries (Python 3.9+)
merged = {**dict1, **dict2}

# Dictionary comprehension
filtered = {k: v for k, v in data.items() if v is not None}
```

## String Formatting
Use f-strings for string formatting:
```python
# Good
message = f"Found {count} sensors for {utility}"

# Avoid
message = "Found {} sensors for {}".format(count, utility)
```

## None Checks
Be explicit about None checks:
```python
# Good
if value is None:
    return default

# Avoid
if not value:  # This is False for 0, "", [], etc.
    return default
```

## Function Parameters
- Use keyword-only arguments for clarity (use `*` separator)
- Provide default values for optional parameters
- Document complex parameters in docstrings

```python
async def fetch_data(
    self,
    node_id: str,
    *,
    from_time: int | None = None,
    to_time: int | None = None,
    interval: str = "d",
) -> dict:
    """Fetch data from API.
    
    Args:
        node_id: The node identifier
        from_time: Start timestamp (default: 30 days ago)
        to_time: End timestamp (default: now)
        interval: Data interval (d=daily, h=hourly)
    """
```

## Constants
Define constants in `const.py`:
- Use UPPER_SNAKE_CASE
- Group related constants together
- Add comments for non-obvious values

```python
# Update Intervals
UPDATE_INTERVAL_DATA = 3600  # 1 hour for consumption data
UPDATE_INTERVAL_BILLING = 86400  # 24 hours for billing data

# Utility Codes
UTILITY_HOT_WATER = "HW"
UTILITY_COLD_WATER = "CW"
```

## Module-Level Variables
Minimize module-level mutable state:
```python
# Good - immutable
DOMAIN = "ecoguard"
DEFAULT_INTERVAL = "d"

# Avoid - mutable module state
_cache = {}  # Use class attribute or function local instead
```

## Imports
Group imports in standard order:
```python
# Standard library
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

# Third-party
from homeassistant.core import HomeAssistant
import aiohttp

# Local
from .const import DOMAIN
from .api import EcoGuardAPI

# Type checking only
if TYPE_CHECKING:
    from asyncio import Task
```

## Code Documentation
Document public APIs:
```python
async def get_data(self, node_id: str) -> dict:
    """Fetch consumption data for a node.
    
    Args:
        node_id: The node identifier from EcoGuard
        
    Returns:
        Dictionary containing consumption data
        
    Raises:
        EcoGuardAuthenticationError: If authentication fails
        EcoGuardAPIError: If API request fails
    """
```

## Testing Considerations
Write testable code:
- Accept dependencies via constructor (dependency injection)
- Separate I/O from business logic
- Use small, focused functions
- Mock external dependencies in tests

```python
class EcoGuardAPI:
    """API client."""
    
    def __init__(
        self,
        username: str,
        password: str,
        domain: str,
        session: aiohttp.ClientSession | None = None,
    ):
        """Initialize API client."""
        self._session = session  # Allow injecting mock session for tests
```
