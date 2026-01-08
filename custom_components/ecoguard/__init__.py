"""The EcoGuard integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import EcoGuardAPI
from .coordinator import EcoGuardDataUpdateCoordinator, EcoGuardLatestReceptionCoordinator
from .storage import migrate_cache_from_domain

if TYPE_CHECKING:
    from asyncio import Task

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ecoguard"
PLATFORMS: list[Platform] = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class EcoGuardRuntimeData:
    """Runtime data for EcoGuard config entry."""

    coordinator: EcoGuardDataUpdateCoordinator
    latest_reception_coordinator: EcoGuardLatestReceptionCoordinator
    api: EcoGuardAPI
    entity_registry_update_task: Task | None = None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the EcoGuard component.
    
    This is called once when the integration is loaded.
    Service actions (if any) should be registered here, not in async_setup_entry.
    See: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/action-setup
    """
    # No service actions currently, but this is where they would be registered
    # if we add any in the future
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EcoGuard from a config entry."""
    _LOGGER.debug("Setting up entry %s for domain %s", entry.entry_id, entry.data.get("domain"))

    # Get credentials from config entry
    username = entry.data["username"]
    password = entry.data["password"]
    domain = entry.data["domain"]
    node_id = entry.data.get("node_id")
    nord_pool_area = entry.data.get("nord_pool_area")

    if not node_id:
        _LOGGER.error("Missing node_id in config entry data: %s", entry.data)
        return False

    # Migrate cache from domain key to entry_id if needed (silently - don't log if domain key doesn't exist)
    try:
        # Check if domain-based cache exists before trying to migrate
        from .storage import load_cached_data
        domain_cache = await load_cached_data(hass, domain)
        if domain_cache:
            _LOGGER.debug("Found domain-based cache, migrating to entry_id")
            await migrate_cache_from_domain(hass, domain, entry.entry_id)
    except Exception as err:
        _LOGGER.warning("Failed to migrate cache, continuing anyway: %s", err)

    # Create API client
    api = EcoGuardAPI(
        username=username,
        password=password,
        domain=domain,
    )

    try:
        # Create coordinators
        _LOGGER.debug("Creating coordinators for node_id: %s", node_id)
        coordinator = EcoGuardDataUpdateCoordinator(
            hass=hass,
            api=api,
            node_id=node_id,
            domain=domain,
            nord_pool_area=nord_pool_area,
            entry_id=entry.entry_id,
        )
        
        # Create separate coordinator for latest reception (updates more frequently)
        latest_reception_coordinator = EcoGuardLatestReceptionCoordinator(
            hass=hass,
            api=api,
            node_id=node_id,
        )

        # Store runtime data using ConfigEntry.runtime_data (recommended pattern)
        # This is better than hass.data[DOMAIN][entry.entry_id] as it's type-safe
        # and automatically cleaned up when the entry is unloaded
        entry.runtime_data = EcoGuardRuntimeData(
            coordinator=coordinator,
            latest_reception_coordinator=latest_reception_coordinator,
            api=api,
        )

        # Forward entry setup to sensor platform
        _LOGGER.debug("Forwarding entry setup to platforms")
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Entry setup completed successfully")
        
        # Fetch data function (reusable for both startup and reload)
        async def _fetch_batch_data():
            """Fetch batch data in background."""
            try:
                await coordinator._batch_fetch_sensor_data()
                _LOGGER.info("Background data fetch completed")
            except Exception as err:
                _LOGGER.warning("Error in background data fetch: %s", err, exc_info=True)
        
        async def _fetch_latest_reception():
            """Fetch latest reception data in background."""
            try:
                latest_reception = await api.get_latest_reception(node_id)
                if latest_reception:
                    latest_reception_coordinator.async_set_updated_data(latest_reception)
                    _LOGGER.debug("Latest reception data updated: %d entries", len(latest_reception))
            except Exception as err:
                _LOGGER.warning("Error fetching latest reception: %s", err, exc_info=True)
        
        # Check if Home Assistant is already started (reload scenario)
        if hass.state == CoreState.running:
            # HA is already running, so this is a reload - trigger data fetch immediately
            _LOGGER.info("Home Assistant already running, triggering data refresh after reload")
            hass.async_create_task(_fetch_batch_data())
            hass.async_create_task(_fetch_latest_reception())
        else:
            # HA is still starting, wait for the started event
            async def _start_data_fetching_after_startup(event):
                """Start fetching data after Home Assistant has fully started."""
                _LOGGER.debug("Home Assistant started, beginning background data fetch")
                hass.async_create_task(_fetch_batch_data())
                hass.async_create_task(_fetch_latest_reception())
            
            # Listen for homeassistant_started event to begin data fetching
            hass.bus.async_listen_once("homeassistant_started", _start_data_fetching_after_startup)
        
        return True
    except Exception as err:
        _LOGGER.exception("Error setting up entry %s: %s", entry.entry_id, err)
        # Clean up API if setup fails
        try:
            await api.async_close()
        except Exception:
            pass
        return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and hasattr(entry, "runtime_data"):
        runtime_data: EcoGuardRuntimeData = entry.runtime_data
        
        # Cancel entity registry update task if it exists
        # Just cancel it, don't wait - the task has its own timeout and will exit quickly
        if runtime_data.entity_registry_update_task and not runtime_data.entity_registry_update_task.done():
            try:
                runtime_data.entity_registry_update_task.cancel()
            except Exception as e:
                # Task may already be cancelled or in an invalid state
                _LOGGER.debug("Error cancelling entity registry update task: %s", e)
        
        # Cancel pending coordinator requests
        # Just cancel them, don't wait - they'll handle their own cleanup
        coordinator = runtime_data.coordinator
        if coordinator and hasattr(coordinator, "_pending_requests"):
            for cache_key, task in list(coordinator._pending_requests.items()):
                if not task.done():
                    try:
                        task.cancel()
                    except Exception as e:
                        # Task may already be cancelled or in an invalid state
                        _LOGGER.debug("Error cancelling pending request task for %s: %s", cache_key, e)
            coordinator._pending_requests.clear()
        
        # Close API client (this will close the aiohttp session)
        if runtime_data.api:
            await runtime_data.api.async_close()
        
        # Clear runtime data (will be automatically cleaned up, but explicit is better)
        entry.runtime_data = None

    return unload_ok


async def trigger_data_fetch_for_entry(hass: HomeAssistant, entry_id: str) -> None:
    """Trigger data fetching for a specific config entry.
    
    This is called after a new integration is added via the config flow
    to update sensors with real values without blocking the setup process.?
    """
    _LOGGER.debug("Triggering data fetch for entry: %s", entry_id)
    
    # Get the entry
    entry = hass.config_entries.async_get_entry(entry_id)
    if not entry:
        _LOGGER.warning("Entry %s not found, cannot trigger data fetch", entry_id)
        return
    
    # Check if entry has runtime data (setup must be complete)
    if not hasattr(entry, "runtime_data") or entry.runtime_data is None:
        _LOGGER.debug("Entry %s not yet set up, will fetch data after setup", entry_id)
        # Schedule a delayed retry
        async def _retry_after_setup():
            import asyncio
            await asyncio.sleep(2.0)  # Wait for setup to complete
            await trigger_data_fetch_for_entry(hass, entry_id)
        hass.async_create_task(_retry_after_setup())
        return
    
    runtime_data: EcoGuardRuntimeData = entry.runtime_data
    coordinator = runtime_data.coordinator
    latest_reception_coordinator = runtime_data.latest_reception_coordinator
    api = runtime_data.api
    node_id = entry.data.get("node_id")
    
    if not node_id:
        _LOGGER.warning("Entry %s has no node_id, cannot fetch data", entry_id)
        return
    
    # Fetch batch data (consumption and price) in background
    async def _fetch_batch_data():
        """Fetch batch data in background."""
        try:
            await coordinator._batch_fetch_sensor_data()
            _LOGGER.info("Data fetch completed for entry %s", entry_id)
        except Exception as err:
            _LOGGER.warning("Error in data fetch for entry %s: %s", entry_id, err, exc_info=True)
    
    # Fetch latest reception data in background
    async def _fetch_latest_reception():
        """Fetch latest reception data in background."""
        try:
            latest_reception = await api.get_latest_reception(node_id)
            if latest_reception:
                latest_reception_coordinator.async_set_updated_data(latest_reception)
                _LOGGER.debug("Latest reception data updated for entry %s: %d entries", entry_id, len(latest_reception))
        except Exception as err:
            _LOGGER.warning("Error fetching latest reception for entry %s: %s", entry_id, err, exc_info=True)
    
    # Start both fetches as background tasks (non-blocking)
    hass.async_create_task(_fetch_batch_data())
    hass.async_create_task(_fetch_latest_reception())

