"""Sensor platform for EcoGuard integration."""

from __future__ import annotations

import logging
import asyncio

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from .const import DOMAIN
from .coordinator import EcoGuardDataUpdateCoordinator
from .translations import (
    clear_translation_cache,
)
from .entity_registry_updater import (
    update_entity_registry_with_timeout,
)

from .sensor_factory import (
    create_installation_sensors,
    create_daily_aggregate_sensors,
    create_daily_combined_water_sensors,
    create_monthly_aggregate_sensors,
    create_monthly_meter_sensors,
    create_combined_water_sensors,
    create_special_sensors,
)

from .sensors import (
    EcoGuardDailyConsumptionSensor,
    EcoGuardDailyCostSensor,
    EcoGuardLatestReceptionSensor,
    EcoGuardMonthlyMeterSensor,
)

_LOGGER = logging.getLogger(__name__)

# Counter for staggering sensor data fetches to avoid overwhelming the API
_sensor_fetch_counter = 0
_sensor_fetch_lock = asyncio.Lock()

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

    # Track measuring points for which we've already created latest reception sensors
    measuring_points_with_reception_sensor = set()

    # Create installation sensors (daily consumption, daily cost, latest reception)
    installation_sensors, utility_codes = create_installation_sensors(
        hass=hass,
        coordinator=coordinator,
        latest_reception_coordinator=latest_reception_coordinator,
        active_installations=active_installations,
        measuring_points_with_reception_sensor=measuring_points_with_reception_sensor,
    )
    sensors.extend(installation_sensors)

    # Create daily aggregate sensors
    daily_aggregate_sensors = create_daily_aggregate_sensors(
        hass=hass,
        coordinator=coordinator,
        utility_codes=utility_codes,
    )
    sensors.extend(daily_aggregate_sensors)

    # Create daily combined water sensors
    daily_combined_sensors = create_daily_combined_water_sensors(
        hass=hass,
        coordinator=coordinator,
        utility_codes=utility_codes,
    )
    sensors.extend(daily_combined_sensors)

    # Create monthly aggregate sensors
    monthly_aggregate_sensors = create_monthly_aggregate_sensors(
        hass=hass,
        coordinator=coordinator,
        utility_codes=utility_codes,
    )
    sensors.extend(monthly_aggregate_sensors)

    # Create monthly per-meter sensors
    monthly_meter_sensors = create_monthly_meter_sensors(
        hass=hass,
        coordinator=coordinator,
        active_installations=active_installations,
    )
    sensors.extend(monthly_meter_sensors)

    # Create combined water sensors
    combined_water_sensors = create_combined_water_sensors(
        hass=hass,
        coordinator=coordinator,
        utility_codes=utility_codes,
    )
    sensors.extend(combined_water_sensors)

    # Create special sensors (other items, end-of-month estimate, total monthly cost)
    special_sensors = create_special_sensors(
        hass=hass,
        coordinator=coordinator,
    )
    sensors.extend(special_sensors)

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
