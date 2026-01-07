"""The EcoGuard integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import EcoGuardAPI
from .coordinator import EcoGuardDataUpdateCoordinator, EcoGuardLatestReceptionCoordinator

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ecoguard"
PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the EcoGuard component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EcoGuard from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Get credentials from config entry
    username = entry.data["username"]
    password = entry.data["password"]
    domain = entry.data["domain"]
    node_id = entry.data.get("node_id")
    nord_pool_area = entry.data.get("nord_pool_area")

    # Create API client
    api = EcoGuardAPI(
        username=username,
        password=password,
        domain=domain,
    )

    # Create coordinators
    coordinator = EcoGuardDataUpdateCoordinator(
        hass=hass,
        api=api,
        node_id=node_id,
        domain=domain,
        nord_pool_area=nord_pool_area,
    )
    
    # Create separate coordinator for latest reception (updates more frequently)
    latest_reception_coordinator = EcoGuardLatestReceptionCoordinator(
        hass=hass,
        api=api,
        node_id=node_id,
    )

    # Store coordinators
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "latest_reception_coordinator": latest_reception_coordinator,
        "api": api,
    }

    # Forward entry setup to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Clean up API client and cancel background tasks
        if entry.entry_id in hass.data[DOMAIN]:
            # Cancel entity registry update task if it exists
            # Just cancel it, don't wait - the task has its own timeout and will exit quickly
            update_task = hass.data[DOMAIN][entry.entry_id].get("entity_registry_update_task")
            if update_task and not update_task.done():
                update_task.cancel()
                # Don't wait - task will handle its own cleanup with timeout
            
            # Cancel pending coordinator requests
            # Just cancel them, don't wait - they'll handle their own cleanup
            coordinator = hass.data[DOMAIN][entry.entry_id].get("coordinator")
            if coordinator and hasattr(coordinator, "_pending_requests"):
                for cache_key, task in list(coordinator._pending_requests.items()):
                    if not task.done():
                        task.cancel()
                coordinator._pending_requests.clear()
            
            # Close API client (this will close the aiohttp session)
            api = hass.data[DOMAIN][entry.entry_id].get("api")
            if api:
                await api.async_close()
            hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

