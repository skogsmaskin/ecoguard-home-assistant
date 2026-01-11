"""Tests for the EcoGuard sensor entities."""

from unittest.mock import AsyncMock, MagicMock, patch
import inspect
import pytest
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from custom_components.ecoguard.sensor import (
    async_setup_entry,
)
from custom_components.ecoguard.sensors import (
    EcoGuardDailyConsumptionSensor,
    EcoGuardDailyConsumptionAggregateSensor,
    EcoGuardDailyCostSensor,
    EcoGuardDailyCostAggregateSensor,
    EcoGuardDailyCombinedWaterSensor,
    EcoGuardDailyCombinedWaterCostSensor,
    EcoGuardLatestReceptionSensor,
    EcoGuardMonthlyAccumulatedSensor,
    EcoGuardMonthlyMeterSensor,
    EcoGuardCombinedWaterSensor,
    EcoGuardOtherItemsSensor,
    EcoGuardTotalMonthlyCostSensor,
    EcoGuardEndOfMonthEstimateSensor,
)
from custom_components.ecoguard.helpers import (
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

    # unique_id format: ecoguard_consumption_daily_metered_{utility_slug}_meter_{sensor_name}
    # sensor_name is slugified measuring_point_name or f"mp{measuring_point_id}"
    assert (
        sensor._attr_unique_id
        == "ecoguard_consumption_daily_metered_cold_water_meter_test_measuring_point"
    )
    assert "Consumption Daily" in sensor._attr_name
    assert sensor._attr_state_class is not None
    assert sensor._attr_entity_registry_enabled_default is False


async def test_daily_consumption_sensor_fetch_value(hass: HomeAssistant, coordinator):
    """Test daily consumption sensor updating from coordinator data."""
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

    # unique_id format when measuring_point_name is None: ecoguard_consumption_daily_metered_{utility_slug}_meter_mp{measuring_point_id}
    assert sensor._attr_unique_id == "ecoguard_consumption_daily_metered_cold_water_meter_mp1"

    # Set hass on sensor (normally done by async_added_to_hass)
    sensor.hass = hass
    # Set platform to avoid warning
    sensor.platform = MagicMock()
    # Set entity_id to avoid NoEntitySpecifiedError
    sensor.entity_id = "sensor.test_daily_consumption"

    # Set coordinator data with consumption cache
    coordinator.data = {
        "latest_consumption_cache": {
            "CW_1": {
                "value": 10.5,
                "unit": "m³",
                "time": int(datetime.now().timestamp()),
            }
        }
    }

    with patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
        # Daily consumption sensor uses _update_from_coordinator_data, not _async_fetch_value
        sensor._update_from_coordinator_data()

        assert sensor._attr_native_value == 10.5
        assert sensor._attr_native_unit_of_measurement == "m³"


async def test_daily_consumption_sensor_last_data_date_and_lag(
    hass: HomeAssistant, coordinator
):
    """Test daily consumption sensor sets last_data_date and lag detection correctly."""
    from datetime import datetime, timedelta
    from custom_components.ecoguard.helpers import get_timezone

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

    sensor.hass = hass
    sensor.platform = MagicMock()
    sensor.entity_id = "sensor.test_daily_consumption"

    # Set timezone
    coordinator._settings = [{"Name": "TimeZoneIANA", "Value": "Europe/Oslo"}]

    tz = get_timezone("Europe/Oslo")
    now = datetime.now(tz)
    yesterday = now - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)

    # Set coordinator data with both latest cache and daily cache
    # Daily cache has data from 2 days ago (lagging)
    coordinator.data = {
        "latest_consumption_cache": {
            "CW_1": {
                "value": 10.5,
                "unit": "m³",
                "time": int(now.timestamp()),  # Latest timestamp is today
            }
        },
        "daily_consumption_cache": {
            "CW_1": [
                {
                    "time": int(two_days_ago.timestamp()),
                    "value": 8.0,
                    "unit": "m³",
                },
                {
                    "time": int(yesterday.timestamp()),
                    "value": None,  # Missing data for yesterday
                    "unit": "m³",
                },
                {
                    "time": int(now.timestamp()),
                    "value": None,  # Missing data for today
                    "unit": "m³",
                },
            ]
        },
    }

    with patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
        sensor._update_from_coordinator_data()

        # Should use actual last data date from daily cache (2 days ago), not latest timestamp
        assert sensor._last_data_date is not None
        assert sensor._last_data_date.date() == two_days_ago.date()

        # Should detect lag (2 days ago vs expected yesterday = 1 day lag)
        assert sensor._data_lagging is True
        assert sensor._data_lag_days == 1

        # Check attributes
        attrs = sensor.extra_state_attributes
        assert "last_data_date" in attrs
        assert attrs["data_lagging"] is True
        assert attrs["data_lag_days"] == 1

    # Test with up-to-date data (yesterday, which is expected)
    coordinator.data = {
        "latest_consumption_cache": {
            "CW_1": {
                "value": 10.5,
                "unit": "m³",
                "time": int(yesterday.timestamp()),
            }
        },
        "daily_consumption_cache": {
            "CW_1": [
                {
                    "time": int(yesterday.timestamp()),
                    "value": 10.5,
                    "unit": "m³",
                },
            ]
        },
    }

    with patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
        sensor._update_from_coordinator_data()

        assert sensor._last_data_date is not None
        assert sensor._last_data_date.date() == yesterday.date()

        # Should not be lagging (yesterday is expected)
        assert sensor._data_lagging is False
        assert sensor._data_lag_days == 0

        attrs = sensor.extra_state_attributes
        assert attrs["data_lagging"] is False
        assert attrs["data_lag_days"] == 0


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

    # unique_id format: ecoguard_reception_last_update_{utility_slug}_meter_{sensor_name}
    assert (
        sensor._attr_unique_id
        == "ecoguard_reception_last_update_cold_water_meter_test_measuring_point"
    )
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

    # unique_id format when measuring_point_name is None: ecoguard_reception_last_update_meter_mp{measuring_point_id}
    assert sensor._attr_unique_id == "ecoguard_reception_last_update_meter_mp1"

    # Set hass on sensor (normally done by async_added_to_hass)
    sensor.hass = hass
    # Set platform to avoid warning
    sensor.platform = MagicMock()
    # Set entity_id to avoid NoEntitySpecifiedError
    sensor.entity_id = "sensor.test_latest_reception"

    with patch.object(
        sensor, "async_write_ha_state", new_callable=AsyncMock
    ) as mock_write:
        await sensor._async_fetch_value()

        assert sensor._attr_native_value is not None
        assert isinstance(sensor._attr_native_value, datetime)
        # Verify write_ha_state was called
        mock_write.assert_called_once()


