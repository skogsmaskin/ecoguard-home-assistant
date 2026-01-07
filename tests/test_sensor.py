"""Tests for the EcoGuard sensor entities."""

from unittest.mock import AsyncMock, MagicMock, patch
import inspect
import pytest
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from custom_components.ecoguard.sensor import (
    async_setup_entry,
    EcoGuardDailyConsumptionSensor,
    EcoGuardDailyCostSensor,
    EcoGuardLatestReceptionSensor,
    EcoGuardMonthlyAggregateSensor,
    EcoGuardMonthlyMeterSensor,
    EcoGuardOtherItemsSensor,
    EcoGuardTotalMonthlyCostSensor,
    EcoGuardEndOfMonthEstimateSensor,
    round_to_max_digits,
)

# Import pytest-homeassistant-custom-component fixtures
pytest_plugins = ("pytest_homeassistant_custom_component",)


def _create_config_entry(**kwargs) -> ConfigEntry:
    """Create a ConfigEntry that works with different Home Assistant versions.
    
    Some versions require discovery_keys and subentries_data, others don't accept them.
    This function inspects the signature and conditionally includes them.
    """
    # Get the ConfigEntry signature
    sig = inspect.signature(ConfigEntry.__init__)
    params = sig.parameters
    
    # Check if discovery_keys and subentries_data are in the signature
    needs_discovery_keys = "discovery_keys" in params
    needs_subentries_data = "subentries_data" in params
    
    # Add optional parameters if they're required by this version
    if needs_discovery_keys and "discovery_keys" not in kwargs:
        kwargs["discovery_keys"] = None
    if needs_subentries_data and "subentries_data" not in kwargs:
        kwargs["subentries_data"] = None
    
    return ConfigEntry(**kwargs)


@pytest.fixture
def mock_config_entry() -> ConfigEntry:
    """Create a mock config entry."""
    return _create_config_entry(
        version=1,
        domain="ecoguard",
        title="EcoGuard - Test Node",
        data={
            "username": "test_user",
            "password": "test_password",
            "domain": "test_domain",
            "node_id": 123,
            "nord_pool_area": "NO1",
        },
        source="user",
        entry_id="test_entry_id",
        unique_id="test_domain",
        options={},
        minor_version=1,
    )


async def test_round_to_max_digits():
    """Test rounding to max digits."""
    assert round_to_max_digits(123.456789, 3) == 123.0
    assert round_to_max_digits(1.234567, 3) == 1.23
    assert round_to_max_digits(0.123456, 3) == 0.123
    assert round_to_max_digits(None, 3) is None
    assert round_to_max_digits(0, 3) == 0.0


