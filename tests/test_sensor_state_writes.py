"""Tests for value-based state write functionality in EcoGuard sensors.

This test suite verifies that sensors only write state when values or context
(date/month) meaningfully change, preventing unnecessary recorder entries while
maintaining accurate historical data.
"""

from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest
from datetime import date, datetime, timedelta

from homeassistant.core import HomeAssistant

from custom_components.ecoguard.sensor_base import EcoGuardBaseSensor
from custom_components.ecoguard.sensors.daily import EcoGuardDailyConsumptionSensor
from custom_components.ecoguard.sensors.monthly import EcoGuardMonthlyAccumulatedSensor
from custom_components.ecoguard.coordinator import EcoGuardDataUpdateCoordinator


class TestShouldWriteState:
    """Test the _should_write_state() method logic."""

    def test_first_write_with_valid_value(self, coordinator):
        """Test that first write is allowed when value is valid."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor._last_written_value = None

        # First write with valid value should return True
        assert sensor._should_write_state(100.0) is True

    def test_first_write_with_none_value(self, coordinator):
        """Test that first write returns False when value is None."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor._last_written_value = None
        sensor.RECORDING_INTERVAL = 86400  # Set interval so we don't return early

        # First write with None should return False
        # (Note: This is redundant since _async_write_ha_state_if_changed guards against None,
        # but we test the logic anyway)
        assert sensor._should_write_state(None) is False

    def test_value_change(self, coordinator):
        """Test that value change triggers write."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor.RECORDING_INTERVAL = 86400  # Set interval so we don't return early
        sensor._last_written_value = 100.0

        # Value changed - should write
        assert sensor._should_write_state(200.0) is True
        assert sensor._should_write_state(100.0) is False  # Same value - no write

    def test_daily_sensor_date_change(self, coordinator):
        """Test that daily sensors write when date changes."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor.RECORDING_INTERVAL = 86400  # Daily recording
        sensor._last_written_value = 100.0
        sensor._last_written_date = date(2024, 1, 1)

        # Same date, same value - no write
        assert (
            sensor._should_write_state(100.0, data_date=date(2024, 1, 1)) is False
        )

        # Same date, different value - write
        assert (
            sensor._should_write_state(200.0, data_date=date(2024, 1, 1)) is True
        )

        # Different date, same value - write (daily sensors record once per day)
        assert (
            sensor._should_write_state(100.0, data_date=date(2024, 1, 2)) is True
        )

        # Different date, different value - write
        assert (
            sensor._should_write_state(200.0, data_date=date(2024, 1, 2)) is True
        )

    def test_monthly_sensor_month_change(self, coordinator):
        """Test that monthly sensors write when month changes."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor.RECORDING_INTERVAL = 86400  # Daily recording
        sensor._last_written_value = 100.0
        sensor._last_written_month = (2024, 1)

        # Same month, same value - no write
        assert (
            sensor._should_write_state(100.0, data_month=(2024, 1)) is False
        )

        # Same month, different value - write
        assert (
            sensor._should_write_state(200.0, data_month=(2024, 1)) is True
        )

        # Different month, same value - write
        assert (
            sensor._should_write_state(100.0, data_month=(2024, 2)) is True
        )

    def test_monthly_sensor_date_change(self, coordinator):
        """Test that monthly sensors write when date changes (daily progression)."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor.RECORDING_INTERVAL = 86400  # Daily recording
        sensor._last_written_value = 100.0
        sensor._last_written_month = (2024, 1)
        sensor._last_written_date = date(2024, 1, 1)

        # Same date, same month, same value - no write
        assert (
            sensor._should_write_state(
                100.0, data_date=date(2024, 1, 1), data_month=(2024, 1)
            )
            is False
        )

        # Different date, same month, same value - write (daily progression)
        assert (
            sensor._should_write_state(
                100.0, data_date=date(2024, 1, 2), data_month=(2024, 1)
            )
            is True
        )

        # Different date, different month, same value - write
        assert (
            sensor._should_write_state(
                100.0, data_date=date(2024, 2, 1), data_month=(2024, 2)
            )
            is True
        )

    def test_recording_disabled(self, coordinator):
        """Test that recording disabled sensors always write."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor.RECORDING_ENABLED = False
        sensor._last_written_value = 100.0

        # When recording is disabled, always write (sensor still needs to update)
        assert sensor._should_write_state(100.0) is True
        assert sensor._should_write_state(200.0) is True

    def test_no_interval_configured(self, coordinator):
        """Test that sensors without interval configured always write (record all updates)."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor.RECORDING_INTERVAL = None  # Record all updates
        sensor._last_written_value = 100.0

        # No interval means record all updates (even same value)
        # This is because RECORDING_INTERVAL=None returns True early
        assert sensor._should_write_state(200.0) is True
        assert sensor._should_write_state(100.0) is True  # Same value - still writes (record all updates)