async def test_monthly_accumulated_sensor_consumption(hass: HomeAssistant, coordinator):
    """Test monthly accumulated sensor for consumption."""
    sensor = EcoGuardMonthlyAccumulatedSensor(
        hass=hass,
        coordinator=coordinator,
        utility_code="CW",
        aggregate_type="con",
    )

    assert "consumption" in sensor._attr_name.lower()
    assert sensor._attr_state_class is not None


async def test_monthly_accumulated_sensor_cost(hass: HomeAssistant, coordinator):
    """Test monthly accumulated sensor for cost."""
    sensor = EcoGuardMonthlyAccumulatedSensor(
        hass=hass,
        coordinator=coordinator,
        utility_code="HW",
        aggregate_type="price",
        cost_type="estimated",
    )

    assert "Cost Monthly Accumulated" in sensor._attr_name
    assert "Estimated" in sensor._attr_name


async def test_monthly_accumulated_sensor_fetch_value(hass: HomeAssistant, coordinator):
    """Test monthly accumulated sensor fetching value."""
    sensor = EcoGuardMonthlyAccumulatedSensor(
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
        },
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

    # get_monthly_other_items_cost is now on billing_manager
    with patch.object(
        coordinator.billing_manager,
        "get_monthly_other_items_cost",
        new_callable=AsyncMock,
        return_value={
            "value": 50.0,
            "unit": "NOK",
            "year": 2024,
            "month": 1,
            "item_count": 2,
            "items": [{"Name": "Fee 1", "Amount": 25.0}],
        },
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

    assert "Cost Monthly Accumulated" in sensor._attr_name
    assert "Metered" in sensor._attr_name
    assert "All Utilities" in sensor._attr_name


async def test_total_monthly_cost_sensor_estimated(hass: HomeAssistant, coordinator):
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

    # Check that the name contains the expected pattern
    name_lower = sensor._attr_name.lower()
    assert (
        "final settlement" in name_lower or "estimated" in name_lower
    ) and "cost monthly" in name_lower
    # Verify unique_id matches the new pattern
    assert sensor._attr_unique_id == "ecoguard_cost_monthly_estimated_final_settlement"


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
            # Include utility estimates so the sensor doesn't skip recording
            # At least one utility must have data > 0 for the sensor to record
            "hw_price_estimate": 200.0,
            "cw_price_estimate": 300.0,
        },
    ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
        await sensor._async_fetch_value()

        assert sensor._attr_native_value == 500.0
        assert sensor._attr_native_unit_of_measurement == "NOK"


