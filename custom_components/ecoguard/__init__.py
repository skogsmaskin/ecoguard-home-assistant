"""The EcoGuard integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .api import EcoGuardAPI
from .coordinator import EcoGuardDataUpdateCoordinator, EcoGuardLatestReceptionCoordinator

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ecoguard"
PLATFORMS: list[Platform] = [Platform.SENSOR]


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
        # Clean up API client
        if entry.entry_id in hass.data[DOMAIN]:
            api = hass.data[DOMAIN][entry.entry_id].get("api")
            if api:
                await api.async_close()
            hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