class TestAsyncWriteHaStateIfChanged:
    """Test the _async_write_ha_state_if_changed() method."""

    def test_skips_none_value(self, coordinator):
        """Test that None values are never written."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor._attr_native_value = None
        sensor.async_write_ha_state = MagicMock()

        # Should skip when value is None
        sensor._async_write_ha_state_if_changed()
        sensor.async_write_ha_state.assert_not_called()

        # Should skip even when explicitly passing None
        sensor._async_write_ha_state_if_changed(new_value=None)
        sensor.async_write_ha_state.assert_not_called()

    def test_writes_on_first_valid_value(self, coordinator):
        """Test that first valid value is written."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor._attr_native_value = 100.0
        sensor._last_written_value = None
        sensor.async_write_ha_state = MagicMock()

        sensor._async_write_ha_state_if_changed()
        sensor.async_write_ha_state.assert_called_once()
        assert sensor._last_written_value == 100.0

    def test_writes_on_value_change(self, coordinator):
        """Test that value changes trigger writes."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor._attr_native_value = 200.0
        sensor._last_written_value = 100.0
        sensor.async_write_ha_state = MagicMock()

        sensor._async_write_ha_state_if_changed()
        sensor.async_write_ha_state.assert_called_once()
        assert sensor._last_written_value == 200.0

    def test_skips_same_value(self, coordinator):
        """Test that same value doesn't trigger write."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor.RECORDING_INTERVAL = 86400  # Set interval so we check value changes
        sensor._attr_native_value = 100.0
        sensor._last_written_value = 100.0
        sensor.async_write_ha_state = MagicMock()

        sensor._async_write_ha_state_if_changed()
        sensor.async_write_ha_state.assert_not_called()

    def test_updates_tracking_variables(self, coordinator):
        """Test that tracking variables are updated correctly."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor._attr_native_value = 100.0
        sensor._last_written_value = None
        sensor._last_written_date = None
        sensor._last_written_month = None
        sensor.async_write_ha_state = MagicMock()

        test_date = date(2024, 1, 15)
        test_month = (2024, 1)

        sensor._async_write_ha_state_if_changed(
            data_date=test_date, data_month=test_month
        )

        assert sensor._last_written_value == 100.0
        assert sensor._last_written_date == test_date
        assert sensor._last_written_month == test_month

    def test_daily_sensor_writes_on_date_change(self, coordinator):
        """Test that daily sensors write when date changes."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor.RECORDING_INTERVAL = 86400
        sensor._attr_native_value = 100.0
        sensor._last_written_value = 100.0
        sensor._last_written_date = date(2024, 1, 1)
        sensor.async_write_ha_state = MagicMock()

        # Same date, same value - no write
        sensor._async_write_ha_state_if_changed(data_date=date(2024, 1, 1))
        sensor.async_write_ha_state.assert_not_called()

        # Different date, same value - write
        sensor._async_write_ha_state_if_changed(data_date=date(2024, 1, 2))
        sensor.async_write_ha_state.assert_called_once()
        assert sensor._last_written_date == date(2024, 1, 2)

    def test_transition_from_value_to_none_skipped(self, coordinator):
        """Test that transition from value to None is skipped."""
        sensor = EcoGuardBaseSensor(
            hass=MagicMock(),
            coordinator=coordinator,
            description_key="test",
        )
        sensor._attr_native_value = None
        sensor._last_written_value = 100.0  # Had a value before
        sensor.async_write_ha_state = MagicMock()

        # Should skip writing None even if we had a value before
        sensor._async_write_ha_state_if_changed()
        sensor.async_write_ha_state.assert_not_called()
        # Last written value should remain unchanged
        assert sensor._last_written_value == 100.0


