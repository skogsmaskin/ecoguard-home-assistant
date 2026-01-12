"""Tests for estimated cost sensor fixes.

This test suite verifies:
1. Estimated cost sensors don't use metered cache
2. Estimation metadata is exposed in attributes
3. State is not written when value is None (prevents recording Unknown)
4. Async fetch is triggered for estimated costs
"""

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from homeassistant.core import HomeAssistant, CoreState

from custom_components.ecoguard.sensors.daily import (
    EcoGuardDailyCostAggregateSensor,
    EcoGuardDailyCombinedWaterCostSensor,
    EcoGuardDailyCostSensor,
)


async def test_estimated_aggregate_sensor_skips_metered_cache(
    hass: HomeAssistant, coordinator
):
    """Test that estimated aggregate sensor doesn't use metered cache."""
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [{"UtilityCode": "HW"}],
        },
    ]
    coordinator._measuring_points = [{"ID": 1, "Name": "MP1"}]

    def mock_get_setting(name):
        if name == "Currency":
            return "NOK"
        elif name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    with patch.object(
        coordinator, "get_active_installations", return_value=coordinator._installations
    ), patch.object(
        coordinator, "get_measuring_points", return_value=coordinator._measuring_points
    ), patch.object(
        coordinator, "get_setting", side_effect=mock_get_setting
    ):

        # Set up coordinator data with metered cache (should be ignored for estimated)
        coordinator.data = {
            "latest_cost_cache": {
                "HW_1_metered": {
                    "value": 50.0,  # This should be ignored for estimated
                    "time": int(datetime.now().timestamp()),
                    "unit": "NOK",
                }
            },
            "daily_price_cache": {
                "HW_1_metered": [
                    {
                        "value": 50.0,
                        "time": int(datetime.now().timestamp()),
                        "unit": "NOK",
                    }
                ]
            },
            "daily_consumption_cache": {
                "HW_1": [
                    {
                        "value": 0.125,
                        "time": int(datetime.now().timestamp()),
                        "unit": "m3",
                    }
                ]
            },
        }

        sensor = EcoGuardDailyCostAggregateSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="HW",
            cost_type="estimated",
        )

        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_estimated_cost"

        # Mock async_create_task to capture the async fetch
        hass.async_create_task = MagicMock()
        hass.is_stopping = False
        hass.state = CoreState.running

        # Update from coordinator data
        sensor._update_from_coordinator_data()

        # Verify that async fetch was triggered (not using metered cache)
        assert (
            hass.async_create_task.called
        ), "Async fetch should be triggered for estimated costs"

        # Verify value is None (waiting for async fetch)
        assert (
            sensor._attr_native_value is None
        ), "Value should be None until async fetch completes"


async def test_estimated_combined_water_sensor_skips_metered_cache(
    hass: HomeAssistant, coordinator
):
    """Test that estimated combined water sensor doesn't use metered cache."""
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [
                {"UtilityCode": "HW"},
                {"UtilityCode": "CW"},
            ],
        },
    ]
    coordinator._measuring_points = [{"ID": 1, "Name": "MP1"}]

    def mock_get_setting(name):
        if name == "Currency":
            return "NOK"
        elif name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    with patch.object(
        coordinator, "get_active_installations", return_value=coordinator._installations
    ), patch.object(
        coordinator, "get_measuring_points", return_value=coordinator._measuring_points
    ), patch.object(
        coordinator, "get_setting", side_effect=mock_get_setting
    ):

        # Set up coordinator data with metered cache (should be ignored for estimated)
        coordinator.data = {
            "latest_cost_cache": {
                "HW_1_metered": {
                    "value": 30.0,
                    "time": int(datetime.now().timestamp()),
                    "unit": "NOK",
                },
                "CW_1_metered": {
                    "value": 20.0,
                    "time": int(datetime.now().timestamp()),
                    "unit": "NOK",
                },
            },
            "daily_price_cache": {
                "HW_1_metered": [
                    {
                        "value": 30.0,
                        "time": int(datetime.now().timestamp()),
                        "unit": "NOK",
                    }
                ],
                "CW_1_metered": [
                    {
                        "value": 20.0,
                        "time": int(datetime.now().timestamp()),
                        "unit": "NOK",
                    }
                ],
            },
            "daily_consumption_cache": {
                "HW_1": [
                    {
                        "value": 0.1,
                        "time": int(datetime.now().timestamp()),
                        "unit": "m3",
                    }
                ],
                "CW_1": [
                    {
                        "value": 0.1,
                        "time": int(datetime.now().timestamp()),
                        "unit": "m3",
                    }
                ],
            },
        }

        sensor = EcoGuardDailyCombinedWaterCostSensor(
            hass=hass,
            coordinator=coordinator,
            cost_type="estimated",
        )

        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_estimated_combined_water"

        hass.async_create_task = MagicMock()
        hass.is_stopping = False
        hass.state = CoreState.running

        # Update from coordinator data
        sensor._update_from_coordinator_data()

        # Verify that async fetch was triggered (not using metered cache)
        assert (
            hass.async_create_task.called
        ), "Async fetch should be triggered for estimated costs"

        # Verify value is None (waiting for async fetch)
        assert (
            sensor._attr_native_value is None
        ), "Value should be None until async fetch completes"


