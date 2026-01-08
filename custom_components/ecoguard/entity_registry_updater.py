"""Entity registry updater for EcoGuard integration."""

from __future__ import annotations

from typing import Any
import logging
import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
    RegistryEntryDisabler,
)

from .const import DOMAIN
from .translations import async_get_translation

_LOGGER = logging.getLogger(__name__)


def get_entity_id_by_unique_id(
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


async def update_entity_registry_after_setup(
    hass: HomeAssistant,
    sensors: list[Any],
    existing_unique_ids: set[str],
    individual_meter_sensor_classes: tuple[type, ...],
) -> None:
    """Update entity registry after entities have been added.

    Args:
        hass: Home Assistant instance
        sensors: List of sensor entities to update
        existing_unique_ids: Set of unique IDs that existed before setup
        individual_meter_sensor_classes: Tuple of sensor classes that should be disabled by default
    """
    try:
        # Early exit if Home Assistant is stopping
        if hass.is_stopping:
            return

        # Note: We don't use async_block_till_done() here as it can cause hangs by waiting
        # for all pending tasks, including ones that might be waiting on other operations

        # No delay - proceed immediately to speed up execution
        # If entities aren't found on first try, retry mechanism will handle it

        # Retry mechanism: attempt to find entities with sufficient retries
        # Entities may take longer to be registered, especially during system startup
        # Using 4 retries with 0.5s delay gives up to 2 seconds total wait time
        max_retries = 4
        retry_delay = 0.5  # seconds - balance between reliability and performance

        entity_registry = async_get_entity_registry(hass)

        # Pre-fetch all translations we'll need to avoid repeated async calls in the loop
        # This significantly improves performance when there are many sensors
        translation_cache: dict[str, str] = {}
        translation_keys_to_fetch = [
            "name.consumption_daily",
            "name.cost_daily",
            "name.meter",
            "name.estimated",
            "name.metered",
            "name.reception_last_update",
            "name.consumption_monthly_aggregated",
            "name.cost_monthly_aggregated",
        ]

        # Collect unique utility codes and measuring point IDs
        utility_codes = set()
        measuring_point_ids = set()
        for sensor in sensors:
            if isinstance(sensor, individual_meter_sensor_classes):
                if hasattr(sensor, '_utility_code') and sensor._utility_code:
                    utility_codes.add(sensor._utility_code.lower())
                if hasattr(sensor, '_measuring_point_id') and sensor._measuring_point_id:
                    measuring_point_ids.add(sensor._measuring_point_id)

        # Fetch all common translation keys in parallel
        if not hass.is_stopping:
            translation_tasks = [
                async_get_translation(hass, key) for key in translation_keys_to_fetch
            ]
            # Also fetch utility translations
            for utility_code in utility_codes:
                translation_tasks.append(async_get_translation(hass, f"utility.{utility_code}"))

            # Fetch all translations in parallel
            translation_results = await asyncio.gather(*translation_tasks, return_exceptions=True)

            # Store results in cache
            idx = 0
            for key in translation_keys_to_fetch:
                if idx < len(translation_results) and not isinstance(translation_results[idx], Exception):
                    translation_cache[key] = translation_results[idx]
                idx += 1

            # Store utility translations
            for utility_code in utility_codes:
                if idx < len(translation_results) and not isinstance(translation_results[idx], Exception):
                    translation_cache[f"utility.{utility_code}"] = translation_results[idx]
                idx += 1

        # Helper function to get translation from cache or fetch if missing
        async def get_cached_translation(key: str, **kwargs: Any) -> str:
            """Get translation from cache or fetch if not cached."""
            cache_key = key
            if kwargs:
                # For keys with parameters, we can't cache them generically
                # but we can still use the cache for the base key
                if key == "name.measuring_point" and "id" in kwargs:
                    # Measuring point names are usually already set, so we'll fetch on demand
                    # but try cache first for the base key
                    pass

            if cache_key in translation_cache:
                result = translation_cache[cache_key]
                # Apply formatting if kwargs provided
                if kwargs:
                    try:
                        return result.format(**kwargs)
                    except (KeyError, ValueError):
                        # If formatting fails, fall back to async_get_translation
                        return await async_get_translation(hass, key, **kwargs)
                return result

            # Not in cache, fetch it
            result = await async_get_translation(hass, key, **kwargs)
            translation_cache[cache_key] = result
            return result

        for sensor in sensors:
            # Check if Home Assistant is stopping before processing each sensor
            if hass.is_stopping:
                return

            if hasattr(sensor, '_attr_unique_id') and sensor._attr_unique_id:
                unique_id = sensor._attr_unique_id
                # Remove domain prefix to get the entity_id suffix (object_id)
                if unique_id.startswith(f"{DOMAIN}_"):
                    object_id = unique_id[len(f"{DOMAIN}_"):]
                    desired_entity_id = f"sensor.{object_id}"

                    # Find the entity registry entry by unique_id with retry mechanism
                    # This handles race conditions where entities might not be immediately registered
                    entity_entry = None
                    for attempt in range(max_retries):
                        # Check if Home Assistant is stopping before each retry attempt
                        if hass.is_stopping:
                            return
                        # Try to get entity_id first, then get the entry
                        entity_id = get_entity_id_by_unique_id(entity_registry, unique_id)
                        if entity_id:
                            entity_entry = entity_registry.async_get(entity_id)
                            if entity_entry:
                                break

                        # Fallback: search by unique_id
                        if not entity_entry:
                            for entry in entity_registry.entities.values():
                                if entry.unique_id == unique_id and entry.platform == DOMAIN:
                                    entity_entry = entry
                                    break

                        if entity_entry:
                            break

                        # If not found and we have retries left, wait and try again
                        if attempt < max_retries - 1:
                            # Check if Home Assistant is stopping before retrying
                            if hass.is_stopping:
                                return

                            _LOGGER.debug(
                                "Entity registry entry not found for unique_id=%s (attempt %d/%d), retrying...",
                                unique_id, attempt + 1, max_retries
                            )
                            try:
                                await asyncio.sleep(retry_delay)
                            except asyncio.CancelledError:
                                return

                            # Check again if Home Assistant is stopping
                            if hass.is_stopping:
                                return

                            # Refresh entity registry to get latest state
                            entity_registry = async_get_entity_registry(hass)

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
                        # Skip translation updates if Home Assistant is stopping to avoid delays during shutdown
                        if not hass.is_stopping and isinstance(sensor, individual_meter_sensor_classes):
                            try:
                                translated_name = None

                                # Import sensor classes here to avoid circular imports
                                from .sensor import (
                                    EcoGuardDailyConsumptionSensor,
                                    EcoGuardDailyCostSensor,
                                    EcoGuardLatestReceptionSensor,
                                    EcoGuardMonthlyMeterSensor,
                                )

                                if isinstance(sensor, EcoGuardDailyConsumptionSensor):
                                    # Get translated components using cached translations
                                    measuring_point_display = sensor._measuring_point_name or await get_cached_translation(
                                        "name.measuring_point", id=sensor._measuring_point_id
                                    )
                                    utility_name = await get_cached_translation(
                                        f"utility.{sensor._utility_code.lower()}"
                                    )
                                    if utility_name == f"utility.{sensor._utility_code.lower()}":
                                        utility_name = sensor._utility_code
                                    consumption_daily = await get_cached_translation("name.consumption_daily")
                                    meter = await get_cached_translation("name.meter")
                                    translated_name = f'{consumption_daily} - {meter} "{measuring_point_display}" ({utility_name})'

                                elif isinstance(sensor, EcoGuardDailyCostSensor):
                                    measuring_point_display = sensor._measuring_point_name or await get_cached_translation(
                                        "name.measuring_point", id=sensor._measuring_point_id
                                    )
                                    utility_name = await get_cached_translation(
                                        f"utility.{sensor._utility_code.lower()}"
                                    )
                                    if utility_name == f"utility.{sensor._utility_code.lower()}":
                                        utility_name = sensor._utility_code
                                    cost_daily = await get_cached_translation("name.cost_daily")
                                    meter = await get_cached_translation("name.meter")
                                    if sensor._cost_type == "estimated":
                                        estimated = await get_cached_translation("name.estimated")
                                        translated_name = f'{cost_daily} {estimated} - {meter} "{measuring_point_display}" ({utility_name})'
                                    else:
                                        metered = await get_cached_translation("name.metered")
                                        translated_name = f'{cost_daily} {metered} - {meter} "{measuring_point_display}" ({utility_name})'

                                elif isinstance(sensor, EcoGuardLatestReceptionSensor):
                                    measuring_point_display = sensor._measuring_point_name or await get_cached_translation(
                                        "name.measuring_point", id=sensor._measuring_point_id
                                    )
                                    reception_last_update = await get_cached_translation("name.reception_last_update")
                                    meter = await get_cached_translation("name.meter")
                                    if sensor._utility_code:
                                        utility_name = await get_cached_translation(
                                            f"utility.{sensor._utility_code.lower()}"
                                        )
                                        if utility_name == f"utility.{sensor._utility_code.lower()}":
                                            utility_name = sensor._utility_code
                                        translated_name = f'{reception_last_update} - {meter} "{measuring_point_display}" ({utility_name})'
                                    else:
                                        translated_name = f'{reception_last_update} - {meter} "{measuring_point_display}"'

                                elif isinstance(sensor, EcoGuardMonthlyMeterSensor):
                                    measuring_point_display = sensor._measuring_point_name or await get_cached_translation(
                                        "name.measuring_point", id=sensor._measuring_point_id
                                    )
                                    utility_name = await get_cached_translation(
                                        f"utility.{sensor._utility_code.lower()}"
                                    )
                                    if utility_name == f"utility.{sensor._utility_code.lower()}":
                                        utility_name = sensor._utility_code
                                    if sensor._aggregate_type == "con":
                                        aggregate_name = await get_cached_translation("name.consumption_monthly_aggregated")
                                    else:
                                        aggregate_name = await get_cached_translation("name.cost_monthly_aggregated")
                                    if sensor._aggregate_type == "price" and sensor._cost_type == "estimated":
                                        estimated = await get_cached_translation("name.estimated")
                                        aggregate_name = f"{aggregate_name} {estimated}"
                                    elif sensor._aggregate_type == "price" and sensor._cost_type == "actual":
                                        metered = await get_cached_translation("name.metered")
                                        aggregate_name = f"{aggregate_name} {metered}"
                                    meter = await get_cached_translation("name.meter")
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
                        _LOGGER.debug(
                            "Entity registry entry not found for unique_id=%s after %d retries (entity may be created later)",
                            unique_id, max_retries
                        )
    except asyncio.CancelledError:
        _LOGGER.debug("Entity registry update task cancelled")
        raise
    except Exception as e:
        _LOGGER.warning("Error in entity registry update task: %s", e)


async def update_entity_registry_with_timeout(
    hass: HomeAssistant,
    sensors: list[Any],
    existing_unique_ids: set[str],
    individual_meter_sensor_classes: tuple[type, ...],
    timeout: float = 3.0,
) -> None:
    """Update entity registry with timeout wrapper.

    Args:
        hass: Home Assistant instance
        sensors: List of sensor entities to update
        existing_unique_ids: Set of unique IDs that existed before setup
        individual_meter_sensor_classes: Tuple of sensor classes that should be disabled by default
        timeout: Timeout in seconds (default: 3.0)
    """
    try:
        await asyncio.wait_for(
            update_entity_registry_after_setup(
                hass, sensors, existing_unique_ids, individual_meter_sensor_classes
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _LOGGER.debug("Entity registry update task timed out after %s seconds", timeout)
    except asyncio.CancelledError:
        _LOGGER.debug("Entity registry update task was cancelled")
        raise