class TestDailySensorIntegration:
    """Integration tests for daily sensors using value-based writes."""

    @pytest.mark.asyncio
    async def test_daily_sensor_writes_on_date_change(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that daily consumption sensor writes when date changes."""
        # Set up coordinator data
        coordinator._measuring_points = [{"ID": 1, "Name": "Test MP"}]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "ExternalKey": "test-key",
                "Registers": [{"UtilityCode": "CW"}],
            }
        ]
        coordinator._settings = [{"Name": "Currency", "Value": "NOK"}]

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
            measuring_point_name="Test MP",
        )

        # Mock coordinator data with consumption cache
        # The daily_consumption_cache uses keys like "CW_1" and contains lists of daily entries
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        tz = coordinator.get_setting("TimeZoneIANA") or "UTC"
        from pytz import timezone as get_timezone
        timezone = get_timezone(tz)
        
        yesterday_ts = int(timezone.localize(datetime.combine(yesterday, datetime.min.time())).timestamp())
        today_ts = int(timezone.localize(datetime.combine(today, datetime.min.time())).timestamp())

        coordinator.data = {
            "latest_consumption_cache": {
                "CW_1": {
                    "value": 20.0,
                    "unit": "m³",
                    "time": today_ts,
                },
            },
            "daily_consumption_cache": {
                "CW_1": [
                    {
                        "value": 10.0,
                        "date": yesterday,
                        "unit": "m³",
                        "timestamp": yesterday_ts,
                    },
                    {
                        "value": 20.0,
                        "date": today,
                        "unit": "m³",
                        "timestamp": today_ts,
                    },
                ],
            },
        }

        sensor.async_write_ha_state = MagicMock()

        # First update - should write (first write)
        sensor._update_from_coordinator_data()
        assert sensor.async_write_ha_state.call_count == 1
        assert sensor._last_written_value == 20.0
        assert sensor._last_written_date == today

        # Update again with same date - should not write
        sensor._update_from_coordinator_data()
        assert sensor.async_write_ha_state.call_count == 1  # Still 1

        # Simulate date change by updating coordinator data
        tomorrow = today + timedelta(days=1)
        tomorrow_ts = int(timezone.localize(datetime.combine(tomorrow, datetime.min.time())).timestamp())
        coordinator.data["latest_consumption_cache"]["CW_1"]["value"] = 30.0
        coordinator.data["latest_consumption_cache"]["CW_1"]["time"] = tomorrow_ts
        coordinator.data["daily_consumption_cache"]["CW_1"].append({
            "value": 30.0,
            "date": tomorrow,
            "unit": "m³",
            "timestamp": tomorrow_ts,
        })

        # Update with new date - should write
        sensor._update_from_coordinator_data()
        assert sensor.async_write_ha_state.call_count == 2
        assert sensor._last_written_value == 30.0
        assert sensor._last_written_date == tomorrow

    @pytest.mark.asyncio
    async def test_daily_sensor_skips_none_value(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that daily sensor doesn't write when value is None."""
        coordinator._measuring_points = [{"ID": 1, "Name": "Test MP"}]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "ExternalKey": "test-key",
                "Registers": [{"UtilityCode": "CW"}],
            }
        ]
        coordinator._settings = [{"Name": "Currency", "Value": "NOK"}]

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
            measuring_point_name="Test MP",
        )

        # No coordinator data - value will be None
        coordinator.data = None

        sensor.async_write_ha_state = MagicMock()

        # Should not write when value is None
        sensor._update_from_coordinator_data()
        sensor.async_write_ha_state.assert_not_called()


