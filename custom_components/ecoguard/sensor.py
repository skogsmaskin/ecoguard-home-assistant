"""Sensor platform for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import logging
import math
import json
import asyncio
from pathlib import Path

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
    RegistryEntryDisabler,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EcoGuardDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Cache for translation files
_translation_cache: dict[str, dict[str, Any]] = {}


def _get_entity_id_by_unique_id(
    entity_registry: Any,
    unique_id: str,
    platform: str = "sensor",
    domain: str = DOMAIN,
) -> str | None:
    """Get entity_id from entity registry by unique_id.

    This helper function ensures the correct parameter order for async_get_entity_id:
    async_get_entity_id(platform, domain, unique_id)

    Args:
        entity_registry: The entity registry instance
        unique_id: The unique_id of the entity
        platform: The platform name (default: "sensor")
        domain: The integration domain (default: DOMAIN)

    Returns:
        The entity_id if found, None otherwise
    """
    try:
        entity_id = entity_registry.async_get_entity_id(platform, domain, unique_id)
        if not entity_id:
            _LOGGER.debug(
                "Entity not found in registry: platform=%s, domain=%s, unique_id=%s",
                platform,
                domain,
                unique_id,
            )
        return entity_id
    except Exception as e:
        _LOGGER.warning(
            "Failed to get entity_id from registry: platform=%s, domain=%s, unique_id=%s, error=%s",
            platform,
            domain,
            unique_id,
            e,
        )
        return None


def _clear_translation_cache() -> None:
    """Clear the translation cache (useful for development/reloads)."""
    global _translation_cache
    _translation_cache.clear()
    _LOGGER.debug("Translation cache cleared")


def _load_translation_file_sync(lang: str) -> dict[str, Any] | None:
    """Load translation file synchronously (to be run in thread)."""
    try:
        # Check cache first
        if lang in _translation_cache:
            return _translation_cache[lang]

        # Get the integration directory
        integration_dir = Path(__file__).parent

        # For English, use strings.json (Home Assistant standard)
        if lang == "en":
            strings_file = integration_dir / "strings.json"
            if strings_file.exists():
                with open(strings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _translation_cache["en"] = data
                    return data
            # Fallback to en.json if strings.json doesn't exist
            translation_file = integration_dir / "translations" / "en.json"
            if translation_file.exists():
                with open(translation_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _translation_cache["en"] = data
                    return data
        else:
            # For other languages, try translations/{lang}.json
            translation_file = integration_dir / "translations" / f"{lang}.json"
            if translation_file.exists():
                with open(translation_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _translation_cache[lang] = data
                    return data

            # Fallback to strings.json for English if language file doesn't exist
            strings_file = integration_dir / "strings.json"
            if strings_file.exists():
                with open(strings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _translation_cache["en"] = data
                    return data
    except Exception as e:
        _LOGGER.debug("Failed to load translation file for lang %s: %s", lang, e)

    return None


async def _load_translation_file(hass: HomeAssistant, lang: str) -> dict[str, Any] | None:
    """Load translation file asynchronously to access sensor section."""
    # Check cache first
    if lang in _translation_cache:
        return _translation_cache[lang]

    # Run the blocking file I/O in a thread pool
    try:
        data = await asyncio.to_thread(_load_translation_file_sync, lang)
        if data:
            _LOGGER.debug("Loaded translation file for lang %s, keys in common: %s", lang, list(data.get("common", {}).keys()))
        return data
    except Exception as e:
        _LOGGER.debug("Failed to load translation file for lang %s: %s", lang, e)
        return None


async def _async_get_translation(hass: HomeAssistant, key: str, **kwargs: Any) -> str:
    """Get a translated string from the integration's translation files."""
    try:
        # Get the current language from hass.config.language
        lang = getattr(hass.config, 'language', 'en')

        # Load translation file directly to access common section
        # (The translation helper only loads config section)
        translation_data = await _load_translation_file(hass, lang)

        if translation_data and "common" in translation_data:
            common_data = translation_data["common"]

            # Convert key from "utility.hw" to "utility_hw" format
            translation_key = key.replace(".", "_")

            if translation_key in common_data:
                text = common_data[translation_key]
                if isinstance(text, str):
                    _LOGGER.debug("Found translation for key %s (as %s): %s (lang=%s)", key, translation_key, text, lang)
                    return text.format(**kwargs) if kwargs else text
            else:
                _LOGGER.debug("Translation key %s (as %s) not found in common section (lang=%s). Available keys: %s",
                             key, translation_key, lang, list(common_data.keys())[:10])

        # Fallback to English
        if lang != "en":
            translation_data = await _load_translation_file(hass, "en")
            if translation_data and "common" in translation_data:
                common_data = translation_data["common"]
                translation_key = key.replace(".", "_")

                if translation_key in common_data:
                    text = common_data[translation_key]
                    if isinstance(text, str):
                        return text.format(**kwargs) if kwargs else text
    except Exception as e:
        _LOGGER.warning("Translation lookup failed for key %s (lang=%s): %s", key, getattr(hass.config, 'language', 'en'), e)

    # Fallback to English defaults
    defaults = {
        "utility.hw": "Hot Water",
        "utility.cw": "Cold Water",
        "name.estimated": "Estimated",
        "name.metered": "Metered",
        "name.measuring_point": "Measuring Point {id}",
        "name.meter": "Meter",
        "name.device_name": "EcoGuard Node {node_id}",
        "name.combined_water": "Combined Water",
        "name.consumption_daily": "Consumption Daily",
        "name.cost_daily": "Cost Daily",
        "name.consumption_monthly_aggregated": "Consumption Monthly Aggregated",
        "name.cost_monthly_aggregated": "Cost Monthly Aggregated",
        "name.cost_monthly_other_items": "Cost Monthly Other Items",
        "name.combined": "Combined",
        "name.all_utilities": "All Utilities",
        "name.cost_monthly_end_of_month_total_estimate": "Cost Monthly End of Month Total Estimate",
        "name.reception_last_update": "Reception Last Update",
    }

    default = defaults.get(key, key)
    if default == key:
        _LOGGER.debug("Translation key %s not found in defaults dictionary, returning key as-is", key)
    return default.format(**kwargs) if kwargs else default


