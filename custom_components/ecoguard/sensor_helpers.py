"""Helper utilities for EcoGuard sensor classes."""

from __future__ import annotations

from typing import Any, Callable
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from .entity_registry_updater import get_entity_id_by_unique_id

_LOGGER = logging.getLogger(__name__)


async def async_update_entity_registry_name(
    sensor: SensorEntity, new_name: str
) -> None:
    """Update the entity registry name for a sensor.

    This helper function centralizes the logic for updating entity registry names
    to reduce code duplication across sensor classes.

    Args:
        sensor: The sensor entity instance
        new_name: The new name to set in the entity registry
    """
    if not hasattr(sensor, "_attr_unique_id") or not sensor._attr_unique_id:
        return

    if not hasattr(sensor, "hass") or not sensor.hass:
        return

    try:
        entity_registry = async_get_entity_registry(sensor.hass)
        # Try to get entity_id by unique_id
        entity_id = get_entity_id_by_unique_id(entity_registry, sensor._attr_unique_id)
        if entity_id:
            entity_entry = entity_registry.async_get(entity_id)
            if entity_entry and entity_entry.name != new_name:
                entity_registry.async_update_entity(entity_id, name=new_name)
                _LOGGER.debug(
                    "Updated entity registry name for %s to '%s'", entity_id, new_name
                )
    except Exception as e:
        _LOGGER.debug("Failed to update entity registry name: %s", e)


def slugify_name(name: str | None) -> str:
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


def utility_code_to_slug(utility_code: str) -> str:
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


def collect_meters_with_data(
    active_installations: list[dict[str, Any]],
    utility_code: str,
    coordinator: Any,
    get_meter_data: Callable[[int, str], dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """Collect meters with data for a given utility code.

    This is a shared helper function used by both daily and monthly aggregate sensors
    to collect which meters have data, avoiding code duplication.

    Args:
        active_installations: List of active installations.
        utility_code: Utility code to filter by (e.g., "HW", "CW").
        coordinator: The coordinator instance (for accessing measuring points).
        get_meter_data: Callable that takes (measuring_point_id, utility_code) and
            returns meter data dict with "value" key, or None if no data.

    Returns:
        List of meters with data, each containing measuring_point_id,
        measuring_point_name, and value.
    """
    meters_with_data = []

    for installation in active_installations:
        registers = installation.get("Registers", [])
        measuring_point_id = installation.get("MeasuringPointID")

        # Check if this installation has the utility we're looking for
        has_utility = False
        for register in registers:
            if register.get("UtilityCode") == utility_code:
                has_utility = True
                break

        if not has_utility:
            continue

        # Get measuring point name
        measuring_point_name = None
        for mp in coordinator.get_measuring_points():
            if mp.get("ID") == measuring_point_id:
                measuring_point_name = mp.get("Name")
                break

        # Check if this meter has data using the provided callback
        meter_data = get_meter_data(measuring_point_id, utility_code)

        if meter_data and meter_data.get("value") is not None:
            meters_with_data.append(
                {
                    "measuring_point_id": measuring_point_id,
                    "measuring_point_name": measuring_point_name,
                    "value": meter_data.get("value", 0.0),
                }
            )

    return meters_with_data


def create_monthly_meter_data_getter(
    monthly_cache: dict[str, Any],
    daily_cache: dict[str, Any],
    aggregate_type: str,
    cost_type: str,
    year: int,
    month: int,
    from_time: int,
    to_time: int,
) -> Callable[[int, str], dict[str, Any] | None]:
    """Create a get_meter_data callback for monthly sensors.

    This helper creates a callback function that:
    1. First tries to get data from monthly aggregate cache
    2. Falls back to calculating from daily consumption cache if not found

    Args:
        monthly_cache: Monthly aggregate cache dictionary.
        daily_cache: Daily consumption cache dictionary.
        aggregate_type: "con" for consumption, "price" for price/cost.
        cost_type: "actual" for metered API data, "estimated" for estimated.
        year: Year to check.
        month: Month to check.
        from_time: Start timestamp for the month.
        to_time: End timestamp for the month.

    Returns:
        A callback function that takes (measuring_point_id, utility_code)
        and returns meter data dict or None.
    """
    def get_meter_data(
        measuring_point_id: int, utility_code: str
    ) -> dict[str, Any] | None:
        """Get meter data from monthly cache or calculate from daily cache."""
        # First try monthly aggregate cache
        cache_key = f"{utility_code}_{measuring_point_id}_{year}_{month}_{aggregate_type}_{cost_type}"
        meter_data = monthly_cache.get(cache_key)

        if meter_data and meter_data.get("value") is not None:
            return meter_data

        # If not in monthly cache and this is consumption, calculate from daily cache
        if aggregate_type == "con" and daily_cache:
            daily_cache_key = f"{utility_code}_{measuring_point_id}"
            daily_values = daily_cache.get(daily_cache_key)

            if daily_values:
                # Filter daily values for this month
                month_values = [
                    v
                    for v in daily_values
                    if from_time <= v.get("time", 0) < to_time
                    and v.get("value") is not None
                ]

                if month_values:
                    total_value = sum(v["value"] for v in month_values)
                    unit = month_values[0].get("unit", "") if month_values else ""
                    return {
                        "value": total_value,
                        "unit": unit,
                    }

        return None

    return get_meter_data
