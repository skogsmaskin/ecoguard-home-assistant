"""Monthly sensors for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import logging
import asyncio
import zoneinfo

from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from ..coordinator import EcoGuardDataUpdateCoordinator
from ..helpers import round_to_max_digits
from ..translations import (
    async_get_translation,
    get_translation_default,
)

from ..sensor_helpers import (
    async_update_entity_registry_name,
    slugify_name,
    utility_code_to_slug,
)
from ..sensor_base import EcoGuardBaseSensor

_LOGGER = logging.getLogger(__name__)

class EcoGuardMonthlyAggregateSensor(EcoGuardBaseSensor):
    """Sensor for monthly aggregate consumption or price."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        utility_code: str,
        aggregate_type: str,
        cost_type: str = "actual",
    ) -> None:
        """Initialize the monthly aggregate sensor.

        Args:
            hass: Home Assistant instance
            coordinator: The coordinator instance
            utility_code: Utility code (e.g., "HW", "CW")
            aggregate_type: "con" for consumption, "price" for price/cost
            cost_type: "actual" for metered API data, "estimated" for estimated (only for price)

        Note:
            The aggregate_type parameter uses "price" to match the EcoGuard API terminology
            (the API uses "[price]" in utility codes like "HW[price]"). However, user-facing
            sensor names use "cost" terminology (e.g., "Cost Monthly Aggregated") as it's more
            natural in English. This distinction is intentional: "price" for API/internal use,
            "cost" for user-facing display.
        """
        super().__init__(coordinator, hass=hass)
        self._hass = hass
        self._utility_code = utility_code
        self._aggregate_type = aggregate_type
        self._cost_type = cost_type

        # Build sensor name
        # Use English defaults here; will be updated in async_added_to_hass
        utility_name = get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        if aggregate_type == "con":
            # Use "Consumption Monthly Aggregated" format to ensure entity_id starts with "consumption_monthly_aggregated_"
            aggregate_name = get_translation_default("name.consumption_monthly_aggregated")
        else:
            # Use "Cost Monthly Aggregated" format to ensure entity_id starts with "cost_monthly_aggregated_"
            aggregate_name = get_translation_default("name.cost_monthly_aggregated")

        # Add cost type suffix for price sensors
        if aggregate_type == "price" and cost_type == "estimated":
            estimated = get_translation_default("name.estimated")
            aggregate_name = f"{aggregate_name} {estimated}"
        elif aggregate_type == "price" and cost_type == "actual":
            metered = get_translation_default("name.metered")
            aggregate_name = f"{aggregate_name} {metered}"

        # Format: "Aggregate Name - Utility"
        # This groups similar sensors together when sorted alphabetically
        self._attr_name = f"{aggregate_name} - {utility_name}"

        # Build unique ID following pattern: purpose_group_utility
        # Home Assistant strips the domain prefix, so we want: consumption_monthly_aggregated_cold_water
        utility_slug = utility_code_to_slug(utility_code)
        if aggregate_type == "con":
            unique_id_suffix = f"consumption_monthly_aggregated_{utility_slug}"
        elif aggregate_type == "price" and cost_type == "estimated":
            unique_id_suffix = f"cost_monthly_aggregated_estimated_{utility_slug}"
        else:
            # For "actual" cost_type, use "metered" in the ID for clarity
            unique_id_suffix = f"cost_monthly_aggregated_metered_{utility_slug}"

        self._attr_unique_id = (
            f"{DOMAIN}_{unique_id_suffix}"
        )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        self._attr_device_info = self._get_device_info(coordinator.node_id)

        # Set state class
        if aggregate_type == "con":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        # Always set a default unit from the start to prevent statistics issues
        # Use currency from settings as default for price sensors
        # If currency is not available, use "NOK" as fallback to ensure unit is never empty
        if aggregate_type == "price":
            default_unit = coordinator.get_setting("Currency") or "NOK"
            self._attr_native_unit_of_measurement = default_unit
        else:
            self._attr_native_unit_of_measurement = None
        self._current_year: int | None = None
        self._current_month: int | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "utility_code": self._utility_code,
            "aggregate_type": self._aggregate_type,
            "cost_type": "metered" if self._cost_type == "actual" else self._cost_type,
        }

        if self._current_year is not None and self._current_month is not None:
            attrs["year"] = self._current_year
            attrs["month"] = self._current_month
            attrs["period"] = f"{self._current_year}-{self._current_month:02d}"

        return attrs

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            utility_name = await self._get_translated_utility_name(self._utility_code)

            if self._aggregate_type == "con":
                # Keep "Consumption Monthly Aggregated" format to maintain entity_id starting with "consumption_monthly_aggregated_"
                aggregate_name = await async_get_translation(self._hass, "name.consumption_monthly_aggregated")
            else:
                # Keep "Cost Monthly Aggregated" format to maintain entity_id starting with "cost_monthly_aggregated_"
                aggregate_name = await async_get_translation(self._hass, "name.cost_monthly_aggregated")

            if self._aggregate_type == "price" and self._cost_type == "estimated":
                estimated = await async_get_translation(self._hass, "name.estimated")
                aggregate_name = f"{aggregate_name} {estimated}"
            elif self._aggregate_type == "price" and self._cost_type == "actual":
                metered = await async_get_translation(self._hass, "name.metered")
                aggregate_name = f"{aggregate_name} {metered}"

            # Format: "Aggregate Name - Utility"
            # This groups similar sensors together when sorted alphabetically
            new_name = f"{aggregate_name} - {utility_name}"
            await self._update_name_and_registry(new_name, log_level="debug")
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)


    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.debug("No coordinator data for %s", self.entity_id)
            self._attr_native_value = None
            default_unit = ""
            if self._aggregate_type == "price":
                default_unit = self.coordinator.get_setting("Currency") or "NOK"
            self._attr_native_unit_of_measurement = default_unit
            self._attr_available = True
            self.async_write_ha_state()
            return

        # Get current month
        now = datetime.now()
        year = now.year
        month = now.month

        # Check monthly aggregate cache first
        monthly_cache = coordinator_data.get("monthly_aggregate_cache", {})
        cache_key = f"{self._utility_code}_{year}_{month}_{self._aggregate_type}_{self._cost_type if self._aggregate_type == 'price' else 'actual'}"

        aggregate_data = monthly_cache.get(cache_key)

        # If not in monthly cache, try to calculate from daily cache (smart reuse!)
        if not aggregate_data:
            if self._aggregate_type == "con":
                # Calculate monthly consumption from daily consumption cache
                daily_cache = coordinator_data.get("daily_consumption_cache", {})
                cache_key_daily = f"{self._utility_code}_all"
                daily_values = daily_cache.get(cache_key_daily)

                if daily_values:
                    # Get timezone for date calculations
                    timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
                    try:
                        tz = zoneinfo.ZoneInfo(timezone_str)
                    except Exception:
                        tz = zoneinfo.ZoneInfo("UTC")

                    # Calculate month boundaries
                    from_date = datetime(year, month, 1, tzinfo=tz)
                    if month == 12:
                        to_date = datetime(year + 1, 1, 1, tzinfo=tz)
                    else:
                        to_date = datetime(year, month + 1, 1, tzinfo=tz)

                    from_time = int(from_date.timestamp())
                    to_time = int(to_date.timestamp())

                    # Filter daily values for this month
                    month_values = [
                        v for v in daily_values
                        if from_time <= v.get("time", 0) < to_time and v.get("value") is not None
                    ]

                    if month_values:
                        # Sum all values for the month
                        total_value = sum(v["value"] for v in month_values)
                        unit = month_values[0].get("unit", "") if month_values else ""

                        aggregate_data = {
                            "value": total_value,
                            "unit": unit,
                            "year": year,
                            "month": month,
                            "utility_code": self._utility_code,
                            "aggregate_type": self._aggregate_type,
                        }
                        _LOGGER.debug("Calculated monthly consumption for %s from daily cache: %.2f %s",
                                     self.entity_id, total_value, unit)

            elif self._aggregate_type == "price" and self._cost_type == "actual":
                # Calculate monthly price from daily price cache
                daily_price_cache = coordinator_data.get("daily_price_cache", {})

                # Get timezone for date calculations
                timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
                try:
                    tz = zoneinfo.ZoneInfo(timezone_str)
                except Exception:
                    tz = zoneinfo.ZoneInfo("UTC")

                # Calculate month boundaries
                from_date = datetime(year, month, 1, tzinfo=tz)
                if month == 12:
                    to_date = datetime(year + 1, 1, 1, tzinfo=tz)
                else:
                    to_date = datetime(year, month + 1, 1, tzinfo=tz)

                from_time = int(from_date.timestamp())
                to_time = int(to_date.timestamp())

                # Sum prices from all meters for this utility
                total_price = 0.0
                has_cached_data = False
                unit = ""

                for cache_key_price, daily_prices in daily_price_cache.items():
                    if cache_key_price.startswith(f"{self._utility_code}_") and cache_key_price.endswith("_metered"):
                        # Filter daily prices for this month
                        month_prices = [
                            p for p in daily_prices
                            if from_time <= p.get("time", 0) < to_time and p.get("value") is not None and p.get("value", 0) > 0
                        ]
                        if month_prices:
                            # Sum prices for this meter
                            meter_total = sum(p["value"] for p in month_prices)
                            total_price += meter_total
                            has_cached_data = True
                            if not unit:
                                unit = month_prices[0].get("unit", "")

                if has_cached_data:
                    currency = self.coordinator.get_setting("Currency") or unit or "NOK"
                    aggregate_data = {
                        "value": total_price,
                        "unit": currency,
                        "year": year,
                        "month": month,
                        "utility_code": self._utility_code,
                        "aggregate_type": "price",
                        "cost_type": "actual",
                    }
                    _LOGGER.debug("Calculated monthly price for %s from daily cache: %.2f %s",
                                 self.entity_id, total_price, currency)

        # Always set a default unit to prevent statistics issues
        default_unit = ""
        if self._aggregate_type == "price":
            default_unit = self.coordinator.get_setting("Currency") or "NOK"

        if aggregate_data:
            raw_value = aggregate_data.get("value")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            # Use unit from data, or fall back to default
            self._attr_native_unit_of_measurement = aggregate_data.get("unit") or default_unit
            self._current_year = aggregate_data.get("year")
            self._current_month = aggregate_data.get("month")
            self._attr_available = True

            _LOGGER.info("Updated %s: %s %s (from cache, year=%d, month=%d)",
                         self.entity_id, self._attr_native_value, self._attr_native_unit_of_measurement,
                         self._current_year or year, self._current_month or month)
        else:
            # No data available yet
            # For estimated costs (especially HW), trigger async fetch to calculate using spot prices
            if self._aggregate_type == "price" and self._cost_type == "estimated":
                from homeassistant.core import CoreState
                if self.hass and not self.hass.is_stopping and self.hass.state != CoreState.starting:
                    # Trigger async fetch in background (non-blocking)
                    async def _fetch_estimated_cost():
                        try:
                            _LOGGER.debug("Starting async fetch for estimated monthly aggregate: %s", self.entity_id)
                            await self._async_fetch_value()
                        except Exception as err:
                            _LOGGER.warning("Error in async fetch for %s: %s", self.entity_id, err, exc_info=True)
                    self.hass.async_create_task(_fetch_estimated_cost())
                    _LOGGER.debug("Created async task for estimated monthly aggregate fetch: %s (utility: %s, year: %d, month: %d)",
                                 self.entity_id, self._utility_code, year, month)

            # No data available yet, but keep sensor available
            self._attr_native_value = None
            # Always set unit even when no data to maintain consistency for statistics
            self._attr_native_unit_of_measurement = default_unit
            self._current_year = None
            self._current_month = None
            self._attr_available = True

            _LOGGER.debug("No cached monthly aggregate for %s (cache_key: %s, available keys: %s)",
                          self.entity_id, cache_key, list(monthly_cache.keys())[:5])

        self.async_write_ha_state()

    async def _async_fetch_value(self) -> None:
        """Fetch current month's aggregate value."""
        now = datetime.now()
        year = now.year
        month = now.month

        cost_type_to_use = self._cost_type if self._aggregate_type == "price" else "actual"
        _LOGGER.debug("Fetching monthly aggregate for %s: utility=%s, type=%s, cost_type=%s, year=%d, month=%d",
                     self.entity_id, self._utility_code, self._aggregate_type, cost_type_to_use, year, month)

        aggregate_data = await self.coordinator.get_monthly_aggregate(
            utility_code=self._utility_code,
            year=year,
            month=month,
            aggregate_type=self._aggregate_type,
            cost_type=cost_type_to_use,
        )

        # Always set a default unit to prevent statistics issues
        # Use currency from settings as default for price sensors
        # If currency is not available, use "NOK" as fallback to ensure unit is never empty
        default_unit = ""
        if self._aggregate_type == "price":
            default_unit = self.coordinator.get_setting("Currency") or "NOK"

        if aggregate_data:
            raw_value = aggregate_data.get("value")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            # Use unit from data, or fall back to default
            self._attr_native_unit_of_measurement = aggregate_data.get("unit") or default_unit
            self._current_year = aggregate_data.get("year")
            self._current_month = aggregate_data.get("month")
            self._attr_available = True

            _LOGGER.info("Updated %s (async fetch): %s %s (year=%d, month=%d, cost_type=%s)",
                        self.entity_id, self._attr_native_value, self._attr_native_unit_of_measurement,
                        self._current_year or year, self._current_month or month, cost_type_to_use)

            # Note: We don't trigger coordinator updates here because per-meter sensors are now
            # self-sufficient - they fetch aggregate data directly when needed for proportional allocation
        else:
            self._attr_native_value = None
            # Always set unit even when no data to maintain consistency for statistics
            self._attr_native_unit_of_measurement = default_unit
            self._current_year = None
            self._current_month = None
            self._attr_available = True
            _LOGGER.debug("No monthly aggregate data returned for %s (utility=%s, cost_type=%s)",
                          self.entity_id, self._utility_code, cost_type_to_use)

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()



