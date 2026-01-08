"""Helper utilities for EcoGuard sensor classes."""

from __future__ import annotations

from typing import Any
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