async def test_daily_consumption_sensor(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test daily consumption sensor."""
    # Set coordinator data
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    installation = {
        "MeasuringPointID": 1,
        "ExternalKey": "test-key",
        "DeviceTypeDisplay": "Test Device",
        "Registers": [{"UtilityCode": "CW"}],
    }

    sensor = EcoGuardDailyConsumptionSensor(
        hass=hass,
        coordinator=coordinator,
        installation=installation,
        utility_code="CW",
        measuring_point_id=1,
        measuring_point_name="Test Measuring Point",
    )

    # unique_id format: ecoguard_consumption_daily_{utility_slug}_{sensor_name}
    # sensor_name is slugified measuring_point_name or f"mp{measuring_point_id}"
    assert sensor._attr_unique_id == "ecoguard_consumption_daily_cold_water_test_measuring_point"
    assert "Consumption Daily" in sensor._attr_name
    assert sensor._attr_state_class is not None
    assert sensor._attr_entity_registry_enabled_default is False


async def test_daily_consumption_sensor_fetch_value(
    hass: HomeAssistant, coordinator
):
    """Test daily consumption sensor fetching value."""
    installation = {
        "MeasuringPointID": 1,
        "ExternalKey": "test-key",
        "Registers": [{"UtilityCode": "CW"}],
    }

    sensor = EcoGuardDailyConsumptionSensor(
        hass=hass,
        coordinator=coordinator,
        installation=installation,
        utility_code="CW",
        measuring_point_id=1,
        measuring_point_name=None,
    )

    # unique_id format when measuring_point_name is None: ecoguard_consumption_daily_{utility_slug}_mp{measuring_point_id}
    assert sensor._attr_unique_id == "ecoguard_consumption_daily_cold_water_mp1"
    
    # Set hass on sensor (normally done by async_added_to_hass)
    sensor.hass = hass
    # Set platform to avoid warning
    sensor.platform = MagicMock()
    # Set entity_id to avoid NoEntitySpecifiedError
    sensor.entity_id = "sensor.test_daily_consumption"

    # Mock the coordinator method
    with patch.object(
        coordinator,
        "get_latest_consumption_value",
        new_callable=AsyncMock,
        return_value={
            "value": 10.5,
            "unit": "m³",
            "time": int(datetime.now().timestamp()),
        }
    ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
        await sensor._async_fetch_value()

        assert sensor._attr_native_value == 10.5
        assert sensor._attr_native_unit_of_measurement == "m³"


async def test_latest_reception_sensor(
    hass: HomeAssistant, latest_reception_coordinator
):
    """Test latest reception sensor."""
    latest_reception_coordinator.data = [
        {"PositionID": 1, "LatestReception": 1234567890}
    ]

    sensor = EcoGuardLatestReceptionSensor(
        hass=hass,
        coordinator=latest_reception_coordinator,
        measuring_point_id=1,
        measuring_point_name="Test Measuring Point",
        utility_code="CW",
    )

    # unique_id format: ecoguard_reception_last_update_{utility_slug}_{sensor_name}
    assert sensor._attr_unique_id == "ecoguard_reception_last_update_cold_water_test_measuring_point"
    assert sensor._attr_device_class is not None
    assert sensor._attr_entity_registry_enabled_default is False


async def test_latest_reception_sensor_fetch_value(
    hass: HomeAssistant, latest_reception_coordinator
):
    """Test latest reception sensor fetching value."""
    # Set coordinator data
    latest_reception_coordinator.data = [
        {"PositionID": 1, "LatestReception": 1234567890}
    ]

    sensor = EcoGuardLatestReceptionSensor(
        hass=hass,
        coordinator=latest_reception_coordinator,
        measuring_point_id=1,
        measuring_point_name=None,
    )

    # unique_id format when measuring_point_name is None: ecoguard_reception_last_update_mp{measuring_point_id}
    assert sensor._attr_unique_id == "ecoguard_reception_last_update_mp1"
    
    # Set hass on sensor (normally done by async_added_to_hass)
    sensor.hass = hass
    # Set platform to avoid warning
    sensor.platform = MagicMock()
    # Set entity_id to avoid NoEntitySpecifiedError
    sensor.entity_id = "sensor.test_latest_reception"

    with patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock) as mock_write:
        await sensor._async_fetch_value()

        assert sensor._attr_native_value is not None
        assert isinstance(sensor._attr_native_value, datetime)
        # Verify write_ha_state was called
        mock_write.assert_called_once()


async def test_monthly_aggregate_sensor_consumption(
    hass: HomeAssistant, coordinator
):
    """Test monthly aggregate sensor for consumption."""
    sensor = EcoGuardMonthlyAggregateSensor(
        hass=hass,
        coordinator=coordinator,
        utility_code="CW",
        aggregate_type="con",
    )

    assert "consumption" in sensor._attr_name.lower()
    assert sensor._attr_state_class is not None


async def test_monthly_aggregate_sensor_cost(
    hass: HomeAssistant, coordinator
):
    """Test monthly aggregate sensor for cost."""
    sensor = EcoGuardMonthlyAggregateSensor(
        hass=hass,
        coordinator=coordinator,
        utility_code="HW",
        aggregate_type="price",
        cost_type="estimated",
    )

    assert "Cost Monthly Aggregated" in sensor._attr_name
    assert "Estimated" in sensor._attr_name


async def test_monthly_aggregate_sensor_fetch_value(
    hass: HomeAssistant, coordinator
):
    """Test monthly aggregate sensor fetching value."""
    sensor = EcoGuardMonthlyAggregateSensor(
        hass=hass,
        coordinator=coordinator,
        utility_code="CW",
        aggregate_type="con",
    )

    # Set hass on sensor (normally done by async_added_to_hass)
    sensor.hass = hass
    # Set platform to avoid warning
    sensor.platform = MagicMock()
    # Set entity_id to avoid NoEntitySpecifiedError
    sensor.entity_id = "sensor.test_monthly_aggregate"

    with patch.object(
        coordinator,
        "get_monthly_aggregate",
        new_callable=AsyncMock,
        return_value={
            "value": 100.0,  # Use 100.0 to avoid rounding issues
            "unit": "m³",
            "year": 2024,
            "month": 1,
        }
    ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
        await sensor._async_fetch_value()

        assert sensor._attr_native_value == 100.0
        assert sensor._attr_native_unit_of_measurement == "m³"


async def test_other_items_sensor(hass: HomeAssistant, coordinator):
    """Test other items sensor."""
    sensor = EcoGuardOtherItemsSensor(
        hass=hass,
        coordinator=coordinator,
    )

    assert "other items" in sensor._attr_name.lower()
    assert sensor._attr_state_class is not None


async def test_other_items_sensor_fetch_value(hass: HomeAssistant, coordinator):
    """Test other items sensor fetching value."""
    sensor = EcoGuardOtherItemsSensor(
        hass=hass,
        coordinator=coordinator,
    )

    # Set hass on sensor (normally done by async_added_to_hass)
    sensor.hass = hass
    # Set platform to avoid warning
    sensor.platform = MagicMock()
    # Set entity_id to avoid NoEntitySpecifiedError
    sensor.entity_id = "sensor.test_other_items"

    with patch.object(
        coordinator,
        "get_monthly_other_items_cost",
        new_callable=AsyncMock,
        return_value={
            "value": 50.0,
            "unit": "NOK",
            "year": 2024,
            "month": 1,
            "item_count": 2,
            "items": [{"Name": "Fee 1", "Amount": 25.0}],
        }
    ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
        await sensor._async_fetch_value()

        assert sensor._attr_native_value == 50.0
        assert sensor._attr_native_unit_of_measurement == "NOK"


async def test_total_monthly_cost_sensor(hass: HomeAssistant, coordinator):
    """Test total monthly cost sensor."""
    sensor = EcoGuardTotalMonthlyCostSensor(
        hass=hass,
        coordinator=coordinator,
        cost_type="actual",  # Internal: "actual", Display: "Metered"
    )

    assert "Cost Monthly Aggregated" in sensor._attr_name
    assert "Metered" in sensor._attr_name
    assert "All Utilities" in sensor._attr_name


async def test_total_monthly_cost_sensor_estimated(
    hass: HomeAssistant, coordinator
):
    """Test total monthly cost sensor with estimated type."""
    sensor = EcoGuardTotalMonthlyCostSensor(
        hass=hass,
        coordinator=coordinator,
        cost_type="estimated",
    )

    assert "estimated" in sensor._attr_name.lower()


async def test_end_of_month_estimate_sensor(hass: HomeAssistant, coordinator):
    """Test end of month estimate sensor."""
    sensor = EcoGuardEndOfMonthEstimateSensor(
        hass=hass,
        coordinator=coordinator,
    )

    assert "end of month" in sensor._attr_name.lower() or "estimate" in sensor._attr_name.lower()


async def test_end_of_month_estimate_sensor_fetch_value(
    hass: HomeAssistant, coordinator
):
    """Test end of month estimate sensor fetching value."""
    sensor = EcoGuardEndOfMonthEstimateSensor(
        hass=hass,
        coordinator=coordinator,
    )

    # Set hass on sensor (normally done by async_added_to_hass)
    sensor.hass = hass
    # Set platform to avoid warning
    sensor.platform = MagicMock()
    # Set entity_id to avoid NoEntitySpecifiedError
    sensor.entity_id = "sensor.test_end_of_month_estimate"

    with patch.object(
        coordinator,
        "get_end_of_month_estimate",
        new_callable=AsyncMock,
        return_value={
            "total_bill_estimate": 500.0,
            "currency": "NOK",
            "year": 2024,
            "month": 1,
            "days_elapsed_calendar": 15,
            "days_with_data": 14,
        }
    ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
        await sensor._async_fetch_value()

        assert sensor._attr_native_value == 500.0
        assert sensor._attr_native_unit_of_measurement == "NOK"


async def test_daily_cost_sensor(hass: HomeAssistant, coordinator, mock_coordinator_data: dict):
    """Test daily cost sensor."""
    # Set coordinator data
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    installation = {
        "MeasuringPointID": 1,
        "ExternalKey": "test-key",
        "DeviceTypeDisplay": "Test Device",
        "Registers": [{"UtilityCode": "CW"}],
    }

    sensor = EcoGuardDailyCostSensor(
        hass=hass,
        coordinator=coordinator,
        installation=installation,
        utility_code="CW",
        measuring_point_id=1,
        measuring_point_name="Test Measuring Point",
        cost_type="actual",
    )

    # unique_id format: ecoguard_cost_daily_metered_{utility_slug}_{sensor_name}
    assert sensor._attr_unique_id == "ecoguard_cost_daily_metered_cold_water_test_measuring_point"
    assert "Cost Daily" in sensor._attr_name
    assert "Metered" in sensor._attr_name
    assert sensor._attr_state_class is not None
    assert sensor._attr_entity_registry_enabled_default is False


async def test_monthly_meter_sensor(hass: HomeAssistant, coordinator, mock_coordinator_data: dict):
    """Test monthly meter sensor."""
    # Set coordinator data
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    installation = {
        "MeasuringPointID": 1,
        "ExternalKey": "test-key",
        "DeviceTypeDisplay": "Test Device",
        "Registers": [{"UtilityCode": "CW"}],
    }

    sensor = EcoGuardMonthlyMeterSensor(
        hass=hass,
        coordinator=coordinator,
        installation=installation,
        utility_code="CW",
        measuring_point_id=1,
        measuring_point_name="Test Measuring Point",
        aggregate_type="con",
    )

    # unique_id format: ecoguard_consumption_monthly_{utility_slug}_{sensor_name}
    assert sensor._attr_unique_id == "ecoguard_consumption_monthly_cold_water_test_measuring_point"
    assert "Consumption Monthly Aggregated" in sensor._attr_name
    assert "Meter" in sensor._attr_name
    assert sensor._attr_state_class is not None
    assert sensor._attr_entity_registry_enabled_default is False


async def test_async_setup_entry_sensors(
    hass: HomeAssistant, mock_config_entry: ConfigEntry, coordinator, latest_reception_coordinator
):
    """Test setting up sensors from config entry."""
    # Set up hass.data structure (this is normally done by async_setup_entry in __init__.py)
    hass.data.setdefault("ecoguard", {})
    hass.data["ecoguard"][mock_config_entry.entry_id] = {
        "coordinator": coordinator,
        "latest_reception_coordinator": latest_reception_coordinator,
        "api": MagicMock(),
    }

    # Mock coordinator data
    coordinator._measuring_points = [{"ID": 1, "Name": "Test MP"}]
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [{"UtilityCode": "CW"}, {"UtilityCode": "HW"}],
        }
    ]
    coordinator._settings = [{"Name": "Currency", "Value": "NOK"}]

    # Mock methods
    with patch.object(coordinator, "get_active_installations", return_value=coordinator._installations), \
         patch.object(coordinator, "get_measuring_points", return_value=coordinator._measuring_points), \
         patch.object(coordinator, "get_setting", return_value="NOK"), \
         patch.object(coordinator, "async_config_entry_first_refresh", new_callable=AsyncMock) as mock_refresh1, \
         patch.object(latest_reception_coordinator, "async_config_entry_first_refresh", new_callable=AsyncMock) as mock_refresh2:
        
        latest_reception_coordinator.data = []

        entities = []
        add_called = False

        def async_add_entities(new_entities, update_before_add=False):
            nonlocal add_called
            add_called = True
            entities.extend(new_entities)

        # Call the sensor setup function
        await async_setup_entry(hass, mock_config_entry, async_add_entities)
        await hass.async_block_till_done()

        # Should have called refresh
        mock_refresh1.assert_called_once()
        mock_refresh2.assert_called_once()

        # Should have called async_add_entities (it's called synchronously, not awaited)
        assert add_called, "async_add_entities should have been called"
        
        # Should have created sensors
        # With recent changes, we now have more sensors including:
        # - Individual meter sensors (daily consumption, daily cost, monthly meter, reception) - disabled by default
        # - Aggregate sensors (daily consumption, daily cost, monthly aggregates)
        # - Combined water sensors
        # - Total cost sensors
        # - Other items and end of month estimate
        assert len(entities) > 0, f"Expected sensors to be created, but got {len(entities)} entities. add_called={add_called}"
        
        # Verify that individual meter sensors are disabled by default
        daily_consumption_sensors = [e for e in entities if isinstance(e, EcoGuardDailyConsumptionSensor)]
        if daily_consumption_sensors:
            assert daily_consumption_sensors[0]._attr_entity_registry_enabled_default is False