class EcoGuardMonthlyMeterSensor(EcoGuardBaseSensor):
    """Sensor for monthly consumption or cost for a specific meter."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        installation: dict[str, Any],
        utility_code: str,
        measuring_point_id: int,
        measuring_point_name: str | None,
        aggregate_type: str,
        cost_type: str = "actual",
    ) -> None:
        """Initialize the monthly meter sensor.

        Args:
            hass: Home Assistant instance
            coordinator: The coordinator instance
            installation: Installation data dict
            utility_code: Utility code (e.g., "HW", "CW")
            measuring_point_id: Measuring point ID
            measuring_point_name: Measuring point name
            aggregate_type: "con" for consumption, "price" for price/cost
            cost_type: "actual" for metered API data, "estimated" for estimated (only for price)

        Note:
            The aggregate_type parameter uses "price" to match the EcoGuard API terminology
            (the API uses "[price]" in utility codes like "HW[price]"). However, user-facing
            sensor names use "cost" terminology (e.g., "Cost Monthly Aggregated") as it's more
            natural in English. This distinction is intentional: "price" for API/internal use,
            "cost" for user-facing display.
        """
        super().__init__(coordinator, hass=hass)
        self._hass = hass
        self._installation = installation
        self._utility_code = utility_code
        self._measuring_point_id = measuring_point_id
        self._measuring_point_name = measuring_point_name
        self._aggregate_type = aggregate_type
        self._cost_type = cost_type

        # Build sensor name
        if measuring_point_name:
            measuring_point_display = measuring_point_name
        else:
            measuring_point_display = get_translation_default("name.measuring_point", id=measuring_point_id)

        utility_name = get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        if aggregate_type == "con":
            aggregate_name = get_translation_default("name.consumption_monthly_aggregated")
        else:
            aggregate_name = get_translation_default("name.cost_monthly_aggregated")

        # Add cost type suffix for price sensors
        if aggregate_type == "price" and cost_type == "estimated":
            estimated = get_translation_default("name.estimated")
            aggregate_name = f"{aggregate_name} {estimated}"
        elif aggregate_type == "price" and cost_type == "actual":
            metered = get_translation_default("name.metered")
            aggregate_name = f"{aggregate_name} {metered}"

        # Format: "Aggregate Name - Meter "Measuring Point" (Utility)"
        meter = get_translation_default("name.meter")
        self._attr_name = f'{aggregate_name} - {meter} "{measuring_point_display}" ({utility_name})'

        # Build unique_id following pattern: purpose_group_utility_sensor
        utility_slug = utility_code_to_slug(utility_code)
        sensor_name = slugify_name(measuring_point_name) or f"mp{measuring_point_id}"
        if aggregate_type == "con":
            unique_id_suffix = f"consumption_monthly_metered_{utility_slug}_{sensor_name}"
        elif aggregate_type == "price" and cost_type == "estimated":
            unique_id_suffix = f"cost_monthly_aggregated_estimated_{utility_slug}_{sensor_name}"
        else:
            unique_id_suffix = f"cost_monthly_aggregated_metered_{utility_slug}_{sensor_name}"

        self._attr_unique_id = f"{DOMAIN}_{unique_id_suffix}"

        # Sensor attributes
        device_name = get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
            "model": installation.get("DeviceTypeDisplay", "Unknown"),
        }

        # Disable individual meter sensors by default (users can enable if needed)
        self._attr_entity_registry_enabled_default = False

        # Set state class
        if aggregate_type == "con":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        if aggregate_type == "price":
            default_unit = coordinator.get_setting("Currency") or "NOK"
            self._attr_native_unit_of_measurement = default_unit
        else:
            self._attr_native_unit_of_measurement = None
        self._current_year: int | None = None
        self._current_month: int | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "measuring_point_id": self._measuring_point_id,
            "utility_code": self._utility_code,
            "external_key": self._installation.get("ExternalKey"),
            "device_type": self._installation.get("DeviceTypeDisplay"),
            "sensor_type": "monthly_meter",
            "aggregate_type": self._aggregate_type,
        }

        if self._current_year is not None and self._current_month is not None:
            attrs["year"] = self._current_year
            attrs["month"] = self._current_month
            attrs["period"] = f"{self._current_year}-{self._current_month:02d}"

        if self._aggregate_type == "price":
            attrs["cost_type"] = self._cost_type

        return attrs

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            if self._measuring_point_name:
                measuring_point_display = self._measuring_point_name
            else:
                measuring_point_display = await async_get_translation(
                    self._hass, "name.measuring_point", id=self._measuring_point_id
                )

            utility_name = await async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            if self._aggregate_type == "con":
                aggregate_name = await async_get_translation(self._hass, "name.consumption_monthly_aggregated")
            else:
                aggregate_name = await async_get_translation(self._hass, "name.cost_monthly_aggregated")

            if self._aggregate_type == "price" and self._cost_type == "estimated":
                estimated = await async_get_translation(self._hass, "name.estimated")
                aggregate_name = f"{aggregate_name} {estimated}"
            elif self._aggregate_type == "price" and self._cost_type == "actual":
                metered = await async_get_translation(self._hass, "name.metered")
                aggregate_name = f"{aggregate_name} {metered}"

            meter = await async_get_translation(self._hass, "name.meter")
            new_name = f'{aggregate_name} - {meter} "{measuring_point_display}" ({utility_name})'
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)


    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.debug("No coordinator data for %s", self.entity_id)
            self._attr_native_value = None
            default_unit = ""
            if self._aggregate_type == "price":
                default_unit = self.coordinator.get_setting("Currency") or "NOK"
            self._attr_native_unit_of_measurement = default_unit
            self._attr_available = True
            self.async_write_ha_state()
            return

        # Get current month
        now = datetime.now()
        year = now.year
        month = now.month

        # Check monthly aggregate cache (coordinator caches per-meter aggregates)
        monthly_cache = coordinator_data.get("monthly_aggregate_cache", {})
        cache_key = f"{self._utility_code}_{year}_{month}_{self._aggregate_type}_{self._cost_type if self._aggregate_type == 'price' else 'actual'}"

        # Also check per-meter cache
        per_meter_cache_key = f"{self._utility_code}_{self._measuring_point_id}_{year}_{month}_{self._aggregate_type}_{self._cost_type if self._aggregate_type == 'price' else 'actual'}"

        aggregate_data = monthly_cache.get(cache_key) or monthly_cache.get(per_meter_cache_key)

        _LOGGER.debug(
            "Per-meter sensor %s checking cache: cache_key=%s, per_meter_key=%s, found=%s, value=%s",
            self.entity_id, cache_key, per_meter_cache_key, aggregate_data is not None,
            aggregate_data.get("value") if aggregate_data else None
        )

        # If not in monthly cache, try to calculate from daily cache (smart reuse!)
        if not aggregate_data and self._aggregate_type == "con":
            # Calculate monthly consumption from daily consumption cache for this specific meter
            daily_cache = coordinator_data.get("daily_consumption_cache", {})
            cache_key_daily = f"{self._utility_code}_{self._measuring_point_id}"
            daily_values = daily_cache.get(cache_key_daily)

            if daily_values:
                # Get timezone for date calculations
                timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
                from .helpers import get_timezone
                tz = get_timezone(timezone_str)

                # Calculate month boundaries
                from_date = datetime(year, month, 1, tzinfo=tz)
                if month == 12:
                    to_date = datetime(year + 1, 1, 1, tzinfo=tz)
                else:
                    to_date = datetime(year, month + 1, 1, tzinfo=tz)

                from_time = int(from_date.timestamp())
                to_time = int(to_date.timestamp())

                # Filter daily values for this month
                month_values = [
                    v for v in daily_values
                    if from_time <= v.get("time", 0) < to_time and v.get("value") is not None
                ]

                if month_values:
                    # Sum all values for the month
                    total_value = sum(v["value"] for v in month_values)
                    unit = month_values[0].get("unit", "") if month_values else ""

                    _LOGGER.debug(
                        "Calculated monthly consumption for meter %d (%s) %d-%02d from %d cached daily values (reused data!)",
                        self._measuring_point_id, self._utility_code, year, month, len(month_values)
                    )

                    # Create aggregate data structure
                    aggregate_data = {
                        "value": total_value,
                        "unit": unit,
                        "year": year,
                        "month": month,
                        "utility_code": self._utility_code,
                        "aggregate_type": "con",
                    }

        # For estimated costs: try proportional allocation from aggregate estimated cost
        # This should run on every coordinator update to catch when aggregate data becomes available
        # Only use proportional allocation if we don't have direct per-meter data
        if self._aggregate_type == "price" and self._cost_type == "estimated":
            # Check if we already have direct per-meter data - if so, don't use proportional allocation
            has_direct_data = aggregate_data is not None

            # Try proportional allocation if we don't have direct data
            if not has_direct_data:
                # Get aggregate estimated cost for this utility - check cache first, then fetch if needed
                aggregate_cost_key = f"{self._utility_code}_{year}_{month}_price_estimated"
                aggregate_cost_data = monthly_cache.get(aggregate_cost_key)

                _LOGGER.debug(
                    "Checking proportional allocation for %s: aggregate_cost_key=%s, found_in_cache=%s",
                    self.entity_id, aggregate_cost_key, aggregate_cost_data is not None
                )

                # If not in cache, fetch it directly (self-sufficient approach)
                if not aggregate_cost_data:
                    from homeassistant.core import CoreState
                    if self.hass and not self.hass.is_stopping and self.hass.state != CoreState.starting:
                        # Fetch aggregate data asynchronously
                        async def _fetch_aggregate_and_calculate():
                            try:
                                _LOGGER.debug("Fetching aggregate estimated cost for %s to calculate proportional allocation", self.entity_id)
                                aggregate_cost_data = await self.coordinator.get_monthly_aggregate(
                                    utility_code=self._utility_code,
                                    year=year,
                                    month=month,
                                    aggregate_type="price",
                                    cost_type="estimated",
                                )

                                if aggregate_cost_data:
                                    # Calculate proportional allocation with the fetched data
                                    await self._calculate_and_update_proportional_allocation(
                                        aggregate_cost_data, year, month
                                    )
                            except Exception as err:
                                _LOGGER.warning("Error fetching aggregate data for proportional allocation in %s: %s",
                                              self.entity_id, err, exc_info=True)

                        self.hass.async_create_task(_fetch_aggregate_and_calculate())
                        # Return early - will update when fetch completes
                        return

                # If we have aggregate data in cache, calculate proportional allocation synchronously
                if aggregate_cost_data:
                    total_estimated_cost = aggregate_cost_data.get("value")
                    _LOGGER.debug(
                        "Found aggregate cost data for %s: value=%s, unit=%s - calculating proportional allocation",
                        self.entity_id, total_estimated_cost, aggregate_cost_data.get("unit")
                    )

                    # Calculate proportional allocation synchronously (since we have the data)
                    per_meter_consumption = None

                    # Try to get from monthly consumption cache
                    per_meter_con_key = f"{self._utility_code}_{self._measuring_point_id}_{year}_{month}_con_actual"
                    per_meter_con_data = monthly_cache.get(per_meter_con_key)
                    if per_meter_con_data:
                        per_meter_consumption = per_meter_con_data.get("value")
                    else:
                        # Calculate from daily consumption cache
                        daily_cache = coordinator_data.get("daily_consumption_cache", {})
                        cache_key_daily = f"{self._utility_code}_{self._measuring_point_id}"
                        daily_values = daily_cache.get(cache_key_daily)

                        if daily_values:
                            # Get timezone for date calculations
                            timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
                            from .helpers import get_timezone
                            tz = get_timezone(timezone_str)

                            # Calculate month boundaries
                            from_date = datetime(year, month, 1, tzinfo=tz)
                            if month == 12:
                                to_date = datetime(year + 1, 1, 1, tzinfo=tz)
                            else:
                                to_date = datetime(year, month + 1, 1, tzinfo=tz)

                            from_time = int(from_date.timestamp())
                            to_time = int(to_date.timestamp())

                            # Filter daily values for this month
                            month_values = [
                                v for v in daily_values
                                if from_time <= v.get("time", 0) < to_time and v.get("value") is not None
                            ]

                            if month_values:
                                per_meter_consumption = sum(v["value"] for v in month_values)

                    # Get total consumption for this utility (aggregate)
                    total_consumption_key = f"{self._utility_code}_{year}_{month}_con_actual"
                    total_consumption_data = monthly_cache.get(total_consumption_key)

                    if total_consumption_data:
                        total_consumption = total_consumption_data.get("value")
                    else:
                        # Calculate total consumption from daily cache
                        daily_cache = coordinator_data.get("daily_consumption_cache", {})
                        total_consumption = None

                        # Get timezone for date calculations
                        timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
                        from .helpers import get_timezone
                        tz = get_timezone(timezone_str)

                        # Calculate month boundaries
                        from_date = datetime(year, month, 1, tzinfo=tz)
                        if month == 12:
                            to_date = datetime(year + 1, 1, 1, tzinfo=tz)
                        else:
                            to_date = datetime(year, month + 1, 1, tzinfo=tz)

                        from_time = int(from_date.timestamp())
                        to_time = int(to_date.timestamp())

                        # First try the aggregate "all" key (most efficient)
                        aggregate_cache_key = f"{self._utility_code}_all"
                        if aggregate_cache_key in daily_cache:
                            daily_values = daily_cache[aggregate_cache_key]
                            month_values = [
                                v for v in daily_values
                                if from_time <= v.get("time", 0) < to_time and v.get("value") is not None
                            ]
                            if month_values:
                                total_consumption = sum(v["value"] for v in month_values)

                        # If no aggregate key, sum all meters for this utility
                        if total_consumption is None:
                            total_consumption = 0.0
                            for cache_key, daily_values in daily_cache.items():
                                if cache_key.startswith(f"{self._utility_code}_"):
                                    # Filter daily values for this month
                                    month_values = [
                                        v for v in daily_values
                                        if from_time <= v.get("time", 0) < to_time and v.get("value") is not None
                                    ]
                                    if month_values:
                                        total_consumption += sum(v["value"] for v in month_values)

                    # Calculate proportional cost
                    if (per_meter_consumption is not None and
                        total_consumption is not None and
                        total_consumption > 0 and
                        total_estimated_cost is not None):

                        proportion = per_meter_consumption / total_consumption
                        per_meter_cost = total_estimated_cost * proportion

                        _LOGGER.info(
                            "Calculated per-meter estimated cost for meter %d (%s) %d-%02d: "
                            "%.3f / %.3f = %.1f%% of %.2f = %.2f (proportional allocation)",
                            self._measuring_point_id, self._utility_code, year, month,
                            per_meter_consumption, total_consumption, proportion * 100,
                            total_estimated_cost, per_meter_cost
                        )

                        # Create aggregate data structure
                        aggregate_data = {
                            "value": per_meter_cost,
                            "unit": aggregate_cost_data.get("unit", ""),
                            "year": year,
                            "month": month,
                            "utility_code": self._utility_code,
                            "aggregate_type": "price",
                            "cost_type": "estimated",
                            "measuring_point_id": self._measuring_point_id,
                        }
                    else:
                        _LOGGER.debug(
                            "Cannot calculate proportional cost for %s: per_meter_consumption=%s, total_consumption=%s, total_estimated_cost=%s",
                            self.entity_id, per_meter_consumption, total_consumption, total_estimated_cost
                        )

        default_unit = ""
        if self._aggregate_type == "price":
            default_unit = self.coordinator.get_setting("Currency") or "NOK"

        if aggregate_data:
            raw_value = aggregate_data.get("value")
            old_value = self._attr_native_value
            new_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            self._attr_native_value = new_value
            self._attr_native_unit_of_measurement = aggregate_data.get("unit") or default_unit
            self._current_year = aggregate_data.get("year")
            self._current_month = aggregate_data.get("month")
            self._attr_available = True

            # Log update (always log when we have data, even if value hasn't changed)
            if old_value != new_value:
                _LOGGER.info("Updated %s: %s -> %s %s (from cache, year=%d, month=%d)",
                             self.entity_id, old_value, new_value,
                             self._attr_native_unit_of_measurement,
                             self._current_year or year, self._current_month or month)
            else:
                # Log at debug level if value hasn't changed (to confirm update path is being taken)
                _LOGGER.debug("Sensor %s already has correct value: %s %s (from cache, year=%d, month=%d)",
                             self.entity_id, new_value,
                             self._attr_native_unit_of_measurement,
                             self._current_year or year, self._current_month or month)

            self.async_write_ha_state()
            return

        # No cached data - set placeholder
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = default_unit
        self._attr_available = True
        self.async_write_ha_state()

        # For estimated costs: if we don't have per-meter data, fetch aggregate data directly
        # This makes the sensor self-sufficient - it doesn't depend on other sensors
        if self._aggregate_type == "price" and self._cost_type == "estimated":
            from homeassistant.core import CoreState
            if self.hass and not self.hass.is_stopping and self.hass.state != CoreState.starting:
                # Fetch aggregate data directly (self-sufficient approach)
                async def _fetch_and_calculate_proportional():
                    try:
                        _LOGGER.debug("Fetching aggregate estimated cost for %s to calculate proportional allocation", self.entity_id)
                        aggregate_cost_data = await self.coordinator.get_monthly_aggregate(
                            utility_code=self._utility_code,
                            year=year,
                            month=month,
                            aggregate_type="price",
                            cost_type="estimated",
                        )

                        if aggregate_cost_data:
                            await self._calculate_and_update_proportional_allocation(
                                aggregate_cost_data, year, month
                            )
                        else:
                            _LOGGER.debug("No aggregate estimated cost data available for %s", self.entity_id)
                    except Exception as err:
                        _LOGGER.warning("Error fetching aggregate data for proportional allocation in %s: %s",
                                      self.entity_id, err, exc_info=True)

                self.hass.async_create_task(_fetch_and_calculate_proportional())
                # For estimated costs, proportional allocation handles the update, so we don't need _async_fetch_value
                return

        # For non-estimated costs (or if proportional allocation didn't trigger), try to fetch per-meter data
        # Only trigger async fetch if HA is fully started (not during startup)
        from homeassistant.core import CoreState
        if self.hass and not self.hass.is_stopping and self.hass.state != CoreState.starting:
            # Add a small delay to avoid immediate API calls during sensor creation
            async def _deferred_fetch():
                await asyncio.sleep(5.0)  # Wait 5 seconds after HA starts
                if not self.hass.is_stopping:
                    await self._async_fetch_value()
            self.hass.async_create_task(_deferred_fetch())

    async def _calculate_and_update_proportional_allocation(
        self, aggregate_cost_data: dict[str, Any], year: int, month: int
    ) -> None:
        """Calculate proportional allocation and update sensor state."""
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            return

        monthly_cache = coordinator_data.get("monthly_aggregate_cache", {})
        total_estimated_cost = aggregate_cost_data.get("value")

        # Get per-meter consumption
        per_meter_consumption = None
        per_meter_con_key = f"{self._utility_code}_{self._measuring_point_id}_{year}_{month}_con_actual"
        per_meter_con_data = monthly_cache.get(per_meter_con_key)
        if per_meter_con_data:
            per_meter_consumption = per_meter_con_data.get("value")
        else:
            # Calculate from daily consumption cache
            daily_cache = coordinator_data.get("daily_consumption_cache", {})
            cache_key_daily = f"{self._utility_code}_{self._measuring_point_id}"
            daily_values = daily_cache.get(cache_key_daily)

            if daily_values:
                timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
                from .helpers import get_timezone
                tz = get_timezone(timezone_str)

                from_date = datetime(year, month, 1, tzinfo=tz)
                if month == 12:
                    to_date = datetime(year + 1, 1, 1, tzinfo=tz)
                else:
                    to_date = datetime(year, month + 1, 1, tzinfo=tz)

                from_time = int(from_date.timestamp())
                to_time = int(to_date.timestamp())

                month_values = [
                    v for v in daily_values
                    if from_time <= v.get("time", 0) < to_time and v.get("value") is not None
                ]

                if month_values:
                    per_meter_consumption = sum(v["value"] for v in month_values)

        # Get total consumption for this utility
        total_consumption_key = f"{self._utility_code}_{year}_{month}_con_actual"
        total_consumption_data = monthly_cache.get(total_consumption_key)

        if total_consumption_data:
            total_consumption = total_consumption_data.get("value")
        else:
            # Calculate total consumption from daily cache
            daily_cache = coordinator_data.get("daily_consumption_cache", {})
            total_consumption = None

            timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
            from .helpers import get_timezone
            tz = get_timezone(timezone_str)

            from_date = datetime(year, month, 1, tzinfo=tz)
            if month == 12:
                to_date = datetime(year + 1, 1, 1, tzinfo=tz)
            else:
                to_date = datetime(year, month + 1, 1, tzinfo=tz)

            from_time = int(from_date.timestamp())
            to_time = int(to_date.timestamp())

            aggregate_cache_key = f"{self._utility_code}_all"
            if aggregate_cache_key in daily_cache:
                daily_values = daily_cache[aggregate_cache_key]
                month_values = [
                    v for v in daily_values
                    if from_time <= v.get("time", 0) < to_time and v.get("value") is not None
                ]
                if month_values:
                    total_consumption = sum(v["value"] for v in month_values)

            if total_consumption is None:
                total_consumption = 0.0
                for cache_key, daily_values in daily_cache.items():
                    if cache_key.startswith(f"{self._utility_code}_"):
                        month_values = [
                            v for v in daily_values
                            if from_time <= v.get("time", 0) < to_time and v.get("value") is not None
                        ]
                        if month_values:
                            total_consumption += sum(v["value"] for v in month_values)

        # Calculate proportional cost
        if (per_meter_consumption is not None and
            total_consumption is not None and
            total_consumption > 0 and
            total_estimated_cost is not None):

            proportion = per_meter_consumption / total_consumption
            per_meter_cost = total_estimated_cost * proportion

            _LOGGER.debug(
                "Calculated per-meter estimated cost for meter %d (%s) %d-%02d: "
                "%.3f / %.3f = %.1f%% of %.2f = %.2f (proportional allocation)",
                self._measuring_point_id, self._utility_code, year, month,
                per_meter_consumption, total_consumption, proportion * 100,
                total_estimated_cost, per_meter_cost
            )

            # Update sensor state
            default_unit = self.coordinator.get_setting("Currency") or "NOK"
            self._attr_native_value = round_to_max_digits(per_meter_cost)
            self._attr_native_unit_of_measurement = aggregate_cost_data.get("unit", default_unit)
            self._current_year = year
            self._current_month = month
            self._attr_available = True
            self.async_write_ha_state()
        else:
            _LOGGER.debug(
                "Cannot calculate proportional cost for %s: per_meter_consumption=%s, total_consumption=%s, total_estimated_cost=%s",
                self.entity_id, per_meter_consumption, total_consumption, total_estimated_cost
            )

    async def _async_fetch_value(self) -> None:
        """Fetch current month's aggregate value for this specific meter."""
        now = datetime.now()
        year = now.year
        month = now.month

        aggregate_data = await self.coordinator.get_monthly_aggregate_for_meter(
            utility_code=self._utility_code,
            measuring_point_id=self._measuring_point_id,
            external_key=self._installation.get("ExternalKey"),
            year=year,
            month=month,
            aggregate_type=self._aggregate_type,
            cost_type=self._cost_type if self._aggregate_type == "price" else "actual",
        )

        default_unit = ""
        if self._aggregate_type == "price":
            default_unit = self.coordinator.get_setting("Currency") or "NOK"

        if aggregate_data:
            raw_value = aggregate_data.get("value")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            self._attr_native_unit_of_measurement = aggregate_data.get("unit") or default_unit
            self._current_year = aggregate_data.get("year")
            self._current_month = aggregate_data.get("month")
        else:
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = default_unit
            self._current_year = None
            self._current_month = None

        self.async_write_ha_state()



