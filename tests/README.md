# EcoGuard Integration Tests

This directory contains the test suite for the EcoGuard Home Assistant integration.

## Running Tests

### Prerequisites

Install test dependencies:

```bash
pip install -r tests/requirements.txt
```

### Run All Tests

```bash
pytest
```

### Run Specific Test Files

```bash
# Test config flow
pytest tests/test_config_flow.py

# Test API client
pytest tests/test_api.py

# Test coordinator
pytest tests/test_coordinator.py

# Test sensors
pytest tests/test_sensor.py

# Test integration setup
pytest tests/test_init.py

# Test translations
pytest tests/test_translations.py
```

### Run with Coverage

```bash
pytest --cov=custom_components.ecoguard --cov-report=html
```

### Run Specific Test

```bash
pytest tests/test_config_flow.py::test_form
```

## Test Structure

- `conftest.py` - Shared fixtures and test configuration
- `test_config_flow.py` - Tests for configuration flow (100% coverage required)
- `test_api.py` - Tests for the EcoGuard API client
- `test_coordinator.py` - Tests for data coordinators
- `test_sensor.py` - Tests for sensor entities
- `test_init.py` - Tests for integration initialization
- `test_translations.py` - Tests for translation functionality

## Test Coverage Goals

According to Home Assistant integration quality standards:

- **Config Flow**: 100% coverage required
- **Overall**: 95%+ coverage recommended

## Writing New Tests

When adding new functionality, ensure you:

1. Add tests for new features
2. Maintain or improve test coverage
3. Use appropriate fixtures from `conftest.py`
4. Mock external API calls
5. Test both success and error cases

## Fixtures

Common fixtures available in `conftest.py`:

- `mock_api` - Mock EcoGuard API client
- `mock_config_entry` - Mock Home Assistant config entry
- `mock_coordinator_data` - Sample coordinator data
- `coordinator` - Coordinator instance for testing
- `latest_reception_coordinator` - Latest reception coordinator instance