async def test_estimated_aggregate_sensor_exposes_metadata(
    hass: HomeAssistant, coordinator
):
    """Test that estimated aggregate sensor exposes estimation metadata."""
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [{"UtilityCode": "HW"}],
        },
    ]
    coordinator._measuring_points = [{"ID": 1, "Name": "MP1"}]

    def mock_get_setting(name):
        if name == "Currency":
            return "NOK"
        elif name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    with patch.object(
        coordinator, "get_active_installations", return_value=coordinator._installations
    ), patch.object(
        coordinator, "get_measuring_points", return_value=coordinator._measuring_points
    ), patch.object(
        coordinator, "get_setting", side_effect=mock_get_setting
    ):

        # Mock get_latest_estimated_cost to return data with metadata
        mock_estimated_cost = {
            "value": 45.50,
            "time": int(datetime.now().timestamp()),
            "unit": "NOK",
            "utility_code": "HW",
            "cost_type": "estimated",
            "calculation_method": "spot_price_calibrated",
            "consumption_m3": 0.125,
            "energy_per_m3_kwh": 45.0,
            "total_energy_kwh": 5.625,
            "spot_price_per_kwh": 0.5234,
            "spot_price_currency": "NOK",
            "heating_cost": 29.45,
            "calibration_ratio": 1.85,
            "base_heating_cost": 15.92,
            "nord_pool_area": "NO1",
            "price_source": "nord_pool_api",
        }

        coordinator.get_latest_estimated_cost = AsyncMock(
            return_value=mock_estimated_cost
        )

        sensor = EcoGuardDailyCostAggregateSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="HW",
            cost_type="estimated",
        )

        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_estimated_cost"

        # Trigger async fetch
        await sensor._async_fetch_value()

        # Check that value is set
        assert sensor._attr_native_value == 45.50

        # Check that estimation metadata is stored
        assert sensor._estimation_metadata is not None
        assert (
            sensor._estimation_metadata["calculation_method"] == "spot_price_calibrated"
        )
        assert sensor._estimation_metadata["spot_price_per_kwh"] == 0.5234
        assert sensor._estimation_metadata["calibration_ratio"] == 1.85

        # Check that metadata is exposed in attributes
        attrs = sensor.extra_state_attributes
        assert "estimation" in attrs
        assert attrs["estimation"]["calculation_method"] == "spot_price_calibrated"
        assert attrs["estimation"]["spot_price_per_kwh"] == 0.5234