class EcoGuardCombinedWaterSensor(EcoGuardBaseSensor):
    """Sensor for combined water (HW + CW) consumption or cost."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        aggregate_type: str,
        cost_type: str = "actual",
    ) -> None:
        """Initialize the combined water sensor.

        Args:
            hass: Home Assistant instance
            coordinator: The coordinator instance
            aggregate_type: "con" for consumption, "price" for price/cost
            cost_type: "actual" for metered API data, "estimated" for estimated (only for price)

        Note:
            The aggregate_type parameter uses "price" to match the EcoGuard API terminology
            (the API uses "[price]" in utility codes like "HW[price]"). However, user-facing
            sensor names use "cost" terminology (e.g., "Cost Monthly Aggregated") as it's more
            natural in English. This distinction is intentional: "price" for API/internal use,
            "cost" for user-facing display.
        """
        super().__init__(coordinator, hass=hass)
        self._hass = hass
        self._aggregate_type = aggregate_type
        self._cost_type = cost_type

        if aggregate_type == "con":
            aggregate_name = get_translation_default("name.consumption_monthly_aggregated")
        else:
            aggregate_name = get_translation_default("name.cost_monthly_aggregated")

        if aggregate_type == "price" and cost_type == "estimated":
            estimated = get_translation_default("name.estimated")
            aggregate_name = f"{aggregate_name} {estimated}"
        elif aggregate_type == "price" and cost_type == "actual":
            metered = get_translation_default("name.metered")
            aggregate_name = f"{aggregate_name} {metered}"

        # Format: "Aggregate Name - Combined Water"
        water_name = get_translation_default("name.combined_water")
        if water_name == "name.combined_water":  # Fallback if not found
            water_name = "Combined Water"
        self._attr_name = f"{aggregate_name} - {water_name}"

        # Build unique_id following pattern: purpose_group_combined_water
        if aggregate_type == "con":
            unique_id_suffix = "consumption_monthly_aggregated_combined_water"
        elif aggregate_type == "price" and cost_type == "estimated":
            unique_id_suffix = "cost_monthly_aggregated_estimated_combined_water"
        else:
            unique_id_suffix = "cost_monthly_aggregated_metered_combined_water"

        self._attr_unique_id = f"{DOMAIN}_{unique_id_suffix}"

        # Sensor attributes
        device_name = get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

        # Set state class
        if aggregate_type == "con":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        if aggregate_type == "price":
            default_unit = coordinator.get_setting("Currency") or "NOK"
            self._attr_native_unit_of_measurement = default_unit
        else:
            # For consumption, use "m³" as default
            self._attr_native_unit_of_measurement = "m³"
        self._current_year: int | None = None
        self._current_month: int | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "sensor_type": "combined_water",
            "aggregate_type": self._aggregate_type,
            "utilities": ["HW", "CW"],
        }

        if self._current_year is not None and self._current_month is not None:
            attrs["year"] = self._current_year
            attrs["month"] = self._current_month
            attrs["period"] = f"{self._current_year}-{self._current_month:02d}"

        if self._aggregate_type == "price":
            attrs["cost_type"] = self._cost_type

        return attrs

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            if self._aggregate_type == "con":
                aggregate_name = await async_get_translation(self._hass, "name.consumption_monthly_aggregated")
            else:
                aggregate_name = await async_get_translation(self._hass, "name.cost_monthly_aggregated")

            if self._aggregate_type == "price" and self._cost_type == "estimated":
                estimated = await async_get_translation(self._hass, "name.estimated")
                aggregate_name = f"{aggregate_name} {estimated}"
            elif self._aggregate_type == "price" and self._cost_type == "actual":
                metered = await async_get_translation(self._hass, "name.metered")
                aggregate_name = f"{aggregate_name} {metered}"

            water_name = await async_get_translation(self._hass, "name.combined_water")
            if water_name == "name.combined_water":
                water_name = "Combined Water"

            new_name = f"{aggregate_name} - {water_name}"
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)


    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # This sensor sums HW + CW, which requires async operations
        # Try to read from monthly aggregate cache first
        coordinator_data = self.coordinator.data
        if coordinator_data:
            now = datetime.now()
            year = now.year
            month = now.month
            monthly_cache = coordinator_data.get("monthly_aggregate_cache", {})

            # Get HW and CW aggregates
            hw_cache_key = f"HW_{year}_{month}_{self._aggregate_type}_{self._cost_type if self._aggregate_type == 'price' else 'actual'}"
            cw_cache_key = f"CW_{year}_{month}_{self._aggregate_type}_{self._cost_type if self._aggregate_type == 'price' else 'actual'}"

            hw_data = monthly_cache.get(hw_cache_key)
            cw_data = monthly_cache.get(cw_cache_key)

            # Extract values (None if data doesn't exist or value is None)
            hw_value = hw_data.get("value") if hw_data else None
            cw_value = cw_data.get("value") if cw_data else None

            # Only show a value if we have data for BOTH HW and CW
            # If HW is Unknown (missing from cache or value is None), we show Unknown rather than just CW cost
            # (showing partial data would be misleading - it looks like total combined cost but is missing HW)
            if hw_value is not None and cw_value is not None:
                # Both values are available (not None)
                hw_value = hw_value or 0.0
                cw_value = cw_value or 0.0
                total_value = hw_value + cw_value

                # Get unit from data (HW or CW), or use default
                default_unit = ""
                if self._aggregate_type == "price":
                    default_unit = self.coordinator.get_setting("Currency") or "NOK"
                else:
                    # For consumption, use "m³" as default
                    default_unit = "m³"

                # Use unit from one of the data sources, or fall back to default
                unit = (hw_data.get("unit") if hw_data else None) or (cw_data.get("unit") if cw_data else None) or default_unit

                self._attr_native_value = round_to_max_digits(total_value)
                self._attr_native_unit_of_measurement = unit
                self._current_year = year
                self._current_month = month
                self._attr_available = True
                self.async_write_ha_state()
                return
            else:
                # Missing data for one or both utilities - show Unknown
                if self._aggregate_type == "price" and self._cost_type == "actual":
                    _LOGGER.debug("Missing data for monthly combined water cost: %s (hw_value=%s, cw_value=%s) - showing Unknown",
                                 self.entity_id, hw_value, cw_value)
                self._attr_native_value = None
                default_unit = ""
                if self._aggregate_type == "price":
                    default_unit = self.coordinator.get_setting("Currency") or "NOK"
                else:
                    # For consumption, use "m³" as default
                    default_unit = "m³"
                self._attr_native_unit_of_measurement = default_unit
                self._attr_available = True
                self.async_write_ha_state()
                return

        # No cached data - set placeholder and defer async fetch until after startup
        default_unit = ""
        if self._aggregate_type == "price":
            default_unit = self.coordinator.get_setting("Currency") or "NOK"
        else:
            # For consumption, use "m³" as default
            default_unit = "m³"
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = default_unit
        self._attr_available = True
        self.async_write_ha_state()

        # Only trigger async fetch if HA is fully started (not during startup)
        from homeassistant.core import CoreState
        if self.hass and not self.hass.is_stopping and self.hass.state != CoreState.starting:
            # Add a small delay to avoid immediate API calls during sensor creation
            async def _deferred_fetch():
                await asyncio.sleep(5.0)  # Wait 5 seconds after HA starts
                if not self.hass.is_stopping:
                    await self._async_fetch_value()
            self.hass.async_create_task(_deferred_fetch())

    async def _async_fetch_value(self) -> None:
        """Fetch current month's combined water (HW + CW) value."""
        now = datetime.now()
        year = now.year
        month = now.month

        # Get HW and CW aggregates
        hw_data = await self.coordinator.get_monthly_aggregate(
            utility_code="HW",
            year=year,
            month=month,
            aggregate_type=self._aggregate_type,
            cost_type=self._cost_type if self._aggregate_type == "price" else "actual",
        )

        cw_data = await self.coordinator.get_monthly_aggregate(
            utility_code="CW",
            year=year,
            month=month,
            aggregate_type=self._aggregate_type,
            cost_type=self._cost_type if self._aggregate_type == "price" else "actual",
        )

        default_unit = ""
        if self._aggregate_type == "price":
            default_unit = self.coordinator.get_setting("Currency") or "NOK"
        else:
            # For consumption, use "m³" as default
            default_unit = "m³"

        hw_value = hw_data.get("value") if hw_data else None
        cw_value = cw_data.get("value") if cw_data else None

        if hw_value is not None and cw_value is not None:
            total_value = hw_value + cw_value
            self._attr_native_value = round_to_max_digits(total_value)
            # Use unit from one of the data sources, or fall back to default
            unit = hw_data.get("unit") or cw_data.get("unit") or default_unit
            self._attr_native_unit_of_measurement = unit
            self._current_year = year
            self._current_month = month
        elif hw_value is not None:
            # Only HW data available
            self._attr_native_value = round_to_max_digits(hw_value)
            self._attr_native_unit_of_measurement = hw_data.get("unit") or default_unit
            self._current_year = year
            self._current_month = month
        elif cw_value is not None:
            # Only CW data available
            self._attr_native_value = round_to_max_digits(cw_value)
            self._attr_native_unit_of_measurement = cw_data.get("unit") or default_unit
            self._current_year = year
            self._current_month = month
        else:
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = default_unit
            self._current_year = None
            self._current_month = None

        self.async_write_ha_state()