async def test_daily_cost_sensor(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
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

    # unique_id format: ecoguard_cost_daily_metered_{utility_slug}_meter_{sensor_name}
    assert (
        sensor._attr_unique_id
        == "ecoguard_cost_daily_metered_cold_water_meter_test_measuring_point"
    )
    assert "Cost Daily" in sensor._attr_name
    assert "Metered" in sensor._attr_name
    assert sensor._attr_state_class is not None
    assert sensor._attr_entity_registry_enabled_default is False


async def test_monthly_meter_sensor(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
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

    # unique_id format: ecoguard_consumption_monthly_accumulated_{utility_slug}_meter_{sensor_name}
    assert (
        sensor._attr_unique_id
        == "ecoguard_consumption_monthly_accumulated_cold_water_meter_test_measuring_point"
    )
    assert "Consumption Monthly Accumulated" in sensor._attr_name
    assert "Meter" in sensor._attr_name
    assert sensor._attr_state_class is not None
    assert sensor._attr_entity_registry_enabled_default is False


async def test_async_setup_entry_sensors(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    coordinator,
    latest_reception_coordinator,
):
    """Test setting up sensors from config entry."""
    # Set up runtime_data structure (this is normally done by async_setup_entry in __init__.py)
    from custom_components.ecoguard import EcoGuardRuntimeData

    mock_config_entry.runtime_data = EcoGuardRuntimeData(
        coordinator=coordinator,
        latest_reception_coordinator=latest_reception_coordinator,
        api=MagicMock(),
    )

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
    with patch.object(
        coordinator, "get_active_installations", return_value=coordinator._installations
    ), patch.object(
        coordinator, "get_measuring_points", return_value=coordinator._measuring_points
    ), patch.object(
        coordinator, "get_setting", return_value="NOK"
    ), patch.object(
        coordinator, "async_config_entry_first_refresh", new_callable=AsyncMock
    ) as mock_refresh1, patch.object(
        latest_reception_coordinator,
        "async_config_entry_first_refresh",
        new_callable=AsyncMock,
    ) as mock_refresh2:

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
        # - Aggregate sensors (daily consumption, daily cost, monthly accumulated)
        # - Combined water sensors
        # - Total cost sensors
        # - Other items and end of month estimate
        assert (
            len(entities) > 0
        ), f"Expected sensors to be created, but got {len(entities)} entities. add_called={add_called}"

        # Verify that individual meter sensors are disabled by default
        daily_consumption_sensors = [
            e for e in entities if isinstance(e, EcoGuardDailyConsumptionSensor)
        ]
        if daily_consumption_sensors:
            assert (
                daily_consumption_sensors[0]._attr_entity_registry_enabled_default
                is False
            )


# ============================================================================
# Tests for v2.0.0 New Sensor Types
# ============================================================================


async def test_daily_cost_aggregate_sensor_metered(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test daily cost aggregate sensor (metered)."""
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    sensor = EcoGuardDailyCostAggregateSensor(
        hass=hass,
        coordinator=coordinator,
        utility_code="CW",
        cost_type="actual",
    )

    assert sensor._attr_unique_id == "ecoguard_cost_daily_metered_cold_water"
    assert "Cost Daily" in sensor._attr_name
    assert "Metered" in sensor._attr_name
    assert "Cold Water" in sensor._attr_name
    assert sensor._attr_state_class is not None


async def test_daily_cost_aggregate_sensor_estimated(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test daily cost aggregate sensor (estimated)."""
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    sensor = EcoGuardDailyCostAggregateSensor(
        hass=hass,
        coordinator=coordinator,
        utility_code="HW",
        cost_type="estimated",
    )

    assert sensor._attr_unique_id == "ecoguard_cost_daily_estimated_hot_water"
    assert "Cost Daily" in sensor._attr_name
    assert "Estimated" in sensor._attr_name
    assert "Hot Water" in sensor._attr_name


async def test_daily_cost_aggregate_sensor_update_from_cache(
    hass: HomeAssistant, coordinator
):
    """Test daily cost aggregate sensor updating from coordinator cache."""
    coordinator.data = {
        "latest_cost_cache": {
            "CW_1_metered": {
                "value": 10.0,
                "time": int(datetime.now().timestamp()),
                "unit": "NOK",
            },
            "CW_2_metered": {
                "value": 15.0,
                "time": int(datetime.now().timestamp()),
                "unit": "NOK",
            },
        }
    }
    coordinator._installations = [
        {"MeasuringPointID": 1, "Registers": [{"UtilityCode": "CW"}]},
        {"MeasuringPointID": 2, "Registers": [{"UtilityCode": "CW"}]},
    ]
    coordinator._measuring_points = [
        {"ID": 1, "Name": "MP1"},
        {"ID": 2, "Name": "MP2"},
    ]

    sensor = EcoGuardDailyCostAggregateSensor(
        hass=hass,
        coordinator=coordinator,
        utility_code="CW",
        cost_type="actual",
    )

    sensor.hass = hass
    sensor.platform = MagicMock()
    sensor.entity_id = "sensor.test_daily_cost_aggregate"

    with patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
        sensor._update_from_coordinator_data()

        # Should sum costs from both meters
        assert sensor._attr_native_value == 25.0
        assert len(sensor._meters_with_data) == 2


async def test_daily_consumption_aggregate_sensor_with_aggregated_cache(
    hass: HomeAssistant, coordinator
):
    """Test daily consumption aggregate sensor with aggregated cache key.

    This test verifies that when using the aggregated cache key (utility_code_all),
    the sensor correctly populates meter_count by checking individual meter caches.
    """
    # Set up coordinator data with both aggregated cache and individual meter caches
    coordinator.data = {
        "latest_consumption_cache": {
            # Aggregated cache for all HW meters
            "HW_all": {
                "value": 25.5,
                "time": int(datetime.now().timestamp()),
                "unit": "m³",
            },
            # Individual meter caches (needed for meter_count)
            "HW_1": {
                "value": 10.0,
                "time": int(datetime.now().timestamp()),
                "unit": "m³",
            },
            "HW_2": {
                "value": 15.5,
                "time": int(datetime.now().timestamp()),
                "unit": "m³",
            },
        },
        "daily_consumption_cache": {},
    }
    coordinator._installations = [
        {"MeasuringPointID": 1, "Registers": [{"UtilityCode": "HW"}]},
        {"MeasuringPointID": 2, "Registers": [{"UtilityCode": "HW"}]},
    ]
    coordinator._measuring_points = [
        {"ID": 1, "Name": "Hot Water Meter 1"},
        {"ID": 2, "Name": "Hot Water Meter 2"},
    ]
    coordinator._settings = [{"Name": "TimeZoneIANA", "Value": "UTC"}]

    sensor = EcoGuardDailyConsumptionAggregateSensor(
        hass=hass,
        coordinator=coordinator,
        utility_code="HW",
    )

    sensor.hass = hass
    sensor.platform = MagicMock()
    sensor.entity_id = "sensor.test_daily_consumption_aggregate"

    with patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
        sensor._update_from_coordinator_data()

        # Should use aggregated value
        assert sensor._attr_native_value == 25.5
        assert sensor._attr_native_unit_of_measurement == "m³"

        # meter_count should be populated from individual meter caches
        assert len(sensor._meters_with_data) == 2
        assert sensor.extra_state_attributes["meter_count"] == 2

        # Verify meter details
        meters = sensor.extra_state_attributes["meters"]
        assert len(meters) == 2
        assert meters[0]["measuring_point_id"] == 1
        assert meters[0]["measuring_point_name"] == "Hot Water Meter 1"
        assert meters[0]["value"] == 10.0
        assert meters[1]["measuring_point_id"] == 2
        assert meters[1]["measuring_point_name"] == "Hot Water Meter 2"
        assert meters[1]["value"] == 15.5


async def test_daily_combined_water_sensor(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test daily combined water consumption sensor."""
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    sensor = EcoGuardDailyCombinedWaterSensor(
        hass=hass,
        coordinator=coordinator,
    )

    assert sensor._attr_unique_id == "ecoguard_consumption_daily_metered_combined_water"
    assert "Consumption Daily" in sensor._attr_name
    assert "Combined Water" in sensor._attr_name
    assert sensor._attr_state_class is not None


async def test_daily_combined_water_sensor_update_from_cache(
    hass: HomeAssistant, coordinator
):
    """Test daily combined water sensor updating from coordinator cache."""
    coordinator.data = {
        "latest_consumption_cache": {
            "HW_1": {
                "value": 5.0,
                "time": int(datetime.now().timestamp()),
                "unit": "m³",
            },
            "CW_1": {
                "value": 10.0,
                "time": int(datetime.now().timestamp()),
                "unit": "m³",
            },
        }
    }
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "Registers": [{"UtilityCode": "HW"}, {"UtilityCode": "CW"}],
        },
    ]
    coordinator._measuring_points = [{"ID": 1, "Name": "MP1"}]

    sensor = EcoGuardDailyCombinedWaterSensor(
        hass=hass,
        coordinator=coordinator,
    )

    sensor.hass = hass
    sensor.platform = MagicMock()
    sensor.entity_id = "sensor.test_combined_water"

    with patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
        sensor._update_from_coordinator_data()

        # Should sum HW and CW consumption
        assert sensor._attr_native_value == 15.0
        assert len(sensor._hw_meters_with_data) == 1
        assert len(sensor._cw_meters_with_data) == 1


async def test_daily_combined_water_cost_sensor_metered(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test daily combined water cost sensor (metered)."""
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    sensor = EcoGuardDailyCombinedWaterCostSensor(
        hass=hass,
        coordinator=coordinator,
        cost_type="actual",
    )

    assert sensor._attr_unique_id == "ecoguard_cost_daily_metered_combined_water"
    assert "Cost Daily" in sensor._attr_name
    assert "Metered" in sensor._attr_name
    assert "Combined Water" in sensor._attr_name


async def test_daily_combined_water_cost_sensor_estimated(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test daily combined water cost sensor (estimated)."""
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    sensor = EcoGuardDailyCombinedWaterCostSensor(
        hass=hass,
        coordinator=coordinator,
        cost_type="estimated",
    )

    assert sensor._attr_unique_id == "ecoguard_cost_daily_estimated_combined_water"
    assert "Estimated" in sensor._attr_name


async def test_monthly_combined_water_sensor_consumption(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test monthly combined water sensor for consumption."""
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    sensor = EcoGuardCombinedWaterSensor(
        hass=hass,
        coordinator=coordinator,
        aggregate_type="con",
    )

    assert "Consumption Monthly Accumulated" in sensor._attr_name
    assert "Combined Water" in sensor._attr_name
    assert sensor._attr_state_class is not None


async def test_monthly_combined_water_sensor_cost_metered(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test monthly combined water sensor for cost (metered)."""
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    sensor = EcoGuardCombinedWaterSensor(
        hass=hass,
        coordinator=coordinator,
        aggregate_type="price",
        cost_type="actual",
    )

    assert "Cost Monthly Accumulated" in sensor._attr_name
    assert "Metered" in sensor._attr_name
    assert "Combined Water" in sensor._attr_name


async def test_monthly_combined_water_sensor_cost_estimated(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test monthly combined water sensor for cost (estimated)."""
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    sensor = EcoGuardCombinedWaterSensor(
        hass=hass,
        coordinator=coordinator,
        aggregate_type="price",
        cost_type="estimated",
    )

    assert "Cost Monthly Accumulated" in sensor._attr_name
    assert "Estimated" in sensor._attr_name
    assert "Combined Water" in sensor._attr_name


async def test_monthly_meter_sensor_cost(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test monthly meter sensor for cost."""
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
        aggregate_type="price",
        cost_type="actual",
    )

    assert "Cost Monthly Accumulated" in sensor._attr_name
    assert "Meter" in sensor._attr_name
    assert sensor._attr_state_class is not None
    assert sensor._attr_entity_registry_enabled_default is False


async def test_sensor_descriptions(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test that sensors have descriptions."""
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    installation = {
        "MeasuringPointID": 1,
        "ExternalKey": "test-key",
        "Registers": [{"UtilityCode": "CW"}],
    }

    # Test daily consumption sensor has description
    sensor = EcoGuardDailyConsumptionSensor(
        hass=hass,
        coordinator=coordinator,
        installation=installation,
        utility_code="CW",
        measuring_point_id=1,
        measuring_point_name="Test MP",
    )
    # Check that sensor has _attr_entity_description or entity_description attribute
    # The description is set via _set_entity_description() in sensor_base
    assert hasattr(sensor, "_attr_entity_description") or hasattr(
        sensor, "entity_description"
    )
    desc = getattr(sensor, "_attr_entity_description", None) or getattr(
        sensor, "entity_description", None
    )
    assert desc is not None
    assert desc.description is not None
    assert len(desc.description) > 0

    # Test daily cost sensor has description
    cost_sensor = EcoGuardDailyCostSensor(
        hass=hass,
        coordinator=coordinator,
        installation=installation,
        utility_code="CW",
        measuring_point_id=1,
        measuring_point_name="Test MP",
        cost_type="actual",
    )
    assert hasattr(cost_sensor, "_attr_entity_description") or hasattr(
        cost_sensor, "entity_description"
    )
    desc = getattr(cost_sensor, "_attr_entity_description", None) or getattr(
        cost_sensor, "entity_description", None
    )
    assert desc is not None
    assert desc.description is not None