async def test_estimated_combined_water_sensor_exposes_metadata(
    hass: HomeAssistant, coordinator
):
    """Test that estimated combined water sensor exposes estimation metadata."""
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [
                {"UtilityCode": "HW"},
                {"UtilityCode": "CW"},
            ],
        },
    ]
    coordinator._measuring_points = [{"ID": 1, "Name": "MP1"}]

    def mock_get_setting(name):
        if name == "Currency":
            return "NOK"
        elif name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    with patch.object(
        coordinator, "get_active_installations", return_value=coordinator._installations
    ), patch.object(
        coordinator, "get_measuring_points", return_value=coordinator._measuring_points
    ), patch.object(
        coordinator, "get_setting", side_effect=mock_get_setting
    ):

        # Mock get_latest_estimated_cost to return data with metadata
        hw_estimated_cost = {
            "value": 35.50,
            "time": int(datetime.now().timestamp()),
            "unit": "NOK",
            "calculation_method": "spot_price_calibrated",
            "spot_price_per_kwh": 0.5234,
            "calibration_ratio": 1.85,
        }
        cw_estimated_cost = {
            "value": 12.50,
            "time": int(datetime.now().timestamp()),
            "unit": "NOK",
            "calculation_method": "billing_rate",
            "rate_per_m3": 12.50,
            "consumption_m3": 1.0,
        }

        async def mock_get_estimated_cost(utility_code, **kwargs):
            if utility_code == "HW":
                return hw_estimated_cost
            elif utility_code == "CW":
                return cw_estimated_cost
            return None

        coordinator.get_latest_estimated_cost = AsyncMock(
            side_effect=mock_get_estimated_cost
        )

        sensor = EcoGuardDailyCombinedWaterCostSensor(
            hass=hass,
            coordinator=coordinator,
            cost_type="estimated",
        )

        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_estimated_combined_water"

        # Trigger async fetch
        await sensor._async_fetch_value()

        # Check that value is set
        assert sensor._attr_native_value == 48.0  # 35.50 + 12.50

        # Check that estimation metadata is stored
        assert sensor._estimation_metadata is not None
        assert "hw" in sensor._estimation_metadata
        assert "cw" in sensor._estimation_metadata
        assert (
            sensor._estimation_metadata["hw"]["calculation_method"]
            == "spot_price_calibrated"
        )
        assert sensor._estimation_metadata["cw"]["calculation_method"] == "billing_rate"

        # Check that metadata is exposed in attributes
        attrs = sensor.extra_state_attributes
        assert "estimation" in attrs
        assert "hw" in attrs["estimation"]
        assert "cw" in attrs["estimation"]
        assert attrs["estimation"]["hw"]["spot_price_per_kwh"] == 0.5234
        assert attrs["estimation"]["cw"]["rate_per_m3"] == 12.50


async def test_estimated_aggregate_sensor_skips_writing_none(
    hass: HomeAssistant, coordinator
):
    """Test that estimated aggregate sensor doesn't write state when value is None."""
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [{"UtilityCode": "HW"}],
        },
    ]
    coordinator._measuring_points = [{"ID": 1, "Name": "MP1"}]

    def mock_get_setting(name):
        if name == "Currency":
            return "NOK"
        elif name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    with patch.object(
        coordinator, "get_active_installations", return_value=coordinator._installations
    ), patch.object(
        coordinator, "get_measuring_points", return_value=coordinator._measuring_points
    ), patch.object(
        coordinator, "get_setting", side_effect=mock_get_setting
    ):

        coordinator.data = {
            "daily_consumption_cache": {
                "HW_1": [
                    {
                        "value": 0.125,
                        "time": int(datetime.now().timestamp()),
                        "unit": "m3",
                    }
                ]
            },
        }

        sensor = EcoGuardDailyCostAggregateSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="HW",
            cost_type="estimated",
        )

        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_estimated_cost"

        # Mock async_write_ha_state to verify it's not called
        sensor.async_write_ha_state = MagicMock()
        sensor._async_write_ha_state_if_changed = MagicMock()

        hass.async_create_task = MagicMock()
        hass.is_stopping = False
        hass.state = CoreState.running

        # Update from coordinator data (value will be None, waiting for async fetch)
        sensor._update_from_coordinator_data()

        # Verify that async_write_ha_state was NOT called (we skip writing None)
        sensor.async_write_ha_state.assert_not_called()
        sensor._async_write_ha_state_if_changed.assert_not_called()

        # Verify async fetch was triggered
        assert hass.async_create_task.called


