"""Special sensors for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import logging
import asyncio

from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from ..coordinator import EcoGuardDataUpdateCoordinator
from ..helpers import round_to_max_digits
from ..translations import (
    async_get_translation,
    get_translation_default,
)

from ..sensor_base import EcoGuardBaseSensor

_LOGGER = logging.getLogger(__name__)

class EcoGuardOtherItemsSensor(EcoGuardBaseSensor):
    """Sensor for other items (general fees) from billing results.

    Uses the most recent billing data as the source of truth.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
    ) -> None:
        """Initialize the other items sensor."""
        super().__init__(coordinator, hass=hass)
        self._hass = hass

        # Use "Cost Monthly Other Items" format to ensure entity_id starts with "cost_monthly_other_items"
        # This will be updated in async_added_to_hass with proper translations
        self._attr_name = get_translation_default("name.cost_monthly_other_items")
        # Build unique_id following pattern: purpose_group_sensor
        # Home Assistant strips the domain prefix, so we want: cost_monthly_other_items
        self._attr_unique_id = (
            f"{DOMAIN}_cost_monthly_other_items"
        )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        self._attr_device_info = self._get_device_info(coordinator.node_id)

        # Set state class
        self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        # Always set currency unit from the start to prevent statistics issues
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._current_year: int | None = None
        self._current_month: int | None = None
        self._item_count: int | None = None
        self._items: list[dict[str, Any]] = []

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "sensor_type": "other_items_cost",
            "note": "Uses last bill's values (most recent billing period available)",
        }

        if self._current_year is not None and self._current_month is not None:
            attrs["year"] = self._current_year
            attrs["month"] = self._current_month
            attrs["period"] = f"{self._current_year}-{self._current_month:02d}"

        if self._item_count is not None:
            attrs["item_count"] = self._item_count

        if self._items:
            attrs["items"] = self._items

        return attrs

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            # Keep "Cost Monthly Other Items" format to maintain entity_id starting with "cost_monthly_other_items"
            # The translation key might be used for display, but we keep the name format consistent
            new_name = await async_get_translation(self._hass, "name.cost_monthly_other_items")
            await self._update_name_and_registry(new_name, log_level="debug")
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)


    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.debug("No coordinator data for %s", self.entity_id)
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._attr_available = True
            self.async_write_ha_state()
            return

        # Try to get from billing results cache (coordinator has this cached)
        # Try to read from cache first
        now = datetime.now()
        year = now.year
        month = now.month
        billing_cache = coordinator_data.get("billing_results_cache", {})
        cache_key = f"monthly_other_items_{year}_{month}"
        cached_result = billing_cache.get(cache_key)

        if cached_result:
            # Use cached data
            cost_data = cached_result.get("cost_data")
            if cost_data and cost_data.get("value") is not None:
                self._attr_native_value = round_to_max_digits(cost_data.get("value", 0.0))
                currency = self.coordinator.get_setting("Currency") or ""
                self._attr_native_unit_of_measurement = currency
                self._current_year = cost_data.get("year")
                self._current_month = cost_data.get("month")
                self._item_count = cost_data.get("item_count")
                self._items = cost_data.get("items", [])
                self._attr_available = True
                self.async_write_ha_state()
                return

        # No cached data - set placeholder and defer async fetch until after startup
        self._attr_native_value = None
        currency = self.coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
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
        """Fetch current month's other items cost."""
        now = datetime.now()
        year = now.year
        month = now.month

        cost_data = await self.coordinator.billing_manager.get_monthly_other_items_cost(
            year=year,
            month=month,
        )

        # Always set currency unit to prevent statistics issues
        default_currency = self.coordinator.get_setting("Currency") or ""

        if cost_data:
            raw_value = cost_data.get("value")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            # Use unit from data, or fall back to default currency
            self._attr_native_unit_of_measurement = cost_data.get("unit") or default_currency
            self._current_year = cost_data.get("year")
            self._current_month = cost_data.get("month")
            self._item_count = cost_data.get("item_count")
            self._items = cost_data.get("items", [])
        else:
            self._attr_native_value = None
            # Always set unit even when no data to maintain consistency for statistics
            self._attr_native_unit_of_measurement = default_currency
            self._current_year = None
            self._current_month = None
            self._item_count = None
            self._items = []

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()



