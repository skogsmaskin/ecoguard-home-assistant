# Testing Guidelines

## Test Framework
- **Framework**: pytest with pytest-homeassistant-custom-component
- **Async Support**: pytest-asyncio (asyncio_mode = auto)
- **Mocking**: pytest-mock and aioresponses
- **Configuration**: pytest.ini in root directory

## Running Tests

### Basic Commands
```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=custom_components.ecoguard --cov-report=html

# Run specific test file
pytest tests/test_api.py

# Run with verbose output
pytest -v

# Run tests matching pattern
pytest -k "test_authenticate"
```

### Test Organization
```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── requirements.txt         # Test dependencies
├── test_api.py             # API client tests
├── test_config_flow.py     # Config flow tests
├── test_coordinator.py     # Coordinator tests
├── test_sensor.py          # Sensor tests
└── test_*.py               # Other test modules
```

## Test Fixtures

### Common Fixtures (in conftest.py)
```python
@pytest.fixture
def mock_session():
    """Mock aiohttp session."""
    return MagicMock()

@pytest.fixture
def api(mock_session):
    """Create API instance."""
    return EcoGuardAPI(
        username="test_user",
        password="test_password",
        domain="test_domain",
        session=mock_session,
    )
```

### Home Assistant Fixtures
From pytest-homeassistant-custom-component:
- `hass`: Home Assistant instance
- `enable_custom_integrations`: Enable custom integration loading

## Mocking Patterns

### Mock API Responses
Use aioresponses for HTTP mocking:
```python
from aioresponses import aioresponses

async def test_fetch_data(api):
    """Test data fetching."""
    with aioresponses() as mock_resp:
        mock_resp.get(
            "https://api.example.com/data",
            status=200,
            payload={"data": "value"}
        )
        
        result = await api.fetch_data()
        assert result["data"] == "value"
```

### Mock Methods
Use pytest-mock for method mocking:
```python
async def test_coordinator_update(coordinator, mocker):
    """Test coordinator update."""
    mock_fetch = mocker.patch.object(
        coordinator.api,
        "get_data",
        return_value={"consumption": 100}
    )
    
    await coordinator.async_refresh()
    
    mock_fetch.assert_called_once()
    assert coordinator.data["consumption"] == 100
```

### Mock Async Context Managers
```python
response = MagicMock()
response.status = 200
response.json = AsyncMock(return_value={"token": "test"})
response.__aenter__ = AsyncMock(return_value=response)
response.__aexit__ = AsyncMock(return_value=None)

mock_session.post = MagicMock(return_value=response)
```

## Test Categories

### Unit Tests
Test individual components in isolation:
- Mock all external dependencies
- Test single responsibility
- Fast execution

```python
async def test_parse_consumption_data():
    """Test consumption data parsing."""
    raw_data = {"value": 100, "unit": "m3"}
    parsed = parse_consumption(raw_data)
    assert parsed.value == 100
    assert parsed.unit == "m3"
```

### Integration Tests
Test component interactions:
- May use real Home Assistant test instance
- Test actual integration behavior
- Mark with `@pytest.mark.integration`

```python
@pytest.mark.integration
async def test_sensor_setup(hass, config_entry):
    """Test sensor platform setup."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    
    state = hass.states.get("sensor.consumption_daily_metered_hot_water")
    assert state is not None
```

## Test Assertions

### Common Assertions
```python
# Equality
assert value == expected
assert result is None

# Collections
assert len(sensors) == 5
assert sensor_id in sensor_list
assert "key" in data_dict

# Exceptions
with pytest.raises(EcoGuardAPIError):
    await api.invalid_operation()

# Async assertions
result = await async_function()
assert result is not None
```

### Mock Call Verification
```python
# Called once
mock_api.authenticate.assert_called_once()

# Called with specific args
mock_api.get_data.assert_called_once_with("node_123")

# Call count
assert mock_api.fetch.call_count == 3

# Not called
mock_api.refresh.assert_not_called()
```

## Testing Best Practices

### Test Naming
Use descriptive test names:
```python
# Good
async def test_authenticate_with_invalid_credentials_raises_error():
    """Test that invalid credentials raise authentication error."""

# Avoid
async def test_auth():
    """Test auth."""
```

### Test Structure (AAA Pattern)
```python
async def test_feature():
    """Test description."""
    # Arrange - Set up test data and mocks
    api = EcoGuardAPI("user", "pass", "domain")
    mock_response = {"data": "value"}
    
    # Act - Execute the code being tested
    result = await api.fetch_data()
    
    # Assert - Verify the results
    assert result == mock_response
```

### One Assertion Per Test
Focus tests on single behavior:
```python
# Good - focused test
async def test_authenticate_sets_access_token():
    """Test that authentication sets access token."""
    await api.authenticate()
    assert api._access_token == "test_token"

# Avoid - testing multiple things
async def test_authenticate():
    """Test authentication."""
    await api.authenticate()
    assert api._access_token == "test_token"
    assert api._refresh_token == "refresh"
    assert api._token_expiry > 0
```

### Parametrized Tests
Test multiple scenarios efficiently:
```python
@pytest.mark.parametrize("status,expected_error", [
    (400, EcoGuardAPIError),
    (401, EcoGuardAuthenticationError),
    (500, EcoGuardAPIError),
])
async def test_error_responses(api, status, expected_error):
    """Test error response handling."""
    with aioresponses() as mock_resp:
        mock_resp.post("http://api/endpoint", status=status)
        
        with pytest.raises(expected_error):
            await api.authenticate()
```

## Coverage Goals
- Aim for >80% code coverage
- Focus on critical paths (API, coordinators, sensors)
- Don't test Home Assistant core functionality
- Test error handling and edge cases

## Test Data
Use realistic test data:
```python
SAMPLE_NODE = {
    "ID": "node_123",
    "Name": "Test Node",
    "Domain": "test_domain"
}

SAMPLE_CONSUMPTION = {
    "value": 150.5,
    "timestamp": 1609459200,
    "utility_code": "HW"
}
```

## Async Testing
Always use async test functions for async code:
```python
# Good
async def test_async_operation():
    """Test async operation."""
    result = await async_function()
    assert result is not None

# Avoid
def test_async_operation():
    """Test async operation."""
    result = asyncio.run(async_function())  # Don't use asyncio.run in tests
```

## Test Markers
Use markers for test categorization:
```python
@pytest.mark.slow
async def test_full_integration():
    """Test that takes a long time."""
    # Long-running test
    
# Run with: pytest -m "not slow"
```

## Debugging Tests
```bash
# Run with print statements visible
pytest -s

# Drop into debugger on failure
pytest --pdb

# Run last failed tests
pytest --lf

# Show full diff
pytest -vv
```

## CI/CD Considerations
- Tests must pass before merging
- Use same Python version as target Home Assistant
- Tests should be deterministic (no flaky tests)
- Mock time-dependent operations