class TestMonthlySensorIntegration:
    """Integration tests for monthly sensors using value-based writes."""

    @pytest.mark.asyncio
    async def test_monthly_sensor_writes_on_date_change(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that monthly accumulated sensor writes daily for progression."""
        coordinator._measuring_points = [{"ID": 1, "Name": "Test MP"}]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "ExternalKey": "test-key",
                "Registers": [{"UtilityCode": "CW"}],
            }
        ]
        coordinator._settings = [{"Name": "Currency", "Value": "NOK"}]

        installation = {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [{"UtilityCode": "CW"}],
        }

        sensor = EcoGuardMonthlyAccumulatedSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="CW",
            aggregate_type="con",  # "con" for consumption, not "consumption"
        )
        
        # Set hass and platform on sensor (normally done by async_added_to_hass)
        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_monthly_consumption"

        # Mock coordinator data with monthly cache
        # Cache key format: "{utility_code}_{year}_{month}_{aggregate_type}_{cost_type}"
        now = datetime.now()
        year = now.year
        month = now.month
        cache_key = f"CW_{year}_{month}_con_actual"  # consumption, actual cost type

        coordinator.data = {
            "monthly_aggregate_cache": {
                cache_key: {
                    "value": 100.0,
                    "year": year,
                    "month": month,
                    "unit": "m³",
                }
            }
        }

        sensor.async_write_ha_state = MagicMock()
        
        # Patch datetime.now() for the monthly sensor
        with patch("custom_components.ecoguard.sensors.monthly.datetime") as mock_dt:
            mock_dt.now.return_value = now
            
            # First update - should write (first write)
            sensor._update_from_coordinator_data()
            assert sensor.async_write_ha_state.call_count == 1
            assert sensor._last_written_value == 100.0
            assert sensor._last_written_date == now.date()
            assert sensor._last_written_month == (year, month)

            # Update again with same date - should not write
            sensor._update_from_coordinator_data()
            assert sensor.async_write_ha_state.call_count == 1  # Still 1

            # Simulate date change (next day, same month)
            tomorrow = now + timedelta(days=1)
            mock_dt.now.return_value = tomorrow

            # Update with new date - should write (daily progression)
            sensor._update_from_coordinator_data()
            assert sensor.async_write_ha_state.call_count == 2
            assert sensor._last_written_date == tomorrow.date()

    @pytest.mark.asyncio
    async def test_monthly_sensor_writes_on_month_change(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that monthly sensor writes when month changes."""
        coordinator._measuring_points = [{"ID": 1, "Name": "Test MP"}]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "ExternalKey": "test-key",
                "Registers": [{"UtilityCode": "CW"}],
            }
        ]
        coordinator._settings = [{"Name": "Currency", "Value": "NOK"}]

        installation = {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [{"UtilityCode": "CW"}],
        }

        sensor = EcoGuardMonthlyAccumulatedSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="CW",
            aggregate_type="con",  # "con" for consumption, not "consumption"
        )
        
        # Set hass and platform on sensor (normally done by async_added_to_hass)
        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_monthly_consumption"

        # Mock coordinator data for January
        jan_date = datetime(2024, 1, 15)
        jan_cache_key = "CW_2024_1_con_actual"  # consumption, actual cost type

        coordinator.data = {
            "monthly_aggregate_cache": {
                jan_cache_key: {
                    "value": 100.0,
                    "year": 2024,
                    "month": 1,
                    "unit": "m³",
                }
            }
        }

        sensor.async_write_ha_state = MagicMock()

        with patch("custom_components.ecoguard.sensors.monthly.datetime") as mock_dt:
            mock_dt.now.return_value = jan_date

            # First update - should write
            sensor._update_from_coordinator_data()
            assert sensor.async_write_ha_state.call_count == 1
            assert sensor._last_written_month == (2024, 1)

            # Update coordinator data for February
            feb_date = datetime(2024, 2, 1)
            feb_cache_key = "CW_2024_2_con_actual"  # consumption, actual cost type
            coordinator.data["monthly_aggregate_cache"][feb_cache_key] = {
                "value": 200.0,
                "year": 2024,
                "month": 2,
                "unit": "m³",
            }

            mock_dt.now.return_value = feb_date

            # Update with new month - should write
            sensor._update_from_coordinator_data()
            assert sensor.async_write_ha_state.call_count == 2
            assert sensor._last_written_month == (2024, 2)

    @pytest.mark.asyncio
    async def test_monthly_sensor_skips_none_value(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that monthly sensor doesn't write when value is None."""
        coordinator._measuring_points = [{"ID": 1, "Name": "Test MP"}]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "ExternalKey": "test-key",
                "Registers": [{"UtilityCode": "CW"}],
            }
        ]
        coordinator._settings = [{"Name": "Currency", "Value": "NOK"}]

        installation = {
            "MeasuringPointID": 1,
            "ExternalKey": "test-key",
            "Registers": [{"UtilityCode": "CW"}],
        }

        sensor = EcoGuardMonthlyAccumulatedSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="CW",
            aggregate_type="con",  # "con" for consumption, not "consumption"
        )
        
        # Set hass and platform on sensor (normally done by async_added_to_hass)
        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_monthly_consumption"

        # No coordinator data - value will be None
        coordinator.data = None

        sensor.async_write_ha_state = MagicMock()

        # Should not write when value is None
        sensor._update_from_coordinator_data()
        sensor.async_write_ha_state.assert_not_called()


