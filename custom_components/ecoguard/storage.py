"""Storage helper for EcoGuard integration cache."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_cache"


async def load_cached_data(hass: HomeAssistant, key: str) -> dict[str, Any] | None:
    """Load cached data for a config entry.

    Args:
        hass: Home Assistant instance
        key: Storage key (can be entry_id or domain)

    Returns:
        Cached data dict with keys: installations, measuring_points, node_data, settings
        Returns None if no cache exists or on error
    """
    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{key}")

    try:
        data = await store.async_load()
        if data:
            _LOGGER.debug("Loaded cached data for key %s", key)
            return data
        else:
            _LOGGER.debug("No cached data found for key %s", key)
            return None
    except Exception as err:
        _LOGGER.warning("Failed to load cached data for key %s: %s", key, err)
        return None


async def save_cached_data(
    hass: HomeAssistant,
    key: str,
    installations: list[dict[str, Any]] | None = None,
    measuring_points: list[dict[str, Any]] | None = None,
    node_data: dict[str, Any] | None = None,
    settings: list[dict[str, Any]] | None = None,
) -> None:
    """Save cached data for a config entry.

    Args:
        hass: Home Assistant instance
        key: Storage key (can be entry_id or domain)
        installations: List of installations to cache
        measuring_points: List of measuring points to cache
        node_data: Node data to cache
        settings: List of settings to cache
    """
    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{key}")

    # Load existing data first to preserve fields we're not updating
    existing_data = await load_cached_data(hass, key) or {}

    # Update only the fields that are provided
    if installations is not None:
        existing_data["installations"] = installations
    if measuring_points is not None:
        existing_data["measuring_points"] = measuring_points
    if node_data is not None:
        existing_data["node_data"] = node_data
    if settings is not None:
        existing_data["settings"] = settings

    try:
        await store.async_save(existing_data)
        _LOGGER.debug("Saved cached data for key %s", key)
    except Exception as err:
        _LOGGER.warning("Failed to save cached data for key %s: %s", key, err)


async def migrate_cache_from_domain(
    hass: HomeAssistant, domain: str, entry_id: str
) -> None:
    """Migrate cached data from domain key to entry_id key.

    Args:
        hass: Home Assistant instance
        domain: Domain name used as temporary key
        entry_id: Config entry ID to migrate to
    """
    data = await load_cached_data(hass, domain)
    if data:
        _LOGGER.debug("Migrating cache from domain %s to entry_id %s", domain, entry_id)
        await save_cached_data(
            hass,
            entry_id,
            installations=data.get("installations"),
            measuring_points=data.get("measuring_points"),
            node_data=data.get("node_data"),
            settings=data.get("settings"),
        )
        # Delete the old domain-based cache
        try:
            store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{domain}")
            await store.async_remove()
            _LOGGER.debug(
                "Successfully migrated and removed old cache for domain %s", domain
            )
        except Exception as err:
            _LOGGER.warning("Failed to remove old cache for domain %s: %s", domain, err)
