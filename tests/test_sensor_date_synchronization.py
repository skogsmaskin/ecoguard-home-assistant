"""Tests for date synchronization in combined and aggregate sensors.

These tests verify that sensors correctly use the same date for all meters
when meters have different data dates, ensuring synchronized data across meters.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from datetime import datetime, timezone, timedelta

from homeassistant.core import HomeAssistant

from custom_components.ecoguard.sensors import (
    EcoGuardDailyCombinedWaterSensor,
    EcoGuardDailyCombinedWaterCostSensor,
    EcoGuardDailyConsumptionAggregateSensor,
    EcoGuardDailyCostAggregateSensor,
)


class TestDateSynchronization:
    """Test date synchronization for combined and aggregate sensors."""

    @pytest.mark.asyncio
    async def test_combined_water_consumption_uses_common_date(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that combined water consumption sensor uses the same date for all meters.

        When HW has data from 11 days ago and CW has data from today,
        both should use data from 11 days ago.
        """
        coordinator._measuring_points = [
            {"ID": 1, "Name": "MP1"},
        ]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "Registers": [{"UtilityCode": "HW"}, {"UtilityCode": "CW"}],
            },
        ]
        coordinator._settings = [{"Name": "TimeZoneIANA", "Value": "UTC"}]

        # Create timestamps: HW from 11 days ago, CW from today
        now = datetime.now(timezone.utc)
        hw_date = now - timedelta(days=11)
        cw_date = now

        hw_timestamp = int(hw_date.timestamp())
        cw_timestamp = int(cw_date.timestamp())

        # Daily cache: HW only has data up to 11 days ago (lagging), CW has data up to today
        # This simulates the real scenario where HW data is lagging
        hw_daily_cache = [
            {"time": hw_timestamp, "value": 5.0, "unit": "m³"},  # Last available data
        ]
        cw_daily_cache = [
            {"time": hw_timestamp, "value": 10.0, "unit": "m³"},  # Old data
            {"time": cw_timestamp, "value": 12.0, "unit": "m³"},  # Newer data (most recent)
        ]

        # Latest cache: HW has data from 11 days ago (lagging), CW has data from today
        coordinator.data = {
            "latest_consumption_cache": {
                "HW_1": {
                    "value": 5.0,  # Value from 11 days ago
                    "time": hw_timestamp,
                    "unit": "m³",
                },
                "CW_1": {
                    "value": 12.0,  # Value from today
                    "time": cw_timestamp,
                    "unit": "m³",
                },
            },
            "daily_consumption_cache": {
                "HW_1": hw_daily_cache,
                "CW_1": cw_daily_cache,
            },
        }

        sensor = EcoGuardDailyCombinedWaterSensor(
            hass=hass,
            coordinator=coordinator,
        )
        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_combined_water"

        def get_setting(name: str):
            """Helper to get setting from coordinator._settings."""
            for setting in coordinator._settings:
                if setting.get("Name") == name:
                    return setting.get("Value")
            return None

        with patch.object(
            coordinator, "get_active_installations", return_value=coordinator._installations
        ), patch.object(
            coordinator, "get_measuring_points", return_value=coordinator._measuring_points
        ), patch.object(
            coordinator, "get_setting", side_effect=get_setting
        ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
            sensor._update_from_coordinator_data()

            # Should use data from 11 days ago (common date) for both meters
            # HW: 5.0, CW: 10.0, Total: 15.0
            assert sensor._attr_native_value == 15.0
            assert len(sensor._hw_meters_with_data) == 1
            assert len(sensor._cw_meters_with_data) == 1
            assert sensor._hw_meters_with_data[0]["value"] == 5.0
            assert sensor._cw_meters_with_data[0]["value"] == 10.0

            # Last data date should be the common date (11 days ago)
            assert sensor._last_data_date is not None
            assert sensor._last_data_date.date() == hw_date.date()

    @pytest.mark.asyncio
    async def test_combined_water_cost_uses_common_date(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that combined water cost sensor uses the same date for all meters.

        When HW has data from 11 days ago and CW has data from today,
        both should use data from 11 days ago.
        """
        coordinator._measuring_points = [
            {"ID": 1, "Name": "MP1"},
        ]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "Registers": [{"UtilityCode": "HW"}, {"UtilityCode": "CW"}],
            },
        ]
        coordinator._settings = [
            {"Name": "TimeZoneIANA", "Value": "UTC"},
            {"Name": "Currency", "Value": "NOK"},
        ]

        # Create timestamps: HW from 11 days ago, CW from today
        now = datetime.now(timezone.utc)
        hw_date = now - timedelta(days=11)
        cw_date = now

        hw_timestamp = int(hw_date.timestamp())
        cw_timestamp = int(cw_date.timestamp())

        # Daily price cache: HW only has data up to 11 days ago (lagging), CW has data up to today
        # This simulates the real scenario where HW data is lagging
        hw_price_cache = [
            {"time": hw_timestamp, "value": 50.0, "unit": "NOK"},  # Last available data
        ]
        cw_price_cache = [
            {"time": hw_timestamp, "value": 100.0, "unit": "NOK"},  # Old data
            {"time": cw_timestamp, "value": 120.0, "unit": "NOK"},  # Newer data (most recent)
        ]

        # Latest cache: HW has data from 11 days ago (lagging), CW has data from today
        coordinator.data = {
            "latest_cost_cache": {
                "HW_1_metered": {
                    "value": 50.0,  # Value from 11 days ago
                    "time": hw_timestamp,
                    "unit": "NOK",
                },
                "CW_1_metered": {
                    "value": 120.0,  # Value from today
                    "time": cw_timestamp,
                    "unit": "NOK",
                },
            },
            "daily_price_cache": {
                "HW_1_metered": hw_price_cache,
                "CW_1_metered": cw_price_cache,
            },
            "daily_consumption_cache": {
                "HW_1": [],
                "CW_1": [],
            },
        }

        sensor = EcoGuardDailyCombinedWaterCostSensor(
            hass=hass,
            coordinator=coordinator,
            cost_type="actual",
        )
        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_combined_water_cost"

        def get_setting(name: str):
            """Helper to get setting from coordinator._settings."""
            for setting in coordinator._settings:
                if setting.get("Name") == name:
                    return setting.get("Value")
            return None

        with patch.object(
            coordinator, "get_active_installations", return_value=coordinator._installations
        ), patch.object(
            coordinator, "get_measuring_points", return_value=coordinator._measuring_points
        ), patch.object(
            coordinator, "get_setting", side_effect=get_setting
        ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
            sensor._update_from_coordinator_data()

            # Should use data from 11 days ago (common date) for both meters
            # HW: 50.0, CW: 100.0, Total: 150.0
            assert sensor._attr_native_value == 150.0
            assert len(sensor._hw_meters_with_data) == 1
            assert len(sensor._cw_meters_with_data) == 1
            assert sensor._hw_meters_with_data[0]["value"] == 50.0
            assert sensor._cw_meters_with_data[0]["value"] == 100.0

            # Last data date should be the common date (11 days ago)
            assert sensor._last_data_date is not None
            assert sensor._last_data_date.date() == hw_date.date()

    @pytest.mark.asyncio
    async def test_consumption_aggregate_uses_common_date(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that consumption aggregate sensor uses the same date for all meters.

        When meter 1 has data from 5 days ago and meter 2 has data from today,
        both should use data from 5 days ago.
        """
        coordinator._measuring_points = [
            {"ID": 1, "Name": "MP1"},
            {"ID": 2, "Name": "MP2"},
        ]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "Registers": [{"UtilityCode": "CW"}],
            },
            {
                "MeasuringPointID": 2,
                "Registers": [{"UtilityCode": "CW"}],
            },
        ]
        coordinator._settings = [{"Name": "TimeZoneIANA", "Value": "UTC"}]

        # Create timestamps: meter 1 from 5 days ago, meter 2 from today
        now = datetime.now(timezone.utc)
        meter1_date = now - timedelta(days=5)
        meter2_date = now

        meter1_timestamp = int(meter1_date.timestamp())
        meter2_timestamp = int(meter2_date.timestamp())

        # Daily cache: meter 1 only has data up to 5 days ago (lagging), meter 2 has data up to today
        # This simulates the real scenario where meter 1 data is lagging
        meter1_daily_cache = [
            {"time": meter1_timestamp, "value": 10.0, "unit": "m³"},  # Last available data
        ]
        meter2_daily_cache = [
            {"time": meter1_timestamp, "value": 15.0, "unit": "m³"},  # Old data
            {"time": meter2_timestamp, "value": 18.0, "unit": "m³"},  # Newer data (most recent)
        ]

        # Latest cache: meter 1 has data from 5 days ago (lagging), meter 2 has data from today
        coordinator.data = {
            "latest_consumption_cache": {
                "CW_1": {
                    "value": 10.0,  # Value from 5 days ago
                    "time": meter1_timestamp,
                    "unit": "m³",
                },
                "CW_2": {
                    "value": 18.0,  # Value from today
                    "time": meter2_timestamp,
                    "unit": "m³",
                },
            },
            "daily_consumption_cache": {
                "CW_1": meter1_daily_cache,
                "CW_2": meter2_daily_cache,
            },
        }

        sensor = EcoGuardDailyConsumptionAggregateSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="CW",
        )
        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_cw_aggregate"

        def get_setting(name: str):
            """Helper to get setting from coordinator._settings."""
            for setting in coordinator._settings:
                if setting.get("Name") == name:
                    return setting.get("Value")
            return None

        with patch.object(
            coordinator, "get_active_installations", return_value=coordinator._installations
        ), patch.object(
            coordinator, "get_measuring_points", return_value=coordinator._measuring_points
        ), patch.object(
            coordinator, "get_setting", side_effect=get_setting
        ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
            sensor._update_from_coordinator_data()

            # Should use data from 5 days ago (common date) for both meters
            # Meter 1: 10.0, Meter 2: 15.0, Total: 25.0
            assert sensor._attr_native_value == 25.0
            assert len(sensor._meters_with_data) == 2
            meter_values = {m["measuring_point_id"]: m["value"] for m in sensor._meters_with_data}
            assert meter_values[1] == 10.0
            assert meter_values[2] == 15.0

            # Last data date should be the common date (5 days ago)
            assert sensor._last_data_date is not None
            assert sensor._last_data_date.date() == meter1_date.date()

    @pytest.mark.asyncio
    async def test_cost_aggregate_uses_common_date(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that cost aggregate sensor uses the same date for all meters.

        When meter 1 has data from 3 days ago and meter 2 has data from today,
        both should use data from 3 days ago.
        """
        coordinator._measuring_points = [
            {"ID": 1, "Name": "MP1"},
            {"ID": 2, "Name": "MP2"},
        ]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "Registers": [{"UtilityCode": "HW"}],
            },
            {
                "MeasuringPointID": 2,
                "Registers": [{"UtilityCode": "HW"}],
            },
        ]
        coordinator._settings = [
            {"Name": "TimeZoneIANA", "Value": "UTC"},
            {"Name": "Currency", "Value": "NOK"},
        ]

        # Create timestamps: meter 1 from 3 days ago, meter 2 from today
        now = datetime.now(timezone.utc)
        meter1_date = now - timedelta(days=3)
        meter2_date = now

        meter1_timestamp = int(meter1_date.timestamp())
        meter2_timestamp = int(meter2_date.timestamp())

        # Daily price cache: meter 1 only has data up to 3 days ago (lagging), meter 2 has data up to today
        # This simulates the real scenario where meter 1 data is lagging
        meter1_price_cache = [
            {"time": meter1_timestamp, "value": 30.0, "unit": "NOK"},  # Last available data
        ]
        meter2_price_cache = [
            {"time": meter1_timestamp, "value": 40.0, "unit": "NOK"},  # Old data
            {"time": meter2_timestamp, "value": 45.0, "unit": "NOK"},  # Newer data (most recent)
        ]

        # Latest cache: meter 1 has data from 3 days ago (lagging), meter 2 has data from today
        coordinator.data = {
            "latest_cost_cache": {
                "HW_1_metered": {
                    "value": 30.0,  # Value from 3 days ago
                    "time": meter1_timestamp,
                    "unit": "NOK",
                },
                "HW_2_metered": {
                    "value": 45.0,  # Value from today
                    "time": meter2_timestamp,
                    "unit": "NOK",
                },
            },
            "daily_price_cache": {
                "HW_1_metered": meter1_price_cache,
                "HW_2_metered": meter2_price_cache,
            },
            "daily_consumption_cache": {
                "HW_1": [],
                "HW_2": [],
            },
        }

        sensor = EcoGuardDailyCostAggregateSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="HW",
            cost_type="actual",
        )
        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_hw_cost_aggregate"

        def get_setting(name: str):
            """Helper to get setting from coordinator._settings."""
            for setting in coordinator._settings:
                if setting.get("Name") == name:
                    return setting.get("Value")
            return None

        with patch.object(
            coordinator, "get_active_installations", return_value=coordinator._installations
        ), patch.object(
            coordinator, "get_measuring_points", return_value=coordinator._measuring_points
        ), patch.object(
            coordinator, "get_setting", side_effect=get_setting
        ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
            sensor._update_from_coordinator_data()

            # Should use data from 3 days ago (common date) for both meters
            # Meter 1: 30.0, Meter 2: 40.0, Total: 70.0
            assert sensor._attr_native_value == 70.0
            assert len(sensor._meters_with_data) == 2
            meter_values = {m["measuring_point_id"]: m["value"] for m in sensor._meters_with_data}
            assert meter_values[1] == 30.0
            assert meter_values[2] == 40.0

            # Last data date should be the common date (3 days ago)
            assert sensor._last_data_date is not None
            assert sensor._last_data_date.date() == meter1_date.date()

    @pytest.mark.asyncio
    async def test_combined_water_no_common_date_shows_unknown(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that combined water sensor shows Unknown when no common date exists.

        When HW has no data and CW has data, sensor should show Unknown.
        """
        coordinator._measuring_points = [
            {"ID": 1, "Name": "MP1"},
        ]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "Registers": [{"UtilityCode": "HW"}, {"UtilityCode": "CW"}],
            },
        ]
        coordinator._settings = [
            {"Name": "TimeZoneIANA", "Value": "UTC"},
            {"Name": "Currency", "Value": "NOK"},
        ]

        now = datetime.now(timezone.utc)
        cw_timestamp = int(now.timestamp())

        # Only CW has data, HW has no data
        coordinator.data = {
            "latest_cost_cache": {
                "CW_1_metered": {
                    "value": 100.0,
                    "time": cw_timestamp,
                    "unit": "NOK",
                },
            },
            "daily_price_cache": {
                "CW_1_metered": [
                    {"time": cw_timestamp, "value": 100.0, "unit": "NOK"},
                ],
            },
            "daily_consumption_cache": {
                "HW_1": [],  # No data
                "CW_1": [],
            },
        }

        sensor = EcoGuardDailyCombinedWaterCostSensor(
            hass=hass,
            coordinator=coordinator,
            cost_type="actual",
        )
        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_combined_water_cost"

        def get_setting(name: str):
            """Helper to get setting from coordinator._settings."""
            for setting in coordinator._settings:
                if setting.get("Name") == name:
                    return setting.get("Value")
            return None

        with patch.object(
            coordinator, "get_active_installations", return_value=coordinator._installations
        ), patch.object(
            coordinator, "get_measuring_points", return_value=coordinator._measuring_points
        ), patch.object(
            coordinator, "get_setting", side_effect=get_setting
        ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
            sensor._update_from_coordinator_data()

            # Should show Unknown when one utility has no data
            # Even though CW has data, we can't show a value without both utilities
            assert sensor._attr_native_value is None
            # Note: _last_data_date may be set from available data, but value should still be None
            # The important thing is that the sensor shows Unknown when data is incomplete

    @pytest.mark.asyncio
    async def test_aggregate_uses_earliest_date_when_multiple_meters(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that aggregate sensor uses the earliest date when multiple meters have different dates.

        Meter 1: 10 days ago, Meter 2: 5 days ago, Meter 3: today
        All should use data from 10 days ago.
        """
        coordinator._measuring_points = [
            {"ID": 1, "Name": "MP1"},
            {"ID": 2, "Name": "MP2"},
            {"ID": 3, "Name": "MP3"},
        ]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "Registers": [{"UtilityCode": "CW"}],
            },
            {
                "MeasuringPointID": 2,
                "Registers": [{"UtilityCode": "CW"}],
            },
            {
                "MeasuringPointID": 3,
                "Registers": [{"UtilityCode": "CW"}],
            },
        ]
        coordinator._settings = [
            {"Name": "TimeZoneIANA", "Value": "UTC"},
            {"Name": "Currency", "Value": "NOK"},
        ]

        # Create timestamps: 10 days ago, 5 days ago, today
        now = datetime.now(timezone.utc)
        date_10_days = now - timedelta(days=10)
        date_5_days = now - timedelta(days=5)
        date_today = now

        ts_10_days = int(date_10_days.timestamp())
        ts_5_days = int(date_5_days.timestamp())
        ts_today = int(date_today.timestamp())

        # Daily price cache with entries for all dates
        coordinator.data = {
            "latest_cost_cache": {
                "CW_1_metered": {
                    "value": 10.0,  # Value from 10 days ago
                    "time": ts_10_days,
                    "unit": "NOK",
                },
                "CW_2_metered": {
                    "value": 25.0,  # Value from 5 days ago
                    "time": ts_5_days,
                    "unit": "NOK",
                },
                "CW_3_metered": {
                    "value": 40.0,  # Value from today
                    "time": ts_today,
                    "unit": "NOK",
                },
            },
            "daily_price_cache": {
                # Meter 1: last data is 10 days ago (lagging)
                "CW_1_metered": [
                    {"time": ts_10_days, "value": 10.0, "unit": "NOK"},  # Last available data
                ],
                # Meter 2: last data is 5 days ago
                "CW_2_metered": [
                    {"time": ts_5_days, "value": 25.0, "unit": "NOK"},  # Last available data
                ],
                # Meter 3: last data is today
                "CW_3_metered": [
                    {"time": ts_today, "value": 40.0, "unit": "NOK"},  # Last available data
                ],
            },
            "daily_consumption_cache": {
                "CW_1": [],
                "CW_2": [],
                "CW_3": [],
            },
        }

        sensor = EcoGuardDailyCostAggregateSensor(
            hass=hass,
            coordinator=coordinator,
            utility_code="CW",
            cost_type="actual",
        )
        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_cw_cost_aggregate"

        def get_setting(name: str):
            """Helper to get setting from coordinator._settings."""
            for setting in coordinator._settings:
                if setting.get("Name") == name:
                    return setting.get("Value")
            return None

        with patch.object(
            coordinator, "get_active_installations", return_value=coordinator._installations
        ), patch.object(
            coordinator, "get_measuring_points", return_value=coordinator._measuring_points
        ), patch.object(
            coordinator, "get_setting", side_effect=get_setting
        ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
            sensor._update_from_coordinator_data()

            # Should use data from 10 days ago (earliest common date)
            # Meter 1: 10.0, Meter 2: no data from 10 days ago (None), Meter 3: no data from 10 days ago (None)
            # Since meter 2 and 3 don't have data from 10 days ago, they won't contribute
            # But wait - meter 2 and 3 don't have data from 10 days ago, so common_date should be None?
            # Actually, the common_date is the minimum of all last_data_dates, so if meter 2's last date is 5 days ago
            # and meter 3's last date is today, the common_date would be 5 days ago (the minimum).
            # But meter 1's last date is 10 days ago, so common_date should be 10 days ago.
            # However, meter 2 and 3 don't have data from 10 days ago, so they won't contribute to the total.
            # Let me check the logic again...

            # Actually, the common_date is the minimum of all last_data_dates where meters have data.
            # Meter 1: last date = 10 days ago
            # Meter 2: last date = 5 days ago  
            # Meter 3: last date = today
            # Common date = min(10 days, 5 days, today) = 10 days ago
            # But meter 2 and 3 don't have entries from 10 days ago, so they won't contribute.
            # So total should be just meter 1: 10.0

            # However, if a meter doesn't have data from the common date, it won't be included.
            # So the sensor should only show meter 1's value: 10.0
            assert sensor._attr_native_value == 10.0
            assert len(sensor._meters_with_data) == 3  # All meters are listed
            meter_values = {m["measuring_point_id"]: m["value"] for m in sensor._meters_with_data}
            assert meter_values[1] == 10.0
            assert meter_values[2] == 0.0  # No data from common date
            assert meter_values[3] == 0.0  # No data from common date

            # Last data date should be the common date (10 days ago)
            assert sensor._last_data_date is not None
            assert sensor._last_data_date.date() == date_10_days.date()

    @pytest.mark.asyncio
    async def test_all_meters_same_date_works_normally(
        self, hass: HomeAssistant, coordinator
    ):
        """Test that sensors work normally when all meters have data from the same date.

        This verifies that the date synchronization doesn't break the normal case.
        """
        coordinator._measuring_points = [
            {"ID": 1, "Name": "MP1"},
        ]
        coordinator._installations = [
            {
                "MeasuringPointID": 1,
                "Registers": [{"UtilityCode": "HW"}, {"UtilityCode": "CW"}],
            },
        ]
        coordinator._settings = [
            {"Name": "TimeZoneIANA", "Value": "UTC"},
            {"Name": "Currency", "Value": "NOK"},
        ]

        # Both meters have data from the same date (yesterday)
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        yesterday_timestamp = int(yesterday.timestamp())

        coordinator.data = {
            "latest_cost_cache": {
                "HW_1_metered": {
                    "value": 50.0,
                    "time": yesterday_timestamp,
                    "unit": "NOK",
                },
                "CW_1_metered": {
                    "value": 100.0,
                    "time": yesterday_timestamp,
                    "unit": "NOK",
                },
            },
            "daily_price_cache": {
                "HW_1_metered": [
                    {"time": yesterday_timestamp, "value": 50.0, "unit": "NOK"},
                ],
                "CW_1_metered": [
                    {"time": yesterday_timestamp, "value": 100.0, "unit": "NOK"},
                ],
            },
            "daily_consumption_cache": {
                "HW_1": [],
                "CW_1": [],
            },
        }

        sensor = EcoGuardDailyCombinedWaterCostSensor(
            hass=hass,
            coordinator=coordinator,
            cost_type="actual",
        )
        sensor.hass = hass
        sensor.platform = MagicMock()
        sensor.entity_id = "sensor.test_combined_water_cost"

        def get_setting(name: str):
            """Helper to get setting from coordinator._settings."""
            for setting in coordinator._settings:
                if setting.get("Name") == name:
                    return setting.get("Value")
            return None

        with patch.object(
            coordinator, "get_active_installations", return_value=coordinator._installations
        ), patch.object(
            coordinator, "get_measuring_points", return_value=coordinator._measuring_points
        ), patch.object(
            coordinator, "get_setting", side_effect=get_setting
        ), patch.object(sensor, "async_write_ha_state", new_callable=AsyncMock):
            sensor._update_from_coordinator_data()

            # Should work normally when all meters have data from the same date
            # HW: 50.0, CW: 100.0, Total: 150.0
            assert sensor._attr_native_value == 150.0
            assert len(sensor._hw_meters_with_data) == 1
            assert len(sensor._cw_meters_with_data) == 1
            assert sensor._hw_meters_with_data[0]["value"] == 50.0
            assert sensor._cw_meters_with_data[0]["value"] == 100.0

            # Last data date should be yesterday
            assert sensor._last_data_date is not None
            assert sensor._last_data_date.date() == yesterday.date()