def _get_translation_default(key: str, **kwargs: Any) -> str:
    """Get English default translation (for use in __init__ to avoid blocking I/O).

    Actual translations will be loaded in async_added_to_hass.
    """
    defaults = {
        "utility.hw": "Hot Water",
        "utility.cw": "Cold Water",
        "name.estimated": "Estimated",
        "name.metered": "Metered",
        "name.measuring_point": "Measuring Point {id}",
        "name.meter": "Meter",
        "name.device_name": "EcoGuard Node {node_id}",
        "name.combined_water": "Combined Water",
        "name.consumption_daily": "Consumption Daily",
        "name.cost_daily": "Cost Daily",
        "name.consumption_monthly_aggregated": "Consumption Monthly Aggregated",
        "name.cost_monthly_aggregated": "Cost Monthly Aggregated",
        "name.cost_monthly_other_items": "Cost Monthly Other Items",
        "name.combined": "Combined",
        "name.all_utilities": "All Utilities",
        "name.cost_monthly_end_of_month_total_estimate": "Cost Monthly End of Month Total Estimate",
        "name.reception_last_update": "Reception Last Update",
    }

    default = defaults.get(key, key)
    return default.format(**kwargs) if kwargs else default


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
        entity_id = _get_entity_id_by_unique_id(entity_registry, sensor._attr_unique_id)
        if entity_id:
            entity_entry = entity_registry.async_get(entity_id)
            if entity_entry and entity_entry.name != new_name:
                entity_registry.async_update_entity(entity_id, name=new_name)
                _LOGGER.debug("Updated entity registry name for %s to '%s'", entity_id, new_name)
    except Exception as e:
        _LOGGER.debug("Failed to update entity registry name: %s", e)