async def test_estimated_combined_water_sensor_skips_writing_none(
    hass: HomeAssistant, coordinator
):
    """Test that estimated combined water sensor doesn't write state when value is None."""
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [
                {"UtilityCode": "HW"},
                {"UtilityCode": "CW"},
            ],
        },
    ]
    coordinator._measuring_points = [{"ID": 1, "Name": "MP1"}]

    def mock_get_setting(name):
        if name == "Currency":
            return "NOK"
        elif name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    with patch.object(
        coordinator, "get_active_installations", return_value=coordinator._installations
    ), patch.object(
        coordinator, "get_measuring_points", return_value=coordinator._measuring_points
    ), patch.object(
        coordinator, "get_setting", side_effect=mock_get_setting
    ):

        coordinator.data = {
            "daily_consumption_cache": {
                "HW_1": [
                    {
                        "value": 0.1,
                        "time": int(datetime.now().timestamp()),
                        "unit": "m3",
                    }
                ],
                "CW_1": [
                    {
                        "value": 0.1,
                        "time": int(datetime.now().timestamp()),
                        "unit": "m3",
                    }
                ],
            },
        }

        sensor = EcoGuardDailyCombinedWaterCostSensor(
            hass=hass,
            coordinator=coordinator,
            cost_type="estimated",
        )

        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_estimated_combined_water"

        # Mock async_write_ha_state to verify it's not called
        sensor.async_write_ha_state = MagicMock()
        sensor._async_write_ha_state_if_changed = MagicMock()

        hass.async_create_task = MagicMock()
        hass.is_stopping = False
        hass.state = CoreState.running

        # Update from coordinator data (value will be None, waiting for async fetch)
        sensor._update_from_coordinator_data()

        # Verify that async_write_ha_state was NOT called (we skip writing None)
        sensor.async_write_ha_state.assert_not_called()
        sensor._async_write_ha_state_if_changed.assert_not_called()

        # Verify async fetch was triggered
        assert hass.async_create_task.called


async def test_estimated_aggregate_sensor_writes_when_async_fetch_completes(
    hass: HomeAssistant, coordinator
):
    """Test that estimated aggregate sensor writes state when async fetch completes with value."""
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [{"UtilityCode": "HW"}],
        },
    ]
    coordinator._measuring_points = [{"ID": 1, "Name": "MP1"}]

    def mock_get_setting(name):
        if name == "Currency":
            return "NOK"
        elif name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    with patch.object(
        coordinator, "get_active_installations", return_value=coordinator._installations
    ), patch.object(
        coordinator, "get_measuring_points", return_value=coordinator._measuring_points
    ), patch.object(
        coordinator, "get_setting", side_effect=mock_get_setting
    ):

        # Mock get_latest_estimated_cost to return valid data
        mock_estimated_cost = {
            "value": 45.50,
            "time": int(datetime.now().timestamp()),
            "unit": "NOK",
            "calculation_method": "spot_price_calibrated",
            "spot_price_per_kwh": 0.5234,
        }

        coordinator.get_latest_estimated_cost = AsyncMock(
            return_value=mock_estimated_cost
        )

        sensor = EcoGuardDailyCostAggregateSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="HW",
            cost_type="estimated",
        )

        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_estimated_cost"

        # Mock async_write_ha_state_if_changed to verify it's called
        sensor._async_write_ha_state_if_changed = MagicMock()

        # Trigger async fetch
        await sensor._async_fetch_value()

        # Verify that value is set
        assert sensor._attr_native_value == 45.50

        # Verify that state write was called (with valid value)
        sensor._async_write_ha_state_if_changed.assert_called_once()


