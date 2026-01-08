"""Base class for EcoGuard sensors with common functionality."""

from __future__ import annotations

from typing import Any
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EcoGuardDataUpdateCoordinator
from .translations import async_get_translation
from .sensor_helpers import async_update_entity_registry_name

_LOGGER = logging.getLogger(__name__)


class EcoGuardBaseSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Base class for EcoGuard sensors with common functionality.

    This class provides:
    - Common async_added_to_hass() implementation
    - Template method for translation updates
    - Common coordinator update handling
    - Common device info setup
    """

    def __init__(
        self,
        coordinator: EcoGuardDataUpdateCoordinator,
        hass: Any | None = None,
    ) -> None:
        """Initialize the base sensor.

        Args:
            coordinator: The data update coordinator
            hass: Home Assistant instance (optional, can be set later)
        """
        super().__init__(coordinator)
        self._hass = hass

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, update translations and set Unknown state.

        This is a common pattern across all EcoGuard sensors:
        1. Call parent's async_added_to_hass()
        2. Update sensor name with translations
        3. Set sensor to Unknown state (available=True, native_value=None)
        4. No API calls during startup
        """
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
        """Update the sensor name with translated strings.

        This is a template method that subclasses should override to provide
        sensor-specific translation logic. The default implementation does nothing.

        Subclasses should:
        1. Get translated strings using async_get_translation()
        2. Build the new name
        3. Update self._attr_name if changed
        4. Call async_update_entity_registry_name() to update the registry
        5. Update device name if needed
        """
        # Default implementation does nothing - subclasses should override
        pass

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data.

        This is a common pattern: when the coordinator updates, call
        _update_from_coordinator_data() to read from the cache.

        Subclasses should implement _update_from_coordinator_data() to
        handle sensor-specific data extraction.
        """
        _LOGGER.debug("Coordinator update received for %s (data available: %s)",
                     self.entity_id, self.coordinator.data is not None)
        self._update_from_coordinator_data()

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls).

        This is a template method that subclasses must implement to extract
        sensor-specific data from coordinator.data.

        The default implementation sets the sensor to Unknown state.
        """
        # Default implementation - subclasses should override
        self._attr_native_value = None
        self._attr_available = True
        self.async_write_ha_state()

    def _get_device_info(self, node_id: int, model: str | None = None) -> dict[str, Any]:
        """Get standard device info for EcoGuard sensors.

        Args:
            node_id: The node ID
            model: Optional device model (e.g., from installation data)

        Returns:
            Device info dictionary
        """
        device_info: dict[str, Any] = {
            "identifiers": {(DOMAIN, str(node_id))},
            "name": f"EcoGuard Node {node_id}",
            "manufacturer": "EcoGuard",
        }
        if model:
            device_info["model"] = model
        return device_info

    def _update_device_name(self, device_name: str) -> None:
        """Update the device name in device_info if it has changed.

        Args:
            device_name: The new device name
        """
        if self._attr_device_info.get("name") != device_name:
            self._attr_device_info["name"] = device_name
            self.async_write_ha_state()

    async def _update_name_and_registry(
        self,
        new_name: str,
        log_level: str = "info",
    ) -> None:
        """Update sensor name and entity registry name.

        Args:
            new_name: The new sensor name
            log_level: Logging level ("info" or "debug")
        """
        if self._attr_name != new_name:
            old_name = self._attr_name
            self._attr_name = new_name
            self.async_write_ha_state()
            if log_level == "info":
                _LOGGER.info("Updated sensor name from '%s' to '%s'", old_name, new_name)
            else:
                _LOGGER.debug("Updated sensor name from '%s' to '%s'", old_name, new_name)

        # Always update the entity registry name so it shows correctly in modals
        await async_update_entity_registry_name(self, new_name)

    async def _get_translated_utility_name(self, utility_code: str) -> str:
        """Get translated utility name.

        Args:
            utility_code: The utility code (e.g., "HW", "CW")

        Returns:
            Translated utility name, or utility_code if translation not found
        """
        if not self._hass:
            return utility_code

        utility_name = await async_get_translation(
            self._hass, f"utility.{utility_code.lower()}"
        )
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code
        return utility_name

    async def _get_translated_device_name(self, node_id: int) -> str:
        """Get translated device name.

        Args:
            node_id: The node ID

        Returns:
            Translated device name
        """
        if not self._hass:
            return f"EcoGuard Node {node_id}"

        return await async_get_translation(
            self._hass, "name.device_name", node_id=node_id
        )