def round_to_max_digits(value: float | None, max_digits: int = 3) -> float | None:
    """Round a value to a maximum number of significant digits.

    Args:
        value: The value to round
        max_digits: Maximum number of significant digits (default: 3)

    Returns:
        Rounded value, or None if input is None
    """
    if value is None:
        return None

    if value == 0:
        return 0.0

    # Calculate the number of decimal places needed for max_digits significant digits
    magnitude = math.floor(math.log10(abs(value)))
    decimal_places = max(0, max_digits - 1 - magnitude)

    # Round to the calculated decimal places
    return round(value, decimal_places)


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
    _clear_translation_cache()

    coordinator: EcoGuardDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    latest_reception_coordinator = hass.data[DOMAIN][entry.entry_id].get(
        "latest_reception_coordinator"
    )

    # Wait for initial data
    try:
        await coordinator.async_config_entry_first_refresh()
        if latest_reception_coordinator:
            await latest_reception_coordinator.async_config_entry_first_refresh()
    except Exception as err:
        _LOGGER.error("Failed to refresh coordinator data: %s", err)

    # Create sensors for each active installation
    sensors: list[SensorEntity] = []
    active_installations = coordinator.get_active_installations()

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
    for entry in entity_registry.entities.values():
        if entry.platform == DOMAIN and entry.unique_id:
            existing_unique_ids.add(entry.unique_id)

    _LOGGER.info("Creating %d EcoGuard sensors", len(sensors))
    async_add_entities(sensors, update_before_add=False)

    # Schedule entity registry updates to run after entities are registered
    # This ensures entity_ids match our desired format and individual meter sensors are disabled
    async def _update_entity_registry_after_setup() -> None:
        """Update entity registry after entities have been added."""
        # Wait a moment for entities to be registered
        await hass.async_block_till_done()

        entity_registry = async_get_entity_registry(hass)

        # Define which sensor classes are individual meter sensors (should be disabled by default)
        individual_meter_sensor_classes = (
            EcoGuardDailyConsumptionSensor,
            EcoGuardDailyCostSensor,
            EcoGuardMonthlyMeterSensor,
            EcoGuardLatestReceptionSensor,
        )

        for sensor in sensors:
            if hasattr(sensor, '_attr_unique_id') and sensor._attr_unique_id:
                unique_id = sensor._attr_unique_id
                # Remove domain prefix to get the entity_id suffix (object_id)
                if unique_id.startswith(f"{DOMAIN}_"):
                    object_id = unique_id[len(f"{DOMAIN}_"):]
                    desired_entity_id = f"sensor.{object_id}"

                    # Find the entity registry entry by unique_id
                    entity_entry = None
                    # Try to get entity_id first, then get the entry
                    entity_id = _get_entity_id_by_unique_id(entity_registry, unique_id)
                    if entity_id:
                        entity_entry = entity_registry.async_get(entity_id)
                    else:
                        # Fallback: search by unique_id
                        for entry in entity_registry.entities.values():
                            if entry.unique_id == unique_id and entry.platform == DOMAIN:
                                entity_entry = entry
                                break

                    if entity_entry:
                        # Update the entity_id if it doesn't match
                        if entity_entry.entity_id != desired_entity_id:
                            _LOGGER.info("Updating entity_id from %s to %s (unique_id=%s)",
                                       entity_entry.entity_id, desired_entity_id, unique_id)
                            try:
                                entity_registry.async_update_entity(
                                    entity_entry.entity_id,
                                    new_entity_id=desired_entity_id,
                                )
                                # Re-fetch entity_entry after entity_id update
                                entity_entry = entity_registry.async_get(desired_entity_id)
                            except ValueError as e:
                                # Entity ID might already exist, log and continue
                                _LOGGER.warning("Could not update entity_id to %s: %s", desired_entity_id, e)

                        # Update entity registry name for individual meter sensors to ensure translations are applied
                        # This is needed because the entity might not be registered when async_added_to_hass runs
                        # Build the translated name directly here to ensure it's correct
                        if isinstance(sensor, (EcoGuardDailyConsumptionSensor, EcoGuardDailyCostSensor,
                                             EcoGuardLatestReceptionSensor, EcoGuardMonthlyMeterSensor)):
                            try:
                                translated_name = None

                                if isinstance(sensor, EcoGuardDailyConsumptionSensor):
                                    # Get translated components
                                    measuring_point_display = sensor._measuring_point_name or await _async_get_translation(
                                        hass, "name.measuring_point", id=sensor._measuring_point_id
                                    )
                                    utility_name = await _async_get_translation(
                                        hass, f"utility.{sensor._utility_code.lower()}"
                                    )
                                    if utility_name == f"utility.{sensor._utility_code.lower()}":
                                        utility_name = sensor._utility_code
                                    consumption_daily = await _async_get_translation(hass, "name.consumption_daily")
                                    meter = await _async_get_translation(hass, "name.meter")
                                    translated_name = f'{consumption_daily} - {meter} "{measuring_point_display}" ({utility_name})'

                                elif isinstance(sensor, EcoGuardDailyCostSensor):
                                    measuring_point_display = sensor._measuring_point_name or await _async_get_translation(
                                        hass, "name.measuring_point", id=sensor._measuring_point_id
                                    )
                                    utility_name = await _async_get_translation(
                                        hass, f"utility.{sensor._utility_code.lower()}"
                                    )
                                    if utility_name == f"utility.{sensor._utility_code.lower()}":
                                        utility_name = sensor._utility_code
                                    cost_daily = await _async_get_translation(hass, "name.cost_daily")
                                    meter = await _async_get_translation(hass, "name.meter")
                                    if sensor._cost_type == "estimated":
                                        estimated = await _async_get_translation(hass, "name.estimated")
                                        translated_name = f'{cost_daily} {estimated} - {meter} "{measuring_point_display}" ({utility_name})'
                                    else:
                                        metered = await _async_get_translation(hass, "name.metered")
                                        translated_name = f'{cost_daily} {metered} - {meter} "{measuring_point_display}" ({utility_name})'

                                elif isinstance(sensor, EcoGuardLatestReceptionSensor):
                                    measuring_point_display = sensor._measuring_point_name or await _async_get_translation(
                                        hass, "name.measuring_point", id=sensor._measuring_point_id
                                    )
                                    reception_last_update = await _async_get_translation(hass, "name.reception_last_update")
                                    meter = await _async_get_translation(hass, "name.meter")
                                    if sensor._utility_code:
                                        utility_name = await _async_get_translation(
                                            hass, f"utility.{sensor._utility_code.lower()}"
                                        )
                                        if utility_name == f"utility.{sensor._utility_code.lower()}":
                                            utility_name = sensor._utility_code
                                        translated_name = f'{reception_last_update} - {meter} "{measuring_point_display}" ({utility_name})'
                                    else:
                                        translated_name = f'{reception_last_update} - {meter} "{measuring_point_display}"'

                                elif isinstance(sensor, EcoGuardMonthlyMeterSensor):
                                    measuring_point_display = sensor._measuring_point_name or await _async_get_translation(
                                        hass, "name.measuring_point", id=sensor._measuring_point_id
                                    )
                                    utility_name = await _async_get_translation(
                                        hass, f"utility.{sensor._utility_code.lower()}"
                                    )
                                    if utility_name == f"utility.{sensor._utility_code.lower()}":
                                        utility_name = sensor._utility_code
                                    if sensor._aggregate_type == "con":
                                        aggregate_name = await _async_get_translation(hass, "name.consumption_monthly_aggregated")
                                    else:
                                        aggregate_name = await _async_get_translation(hass, "name.cost_monthly_aggregated")
                                    if sensor._aggregate_type == "price" and sensor._cost_type == "estimated":
                                        estimated = await _async_get_translation(hass, "name.estimated")
                                        aggregate_name = f"{aggregate_name} {estimated}"
                                    elif sensor._aggregate_type == "price" and sensor._cost_type == "actual":
                                        metered = await _async_get_translation(hass, "name.metered")
                                        aggregate_name = f"{aggregate_name} {metered}"
                                    meter = await _async_get_translation(hass, "name.meter")
                                    translated_name = f'{aggregate_name} - {meter} "{measuring_point_display}" ({utility_name})'

                                if translated_name and entity_entry.name != translated_name:
                                    _LOGGER.debug("Updating entity registry name for %s from '%s' to '%s'",
                                                entity_entry.entity_id, entity_entry.name, translated_name)
                                    entity_registry.async_update_entity(
                                        entity_entry.entity_id,
                                        name=translated_name,
                                    )
                            except Exception as e:
                                _LOGGER.debug("Could not update entity registry name for %s: %s",
                                            entity_entry.entity_id, e)

                        # Only disable individual meter sensors if they're newly created (not in existing_unique_ids)
                        # This preserves the state of entities that existed before
                        if isinstance(sensor, individual_meter_sensor_classes):
                            is_new_entity = unique_id not in existing_unique_ids
                            if is_new_entity and entity_entry.disabled_by is None:
                                _LOGGER.info("Disabling newly created individual meter sensor: %s (unique_id=%s)",
                                           entity_entry.entity_id, unique_id)
                                try:
                                    entity_registry.async_update_entity(
                                        entity_entry.entity_id,
                                        disabled_by=RegistryEntryDisabler.INTEGRATION,
                                    )
                                except Exception as e:
                                    _LOGGER.warning("Could not disable entity %s: %s", entity_entry.entity_id, e)
                            elif not is_new_entity:
                                _LOGGER.debug("Preserving existing entity state for %s (unique_id=%s, disabled_by=%s)",
                                            entity_entry.entity_id, unique_id, entity_entry.disabled_by)
                    else:
                        _LOGGER.debug("Entity registry entry not yet found for unique_id=%s (may be created later)", unique_id)

    # Schedule the update to run after setup completes
    hass.async_create_task(_update_entity_registry_after_setup())


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
            measuring_point_display = _get_translation_default("name.measuring_point", id=measuring_point_id)

        # Use English defaults here; will be updated in async_added_to_hass with proper translations
        utility_name = _get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Consumption Daily - Meter "Measuring Point" (Utility)"
        # This ensures entity_id starts with "consumption_daily_" when slugified
        # The name will be updated in async_added_to_hass with proper translations
        # but the entity_id is already generated from this initial name
        consumption_daily = _get_translation_default("name.consumption_daily")
        meter = _get_translation_default("name.meter")
        self._attr_name = f'{consumption_daily} - {meter} "{measuring_point_display}" ({utility_name})'

        # Build unique_id following pattern: purpose_group_utility_sensor
        # Home Assistant strips the domain prefix, so we want: consumption_daily_cold_water_kaldtvann_bad
        # Use measuring_point_id to ensure uniqueness across nodes
        utility_slug = _utility_code_to_slug(utility_code)
        sensor_name = _slugify_name(measuring_point_name) or f"mp{measuring_point_id}"
        unique_id = (
            f"{DOMAIN}_consumption_daily_{utility_slug}_{sensor_name}"
        )
        self._attr_unique_id = unique_id
        _LOGGER.debug("Daily consumption sensor unique_id: %s", unique_id)

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        await self._async_fetch_value()

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
                measuring_point_display = await _async_get_translation(
                    self._hass, "name.measuring_point", id=self._measuring_point_id
                )
                _LOGGER.debug("Measuring point display: %s", measuring_point_display)

            utility_name = await _async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":  # Fallback if not found
                utility_name = self._utility_code
            _LOGGER.debug("Utility name: %s", utility_name)

            consumption_daily = await _async_get_translation(self._hass, "name.consumption_daily")
            _LOGGER.debug("Consumption daily: %s", consumption_daily)

            # Update the name (this is the display name, not the entity_id)
            # Format: "Consumption Daily - Meter "Measuring Point" (Utility)"
            # Keep "Consumption Daily" format to maintain entity_id starting with "consumption_daily_"
            meter = await _async_get_translation(self._hass, "name.meter")
            new_name = f'{consumption_daily} - {meter} "{measuring_point_display}" ({utility_name})'
            if self._attr_name != new_name:
                old_name = self._attr_name
                self._attr_name = new_name
                self.async_write_ha_state()
                _LOGGER.info("Updated sensor name from '%s' to '%s' (lang=%s)", old_name, new_name, lang)

            # Always update the entity registry name so it shows correctly in modals
            await _async_update_entity_registry_name(self, new_name)

            # Also update device name
            device_name = await _async_get_translation(
                self._hass, "name.device_name", node_id=self.coordinator.node_id
            )
            if self._attr_device_info.get("name") != device_name:
                self._attr_device_info["name"] = device_name
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.warning("Failed to update translated name: %s", e, exc_info=True)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching latest daily consumption value."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch last known daily consumption value asynchronously."""
        consumption_data = await self.coordinator.get_latest_consumption_value(
            utility_code=self._utility_code,
            measuring_point_id=self._measuring_point_id,
            external_key=self._installation.get("ExternalKey"),
        )

        if consumption_data:
            raw_value = consumption_data.get("value")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            self._attr_native_unit_of_measurement = consumption_data.get("unit")

            # Update last data date
            time_stamp = consumption_data.get("time")
            if time_stamp:
                self._last_data_date = datetime.fromtimestamp(time_stamp)
        else:
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = None
            self._last_data_date = None

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
            measuring_point_display = _get_translation_default("name.measuring_point", id=measuring_point_id)

        # Use English defaults here; will be updated in async_added_to_hass
        utility_suffix = ""
        if utility_code:
            utility_name = _get_translation_default(f"utility.{utility_code.lower()}")
            if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
                utility_name = utility_code
            utility_suffix = f" ({utility_name})"

        # Format: "Reception Last Update - Meter "Measuring Point" (Utility)"
        # This ensures entity_id starts with "reception_last_update_" when slugified
        # This will be updated in async_added_to_hass with proper translations
        reception_last_update = _get_translation_default("name.reception_last_update")
        meter = _get_translation_default("name.meter")
        if utility_suffix:
            # utility_suffix already includes parentheses, so we need to extract just the utility name
            utility_name = _get_translation_default(f"utility.{utility_code.lower()}")
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
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            if self._measuring_point_name:
                measuring_point_display = self._measuring_point_name
            else:
                measuring_point_display = await _async_get_translation(
                    self._hass, "name.measuring_point", id=self._measuring_point_id
                )

            # Keep "Reception Last Update" format to maintain entity_id starting with "reception_last_update_"
            # Format: "Reception Last Update - Meter "Measuring Point" (Utility)"
            # This groups similar sensors together when sorted alphabetically
            reception_last_update = await _async_get_translation(self._hass, "name.reception_last_update")
            meter = await _async_get_translation(self._hass, "name.meter")
            if self._utility_code:
                utility_name = await _async_get_translation(
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
        """Handle coordinator update by fetching latest reception."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

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
            aggregate_type: "con" for consumption, "price" for price
            cost_type: "actual" for metered API data, "estimated" for estimated (only for price)
        """
        super().__init__(coordinator)
        self._hass = hass
        self._utility_code = utility_code
        self._aggregate_type = aggregate_type
        self._cost_type = cost_type

        # Build sensor name
        # Use English defaults here; will be updated in async_added_to_hass
        utility_name = _get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        if aggregate_type == "con":
            # Use "Consumption Monthly Aggregated" format to ensure entity_id starts with "consumption_monthly_aggregated_"
            aggregate_name = _get_translation_default("name.consumption_monthly_aggregated")
        else:
            # Use "Cost Monthly Aggregated" format to ensure entity_id starts with "cost_monthly_aggregated_"
            aggregate_name = _get_translation_default("name.cost_monthly_aggregated")

        # Add cost type suffix for price sensors
        if aggregate_type == "price" and cost_type == "estimated":
            estimated = _get_translation_default("name.estimated")
            aggregate_name = f"{aggregate_name} {estimated}"
        elif aggregate_type == "price" and cost_type == "actual":
            metered = _get_translation_default("name.metered")
            aggregate_name = f"{aggregate_name} {metered}"

        # Format: "Aggregate Name - Utility"
        # This groups similar sensors together when sorted alphabetically
        self._attr_name = f"{aggregate_name} - {utility_name}"

        # Build unique ID following pattern: purpose_group_utility
        # Home Assistant strips the domain prefix, so we want: consumption_monthly_metered_cold_water
        utility_slug = _utility_code_to_slug(utility_code)
        if aggregate_type == "con":
            unique_id_suffix = f"consumption_monthly_metered_{utility_slug}"
        elif aggregate_type == "price" and cost_type == "estimated":
            unique_id_suffix = f"cost_monthly_estimated_{utility_slug}"
        else:
            # For "actual" cost_type, use "metered" in the ID for clarity
            unique_id_suffix = f"cost_monthly_metered_{utility_slug}"

        self._attr_unique_id = (
            f"{DOMAIN}_{unique_id_suffix}"
        )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            utility_name = await _async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            if self._aggregate_type == "con":
                # Keep "Consumption Monthly Aggregated" format to maintain entity_id starting with "consumption_monthly_aggregated_"
                aggregate_name = await _async_get_translation(self._hass, "name.consumption_monthly_aggregated")
            else:
                # Keep "Cost Monthly Aggregated" format to maintain entity_id starting with "cost_monthly_aggregated_"
                aggregate_name = await _async_get_translation(self._hass, "name.cost_monthly_aggregated")

            if self._aggregate_type == "price" and self._cost_type == "estimated":
                estimated = await _async_get_translation(self._hass, "name.estimated")
                aggregate_name = f"{aggregate_name} {estimated}"
            elif self._aggregate_type == "price" and self._cost_type == "actual":
                metered = await _async_get_translation(self._hass, "name.metered")
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
        """Handle coordinator update by fetching monthly aggregate."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch current month's aggregate value."""
        now = datetime.now()
        year = now.year
        month = now.month

        aggregate_data = await self.coordinator.get_monthly_aggregate(
            utility_code=self._utility_code,
            year=year,
            month=month,
            aggregate_type=self._aggregate_type,
            cost_type=self._cost_type if self._aggregate_type == "price" else "actual",
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
        else:
            self._attr_native_value = None
            # Always set unit even when no data to maintain consistency for statistics
            self._attr_native_unit_of_measurement = default_unit
            self._current_year = None
            self._current_month = None

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
        self._attr_name = _get_translation_default("name.cost_monthly_other_items")
        # Build unique_id following pattern: purpose_group_sensor
        # Home Assistant strips the domain prefix, so we want: cost_monthly_other_items
        self._attr_unique_id = (
            f"{DOMAIN}_cost_monthly_other_items"
        )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            # Keep "Cost Monthly Other Items" format to maintain entity_id starting with "cost_monthly_other_items"
            # The translation key might be used for display, but we keep the name format consistent
            new_name = await _async_get_translation(self._hass, "name.cost_monthly_other_items")
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching other items cost."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch current month's other items cost."""
        now = datetime.now()
        year = now.year
        month = now.month

        cost_data = await self.coordinator.get_monthly_other_items_cost(
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
        cost_monthly_aggregated = _get_translation_default("name.cost_monthly_aggregated")
        all_utilities = _get_translation_default("name.all_utilities")
        if cost_type == "estimated":
            estimated = _get_translation_default("name.estimated")
            self._attr_name = f"{cost_monthly_aggregated} {estimated} - {all_utilities}"
            # Build unique_id following pattern: purpose_group_total_type
            # Home Assistant strips the domain prefix, so we want: cost_monthly_total_estimated
            self._attr_unique_id = (
                f"{DOMAIN}_cost_monthly_total_estimated"
            )
        else:
            metered = _get_translation_default("name.metered")
            self._attr_name = f"{cost_monthly_aggregated} {metered} - {all_utilities}"
            # Build unique_id following pattern: purpose_group_total_type
            # Home Assistant strips the domain prefix, so we want: cost_monthly_total_metered
            self._attr_unique_id = (
                f"{DOMAIN}_cost_monthly_total_metered"
            )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            # Use "Cost Monthly Aggregated" format with "All Utilities" suffix
            cost_monthly_aggregated = await _async_get_translation(self._hass, "name.cost_monthly_aggregated")
            all_utilities = await _async_get_translation(self._hass, "name.all_utilities")
            if self._cost_type == "estimated":
                estimated = await _async_get_translation(self._hass, "name.estimated")
                new_name = f"{cost_monthly_aggregated} {estimated} - {all_utilities}"
            else:
                metered = await _async_get_translation(self._hass, "name.metered")
                new_name = f"{cost_monthly_aggregated} {metered} - {all_utilities}"
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching total monthly cost."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

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

        # Use "Cost Monthly End of Month Total Estimate" format to ensure entity_id starts with "cost_monthly_end_of_month_total_estimate_"
        # This will be updated in async_added_to_hass with proper translations
        self._attr_name = _get_translation_default("name.cost_monthly_end_of_month_total_estimate")
        # Build unique_id following pattern: purpose_group_sensor
        # Home Assistant strips the domain prefix, so we want: cost_monthly_end_of_month_total_estimate
        self._attr_unique_id = (
            f"{DOMAIN}_cost_monthly_end_of_month_total_estimate"
        )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            # Keep "Cost Monthly End of Month Total Estimate" format to maintain entity_id starting with "cost_monthly_end_of_month_total_estimate"
            new_name = await _async_get_translation(self._hass, "name.cost_monthly_end_of_month_total_estimate")
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching end-of-month estimate."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch end-of-month estimate asynchronously."""
        estimate_data = await self.coordinator.get_end_of_month_estimate()

        # Always set currency unit to prevent statistics issues
        default_currency = self.coordinator.get_setting("Currency") or ""

        if estimate_data:
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
        utility_name = _get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Consumption Daily - Utility"
        consumption_daily = _get_translation_default("name.consumption_daily")
        self._attr_name = f"{consumption_daily} - {utility_name}"

        # Build unique_id following pattern: consumption_daily_utility
        utility_slug = _utility_code_to_slug(utility_code)
        self._attr_unique_id = f"{DOMAIN}_consumption_daily_{utility_slug}"

        # Sensor attributes
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            utility_name = await _async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            consumption_daily = await _async_get_translation(self._hass, "name.consumption_daily")
            new_name = f"{consumption_daily} - {utility_name}"
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching aggregated daily consumption."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch aggregated daily consumption across all meters of this utility type."""
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

            # Fetch consumption for this meter
            consumption_data = await self.coordinator.get_latest_consumption_value(
                utility_code=self._utility_code,
                measuring_point_id=measuring_point_id,
                external_key=installation.get("ExternalKey"),
            )

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

        # Format: "Consumption Daily - Combined Water"
        consumption_daily = _get_translation_default("name.consumption_daily")
        water_name = _get_translation_default("name.combined_water")
        if water_name == "name.combined_water":  # Fallback if not found
            water_name = "Combined Water"
        self._attr_name = f"{consumption_daily} - {water_name}"

        # Build unique_id
        self._attr_unique_id = f"{DOMAIN}_consumption_daily_combined_water"

        # Sensor attributes
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            consumption_daily = await _async_get_translation(self._hass, "name.consumption_daily")
            water_name = await _async_get_translation(self._hass, "name.combined_water")
            if water_name == "name.combined_water":
                water_name = "Combined Water"
            new_name = f"{consumption_daily} - {water_name}"
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching combined daily consumption."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch combined daily consumption (HW + CW) across all meters."""
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

                # Fetch consumption for this meter
                consumption_data = await self.coordinator.get_latest_consumption_value(
                    utility_code=utility_code,
                    measuring_point_id=measuring_point_id,
                    external_key=installation.get("ExternalKey"),
                )

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
        if total_value > 0:
            self._attr_native_value = round_to_max_digits(total_value)
            self._attr_native_unit_of_measurement = unit
            if latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp)
            self._hw_meters_with_data = hw_meters_with_data
            self._cw_meters_with_data = cw_meters_with_data
        else:
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = unit
            self._last_data_date = None
            self._hw_meters_with_data = []
            self._cw_meters_with_data = []

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
            measuring_point_display = _get_translation_default("name.measuring_point", id=measuring_point_id)

        utility_name = _get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Cost Daily Metered/Estimated - Meter "Measuring Point" (Utility)"
        cost_daily = _get_translation_default("name.cost_daily")
        meter = _get_translation_default("name.meter")
        if cost_type == "estimated":
            estimated = _get_translation_default("name.estimated")
            self._attr_name = f'{cost_daily} {estimated} - {meter} "{measuring_point_display}" ({utility_name})'
        else:
            metered = _get_translation_default("name.metered")
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
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            if self._measuring_point_name:
                measuring_point_display = self._measuring_point_name
            else:
                measuring_point_display = await _async_get_translation(
                    self._hass, "name.measuring_point", id=self._measuring_point_id
                )

            utility_name = await _async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            cost_daily = await _async_get_translation(self._hass, "name.cost_daily")
            meter = await _async_get_translation(self._hass, "name.meter")
            if self._cost_type == "estimated":
                estimated = await _async_get_translation(self._hass, "name.estimated")
                new_name = f'{cost_daily} {estimated} - {meter} "{measuring_point_display}" ({utility_name})'
            else:
                metered = await _async_get_translation(self._hass, "name.metered")
                new_name = f'{cost_daily} {metered} - {meter} "{measuring_point_display}" ({utility_name})'

            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching latest daily cost value."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch last known daily cost value asynchronously."""
        cost_data = await self.coordinator.get_latest_cost_value(
            utility_code=self._utility_code,
            measuring_point_id=self._measuring_point_id,
            external_key=self._installation.get("ExternalKey"),
            cost_type=self._cost_type,
        )

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
        utility_name = _get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Cost Daily Metered/Estimated - Utility"
        cost_daily = _get_translation_default("name.cost_daily")
        if cost_type == "estimated":
            estimated = _get_translation_default("name.estimated")
            self._attr_name = f"{cost_daily} {estimated} - {utility_name}"
        else:
            metered = _get_translation_default("name.metered")
            self._attr_name = f"{cost_daily} {metered} - {utility_name}"

        # Build unique_id following pattern: cost_daily_metered/estimated_utility
        utility_slug = _utility_code_to_slug(utility_code)
        if cost_type == "estimated":
            self._attr_unique_id = f"{DOMAIN}_cost_daily_estimated_{utility_slug}"
        else:
            self._attr_unique_id = f"{DOMAIN}_cost_daily_metered_{utility_slug}"

        # Sensor attributes
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            utility_name = await _async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            cost_daily = await _async_get_translation(self._hass, "name.cost_daily")
            if self._cost_type == "estimated":
                estimated = await _async_get_translation(self._hass, "name.estimated")
                new_name = f"{cost_daily} {estimated} - {utility_name}"
            else:
                metered = await _async_get_translation(self._hass, "name.metered")
                new_name = f"{cost_daily} {metered} - {utility_name}"

            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching aggregated daily cost."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

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
            cost_data = await self.coordinator.get_latest_cost_value(
                utility_code=self._utility_code,
                measuring_point_id=measuring_point_id,
                external_key=installation.get("ExternalKey"),
                cost_type=self._cost_type,
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
        cost_daily = _get_translation_default("name.cost_daily")
        water_name = _get_translation_default("name.combined_water")
        if water_name == "name.combined_water":  # Fallback if not found
            water_name = "Combined Water"

        if cost_type == "estimated":
            estimated = _get_translation_default("name.estimated")
            self._attr_name = f"{cost_daily} {estimated} - {water_name}"
        else:
            metered = _get_translation_default("name.metered")
            self._attr_name = f"{cost_daily} {metered} - {water_name}"

        # Build unique_id
        if cost_type == "estimated":
            self._attr_unique_id = f"{DOMAIN}_cost_daily_estimated_combined_water"
        else:
            self._attr_unique_id = f"{DOMAIN}_cost_daily_metered_combined_water"

        # Sensor attributes
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            cost_daily = await _async_get_translation(self._hass, "name.cost_daily")
            water_name = await _async_get_translation(self._hass, "name.combined_water")
            if water_name == "name.combined_water":
                water_name = "Combined Water"

            if self._cost_type == "estimated":
                estimated = await _async_get_translation(self._hass, "name.estimated")
                new_name = f"{cost_daily} {estimated} - {water_name}"
            else:
                metered = await _async_get_translation(self._hass, "name.metered")
                new_name = f"{cost_daily} {metered} - {water_name}"

            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching combined daily cost."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch combined daily cost (HW + CW) across all meters."""
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

                # Fetch cost for this meter
                cost_data = await self.coordinator.get_latest_cost_value(
                    utility_code=utility_code,
                    measuring_point_id=measuring_point_id,
                    external_key=installation.get("ExternalKey"),
                    cost_type=self._cost_type,
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
        if total_value > 0:
            self._attr_native_value = round_to_max_digits(total_value)
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            if latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp)
            self._hw_meters_with_data = hw_meters_with_data
            self._cw_meters_with_data = cw_meters_with_data
        else:
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None
            self._hw_meters_with_data = []
            self._cw_meters_with_data = []

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
            aggregate_type: "con" for consumption, "price" for price
            cost_type: "actual" for metered API data, "estimated" for estimated (only for price)
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
            measuring_point_display = _get_translation_default("name.measuring_point", id=measuring_point_id)

        utility_name = _get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        if aggregate_type == "con":
            aggregate_name = _get_translation_default("name.consumption_monthly_aggregated")
        else:
            aggregate_name = _get_translation_default("name.cost_monthly_aggregated")

        # Add cost type suffix for price sensors
        if aggregate_type == "price" and cost_type == "estimated":
            estimated = _get_translation_default("name.estimated")
            aggregate_name = f"{aggregate_name} {estimated}"
        elif aggregate_type == "price" and cost_type == "actual":
            metered = _get_translation_default("name.metered")
            aggregate_name = f"{aggregate_name} {metered}"

        # Format: "Aggregate Name - Meter "Measuring Point" (Utility)"
        meter = _get_translation_default("name.meter")
        self._attr_name = f'{aggregate_name} - {meter} "{measuring_point_display}" ({utility_name})'

        # Build unique_id following pattern: purpose_group_utility_sensor
        utility_slug = _utility_code_to_slug(utility_code)
        sensor_name = _slugify_name(measuring_point_name) or f"mp{measuring_point_id}"
        if aggregate_type == "con":
            unique_id_suffix = f"consumption_monthly_{utility_slug}_{sensor_name}"
        elif aggregate_type == "price" and cost_type == "estimated":
            unique_id_suffix = f"cost_monthly_estimated_{utility_slug}_{sensor_name}"
        else:
            unique_id_suffix = f"cost_monthly_metered_{utility_slug}_{sensor_name}"

        self._attr_unique_id = f"{DOMAIN}_{unique_id_suffix}"

        # Sensor attributes
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            if self._measuring_point_name:
                measuring_point_display = self._measuring_point_name
            else:
                measuring_point_display = await _async_get_translation(
                    self._hass, "name.measuring_point", id=self._measuring_point_id
                )

            utility_name = await _async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            if self._aggregate_type == "con":
                aggregate_name = await _async_get_translation(self._hass, "name.consumption_monthly_aggregated")
            else:
                aggregate_name = await _async_get_translation(self._hass, "name.cost_monthly_aggregated")

            if self._aggregate_type == "price" and self._cost_type == "estimated":
                estimated = await _async_get_translation(self._hass, "name.estimated")
                aggregate_name = f"{aggregate_name} {estimated}"
            elif self._aggregate_type == "price" and self._cost_type == "actual":
                metered = await _async_get_translation(self._hass, "name.metered")
                aggregate_name = f"{aggregate_name} {metered}"

            meter = await _async_get_translation(self._hass, "name.meter")
            new_name = f'{aggregate_name} - {meter} "{measuring_point_display}" ({utility_name})'
            if self._attr_name != new_name:
                self._attr_name = new_name
                self.async_write_ha_state()

                # Also update the entity registry name so it shows correctly in modals
                await _async_update_entity_registry_name(self, new_name)
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching monthly value."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

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
            aggregate_type: "con" for consumption, "price" for price
            cost_type: "actual" for metered API data, "estimated" for estimated (only for price)
        """
        super().__init__(coordinator)
        self._hass = hass
        self._aggregate_type = aggregate_type
        self._cost_type = cost_type

        if aggregate_type == "con":
            aggregate_name = _get_translation_default("name.consumption_monthly_aggregated")
        else:
            aggregate_name = _get_translation_default("name.cost_monthly_aggregated")

        if aggregate_type == "price" and cost_type == "estimated":
            estimated = _get_translation_default("name.estimated")
            aggregate_name = f"{aggregate_name} {estimated}"
        elif aggregate_type == "price" and cost_type == "actual":
            metered = _get_translation_default("name.metered")
            aggregate_name = f"{aggregate_name} {metered}"

        # Format: "Aggregate Name - Combined Water"
        water_name = _get_translation_default("name.combined_water")
        if water_name == "name.combined_water":  # Fallback if not found
            water_name = "Combined Water"
        self._attr_name = f"{aggregate_name} - {water_name}"

        # Build unique_id following pattern: purpose_group_combined_water
        if aggregate_type == "con":
            unique_id_suffix = "consumption_monthly_combined_water"
        elif aggregate_type == "price" and cost_type == "estimated":
            unique_id_suffix = "cost_monthly_estimated_combined_water"
        else:
            unique_id_suffix = "cost_monthly_metered_combined_water"

        self._attr_unique_id = f"{DOMAIN}_{unique_id_suffix}"

        # Sensor attributes
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
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
            self._attr_native_unit_of_measurement = None
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
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            if self._aggregate_type == "con":
                aggregate_name = await _async_get_translation(self._hass, "name.consumption_monthly_aggregated")
            else:
                aggregate_name = await _async_get_translation(self._hass, "name.cost_monthly_aggregated")

            if self._aggregate_type == "price" and self._cost_type == "estimated":
                estimated = await _async_get_translation(self._hass, "name.estimated")
                aggregate_name = f"{aggregate_name} {estimated}"
            elif self._aggregate_type == "price" and self._cost_type == "actual":
                metered = await _async_get_translation(self._hass, "name.metered")
                aggregate_name = f"{aggregate_name} {metered}"

            water_name = await _async_get_translation(self._hass, "name.combined_water")
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
        """Handle coordinator update by fetching combined water value."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

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