class EcoGuardTotalMonthlyCostSensor(EcoGuardBaseSensor):
    """Sensor for total monthly cost across all utilities.

    Always outputs the pure (pre-VAT) value. If VAT is detected in billing results,
    it is automatically removed from the API prices to ensure consistency.

    Can be configured as "metered" (only metered API data) or "estimated" (includes
    estimated HW costs when price data is missing).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        cost_type: str = "actual",
    ) -> None:
        """Initialize the total monthly cost sensor.

        Args:
            hass: Home Assistant instance
            coordinator: The coordinator instance
            cost_type: "actual" for metered API data, "estimated" for estimated costs
        """
        super().__init__(coordinator, hass=hass)
        self._hass = hass

        self._cost_type = cost_type  # "actual" (displayed as "Metered") or "estimated"

        # Use "Cost Monthly Aggregated" format with "All Utilities" suffix
        # This will be updated in async_added_to_hass with proper translations
        cost_monthly_aggregated = get_translation_default("name.cost_monthly_aggregated")
        all_utilities = get_translation_default("name.all_utilities")
        if cost_type == "estimated":
            estimated = get_translation_default("name.estimated")
            self._attr_name = f"{cost_monthly_aggregated} {estimated} - {all_utilities}"
            # Build unique_id following pattern: purpose_group_total_type
            # Home Assistant strips the domain prefix, so we want: cost_monthly_total_estimated
            self._attr_unique_id = (
                f"{DOMAIN}_cost_monthly_total_estimated"
            )
        else:
            metered = get_translation_default("name.metered")
            self._attr_name = f"{cost_monthly_aggregated} {metered} - {all_utilities}"
            # Build unique_id following pattern: purpose_group_total_type
            # Home Assistant strips the domain prefix, so we want: cost_monthly_total_metered
            self._attr_unique_id = (
                f"{DOMAIN}_cost_monthly_total_metered"
            )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        self._attr_device_info = self._get_device_info(coordinator.node_id)

        # Set state class
        self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        # Always set currency unit from the start to prevent statistics issues
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._current_year: int | None = None
        self._current_month: int | None = None
        self._utilities: list[str] = []

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "sensor_type": "total_monthly_cost",
        }

        if self._current_year is not None and self._current_month is not None:
            attrs["year"] = self._current_year
            attrs["month"] = self._current_month
            attrs["period"] = f"{self._current_year}-{self._current_month:02d}"

        if self._utilities:
            attrs["utilities"] = self._utilities

        attrs["cost_type"] = "metered" if self._cost_type == "actual" else self._cost_type

        return attrs

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            # Use "Cost Monthly Aggregated" format with "All Utilities" suffix
            cost_monthly_aggregated = await async_get_translation(self._hass, "name.cost_monthly_aggregated")
            all_utilities = await async_get_translation(self._hass, "name.all_utilities")
            if self._cost_type == "estimated":
                estimated = await async_get_translation(self._hass, "name.estimated")
                new_name = f"{cost_monthly_aggregated} {estimated} - {all_utilities}"
            else:
                metered = await async_get_translation(self._hass, "name.metered")
                new_name = f"{cost_monthly_aggregated} {metered} - {all_utilities}"
            await self._update_name_and_registry(new_name, log_level="debug")
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)


    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # This sensor sums costs from multiple utilities, which requires async operations
        # Try to read from monthly aggregate cache first
        coordinator_data = self.coordinator.data
        if coordinator_data:
            now = datetime.now()
            year = now.year
            month = now.month
            monthly_cache = coordinator_data.get("monthly_aggregate_cache", {})

            # Sum costs from all utilities
            total_cost = 0.0
            utilities_with_data = []
            utility_codes = set()
            active_installations = self.coordinator.get_active_installations()
            for installation in active_installations:
                registers = installation.get("Registers", [])
                for register in registers:
                    utility_code = register.get("UtilityCode")
                    if utility_code and utility_code in ("HW", "CW", "E", "HE"):
                        utility_codes.add(utility_code)

            for utility_code in sorted(utility_codes):
                if utility_code in ("CW", "HW"):
                    cache_key = f"{utility_code}_{year}_{month}_price_{self._cost_type}"
                    price_data = monthly_cache.get(cache_key)
                    if price_data and price_data.get("value") is not None:
                        cost = price_data.get("value", 0.0)
                        total_cost += cost
                        utilities_with_data.append(utility_code)

            if utilities_with_data:
                currency = self.coordinator.get_setting("Currency") or ""
                self._attr_native_value = round_to_max_digits(total_cost)
                self._attr_native_unit_of_measurement = currency
                self._attr_available = True
                self.async_write_ha_state()
                return

        # No cached data - set placeholder and defer async fetch until after startup
        self._attr_native_value = None
        currency = self.coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
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
        """Fetch current month's total cost by summing individual utility costs."""
        now = datetime.now()
        year = now.year
        month = now.month

        # Get all utility codes
        utility_codes = set()
        active_installations = self.coordinator.get_active_installations()
        for installation in active_installations:
            registers = installation.get("Registers", [])
            for register in registers:
                utility_code = register.get("UtilityCode")
                if utility_code and utility_code in ("HW", "CW", "E", "HE"):
                    utility_codes.add(utility_code)

        # Sum costs from individual utilities
        total_cost = 0.0
        utilities_with_data = []

        for utility_code in sorted(utility_codes):
            if utility_code in ("CW", "HW"):
                price_data = await self.coordinator.get_monthly_aggregate(
                    utility_code=utility_code,
                    year=year,
                    month=month,
                    aggregate_type="price",
                    cost_type=self._cost_type,
                )

                if price_data and price_data.get("value") is not None:
                    cost = price_data.get("value", 0.0)
                    total_cost += cost
                    utilities_with_data.append(utility_code)

        # Always set currency unit to prevent statistics issues
        currency = self.coordinator.get_setting("Currency") or ""

        if utilities_with_data:
            raw_value = total_cost

            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            self._attr_native_unit_of_measurement = currency
            self._current_year = year
            self._current_month = month
            self._utilities = utilities_with_data
        else:
            self._attr_native_value = None
            # Always set unit even when no data to maintain consistency for statistics
            self._attr_native_unit_of_measurement = currency
            self._current_year = None
            self._current_month = None
            self._utilities = []

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()