async def test_estimated_combined_water_sensor_writes_when_async_fetch_completes(
    hass: HomeAssistant, coordinator
):
    """Test that estimated combined water sensor writes state when async fetch completes."""
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [
                {"UtilityCode": "HW"},
                {"UtilityCode": "CW"},
            ],
        },
    ]
    coordinator._measuring_points = [{"ID": 1, "Name": "MP1"}]

    def mock_get_setting(name):
        if name == "Currency":
            return "NOK"
        elif name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    with patch.object(
        coordinator, "get_active_installations", return_value=coordinator._installations
    ), patch.object(
        coordinator, "get_measuring_points", return_value=coordinator._measuring_points
    ), patch.object(
        coordinator, "get_setting", side_effect=mock_get_setting
    ):

        # Mock get_latest_estimated_cost to return valid data
        async def mock_get_estimated_cost(utility_code, **kwargs):
            if utility_code == "HW":
                return {
                    "value": 35.50,
                    "time": int(datetime.now().timestamp()),
                    "unit": "NOK",
                }
            elif utility_code == "CW":
                return {
                    "value": 12.50,
                    "time": int(datetime.now().timestamp()),
                    "unit": "NOK",
                }
            return None

        coordinator.get_latest_estimated_cost = AsyncMock(
            side_effect=mock_get_estimated_cost
        )

        sensor = EcoGuardDailyCombinedWaterCostSensor(
            hass=hass,
            coordinator=coordinator,
            cost_type="estimated",
        )

        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_estimated_combined_water"

        # Mock async_write_ha_state_if_changed to verify it's called
        sensor._async_write_ha_state_if_changed = MagicMock()

        # Trigger async fetch
        await sensor._async_fetch_value()

        # Verify that value is set
        assert sensor._attr_native_value == 48.0  # 35.50 + 12.50

        # Verify that state write was called (with valid value)
        sensor._async_write_ha_state_if_changed.assert_called_once()


async def test_estimated_and_metered_show_different_values(
    hass: HomeAssistant, coordinator
):
    """Test that estimated and metered sensors can show different values."""
    coordinator._installations = [
        {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [{"UtilityCode": "HW"}],
        },
    ]
    coordinator._measuring_points = [{"ID": 1, "Name": "MP1"}]

    def mock_get_setting(name):
        if name == "Currency":
            return "NOK"
        elif name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    with patch.object(
        coordinator, "get_active_installations", return_value=coordinator._installations
    ), patch.object(
        coordinator, "get_measuring_points", return_value=coordinator._measuring_points
    ), patch.object(
        coordinator, "get_setting", side_effect=mock_get_setting
    ):

        # Set up coordinator data with metered cache (old data with 11-day lag)
        old_timestamp = int(
            (datetime.now().replace(tzinfo=timezone.utc).timestamp() - 11 * 86400)
        )
        coordinator.data = {
            "latest_cost_cache": {
                "HW_1_metered": {
                    "value": 50.0,  # Old metered value
                    "time": old_timestamp,
                    "unit": "NOK",
                }
            },
            "daily_consumption_cache": {
                "HW_1": [
                    {
                        "value": 0.125,
                        "time": int(datetime.now().timestamp()),  # Current consumption
                        "unit": "m3",
                    }
                ]
            },
        }

        # Create metered sensor
        metered_sensor = EcoGuardDailyCostAggregateSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="HW",
            cost_type="actual",
        )

        metered_sensor.hass = hass
        metered_sensor.platform = MagicMock()
        metered_sensor.entity_id = "sensor.test_metered_cost"

        # Create estimated sensor
        estimated_sensor = EcoGuardDailyCostAggregateSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="HW",
            cost_type="estimated",
        )

        estimated_sensor.hass = hass
        estimated_sensor.platform = MagicMock()
        estimated_sensor.entity_id = "sensor.test_estimated_cost"
        hass.async_create_task = MagicMock()
        hass.is_stopping = False
        hass.state = CoreState.running

        # Mock get_latest_estimated_cost to return different value (current calculation)
        coordinator.get_latest_estimated_cost = AsyncMock(
            return_value={
                "value": 45.50,  # Different from metered (calculated from current consumption)
                "time": int(datetime.now().timestamp()),
                "unit": "NOK",
                "calculation_method": "spot_price_calibrated",
            }
        )

        # Update metered sensor (uses cache)
        metered_sensor._update_from_coordinator_data()

        # Update estimated sensor (triggers async fetch)
        estimated_sensor._update_from_coordinator_data()
        await estimated_sensor._async_fetch_value()

        # Verify they have different values
        assert metered_sensor._attr_native_value == 50.0  # From old metered cache
        assert estimated_sensor._attr_native_value == 45.50  # From current calculation
        assert metered_sensor._attr_native_value != estimated_sensor._attr_native_value


