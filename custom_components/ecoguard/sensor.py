"""Sensor platform for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import logging
import asyncio
import zoneinfo

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EcoGuardDataUpdateCoordinator
from .helpers import round_to_max_digits
from .translations import (
    clear_translation_cache,
    async_get_translation,
    get_translation_default,
)
from .entity_registry_updater import (
    get_entity_id_by_unique_id,
    update_entity_registry_with_timeout,
)

_LOGGER = logging.getLogger(__name__)

# Counter for staggering sensor data fetches to avoid overwhelming the API
_sensor_fetch_counter = 0
_sensor_fetch_lock = asyncio.Lock()






async def _async_update_entity_registry_name(
    sensor: SensorEntity, new_name: str
) -> None:
    """Update the entity registry name for a sensor.

    This helper function centralizes the logic for updating entity registry names
    to reduce code duplication across sensor classes.

    Args:
        sensor: The sensor entity instance
        new_name: The new name to set in the entity registry
    """
    if not hasattr(sensor, '_attr_unique_id') or not sensor._attr_unique_id:
        return

    if not hasattr(sensor, 'hass') or not sensor.hass:
        return

    try:
        entity_registry = async_get_entity_registry(sensor.hass)
        # Try to get entity_id by unique_id
        entity_id = get_entity_id_by_unique_id(entity_registry, sensor._attr_unique_id)
        if entity_id:
            entity_entry = entity_registry.async_get(entity_id)
            if entity_entry and entity_entry.name != new_name:
                entity_registry.async_update_entity(entity_id, name=new_name)
                _LOGGER.debug("Updated entity registry name for %s to '%s'", entity_id, new_name)
    except Exception as e:
        _LOGGER.debug("Failed to update entity registry name: %s", e)


def _slugify_name(name: str | None) -> str:
    """Convert a name to a slug format suitable for entity IDs.

    Args:
        name: The name to slugify

    Returns:
        Slugified name (lowercase, spaces to underscores, special chars removed)
    """
    if not name:
        return ""

    # Convert to lowercase and replace spaces with underscores
    slug = name.lower().strip()
    # Replace spaces and common separators with underscores
    slug = slug.replace(" ", "_").replace("-", "_").replace(".", "_")
    # Remove special characters, keep only alphanumeric and underscores
    slug = "".join(c if c.isalnum() or c == "_" else "" for c in slug)
    # Remove multiple consecutive underscores
    while "__" in slug:
        slug = slug.replace("__", "_")
    # Remove leading/trailing underscores
    slug = slug.strip("_")

    return slug


def _utility_code_to_slug(utility_code: str) -> str:
    """Convert utility code to slug format.

    Args:
        utility_code: Utility code (e.g., "CW", "HW")

    Returns:
        Slugified utility name (e.g., "cold_water", "hot_water")
    """
    utility_map = {
        "CW": "cold_water",
        "HW": "hot_water",
        "E": "electricity",
        "HE": "heat",
    }
    return utility_map.get(utility_code.upper(), utility_code.lower())


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EcoGuard sensors from a config entry."""
    # Clear translation cache to ensure fresh translations are loaded
    clear_translation_cache()

    # Reset sensor fetch counter for this setup
    global _sensor_fetch_counter
    _sensor_fetch_counter = 0

    # Use ConfigEntry.runtime_data (recommended pattern)
    from . import EcoGuardRuntimeData

    runtime_data: EcoGuardRuntimeData = entry.runtime_data
    coordinator: EcoGuardDataUpdateCoordinator = runtime_data.coordinator
    latest_reception_coordinator = runtime_data.latest_reception_coordinator

    # IMPORTANT: We need to wait for coordinator to load cached data before creating sensors
    # Otherwise get_active_installations() will return empty list
    # The coordinator loads from cache synchronously in _async_update_data, so we need to trigger it
    try:
        # Trigger coordinator refresh to load cached data (this is fast, just loads from cache)
        await coordinator.async_config_entry_first_refresh()
        if latest_reception_coordinator:
            await latest_reception_coordinator.async_config_entry_first_refresh()
    except Exception as err:
        _LOGGER.error("Failed to refresh coordinator data: %s", err)

    # Create sensors for each active installation
    sensors: list[SensorEntity] = []
    active_installations = coordinator.get_active_installations()
    _LOGGER.info("Found %d active installations for sensor creation (coordinator data available: %s)",
                 len(active_installations), coordinator.data is not None)
    if not active_installations:
        _LOGGER.warning("No active installations found! Coordinator data: %s, installations: %s",
                       coordinator.data is not None,
                       len(coordinator._installations) if hasattr(coordinator, '_installations') else 0)

    # Track unique utility codes across all installations
    utility_codes = set()

    # Track measuring points for which we've already created latest reception sensors
    measuring_points_with_reception_sensor = set()

    for installation in active_installations:
        measuring_point_id = installation.get("MeasuringPointID")
        registers = installation.get("Registers", [])

        # Get measuring point name for better sensor naming
        measuring_point_name = None
        for mp in coordinator.get_measuring_points():
            if mp.get("ID") == measuring_point_id:
                measuring_point_name = mp.get("Name")
                break

        # Create a latest reception sensor for each meter (only once per measuring point)
        # Find the primary utility code for this measuring point
        primary_utility_code = None
        for register in registers:
            utility_code = register.get("UtilityCode")
            if utility_code:
                primary_utility_code = utility_code
                break  # Use the first utility found

        if measuring_point_id not in measuring_points_with_reception_sensor and latest_reception_coordinator:
            latest_reception_sensor = EcoGuardLatestReceptionSensor(
                hass=hass,
                coordinator=latest_reception_coordinator,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                utility_code=primary_utility_code,
            )
            sensors.append(latest_reception_sensor)
            measuring_points_with_reception_sensor.add(measuring_point_id)

        # Create a sensor for each register (utility) in the installation
        for register in registers:
            utility_code = register.get("UtilityCode")
            if not utility_code:
                continue

            utility_codes.add(utility_code)

            # Create a daily consumption sensor for each meter
            daily_sensor = EcoGuardDailyConsumptionSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
            )
            sensors.append(daily_sensor)

            # Create daily cost sensors for each meter: metered and estimated
            daily_cost_metered_sensor = EcoGuardDailyCostSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                cost_type="actual",
            )
            sensors.append(daily_cost_metered_sensor)

            daily_cost_estimated_sensor = EcoGuardDailyCostSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                cost_type="estimated",
            )
            sensors.append(daily_cost_estimated_sensor)

    # Create daily consumption aggregate sensors for each utility (CW, HW)
    for utility_code in utility_codes:
        if utility_code in ("CW", "HW"):
            daily_aggregate_sensor = EcoGuardDailyConsumptionAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
            )
            sensors.append(daily_aggregate_sensor)

            # Create daily cost aggregate sensors for each utility: metered and estimated
            daily_cost_metered_aggregate_sensor = EcoGuardDailyCostAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                cost_type="actual",
            )
            sensors.append(daily_cost_metered_aggregate_sensor)

            daily_cost_estimated_aggregate_sensor = EcoGuardDailyCostAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                cost_type="estimated",
            )
            sensors.append(daily_cost_estimated_aggregate_sensor)

    # Create daily combined water sensors if both HW and CW exist
    if "CW" in utility_codes and "HW" in utility_codes:
        daily_combined_water_sensor = EcoGuardDailyCombinedWaterSensor(
            hass=hass,
            coordinator=coordinator,
        )
        sensors.append(daily_combined_water_sensor)

        # Create daily combined water cost sensors: metered and estimated
        daily_combined_water_cost_metered_sensor = EcoGuardDailyCombinedWaterCostSensor(
            hass=hass,
            coordinator=coordinator,
            cost_type="actual",
        )
        sensors.append(daily_combined_water_cost_metered_sensor)

        daily_combined_water_cost_estimated_sensor = EcoGuardDailyCombinedWaterCostSensor(
            hass=hass,
            coordinator=coordinator,
            cost_type="estimated",
        )
        sensors.append(daily_combined_water_cost_estimated_sensor)

    # Create monthly aggregate sensors for each utility (CW, HW)
    for utility_code in utility_codes:
        if utility_code in ("CW", "HW"):
            # Monthly consumption sensor
            monthly_con_sensor = EcoGuardMonthlyAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                aggregate_type="con",
            )
            sensors.append(monthly_con_sensor)

            # Monthly cost sensors: metered and estimated
            monthly_cost_metered_sensor = EcoGuardMonthlyAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                aggregate_type="price",
                cost_type="actual",  # Internal: "actual", Display: "Metered"
            )
            sensors.append(monthly_cost_metered_sensor)

            monthly_cost_estimated_sensor = EcoGuardMonthlyAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                aggregate_type="price",
                cost_type="estimated",
            )
            sensors.append(monthly_cost_estimated_sensor)

    # Create monthly sensors per meter (consumption and cost)
    for installation in active_installations:
        measuring_point_id = installation.get("MeasuringPointID")
        registers = installation.get("Registers", [])

        # Get measuring point name
        measuring_point_name = None
        for mp in coordinator.get_measuring_points():
            if mp.get("ID") == measuring_point_id:
                measuring_point_name = mp.get("Name")
                break

        for register in registers:
            utility_code = register.get("UtilityCode")
            if not utility_code or utility_code not in ("CW", "HW"):
                continue

            # Monthly consumption sensor per meter
            monthly_con_meter_sensor = EcoGuardMonthlyMeterSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                aggregate_type="con",
            )
            sensors.append(monthly_con_meter_sensor)
            _LOGGER.debug(
                "Created monthly consumption sensor for meter %d (%s): unique_id=%s",
                measuring_point_id,
                measuring_point_name or f"mp{measuring_point_id}",
                monthly_con_meter_sensor._attr_unique_id,
            )

            # Monthly cost sensors per meter: metered and estimated
            # Note: aggregate_type="price" matches API terminology (API uses "[price]" in utility codes),
            # but sensor names use "cost" terminology for user-facing display
            monthly_cost_metered_meter_sensor = EcoGuardMonthlyMeterSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                aggregate_type="price",
                cost_type="actual",
            )
            sensors.append(monthly_cost_metered_meter_sensor)

            monthly_cost_estimated_meter_sensor = EcoGuardMonthlyMeterSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                aggregate_type="price",
                cost_type="estimated",
            )
            sensors.append(monthly_cost_estimated_meter_sensor)

    # Create combined water sensors (HW + CW) if both utilities exist
    if "CW" in utility_codes and "HW" in utility_codes:
        # Combined water consumption sensor
        combined_water_con_sensor = EcoGuardCombinedWaterSensor(
            hass=hass,
            coordinator=coordinator,
            aggregate_type="con",
        )
        sensors.append(combined_water_con_sensor)

        # Combined water cost sensors: metered and estimated
        combined_water_cost_metered_sensor = EcoGuardCombinedWaterSensor(
            hass=hass,
            coordinator=coordinator,
            aggregate_type="price",
            cost_type="actual",
        )
        sensors.append(combined_water_cost_metered_sensor)

        combined_water_cost_estimated_sensor = EcoGuardCombinedWaterSensor(
            hass=hass,
            coordinator=coordinator,
            aggregate_type="price",
            cost_type="estimated",
        )
        sensors.append(combined_water_cost_estimated_sensor)

    # Create other items (general fees) sensor
    other_items_sensor = EcoGuardOtherItemsSensor(hass=hass, coordinator=coordinator)
    sensors.append(other_items_sensor)

    # End-of-month estimate sensor
    end_of_month_estimate_sensor = EcoGuardEndOfMonthEstimateSensor(hass=hass, coordinator=coordinator)
    sensors.append(end_of_month_estimate_sensor)

    # Create total monthly cost sensors (metered and estimated)
    # These sum the individual utility costs
    metered_cost_sensor = EcoGuardTotalMonthlyCostSensor(
        hass=hass,
        coordinator=coordinator,
        cost_type="actual",  # Internal: "actual", Display: "Metered"
    )
    sensors.append(metered_cost_sensor)

    estimated_cost_sensor = EcoGuardTotalMonthlyCostSensor(
        hass=hass,
        coordinator=coordinator,
        cost_type="estimated",
    )
    sensors.append(estimated_cost_sensor)

    # Check which entities already exist BEFORE adding new ones
    # This allows us to only disable newly created entities, not existing ones
    entity_registry = async_get_entity_registry(hass)
    existing_unique_ids = set()
    for entity_entry in entity_registry.entities.values():
        if entity_entry.platform == DOMAIN and entity_entry.unique_id:
            existing_unique_ids.add(entity_entry.unique_id)

    _LOGGER.info("Creating %d EcoGuard sensors", len(sensors))
    if len(sensors) < 10:
        _LOGGER.warning("Only %d sensors created, expected many more! Sensor types: %s",
                       len(sensors),
                       [type(s).__name__ for s in sensors[:10]])

    # Log sensor details for debugging
    for i, sensor in enumerate(sensors[:5]):  # Log first 5 sensors
        _LOGGER.debug("Sensor %d: %s (unique_id: %s)", i+1, type(sensor).__name__,
                     getattr(sensor, '_attr_unique_id', 'N/A'))

    async_add_entities(sensors, update_before_add=False)

    # Schedule deferred updates after HA is fully started
    # This prevents API calls during startup
    # DISABLED: Deferred sensor updates - no API calls during startup
    # Sensors will show as "Unknown" state initially
    # async def _deferred_sensor_updates():
    #     """Defer sensor updates until after HA is fully started."""
    #     ...
    # hass.async_create_task(_deferred_sensor_updates())

    # Schedule entity registry updates to run after entities are registered
    # This ensures entity_ids match our desired format and individual meter sensors are disabled
    # Define which sensor classes are individual meter sensors (should be disabled by default)
    individual_meter_sensor_classes = (
        EcoGuardDailyConsumptionSensor,
        EcoGuardDailyCostSensor,
        EcoGuardMonthlyMeterSensor,
        EcoGuardLatestReceptionSensor,
    )

    update_task = hass.async_create_task(
        update_entity_registry_with_timeout(
            hass,
            sensors,
            existing_unique_ids,
            individual_meter_sensor_classes,
        )
    )
    # Store update task in runtime_data
    if hasattr(entry, "runtime_data"):
        from . import EcoGuardRuntimeData
        runtime_data: EcoGuardRuntimeData = entry.runtime_data
        runtime_data.entity_registry_update_task = update_task


class EcoGuardSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Base class for EcoGuard sensors."""

    def __init__(
        self,
        coordinator: EcoGuardDataUpdateCoordinator,
        unique_id_suffix: str,
        name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.node_id}_{unique_id_suffix}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": f"EcoGuard Node {coordinator.node_id}",
            "manufacturer": "EcoGuard",
        }


class EcoGuardDailyConsumptionSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Sensor for last known daily consumption for a specific meter."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        installation: dict[str, Any],
        utility_code: str,
        measuring_point_id: int,
        measuring_point_name: str | None,
    ) -> None:
        """Initialize the daily consumption sensor."""
        super().__init__(coordinator)
        self._hass = hass
        self._installation = installation
        self._utility_code = utility_code
        self._measuring_point_id = measuring_point_id
        self._measuring_point_name = measuring_point_name

        # Build sensor name - meter name first, then utility type
        # This groups sensors by meter, which is important when there are multiple meters
        # for the same utility type (e.g., multiple hot water meters in different rooms)
        if measuring_point_name:
            measuring_point_display = measuring_point_name
        else:
            measuring_point_display = get_translation_default("name.measuring_point", id=measuring_point_id)

        # Use English defaults here; will be updated in async_added_to_hass with proper translations
        utility_name = get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Consumption Daily - Meter "Measuring Point" (Utility)"
        # This ensures entity_id starts with "consumption_daily_" when slugified
        # The name will be updated in async_added_to_hass with proper translations
        # but the entity_id is already generated from this initial name
        consumption_daily = get_translation_default("name.consumption_daily")
        meter = get_translation_default("name.meter")
        self._attr_name = f'{consumption_daily} - {meter} "{measuring_point_display}" ({utility_name})'

        # Build unique_id following pattern: purpose_group_utility_sensor
        # Home Assistant strips the domain prefix, so we want: consumption_daily_metered_cold_water_kaldtvann_bad
        # Use measuring_point_id to ensure uniqueness across nodes
        utility_slug = _utility_code_to_slug(utility_code)
        sensor_name = _slugify_name(measuring_point_name) or f"mp{measuring_point_id}"
        unique_id = (
            f"{DOMAIN}_consumption_daily_metered_{utility_slug}_{sensor_name}"
        )
        self._attr_unique_id = unique_id
        _LOGGER.debug("Daily consumption sensor unique_id: %s", unique_id)

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
            "model": installation.get("DeviceTypeDisplay", "Unknown"),
        }

        # Disable individual meter sensors by default (users can enable if needed)
        self._attr_entity_registry_enabled_default = False

        # Set state class and unit (will be updated when we get data)
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = None
        self._last_data_date: datetime | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "measuring_point_id": self._measuring_point_id,
            "utility_code": self._utility_code,
            "external_key": self._installation.get("ExternalKey"),
            "device_type": self._installation.get("DeviceTypeDisplay"),
            "sensor_type": "daily_consumption",
        }

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()
            attrs["last_data_date_readable"] = self._last_data_date.strftime("%Y-%m-%d")

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            lang = getattr(self._hass.config, 'language', 'en')
            _LOGGER.debug("Updating sensor name for lang=%s", lang)

            # Rebuild sensor name with translations
            if self._measuring_point_name:
                measuring_point_display = self._measuring_point_name
            else:
                measuring_point_display = await async_get_translation(
                    self._hass, "name.measuring_point", id=self._measuring_point_id
                )
                _LOGGER.debug("Measuring point display: %s", measuring_point_display)

            utility_name = await async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":  # Fallback if not found
                utility_name = self._utility_code
            _LOGGER.debug("Utility name: %s", utility_name)

            consumption_daily = await async_get_translation(self._hass, "name.consumption_daily")
            _LOGGER.debug("Consumption daily: %s", consumption_daily)

            # Update the name (this is the display name, not the entity_id)
            # Format: "Consumption Daily - Meter "Measuring Point" (Utility)"
            # Keep "Consumption Daily" format to maintain entity_id starting with "consumption_daily_"
            meter = await async_get_translation(self._hass, "name.meter")
            new_name = f'{consumption_daily} - {meter} "{measuring_point_display}" ({utility_name})'
            if self._attr_name != new_name:
                old_name = self._attr_name
                self._attr_name = new_name
                self.async_write_ha_state()
                _LOGGER.info("Updated sensor name from '%s' to '%s' (lang=%s)", old_name, new_name, lang)

            # Always update the entity registry name so it shows correctly in modals
            await _async_update_entity_registry_name(self, new_name)

            # Also update device name
            device_name = await async_get_translation(
                self._hass, "name.device_name", node_id=self.coordinator.node_id
            )
            if self._attr_device_info.get("name") != device_name:
                self._attr_device_info["name"] = device_name
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.warning("Failed to update translated name: %s", e, exc_info=True)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.info("Coordinator update received for %s (data available: %s)",
                     self.entity_id, self.coordinator.data is not None)
        self._update_from_coordinator_data()

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.debug("No coordinator data for %s", self.entity_id)
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = None
            self._last_data_date = None
            self._attr_available = True
            self.async_write_ha_state()
            return

        # Get consumption cache from coordinator data
        consumption_cache = coordinator_data.get("latest_consumption_cache", {})

        # Build cache key
        if self._measuring_point_id:
            cache_key = f"{self._utility_code}_{self._measuring_point_id}"
        else:
            cache_key = f"{self._utility_code}_all"

        consumption_data = consumption_cache.get(cache_key)

        if consumption_data:
            raw_value = consumption_data.get("value")
            new_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            old_value = self._attr_native_value

            self._attr_native_value = new_value
            self._attr_native_unit_of_measurement = consumption_data.get("unit")

            # Update last data date
            time_stamp = consumption_data.get("time")
            if time_stamp:
                self._last_data_date = datetime.fromtimestamp(time_stamp)

            # Mark sensor as available when we have data
            self._attr_available = True

            # Log update for debugging
            if old_value != new_value:
                _LOGGER.info("Updated %s: %s -> %s %s (cache key: %s)",
                             self.entity_id, old_value, new_value,
                             self._attr_native_unit_of_measurement, cache_key)
        else:
            # Log missing cache key for debugging (only once to avoid spam)
            if not hasattr(self, '_cache_miss_logged') or not self._cache_miss_logged:
                available_keys = sorted(consumption_cache.keys())
                _LOGGER.warning("Cache miss for %s: Looking for key '%s', available keys: %s",
                             self.entity_id, cache_key, available_keys[:10] if len(available_keys) > 10 else available_keys)
                self._cache_miss_logged = True

            # Keep sensor available even if no data (shows as "unknown" not "unavailable")
            # This allows sensors to appear in UI immediately
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = None
            self._last_data_date = None
            self._attr_available = True  # Keep available, just show None/unknown

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()


class EcoGuardLatestReceptionSensor(CoordinatorEntity, SensorEntity):
    """Sensor for last update timestamp for a specific meter."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        measuring_point_id: int,
        measuring_point_name: str | None,
        utility_code: str | None = None,
    ) -> None:
        """Initialize the latest reception sensor."""
        super().__init__(coordinator)
        self._hass = hass
        self._measuring_point_id = measuring_point_id
        self._measuring_point_name = measuring_point_name
        self._utility_code = utility_code

        # Build sensor name
        if measuring_point_name:
            measuring_point_display = measuring_point_name
        else:
            measuring_point_display = get_translation_default("name.measuring_point", id=measuring_point_id)

        # Use English defaults here; will be updated in async_added_to_hass
        utility_suffix = ""
        if utility_code:
            utility_name = get_translation_default(f"utility.{utility_code.lower()}")
            if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
                utility_name = utility_code
            utility_suffix = f" ({utility_name})"

        # Format: "Reception Last Update - Meter "Measuring Point" (Utility)"
        # This ensures entity_id starts with "reception_last_update_" when slugified
        # This will be updated in async_added_to_hass with proper translations
        reception_last_update = get_translation_default("name.reception_last_update")
        meter = get_translation_default("name.meter")
        if utility_suffix:
            # utility_suffix already includes parentheses, so we need to extract just the utility name
            utility_name = get_translation_default(f"utility.{utility_code.lower()}")
            if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
                utility_name = utility_code
            self._attr_name = f'{reception_last_update} - {meter} "{measuring_point_display}" ({utility_name})'
        else:
            self._attr_name = f'{reception_last_update} - {meter} "{measuring_point_display}"'

        # Build unique_id following pattern: purpose_group_utility_sensor
        # Home Assistant strips the domain prefix, so we want: reception_last_update_cold_water_kaldtvann_bad
        sensor_name = _slugify_name(measuring_point_name) or f"mp{measuring_point_id}"
        if utility_code:
            utility_slug = _utility_code_to_slug(utility_code)
            self._attr_unique_id = (
                f"{DOMAIN}_reception_last_update_{utility_slug}_{sensor_name}"
            )
        else:
            self._attr_unique_id = (
                f"{DOMAIN}_reception_last_update_{sensor_name}"
            )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

        # Disable individual meter sensors by default (users can enable if needed)
        self._attr_entity_registry_enabled_default = False

        # Set device class to timestamp
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_native_value = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "measuring_point_id": self._measuring_point_id,
        }

        if self._measuring_point_name:
            attrs["measuring_point_name"] = self._measuring_point_name

        if self._utility_code:
            attrs["utility_code"] = self._utility_code

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

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

            # Keep "Reception Last Update" format to maintain entity_id starting with "reception_last_update_"
            # Format: "Reception Last Update - Meter "Measuring Point" (Utility)"
            # This groups similar sensors together when sorted alphabetically
            reception_last_update = await async_get_translation(self._hass, "name.reception_last_update")
            meter = await async_get_translation(self._hass, "name.meter")
            if self._utility_code:
                utility_name = await async_get_translation(
                    self._hass, f"utility.{self._utility_code.lower()}"
                )
                if utility_name == f"utility.{self._utility_code.lower()}":  # Fallback if not found
                    utility_name = self._utility_code
                new_name = f'{reception_last_update} - {meter} "{measuring_point_display}" ({utility_name})'
            else:
                new_name = f'{reception_last_update} - {meter} "{measuring_point_display}"'
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.info("Coordinator update received for %s (data available: %s)",
                     self.entity_id, self.coordinator.data is not None)
        self._update_from_coordinator_data()

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        # The coordinator data is a list of reception entries
        latest_reception_data = self.coordinator.data

        if not latest_reception_data:
            self._attr_native_value = None
            self._attr_available = True
            self.async_write_ha_state()
            return

        # Find the latest reception entry for this measuring point
        # PositionID in latest_reception corresponds to MeasuringPointID
        latest_timestamp = None
        for reception in latest_reception_data:
            position_id = reception.get("PositionID")
            if position_id == self._measuring_point_id:
                latest_timestamp = reception.get("LatestReception")
                if latest_timestamp:
                    # Convert Unix timestamp to timezone-aware datetime (UTC)
                    # Unix timestamps are always in UTC
                    self._attr_native_value = datetime.fromtimestamp(latest_timestamp, tz=timezone.utc)
                else:
                    self._attr_native_value = None
                break

        if latest_timestamp is None:
            # No reception data found for this measuring point
            self._attr_native_value = None

        self._attr_available = True
        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()

    async def _async_fetch_value(self) -> None:
        """Fetch latest reception timestamp for this measuring point."""
        # The coordinator data is a list of reception entries
        latest_reception_data = self.coordinator.data

        if not latest_reception_data:
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        # Find the latest reception entry for this measuring point
        # PositionID in latest_reception corresponds to MeasuringPointID
        latest_timestamp = None
        for reception in latest_reception_data:
            position_id = reception.get("PositionID")
            if position_id == self._measuring_point_id:
                latest_timestamp = reception.get("LatestReception")
                if latest_timestamp:
                    # Convert Unix timestamp to timezone-aware datetime (UTC)
                    # Unix timestamps are always in UTC
                    self._attr_native_value = datetime.fromtimestamp(latest_timestamp, tz=timezone.utc)
                else:
                    self._attr_native_value = None
                break

        if latest_timestamp is None:
            # No reception data found for this measuring point
            self._attr_native_value = None

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()


class EcoGuardMonthlyAggregateSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
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
        super().__init__(coordinator)
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
        utility_slug = _utility_code_to_slug(utility_code)
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

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            utility_name = await async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

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
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.info("Coordinator update received for %s (data available: %s)",
                     self.entity_id, self.coordinator.data is not None)
        self._update_from_coordinator_data()

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


class EcoGuardOtherItemsSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Sensor for other items (general fees) from billing results.

    Uses the most recent billing data as the source of truth.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
    ) -> None:
        """Initialize the other items sensor."""
        super().__init__(coordinator)
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
        device_name = get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

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

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            # Keep "Cost Monthly Other Items" format to maintain entity_id starting with "cost_monthly_other_items"
            # The translation key might be used for display, but we keep the name format consistent
            new_name = await async_get_translation(self._hass, "name.cost_monthly_other_items")
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.info("Coordinator update received for %s (data available: %s)",
                     self.entity_id, self.coordinator.data is not None)
        self._update_from_coordinator_data()

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
        # Check if we have cached billing results first
        from homeassistant.core import CoreState
        is_starting = self.hass.state == CoreState.starting

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


class EcoGuardTotalMonthlyCostSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
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
        super().__init__(coordinator)
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
        device_name = get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

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

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

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
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.info("Coordinator update received for %s (data available: %s)",
                     self.entity_id, self.coordinator.data is not None)
        self._update_from_coordinator_data()

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # This sensor sums costs from multiple utilities, which requires async operations
        from homeassistant.core import CoreState
        is_starting = self.hass.state == CoreState.starting

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


class EcoGuardEndOfMonthEstimateSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
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
        super().__init__(coordinator)
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
        device_name = get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

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

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            # Keep "Cost Monthly Estimated Final Settlement" format to maintain entity_id starting with "cost_monthly_estimated_final_settlement"
            new_name = await async_get_translation(self._hass, "name.cost_monthly_estimated_final_settlement")
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.info("Coordinator update received for %s (data available: %s)",
                     self.entity_id, self.coordinator.data is not None)
        self._update_from_coordinator_data()

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


class EcoGuardDailyConsumptionAggregateSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Sensor for aggregated daily consumption across all meters of a utility type."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        utility_code: str,
    ) -> None:
        """Initialize the daily consumption aggregate sensor.

        Args:
            hass: Home Assistant instance
            coordinator: The coordinator instance
            utility_code: Utility code (e.g., "HW", "CW")
        """
        super().__init__(coordinator)
        self._hass = hass
        self._utility_code = utility_code

        # Build sensor name
        utility_name = get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Consumption Daily Metered - Utility"
        consumption_daily = get_translation_default("name.consumption_daily")
        metered = get_translation_default("name.metered")
        self._attr_name = f"{consumption_daily} {metered} - {utility_name}"

        # Build unique_id following pattern: consumption_daily_metered_utility
        utility_slug = _utility_code_to_slug(utility_code)
        self._attr_unique_id = f"{DOMAIN}_consumption_daily_metered_{utility_slug}"

        # Sensor attributes
        device_name = get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

        # Set state class
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = None
        self._last_data_date: datetime | None = None
        self._meters_with_data: list[dict[str, Any]] = []

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "utility_code": self._utility_code,
            "sensor_type": "daily_consumption_aggregate",
            "meter_count": len(self._meters_with_data),
        }

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()
            attrs["last_data_date_readable"] = self._last_data_date.strftime("%Y-%m-%d")

        if self._meters_with_data:
            attrs["meters"] = [
                {
                    "measuring_point_id": m.get("measuring_point_id"),
                    "measuring_point_name": m.get("measuring_point_name"),
                    "value": m.get("value"),
                }
                for m in self._meters_with_data
            ]

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            utility_name = await async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            consumption_daily = await async_get_translation(self._hass, "name.consumption_daily")
            metered = await async_get_translation(self._hass, "name.metered")
            new_name = f"{consumption_daily} {metered} - {utility_name}"
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.info("Coordinator update received for %s (data available: %s)",
                     self.entity_id, self.coordinator.data is not None)
        self._update_from_coordinator_data()

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.debug("No coordinator data for %s", self.entity_id)
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = None
            self._attr_available = True
            self.async_write_ha_state()
            return

        # Get consumption cache from coordinator data
        consumption_cache = coordinator_data.get("latest_consumption_cache", {})

        # Try to read from "all" cache first (aggregated across all meters)
        cache_key_all = f"{self._utility_code}_all"
        consumption_data = consumption_cache.get(cache_key_all)

        if consumption_data:
            # Use aggregated data directly
            raw_value = consumption_data.get("value")
            new_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            old_value = self._attr_native_value

            self._attr_native_value = new_value
            self._attr_native_unit_of_measurement = consumption_data.get("unit")

            # Update last data date
            time_stamp = consumption_data.get("time")
            if time_stamp:
                self._last_data_date = datetime.fromtimestamp(time_stamp)

            # Mark sensor as available when we have data
            self._attr_available = True

            # Log update for debugging
            if old_value != new_value:
                _LOGGER.info("Updated %s: %s -> %s %s (cache key: %s)",
                             self.entity_id, old_value, new_value,
                             self._attr_native_unit_of_measurement, cache_key_all)

            self.async_write_ha_state()
            return

        # Fallback: Sum consumption across all meters for this utility
        active_installations = self.coordinator.get_active_installations()
        total_value = 0.0
        unit = None
        latest_timestamp = None
        meters_with_data = []

        for installation in active_installations:
            registers = installation.get("Registers", [])
            measuring_point_id = installation.get("MeasuringPointID")

            # Check if this installation has the utility we're looking for
            has_utility = False
            for register in registers:
                if register.get("UtilityCode") == self._utility_code:
                    has_utility = True
                    break

            if not has_utility:
                continue

            # Get measuring point name
            measuring_point_name = None
            for mp in self.coordinator.get_measuring_points():
                if mp.get("ID") == measuring_point_id:
                    measuring_point_name = mp.get("Name")
                    break

            # Read consumption from cache (no API call)
            cache_key = f"{self._utility_code}_{measuring_point_id}"
            consumption_data = consumption_cache.get(cache_key)

            if consumption_data and consumption_data.get("value") is not None:
                value = consumption_data.get("value", 0.0)
                total_value += value

                # Use unit from first meter with data
                if unit is None:
                    unit = consumption_data.get("unit")

                # Track latest timestamp
                time_stamp = consumption_data.get("time")
                if time_stamp:
                    if latest_timestamp is None or time_stamp > latest_timestamp:
                        latest_timestamp = time_stamp

                meters_with_data.append({
                    "measuring_point_id": measuring_point_id,
                    "measuring_point_name": measuring_point_name,
                    "value": value,
                })

        if total_value > 0:
            self._attr_native_value = round_to_max_digits(total_value)
            self._attr_native_unit_of_measurement = unit
            if latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp)
            self._meters_with_data = meters_with_data
        else:
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = unit
            self._last_data_date = None
            self._meters_with_data = []

        self.async_write_ha_state()


class EcoGuardDailyCombinedWaterSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Sensor for combined daily water consumption (HW + CW) across all meters."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
    ) -> None:
        """Initialize the daily combined water sensor.

        Args:
            hass: Home Assistant instance
            coordinator: The coordinator instance
        """
        super().__init__(coordinator)
        self._hass = hass

        # Format: "Consumption Daily Metered - Combined Water"
        consumption_daily = get_translation_default("name.consumption_daily")
        metered = get_translation_default("name.metered")
        water_name = get_translation_default("name.combined_water")
        if water_name == "name.combined_water":  # Fallback if not found
            water_name = "Combined Water"
        self._attr_name = f"{consumption_daily} {metered} - {water_name}"

        # Build unique_id
        self._attr_unique_id = f"{DOMAIN}_consumption_daily_metered_combined_water"

        # Sensor attributes
        device_name = get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

        # Set state class
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = None
        self._last_data_date: datetime | None = None
        self._hw_meters_with_data: list[dict[str, Any]] = []
        self._cw_meters_with_data: list[dict[str, Any]] = []

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "sensor_type": "daily_consumption_combined_water",
            "utilities": ["HW", "CW"],
            "hw_meter_count": len(self._hw_meters_with_data),
            "cw_meter_count": len(self._cw_meters_with_data),
        }

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()
            attrs["last_data_date_readable"] = self._last_data_date.strftime("%Y-%m-%d")

        if self._hw_meters_with_data:
            attrs["hw_meters"] = [
                {
                    "measuring_point_id": m.get("measuring_point_id"),
                    "measuring_point_name": m.get("measuring_point_name"),
                    "value": m.get("value"),
                }
                for m in self._hw_meters_with_data
            ]

        if self._cw_meters_with_data:
            attrs["cw_meters"] = [
                {
                    "measuring_point_id": m.get("measuring_point_id"),
                    "measuring_point_name": m.get("measuring_point_name"),
                    "value": m.get("value"),
                }
                for m in self._cw_meters_with_data
            ]

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            consumption_daily = await async_get_translation(self._hass, "name.consumption_daily")
            metered = await async_get_translation(self._hass, "name.metered")
            water_name = await async_get_translation(self._hass, "name.combined_water")
            if water_name == "name.combined_water":
                water_name = "Combined Water"
            new_name = f"{consumption_daily} {metered} - {water_name}"
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.debug("Coordinator update received for %s", self.entity_id)
        self._update_from_coordinator_data()

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = None
            self._attr_available = True  # Keep available even if no data
            self.async_write_ha_state()
            return

        # Get consumption cache from coordinator data
        consumption_cache = coordinator_data.get("latest_consumption_cache", {})

        active_installations = self.coordinator.get_active_installations()
        hw_total = 0.0
        cw_total = 0.0
        unit = None
        latest_timestamp = None
        hw_meters_with_data = []
        cw_meters_with_data = []

        for installation in active_installations:
            registers = installation.get("Registers", [])
            measuring_point_id = installation.get("MeasuringPointID")

            # Get measuring point name
            measuring_point_name = None
            for mp in self.coordinator.get_measuring_points():
                if mp.get("ID") == measuring_point_id:
                    measuring_point_name = mp.get("Name")
                    break

            # Check for HW and CW in this installation
            for register in registers:
                utility_code = register.get("UtilityCode")
                if utility_code not in ("HW", "CW"):
                    continue

                # Read consumption from cache (no API call)
                cache_key = f"{utility_code}_{measuring_point_id}"
                consumption_data = consumption_cache.get(cache_key)

                if consumption_data and consumption_data.get("value") is not None:
                    value = consumption_data.get("value", 0.0)

                    # Use unit from first meter with data
                    if unit is None:
                        unit = consumption_data.get("unit")

                    # Track latest timestamp
                    time_stamp = consumption_data.get("time")
                    if time_stamp:
                        if latest_timestamp is None or time_stamp > latest_timestamp:
                            latest_timestamp = time_stamp

                    meter_info = {
                        "measuring_point_id": measuring_point_id,
                        "measuring_point_name": measuring_point_name,
                        "value": value,
                    }

                    if utility_code == "HW":
                        hw_total += value
                        hw_meters_with_data.append(meter_info)
                    elif utility_code == "CW":
                        cw_total += value
                        cw_meters_with_data.append(meter_info)

        total_value = hw_total + cw_total
        old_value = self._attr_native_value

        if total_value > 0:
            self._attr_native_value = round_to_max_digits(total_value)
            self._attr_native_unit_of_measurement = unit
            if latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp)
            self._hw_meters_with_data = hw_meters_with_data
            self._cw_meters_with_data = cw_meters_with_data
            self._attr_available = True

            # Log update for debugging
            if old_value != self._attr_native_value:
                _LOGGER.debug("Updated %s: %s -> %s %s (HW: %s, CW: %s)",
                             self.entity_id, old_value, self._attr_native_value,
                             unit, hw_total, cw_total)
        else:
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = unit
            self._last_data_date = None
            self._hw_meters_with_data = []
            self._cw_meters_with_data = []
            # Keep sensor available even if no data (shows as "unknown" not "unavailable")
            self._attr_available = True
            if old_value is not None:
                _LOGGER.debug("Updated %s: %s -> None (no data found)", self.entity_id, old_value)

        self.async_write_ha_state()


class EcoGuardDailyCostSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Sensor for last known daily cost for a specific meter."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        installation: dict[str, Any],
        utility_code: str,
        measuring_point_id: int,
        measuring_point_name: str | None,
        cost_type: str = "actual",
    ) -> None:
        """Initialize the daily cost sensor.

        Args:
            hass: Home Assistant instance
            coordinator: The coordinator instance
            installation: Installation data dict
            utility_code: Utility code (e.g., "HW", "CW")
            measuring_point_id: Measuring point ID
            measuring_point_name: Measuring point name
            cost_type: "actual" for metered API data, "estimated" for estimated costs
        """
        super().__init__(coordinator)
        self._hass = hass
        self._installation = installation
        self._utility_code = utility_code
        self._measuring_point_id = measuring_point_id
        self._measuring_point_name = measuring_point_name
        self._cost_type = cost_type

        # Build sensor name
        if measuring_point_name:
            measuring_point_display = measuring_point_name
        else:
            measuring_point_display = get_translation_default("name.measuring_point", id=measuring_point_id)

        utility_name = get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Cost Daily Metered/Estimated - Meter "Measuring Point" (Utility)"
        cost_daily = get_translation_default("name.cost_daily")
        meter = get_translation_default("name.meter")
        if cost_type == "estimated":
            estimated = get_translation_default("name.estimated")
            self._attr_name = f'{cost_daily} {estimated} - {meter} "{measuring_point_display}" ({utility_name})'
        else:
            metered = get_translation_default("name.metered")
            self._attr_name = f'{cost_daily} {metered} - {meter} "{measuring_point_display}" ({utility_name})'

        # Build unique_id following pattern: cost_daily_metered/estimated_utility_sensor
        utility_slug = _utility_code_to_slug(utility_code)
        sensor_name = _slugify_name(measuring_point_name) or f"mp{measuring_point_id}"
        if cost_type == "estimated":
            unique_id_suffix = f"cost_daily_estimated_{utility_slug}_{sensor_name}"
        else:
            unique_id_suffix = f"cost_daily_metered_{utility_slug}_{sensor_name}"
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
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = None
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._last_data_date: datetime | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "measuring_point_id": self._measuring_point_id,
            "utility_code": self._utility_code,
            "external_key": self._installation.get("ExternalKey"),
            "device_type": self._installation.get("DeviceTypeDisplay"),
            "sensor_type": "daily_cost",
            "cost_type": "metered" if self._cost_type == "actual" else "estimated",
        }

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()
            attrs["last_data_date_readable"] = self._last_data_date.strftime("%Y-%m-%d")

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

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

            cost_daily = await async_get_translation(self._hass, "name.cost_daily")
            meter = await async_get_translation(self._hass, "name.meter")
            if self._cost_type == "estimated":
                estimated = await async_get_translation(self._hass, "name.estimated")
                new_name = f'{cost_daily} {estimated} - {meter} "{measuring_point_display}" ({utility_name})'
            else:
                metered = await async_get_translation(self._hass, "name.metered")
                new_name = f'{cost_daily} {metered} - {meter} "{measuring_point_display}" ({utility_name})'

            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.debug("_handle_coordinator_update called for %s", self.entity_id)
        self._update_from_coordinator_data()

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        _LOGGER.debug("_update_from_coordinator_data called for %s (cost_type=%s, utility=%s, meter=%s)",
                     self.entity_id, self._cost_type, self._utility_code, self._measuring_point_id)
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.debug("No coordinator data for %s", self.entity_id)
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None
            self.async_write_ha_state()
            return

        # Get cost cache from coordinator data
        cost_cache = coordinator_data.get("latest_cost_cache", {})

        # Build cache key - always use metered cache key
        # For estimated costs, use metered cost if available (estimated = metered when metered exists)
        if self._measuring_point_id:
            cache_key = f"{self._utility_code}_{self._measuring_point_id}_metered"
        else:
            cache_key = f"{self._utility_code}_all_metered"

        cost_data = cost_cache.get(cache_key)

        _LOGGER.debug("_update_from_coordinator_data for %s: cost_type=%s, cache_key=%s, cost_data=%s",
                     self.entity_id, self._cost_type, cache_key, cost_data is not None)

        # For estimated costs: if we have metered cost data, use it (estimated = metered when available)
        # Only calculate from consumption if metered cost is not available
        if not cost_data and self._cost_type == "estimated":
            # No metered cost available, trigger async fetch to calculate estimated cost
            # This is needed for HW where metered cost is often not available
            from homeassistant.core import CoreState
            if self.hass and not self.hass.is_stopping and self.hass.state != CoreState.starting:
                # Trigger async fetch in background (non-blocking)
                async def _fetch_estimated_cost():
                    try:
                        _LOGGER.debug("Starting async fetch for estimated cost: %s", self.entity_id)
                        await self._async_fetch_value()
                    except Exception as err:
                        _LOGGER.warning("Error in async fetch for %s: %s", self.entity_id, err, exc_info=True)
                self.hass.async_create_task(_fetch_estimated_cost())
                _LOGGER.debug("Created async task for estimated cost fetch: %s", self.entity_id)
            else:
                _LOGGER.debug("Skipping async fetch for %s: hass=%s, is_stopping=%s, state=%s",
                             self.entity_id,
                             self.hass is not None,
                             self.hass.is_stopping if self.hass else None,
                             self.hass.state if self.hass else None)
            cost_data = None

        if cost_data:
            raw_value = cost_data.get("value")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            self._attr_native_unit_of_measurement = cost_data.get("unit") or self.coordinator.get_setting("Currency") or ""

            # Update last data date
            time_stamp = cost_data.get("time")
            if time_stamp:
                self._last_data_date = datetime.fromtimestamp(time_stamp)
        else:
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()

    async def _async_fetch_value(self) -> None:
        """Fetch estimated cost when metered cost is not available."""
        if self._cost_type != "estimated":
            # Only fetch for estimated costs
            return

        _LOGGER.debug("Fetching estimated cost for %s (utility: %s, meter: %s)",
                     self.entity_id, self._utility_code, self._measuring_point_id)

        # Get estimated cost from coordinator
        cost_data = await self.coordinator.get_latest_estimated_cost(
            utility_code=self._utility_code,
            measuring_point_id=self._measuring_point_id,
            external_key=self._installation.get("ExternalKey"),
        )

        if cost_data:
            raw_value = cost_data.get("value")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            self._attr_native_unit_of_measurement = cost_data.get("unit") or self.coordinator.get_setting("Currency") or ""

            # Update last data date
            time_stamp = cost_data.get("time")
            if time_stamp:
                self._last_data_date = datetime.fromtimestamp(time_stamp)

            _LOGGER.info("Updated %s (estimated): %s %s",
                         self.entity_id, self._attr_native_value, self._attr_native_unit_of_measurement)
        else:
            _LOGGER.debug("No estimated cost data returned for %s (utility: %s, meter: %s)",
                          self.entity_id, self._utility_code, self._measuring_point_id)
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()


class EcoGuardDailyCostAggregateSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Sensor for aggregated daily cost across all meters of a utility type."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        utility_code: str,
        cost_type: str = "actual",
    ) -> None:
        """Initialize the daily cost aggregate sensor.

        Args:
            hass: Home Assistant instance
            coordinator: The coordinator instance
            utility_code: Utility code (e.g., "HW", "CW")
            cost_type: "actual" for metered API data, "estimated" for estimated costs
        """
        super().__init__(coordinator)
        self._hass = hass
        self._utility_code = utility_code
        self._cost_type = cost_type

        # Build sensor name
        utility_name = get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Cost Daily Metered/Estimated - Utility"
        cost_daily = get_translation_default("name.cost_daily")
        if cost_type == "estimated":
            estimated = get_translation_default("name.estimated")
            self._attr_name = f"{cost_daily} {estimated} - {utility_name}"
        else:
            metered = get_translation_default("name.metered")
            self._attr_name = f"{cost_daily} {metered} - {utility_name}"

        # Build unique_id following pattern: cost_daily_metered/estimated_utility
        utility_slug = _utility_code_to_slug(utility_code)
        if cost_type == "estimated":
            self._attr_unique_id = f"{DOMAIN}_cost_daily_estimated_{utility_slug}"
        else:
            self._attr_unique_id = f"{DOMAIN}_cost_daily_metered_{utility_slug}"

        # Sensor attributes
        device_name = get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

        # Set state class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = None
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._last_data_date: datetime | None = None
        self._meters_with_data: list[dict[str, Any]] = []

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "utility_code": self._utility_code,
            "sensor_type": "daily_cost_aggregate",
            "cost_type": "metered" if self._cost_type == "actual" else "estimated",
            "meter_count": len(self._meters_with_data),
        }

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()
            attrs["last_data_date_readable"] = self._last_data_date.strftime("%Y-%m-%d")

        if self._meters_with_data:
            attrs["meters"] = [
                {
                    "measuring_point_id": m.get("measuring_point_id"),
                    "measuring_point_name": m.get("measuring_point_name"),
                    "value": m.get("value"),
                }
                for m in self._meters_with_data
            ]

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            utility_name = await async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            cost_daily = await async_get_translation(self._hass, "name.cost_daily")
            if self._cost_type == "estimated":
                estimated = await async_get_translation(self._hass, "name.estimated")
                new_name = f"{cost_daily} {estimated} - {utility_name}"
            else:
                metered = await async_get_translation(self._hass, "name.metered")
                new_name = f"{cost_daily} {metered} - {utility_name}"

            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.info("Coordinator update received for %s (data available: %s)",
                     self.entity_id, self.coordinator.data is not None)
        self._update_from_coordinator_data()

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.debug("No coordinator data for %s", self.entity_id)
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._attr_available = True
            self.async_write_ha_state()
            return

        # Get cost cache from coordinator data
        cost_cache = coordinator_data.get("latest_cost_cache", {})

        # Sum costs across all meters for this utility
        active_installations = self.coordinator.get_active_installations()
        total_value = 0.0
        latest_timestamp = None
        meters_with_data = []

        for installation in active_installations:
            registers = installation.get("Registers", [])
            measuring_point_id = installation.get("MeasuringPointID")

            # Check if this installation has the utility we're looking for
            has_utility = False
            for register in registers:
                if register.get("UtilityCode") == self._utility_code:
                    has_utility = True
                    break

            if not has_utility:
                continue

            # Get measuring point name
            measuring_point_name = None
            for mp in self.coordinator.get_measuring_points():
                if mp.get("ID") == measuring_point_id:
                    measuring_point_name = mp.get("Name")
                    break

            # Read cost from cache (no API call)
            # Always use metered cache key
            # For estimated costs, use metered cost if available (estimated = metered when metered exists)
            cache_key = f"{self._utility_code}_{measuring_point_id}_metered"
            cost_data = cost_cache.get(cache_key)

            if cost_data and cost_data.get("value") is not None:
                value = cost_data.get("value", 0.0)
                total_value += value

                # Track latest timestamp
                time_stamp = cost_data.get("time")
                if time_stamp:
                    if latest_timestamp is None or time_stamp > latest_timestamp:
                        latest_timestamp = time_stamp

                meters_with_data.append({
                    "measuring_point_id": measuring_point_id,
                    "measuring_point_name": measuring_point_name,
                    "value": value,
                })

        if total_value > 0 or (self._cost_type == "actual" and meters_with_data):
            # Even if total is 0, update if we have meter data (shows 0 is valid)
            self._attr_native_value = round_to_max_digits(total_value) if total_value > 0 else 0.0
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            if latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp)
            self._meters_with_data = meters_with_data
            self._attr_available = True

            _LOGGER.info("Updated %s: %s %s (from %d meters)",
                         self.entity_id, self._attr_native_value, currency, len(meters_with_data))
        else:
            # No metered cost data available
            # For estimated costs, trigger async fetch to calculate from consumption + rate/spot prices
            if self._cost_type == "estimated":
                from homeassistant.core import CoreState
                if self.hass and not self.hass.is_stopping and self.hass.state != CoreState.starting:
                    # Trigger async fetch in background (non-blocking)
                    async def _fetch_estimated_cost():
                        try:
                            _LOGGER.debug("Starting async fetch for estimated cost aggregate: %s", self.entity_id)
                            await self._async_fetch_value()
                        except Exception as err:
                            _LOGGER.warning("Error in async fetch for %s: %s", self.entity_id, err, exc_info=True)
                    self.hass.async_create_task(_fetch_estimated_cost())
                    _LOGGER.debug("Created async task for estimated cost aggregate fetch: %s", self.entity_id)
                else:
                    _LOGGER.debug("Skipping async fetch for %s: hass=%s, is_stopping=%s, state=%s",
                                 self.entity_id,
                                 self.hass is not None,
                                 self.hass.is_stopping if self.hass else None,
                                 self.hass.state if self.hass else None)

            # No data available yet, but keep sensor available
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None
            self._meters_with_data = []
            self._attr_available = True

        self.async_write_ha_state()

    async def _async_fetch_value(self) -> None:
        """Fetch aggregated daily cost across all meters of this utility type."""
        active_installations = self.coordinator.get_active_installations()
        total_value = 0.0
        latest_timestamp = None
        meters_with_data = []

        for installation in active_installations:
            registers = installation.get("Registers", [])
            measuring_point_id = installation.get("MeasuringPointID")

            # Check if this installation has the utility we're looking for
            has_utility = False
            for register in registers:
                if register.get("UtilityCode") == self._utility_code:
                    has_utility = True
                    break

            if not has_utility:
                continue

            # Get measuring point name
            measuring_point_name = None
            for mp in self.coordinator.get_measuring_points():
                if mp.get("ID") == measuring_point_id:
                    measuring_point_name = mp.get("Name")
                    break

            # Fetch cost for this meter
            if self._cost_type == "estimated":
                cost_data = await self.coordinator.get_latest_estimated_cost(
                    utility_code=self._utility_code,
                    measuring_point_id=measuring_point_id,
                    external_key=installation.get("ExternalKey"),
                )
            else:
                cost_data = await self.coordinator.get_latest_metered_cost(
                    utility_code=self._utility_code,
                    measuring_point_id=measuring_point_id,
                    external_key=installation.get("ExternalKey"),
                )

            if cost_data and cost_data.get("value") is not None:
                value = cost_data.get("value", 0.0)
                total_value += value

                # Track latest timestamp
                time_stamp = cost_data.get("time")
                if time_stamp:
                    if latest_timestamp is None or time_stamp > latest_timestamp:
                        latest_timestamp = time_stamp

                meters_with_data.append({
                    "measuring_point_id": measuring_point_id,
                    "measuring_point_name": measuring_point_name,
                    "value": value,
                })

        if total_value > 0:
            self._attr_native_value = round_to_max_digits(total_value)
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            if latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp)
            self._meters_with_data = meters_with_data
        else:
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None
            self._meters_with_data = []

        self.async_write_ha_state()


class EcoGuardDailyCombinedWaterCostSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Sensor for combined daily water cost (HW + CW) across all meters."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        cost_type: str = "actual",
    ) -> None:
        """Initialize the daily combined water cost sensor.

        Args:
            hass: Home Assistant instance
            coordinator: The coordinator instance
            cost_type: "actual" for metered API data, "estimated" for estimated costs
        """
        super().__init__(coordinator)
        self._hass = hass
        self._cost_type = cost_type

        # Format: "Cost Daily Metered/Estimated - Combined Water"
        cost_daily = get_translation_default("name.cost_daily")
        water_name = get_translation_default("name.combined_water")
        if water_name == "name.combined_water":  # Fallback if not found
            water_name = "Combined Water"

        if cost_type == "estimated":
            estimated = get_translation_default("name.estimated")
            self._attr_name = f"{cost_daily} {estimated} - {water_name}"
        else:
            metered = get_translation_default("name.metered")
            self._attr_name = f"{cost_daily} {metered} - {water_name}"

        # Build unique_id
        if cost_type == "estimated":
            self._attr_unique_id = f"{DOMAIN}_cost_daily_estimated_combined_water"
        else:
            self._attr_unique_id = f"{DOMAIN}_cost_daily_metered_combined_water"

        # Sensor attributes
        device_name = get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

        # Set state class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = None
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._last_data_date: datetime | None = None
        self._hw_meters_with_data: list[dict[str, Any]] = []
        self._cw_meters_with_data: list[dict[str, Any]] = []

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "sensor_type": "daily_cost_combined_water",
            "cost_type": "metered" if self._cost_type == "actual" else "estimated",
            "utilities": ["HW", "CW"],
            "hw_meter_count": len(self._hw_meters_with_data),
            "cw_meter_count": len(self._cw_meters_with_data),
        }

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()
            attrs["last_data_date_readable"] = self._last_data_date.strftime("%Y-%m-%d")

        if self._hw_meters_with_data:
            attrs["hw_meters"] = [
                {
                    "measuring_point_id": m.get("measuring_point_id"),
                    "measuring_point_name": m.get("measuring_point_name"),
                    "value": m.get("value"),
                }
                for m in self._hw_meters_with_data
            ]

        if self._cw_meters_with_data:
            attrs["cw_meters"] = [
                {
                    "measuring_point_id": m.get("measuring_point_id"),
                    "measuring_point_name": m.get("measuring_point_name"),
                    "value": m.get("value"),
                }
                for m in self._cw_meters_with_data
            ]

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            cost_daily = await async_get_translation(self._hass, "name.cost_daily")
            water_name = await async_get_translation(self._hass, "name.combined_water")
            if water_name == "name.combined_water":
                water_name = "Combined Water"

            if self._cost_type == "estimated":
                estimated = await async_get_translation(self._hass, "name.estimated")
                new_name = f"{cost_daily} {estimated} - {water_name}"
            else:
                metered = await async_get_translation(self._hass, "name.metered")
                new_name = f"{cost_daily} {metered} - {water_name}"

            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        self._update_from_coordinator_data()

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._attr_available = True  # Keep available even if no data
            self.async_write_ha_state()
            return

        # Get cost cache from coordinator data
        cost_cache = coordinator_data.get("latest_cost_cache", {})

        active_installations = self.coordinator.get_active_installations()
        hw_total = 0.0
        cw_total = 0.0
        latest_timestamp = None
        hw_meters_with_data = []
        cw_meters_with_data = []

        for installation in active_installations:
            registers = installation.get("Registers", [])
            measuring_point_id = installation.get("MeasuringPointID")

            # Get measuring point name
            measuring_point_name = None
            for mp in self.coordinator.get_measuring_points():
                if mp.get("ID") == measuring_point_id:
                    measuring_point_name = mp.get("Name")
                    break

            # Check for HW and CW in this installation
            for register in registers:
                utility_code = register.get("UtilityCode")
                if utility_code not in ("HW", "CW"):
                    continue

                # Read cost from cache (no API call)
                cache_key = f"{utility_code}_{measuring_point_id}_metered"
                cost_data = cost_cache.get(cache_key)

                if cost_data and cost_data.get("value") is not None:
                    value = cost_data.get("value", 0.0)

                    # Track latest timestamp
                    time_stamp = cost_data.get("time")
                    if time_stamp:
                        if latest_timestamp is None or time_stamp > latest_timestamp:
                            latest_timestamp = time_stamp

                    meter_info = {
                        "measuring_point_id": measuring_point_id,
                        "measuring_point_name": measuring_point_name,
                        "value": value,
                    }

                    if utility_code == "HW":
                        hw_total += value
                        hw_meters_with_data.append(meter_info)
                    elif utility_code == "CW":
                        cw_total += value
                        cw_meters_with_data.append(meter_info)
                elif utility_code == "HW" and self._cost_type == "actual":
                    # For metered HW costs: if data is missing (Unknown), we need to know about it
                    # This happens when all HW price values are 0 (no metered price data from API)
                    # We track this so we can show Unknown for the combined sensor
                    _LOGGER.debug("HW cost data is Unknown (missing from cache) for meter %d in combined sensor %s",
                                 measuring_point_id, self.entity_id)

        total_value = hw_total + cw_total

        # For estimated costs: if we don't have data for both HW and CW, trigger async fetch
        # This is needed because HW estimated costs are calculated using spot prices, not from cache
        # We need both utilities to show a value, so fetch if either is missing
        if self._cost_type == "estimated":
            # Check if we're missing data for either utility
            has_hw_data = len(hw_meters_with_data) > 0
            has_cw_data = len(cw_meters_with_data) > 0

            # If we're missing data for either utility, trigger async fetch
            # This ensures we get the complete combined cost with both HW and CW
            if not has_hw_data or not has_cw_data:
                from homeassistant.core import CoreState
                if self.hass and not self.hass.is_stopping and self.hass.state != CoreState.starting:
                    # Trigger async fetch in background (non-blocking)
                    async def _fetch_estimated_cost():
                        try:
                            _LOGGER.debug("Starting async fetch for estimated combined water cost: %s", self.entity_id)
                            await self._async_fetch_value()
                        except Exception as err:
                            _LOGGER.warning("Error in async fetch for %s: %s", self.entity_id, err, exc_info=True)
                    self.hass.async_create_task(_fetch_estimated_cost())
                    _LOGGER.debug("Created async task for estimated combined water cost fetch: %s (has_hw_data=%s, has_cw_data=%s)",
                                 self.entity_id, has_hw_data, has_cw_data)

        # Only set a value if we have data for BOTH HW and CW
        # This ensures the combined sensor only shows a value when both utilities are available
        # If HW is Unknown (missing from cache), we show Unknown rather than just CW cost
        # (showing partial data would be misleading - it looks like total combined cost but is missing HW)
        has_hw_data = len(hw_meters_with_data) > 0
        has_cw_data = len(cw_meters_with_data) > 0

        if has_hw_data and has_cw_data:
            self._attr_native_value = round_to_max_digits(total_value)
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            if latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp)
            self._hw_meters_with_data = hw_meters_with_data
            self._cw_meters_with_data = cw_meters_with_data
            self._attr_available = True
            _LOGGER.debug("Updated %s: HW=%.2f, CW=%.2f, Total=%.2f",
                         self.entity_id, hw_total, cw_total, total_value)
        else:
            # Missing data for one or both utilities - show Unknown
            # This is especially important for metered costs: if HW is Unknown (all 0 values),
            # we show Unknown rather than just CW cost (which would be misleading)
            if self._cost_type == "estimated":
                _LOGGER.debug("Waiting for both utilities: %s (has_hw_data=%s, has_cw_data=%s)",
                             self.entity_id, has_hw_data, has_cw_data)
            elif self._cost_type == "actual":
                _LOGGER.debug("Missing data for combined water cost: %s (has_hw_data=%s, has_cw_data=%s) - showing Unknown",
                             self.entity_id, has_hw_data, has_cw_data)
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None
            self._hw_meters_with_data = []
            self._cw_meters_with_data = []
            # Keep sensor available even if no data (shows as "unknown" not "unavailable")
            self._attr_available = True

        self.async_write_ha_state()

    async def _async_fetch_value(self) -> None:
        """Fetch estimated combined water cost by fetching costs for all HW and CW meters."""
        if self._cost_type != "estimated":
            # Only fetch for estimated costs
            return

        _LOGGER.debug("Fetching estimated combined water cost for %s", self.entity_id)

        active_installations = self.coordinator.get_active_installations()
        hw_total = 0.0
        cw_total = 0.0
        latest_timestamp = None
        hw_meters_with_data = []
        cw_meters_with_data = []

        for installation in active_installations:
            registers = installation.get("Registers", [])
            measuring_point_id = installation.get("MeasuringPointID")

            # Get measuring point name
            measuring_point_name = None
            for mp in self.coordinator.get_measuring_points():
                if mp.get("ID") == measuring_point_id:
                    measuring_point_name = mp.get("Name")
                    break

            # Check for HW and CW in this installation
            for register in registers:
                utility_code = register.get("UtilityCode")
                if utility_code not in ("HW", "CW"):
                    continue

                # Fetch estimated cost for this meter
                cost_data = await self.coordinator.get_latest_estimated_cost(
                    utility_code=utility_code,
                    measuring_point_id=measuring_point_id,
                    external_key=installation.get("ExternalKey"),
                )

                if cost_data and cost_data.get("value") is not None:
                    value = cost_data.get("value", 0.0)

                    # Track latest timestamp
                    time_stamp = cost_data.get("time")
                    if time_stamp:
                        if latest_timestamp is None or time_stamp > latest_timestamp:
                            latest_timestamp = time_stamp

                    meter_info = {
                        "measuring_point_id": measuring_point_id,
                        "measuring_point_name": measuring_point_name,
                        "value": value,
                    }

                    if utility_code == "HW":
                        hw_total += value
                        hw_meters_with_data.append(meter_info)
                    elif utility_code == "CW":
                        cw_total += value
                        cw_meters_with_data.append(meter_info)

        total_value = hw_total + cw_total

        # Only set a value if we have data for BOTH HW and CW
        # This ensures the combined sensor only shows a value when both utilities are available
        has_hw_data = len(hw_meters_with_data) > 0
        has_cw_data = len(cw_meters_with_data) > 0

        if has_hw_data and has_cw_data and total_value > 0:
            self._attr_native_value = round_to_max_digits(total_value)
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            if latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp)
            self._hw_meters_with_data = hw_meters_with_data
            self._cw_meters_with_data = cw_meters_with_data
            self._attr_available = True

            _LOGGER.info("Updated %s (estimated): HW=%.2f, CW=%.2f, Total=%.2f %s",
                         self.entity_id, hw_total, cw_total, total_value, currency)
        else:
            # Missing data for one or both utilities - don't show a value yet
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None
            self._hw_meters_with_data = []
            self._cw_meters_with_data = []
            self._attr_available = True
            _LOGGER.debug("Waiting for both utilities in %s (has_hw_data=%s, has_cw_data=%s)",
                         self.entity_id, has_hw_data, has_cw_data)

        self.async_write_ha_state()


class EcoGuardMonthlyMeterSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
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
        super().__init__(coordinator)
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
        utility_slug = _utility_code_to_slug(utility_code)
        sensor_name = _slugify_name(measuring_point_name) or f"mp{measuring_point_id}"
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

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

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
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.info("Coordinator update received for %s (data available: %s)",
                     self.entity_id, self.coordinator.data is not None)
        self._update_from_coordinator_data()

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


class EcoGuardCombinedWaterSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
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
        super().__init__(coordinator)
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

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        # Set sensor to Unknown state (available=True, native_value=None)
        # No API calls during startup - sensors will show as "Unknown"
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()
        _LOGGER.debug("Sensor %s added to hass (Unknown state, no API calls)", self.entity_id)

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
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data."""
        _LOGGER.info("Coordinator update received for %s (data available: %s)",
                     self.entity_id, self.coordinator.data is not None)
        self._update_from_coordinator_data()

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