class EcoGuardEndOfMonthEstimateSensor(EcoGuardBaseSensor):
    """Sensor for end-of-month bill estimate.

    Estimates the total bill for the current month based on mean daily consumption
    and price so far, projected to the end of the month.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
    ) -> None:
        """Initialize the end-of-month estimate sensor."""
        super().__init__(coordinator, hass=hass)
        self._hass = hass

        # Use "Cost Monthly Estimated Final Settlement" format to ensure entity_id starts with "cost_monthly_estimated_final_settlement_"
        # This will be updated in async_added_to_hass with proper translations
        self._attr_name = get_translation_default("name.cost_monthly_estimated_final_settlement")
        # Build unique_id following pattern: purpose_group_sensor
        # Home Assistant strips the domain prefix, so we want: cost_monthly_estimated_final_settlement
        self._attr_unique_id = (
            f"{DOMAIN}_cost_monthly_estimated_final_settlement"
        )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        self._attr_device_info = self._get_device_info(coordinator.node_id)

        # Set state class
        self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        # Always set currency unit from the start to prevent statistics issues
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._current_year: int | None = None
        self._current_month: int | None = None
        self._days_elapsed_calendar: int | None = None
        self._days_with_data: int | None = None
        self._fetch_task: asyncio.Task | None = None  # Track pending fetch task to prevent duplicates
        self._days_remaining: int | None = None
        self._total_days_in_month: int | None = None
        self._latest_data_timestamp: int | None = None
        self._hw_consumption_estimate: float | None = None
        self._hw_price_estimate: float | None = None
        self._cw_consumption_estimate: float | None = None
        self._cw_price_estimate: float | None = None
        self._other_items_cost: float | None = None
        self._hw_mean_daily_consumption: float | None = None
        self._hw_mean_daily_price: float | None = None
        self._cw_mean_daily_consumption: float | None = None
        self._cw_mean_daily_price: float | None = None
        self._hw_consumption_so_far: float | None = None
        self._hw_price_so_far: float | None = None
        self._cw_consumption_so_far: float | None = None
        self._cw_price_so_far: float | None = None
        self._hw_price_is_estimated: bool = False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "sensor_type": "end_of_month_estimate",
        }

        if self._current_year is not None and self._current_month is not None:
            attrs["year"] = self._current_year
            attrs["month"] = self._current_month
            attrs["period"] = f"{self._current_year}-{self._current_month:02d}"

        if self._days_elapsed_calendar is not None:
            attrs["days_elapsed_calendar"] = self._days_elapsed_calendar
        if self._days_with_data is not None:
            attrs["days_with_data"] = self._days_with_data
        if self._days_remaining is not None:
            attrs["days_remaining"] = self._days_remaining
        if self._total_days_in_month is not None:
            attrs["total_days_in_month"] = self._total_days_in_month
        if self._latest_data_timestamp is not None:
            attrs["latest_data_timestamp"] = self._latest_data_timestamp
            # Also add a human-readable date
            try:
                from datetime import datetime
                latest_date = datetime.fromtimestamp(self._latest_data_timestamp)
                attrs["latest_data_date"] = latest_date.isoformat()
            except Exception:
                pass

        if self._hw_consumption_estimate is not None:
            attrs["hw_consumption_estimate"] = round_to_max_digits(self._hw_consumption_estimate)
        if self._hw_price_estimate is not None:
            attrs["hw_price_estimate"] = round_to_max_digits(self._hw_price_estimate)
        if self._cw_consumption_estimate is not None:
            attrs["cw_consumption_estimate"] = round_to_max_digits(self._cw_consumption_estimate)
        if self._cw_price_estimate is not None:
            attrs["cw_price_estimate"] = round_to_max_digits(self._cw_price_estimate)
        if self._other_items_cost is not None:
            attrs["other_items_cost"] = round_to_max_digits(self._other_items_cost)

        if self._hw_mean_daily_consumption is not None:
            attrs["hw_mean_daily_consumption"] = round_to_max_digits(self._hw_mean_daily_consumption)
        if self._hw_mean_daily_price is not None:
            attrs["hw_mean_daily_price"] = round_to_max_digits(self._hw_mean_daily_price)
        if self._cw_mean_daily_consumption is not None:
            attrs["cw_mean_daily_consumption"] = round_to_max_digits(self._cw_mean_daily_consumption)
        if self._cw_mean_daily_price is not None:
            attrs["cw_mean_daily_price"] = round_to_max_digits(self._cw_mean_daily_price)

        if self._hw_consumption_so_far is not None:
            attrs["hw_consumption_so_far"] = round_to_max_digits(self._hw_consumption_so_far)
        if self._hw_price_so_far is not None:
            attrs["hw_price_so_far"] = round_to_max_digits(self._hw_price_so_far)
        if self._cw_consumption_so_far is not None:
            attrs["cw_consumption_so_far"] = round_to_max_digits(self._cw_consumption_so_far)
        if self._cw_price_so_far is not None:
            attrs["cw_price_so_far"] = round_to_max_digits(self._cw_price_so_far)

        attrs["hw_price_is_estimated"] = self._hw_price_is_estimated

        return attrs

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            # Keep "Cost Monthly Estimated Final Settlement" format to maintain entity_id starting with "cost_monthly_estimated_final_settlement"
            new_name = await async_get_translation(self._hass, "name.cost_monthly_estimated_final_settlement")
            await self._update_name_and_registry(new_name, log_level="debug")
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)


    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # This sensor calculates end-of-month estimate, which requires async operations
        from homeassistant.core import CoreState
        is_starting = self.hass.state == CoreState.starting

        # Try to read from cache if available (coordinator may cache this)
        # For now, just set placeholder and defer async fetch until after startup
        self._attr_native_value = None
        currency = self.coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._attr_available = True
        self.async_write_ha_state()

        # Trigger async fetch (with delay during startup to avoid blocking)
        # Only create a new task if one isn't already pending
        if self.hass and not self.hass.is_stopping:
            # Check if there's already a pending fetch task
            if self._fetch_task is not None and not self._fetch_task.done():
                _LOGGER.debug("Skipping duplicate fetch task for %s (task already pending)", self.entity_id)
                return

            if is_starting:
                # Delay during startup to avoid blocking
                async def delayed_fetch():
                    await asyncio.sleep(5)  # Wait 5 seconds after startup
                    await self._async_fetch_value()
                    self._fetch_task = None  # Clear task reference when done
                self._fetch_task = self.hass.async_create_task(delayed_fetch())
            else:
                async def fetch_and_clear():
                    await self._async_fetch_value()
                    self._fetch_task = None  # Clear task reference when done
                self._fetch_task = self.hass.async_create_task(fetch_and_clear())

    async def _async_fetch_value(self) -> None:
        """Fetch end-of-month estimate asynchronously."""
        _LOGGER.info("Starting async fetch for sensor.cost_monthly_estimated_final_settlement")
        try:
            _LOGGER.debug("Calling coordinator.get_end_of_month_estimate()")
            estimate_data = await self.coordinator.get_end_of_month_estimate()
            _LOGGER.debug("coordinator.get_end_of_month_estimate() returned: %s", "None" if estimate_data is None else f"dict with {len(estimate_data)} keys")
        except Exception as err:
            _LOGGER.error("Exception in get_end_of_month_estimate for sensor.cost_monthly_estimated_final_settlement: %s", err, exc_info=True)
            estimate_data = None

        # Always set currency unit to prevent statistics issues
        default_currency = self.coordinator.get_setting("Currency") or ""

        if estimate_data:
            _LOGGER.info(
                "Updated sensor.cost_monthly_estimated_final_settlement: %.2f %s (HW: %.2f, CW: %.2f, Other: %.2f)",
                estimate_data.get("total_bill_estimate", 0),
                estimate_data.get("currency", default_currency),
                estimate_data.get("hw_price_estimate", 0),
                estimate_data.get("cw_price_estimate", 0),
                estimate_data.get("other_items_cost", 0),
            )
            raw_value = estimate_data.get("total_bill_estimate")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            # Use currency from data, or fall back to default
            self._attr_native_unit_of_measurement = estimate_data.get("currency") or default_currency

            self._current_year = estimate_data.get("year")
            self._current_month = estimate_data.get("month")
            self._days_elapsed_calendar = estimate_data.get("days_elapsed_calendar")
            self._days_with_data = estimate_data.get("days_with_data")
            self._days_remaining = estimate_data.get("days_remaining")
            self._total_days_in_month = estimate_data.get("total_days_in_month")
            self._latest_data_timestamp = estimate_data.get("latest_data_timestamp")
            self._hw_consumption_estimate = estimate_data.get("hw_consumption_estimate")
            self._hw_price_estimate = estimate_data.get("hw_price_estimate")
            self._cw_consumption_estimate = estimate_data.get("cw_consumption_estimate")
            self._cw_price_estimate = estimate_data.get("cw_price_estimate")
            self._other_items_cost = estimate_data.get("other_items_cost")
            self._hw_mean_daily_consumption = estimate_data.get("hw_mean_daily_consumption")
            self._hw_mean_daily_price = estimate_data.get("hw_mean_daily_price")
            self._cw_mean_daily_consumption = estimate_data.get("cw_mean_daily_consumption")
            self._cw_mean_daily_price = estimate_data.get("cw_mean_daily_price")
            self._hw_consumption_so_far = estimate_data.get("hw_consumption_so_far")
            self._hw_price_so_far = estimate_data.get("hw_price_so_far")
            self._cw_consumption_so_far = estimate_data.get("cw_consumption_so_far")
            self._cw_price_so_far = estimate_data.get("cw_price_so_far")
            self._hw_price_is_estimated = estimate_data.get("hw_price_is_estimated", False)
        else:
            self._attr_native_value = None
            # Always set unit even when no data to maintain consistency for statistics
            self._attr_native_unit_of_measurement = default_currency
            self._current_year = None
            self._current_month = None
            self._days_elapsed_calendar = None
            self._days_with_data = None
            self._days_remaining = None
            self._total_days_in_month = None
            self._latest_data_timestamp = None
            self._hw_consumption_estimate = None
            self._hw_price_estimate = None
            self._cw_consumption_estimate = None
            self._cw_price_estimate = None
            self._other_items_cost = None
            self._hw_mean_daily_consumption = None
            self._hw_mean_daily_price = None
            self._cw_mean_daily_consumption = None
            self._cw_mean_daily_price = None
            self._hw_consumption_so_far = None
            self._hw_price_so_far = None
            self._cw_consumption_so_far = None
            self._cw_price_so_far = None
            self._hw_price_is_estimated = False
            _LOGGER.debug(
                "No estimate data available for sensor.cost_monthly_estimated_final_settlement (get_end_of_month_estimate returned None)"
            )

        self.async_write_ha_state()