async def test_individual_estimated_sensor_skips_metered_cache(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test that individual estimated cost sensor doesn't use metered cache."""
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    def mock_get_setting(name):
        if name == "Currency":
            return "NOK"
        elif name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    with patch.object(coordinator, "get_setting", side_effect=mock_get_setting):

        installation = {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "DeviceTypeDisplay": "Test Device",
            "Registers": [{"UtilityCode": "HW"}],
        }

        # Set up coordinator data with metered cache (should be ignored for estimated)
        coordinator.data = {
            "latest_cost_cache": {
                "HW_1_metered": {
                    "value": 50.0,  # This should be ignored for estimated
                    "time": int(datetime.now().timestamp()),
                    "unit": "NOK",
                }
            },
            "daily_consumption_cache": {
                "HW_1": [
                    {
                        "value": 0.125,
                        "time": int(datetime.now().timestamp()),
                        "unit": "m3",
                    }
                ]
            },
        }

        sensor = EcoGuardDailyCostSensor(
            hass=hass,
            coordinator=coordinator,
            installation=installation,
            utility_code="HW",
            measuring_point_id=1,
            measuring_point_name="Test MP",
            cost_type="estimated",
        )

        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_estimated_cost"

        # Mock async_create_task to capture the async fetch
        hass.async_create_task = MagicMock()
        hass.is_stopping = False
        hass.state = CoreState.running

        # Update from coordinator data
        sensor._update_from_coordinator_data()

        # Verify that async fetch was triggered (not using metered cache)
        assert (
            hass.async_create_task.called
        ), "Async fetch should be triggered for estimated costs"

        # Verify value is None (waiting for async fetch)
        assert (
            sensor._attr_native_value is None
        ), "Value should be None until async fetch completes"


async def test_individual_estimated_sensor_exposes_metadata(
    hass: HomeAssistant, coordinator, mock_coordinator_data: dict
):
    """Test that individual estimated cost sensor exposes estimation metadata."""
    coordinator._measuring_points = mock_coordinator_data["measuring_points"]
    coordinator._installations = mock_coordinator_data["installations"]
    coordinator._settings = mock_coordinator_data["settings"]

    def mock_get_setting(name):
        if name == "Currency":
            return "NOK"
        elif name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None

    with patch.object(coordinator, "get_setting", side_effect=mock_get_setting):

        installation = {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "DeviceTypeDisplay": "Test Device",
            "Registers": [{"UtilityCode": "HW"}],
        }

        # Mock get_latest_estimated_cost to return data with metadata
        mock_estimated_cost = {
            "value": 45.50,
            "time": int(datetime.now().timestamp()),
            "unit": "NOK",
            "utility_code": "HW",
            "cost_type": "estimated",
            "calculation_method": "spot_price_calibrated",
            "consumption_m3": 0.125,
            "spot_price_per_kwh": 0.5234,
            "calibration_ratio": 1.85,
        }

        coordinator.get_latest_estimated_cost = AsyncMock(
            return_value=mock_estimated_cost
        )

        sensor = EcoGuardDailyCostSensor(
            hass=hass,
            coordinator=coordinator,
            installation=installation,
            utility_code="HW",
            measuring_point_id=1,
            measuring_point_name="Test MP",
            cost_type="estimated",
        )

        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_estimated_cost"

        # Trigger async fetch
        await sensor._async_fetch_value()

        # Check that value is set
        assert sensor._attr_native_value == 45.50

        # Check that estimation metadata is stored
        assert sensor._estimation_metadata is not None
        assert (
            sensor._estimation_metadata["calculation_method"] == "spot_price_calibrated"
        )

        # Check that metadata is exposed in attributes
        attrs = sensor.extra_state_attributes
        assert "estimation" in attrs
        assert attrs["estimation"]["calculation_method"] == "spot_price_calibrated"