class TestCombinedSensorDataCompleteness:
    """Test that combined sensors only write when all dependencies are available."""

    @pytest.mark.asyncio
    async def test_combined_sensor_waits_for_both_utilities(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that combined water sensor waits for both HW and CW data."""
        from custom_components.ecoguard.sensors.daily import (
            EcoGuardDailyCombinedWaterSensor,
        )

        coordinator._measuring_points = [{"ID": 1, "Name": "Test MP"}]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "ExternalKey": "test-key",
                "Registers": [{"UtilityCode": "CW"}, {"UtilityCode": "HW"}],
            }
        ]
        coordinator._settings = [{"Name": "Currency", "Value": "NOK"}]

        sensor = EcoGuardDailyCombinedWaterSensor(
            hass=hass,
            coordinator=coordinator,
        )

        today = datetime.now().date()

        sensor.async_write_ha_state = MagicMock()

        # Only HW data available - should not write
        now_ts = int(datetime.now().timestamp())
        coordinator.data = {
            "latest_consumption_cache": {
                "HW_1": {
                    "value": 10.0,
                    "unit": "m³",
                    "time": now_ts,
                },
            },
            "daily_consumption_cache": {
                "HW_1": [],  # Empty list for daily cache
            },
        }

        sensor._update_from_coordinator_data()
        sensor.async_write_ha_state.assert_not_called()

        # Only CW data available - should not write
        coordinator.data = {
            "latest_consumption_cache": {
                "CW_1": {
                    "value": 20.0,
                    "unit": "m³",
                    "time": now_ts,
                },
            },
            "daily_consumption_cache": {
                "CW_1": [],  # Empty list for daily cache
            },
        }

        sensor._update_from_coordinator_data()
        sensor.async_write_ha_state.assert_not_called()

        # Both HW and CW data available - should write
        coordinator.data = {
            "latest_consumption_cache": {
                "HW_1": {
                    "value": 10.0,
                    "unit": "m³",
                    "time": now_ts,
                },
                "CW_1": {
                    "value": 20.0,
                    "unit": "m³",
                    "time": now_ts,
                },
            },
            "daily_consumption_cache": {
                "HW_1": [],  # Empty list for daily cache
                "CW_1": [],  # Empty list for daily cache
            },
        }

        sensor._update_from_coordinator_data()
        sensor.async_write_ha_state.assert_called_once()
        assert sensor._attr_native_value == 30.0  # 10 + 20
