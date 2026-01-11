"""Base class for EcoGuard sensors with common functionality."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import logging
from datetime import date

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EcoGuardDataUpdateCoordinator
from .translations import async_get_translation, get_translation_default
from .sensor_helpers import async_update_entity_registry_name

_LOGGER = logging.getLogger(__name__)


@dataclass(kw_only=True)
class EcoGuardSensorEntityDescription(SensorEntityDescription):
    """Describes EcoGuard sensor entity with description support."""

    description: str | None = None


class EcoGuardBaseSensor(
    CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity
):
    """Base class for EcoGuard sensors with common functionality.

    This class provides:
    - Common async_added_to_hass() implementation
    - Template method for translation updates
    - Common coordinator update handling
    - Common device info setup
    - Recording configuration metadata via RECORDING_ENABLED and RECORDING_INTERVAL
    """

    # Class-level attributes for recording configuration metadata
    # These are informational only - actual recording control should be done via:
    # 1. Recorder exclude/include configuration
    # 2. Filter sensors with throttle filters
    # RECORDING_ENABLED: Recommended recording setting (True/False)
    # RECORDING_INTERVAL: Recommended recording interval in seconds (None = record all updates)
    #   Examples: 3600 (hourly), 86400 (daily), None (record all updates)
    RECORDING_ENABLED: bool = True
    RECORDING_INTERVAL: int | None = None  # None = record all updates

    def __init__(
        self,
        coordinator: EcoGuardDataUpdateCoordinator,
        hass: Any | None = None,
        description_key: str | None = None,
    ) -> None:
        """Initialize the base sensor.

        Args:
            coordinator: The data update coordinator
            hass: Home Assistant instance (optional, can be set later)
            description_key: Translation key for the sensor description (optional)
        """
        super().__init__(coordinator)
        self._hass = hass
        self._description_key = description_key
        self._description_text: str | None = None
        # Track last written state for value-based state writes
        # Only write state when value or context (date/month) changes meaningfully
        self._last_written_value: Any = None
        self._last_written_date: date | None = None  # For daily sensors
        self._last_written_month: tuple[int, int] | None = (
            None  # (year, month) for monthly sensors
        )
        # Entity description will be set by _set_entity_description() after name and unique_id are set

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
        # Don't write state during initialization - wait for first data update
        self._attr_native_value = None
        self._attr_available = True
        _LOGGER.debug(
            "Sensor %s added to hass (Unknown state, no API calls, will not record until data available)",
            self.entity_id,
        )

    def _set_entity_description(self) -> None:
        """Set the entity description in __init__ after name and unique_id are set.

        This should be called at the end of each subclass's __init__ method,
        after _attr_unique_id and _attr_name have been set.

        Uses EcoGuardSensorEntityDescription which extends SensorEntityDescription
        with a description field, following the pattern from Home Assistant docs.
        """
        if not self._description_key or not self._attr_unique_id or not self._attr_name:
            return

        # Get default English description for now (will be updated with translations later)
        description_text = get_translation_default(self._description_key)
        self._description_text = description_text

        # Use custom entity description class with description support
        self._attr_entity_description = EcoGuardSensorEntityDescription(
            key=self._attr_unique_id,
            name=self._attr_name,
            description=description_text,
        )
        _LOGGER.debug(
            "Set entity description for %s (unique_id: %s, description: %s)",
            self._attr_name,
            self._attr_unique_id,
            (
                description_text[:50] + "..."
                if len(description_text) > 50
                else description_text
            ),
        )

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
        6. Update entity description with translated description
        """
        # Default implementation does nothing - subclasses should override
        # But we can update the description here if a key was provided
        if self._description_key and self._hass:
            await self._async_update_description()

    async def _async_update_description(self) -> None:
        """Update the sensor description from translations.

        This method updates the description text with the translated version.
        Should be called from _async_update_translated_name() after translations are loaded.
        """
        if not self._description_key or not self._hass:
            return

        try:
            description_text = await async_get_translation(
                self._hass, self._description_key
            )
            self._description_text = description_text

            # Update entity description with translated description
            if self._attr_entity_description and isinstance(
                self._attr_entity_description, EcoGuardSensorEntityDescription
            ):
                self._attr_entity_description = EcoGuardSensorEntityDescription(
                    key=self._attr_unique_id,
                    name=self._attr_name,
                    description=description_text,
                )

            self.async_write_ha_state()  # Update state to reflect new description in attributes
            _LOGGER.debug("Updated sensor description for %s", self.entity_id)
        except Exception as e:
            _LOGGER.debug("Failed to update sensor description: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by reading from cached data.

        This is a common pattern: when the coordinator updates, call
        _update_from_coordinator_data() to read from the cache.

        Subclasses should implement _update_from_coordinator_data() to
        handle sensor-specific data extraction.
        """
        _LOGGER.debug(
            "Coordinator update received for %s (data available: %s)",
            self.entity_id,
            self.coordinator.data is not None,
        )
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
        # Don't write state when value is None - wait for subclass to provide data

    def _should_write_state(
        self,
        new_value: Any,
        data_date: date | None = None,
        data_month: tuple[int, int] | None = None,
    ) -> bool:
        """Check if state should be written based on value and context changes.

        This implements value-based state writes: only write when the value or
        context (date/month) meaningfully changes. This reduces recorder entries
        while maintaining accurate historical data.

        Note: This method assumes new_value is not None (that check is done in _async_write_ha_state_if_changed).
        This method will never be called with a None value.

        Args:
            new_value: The new sensor value (guaranteed to not be None)
            data_date: The date associated with the data (for daily sensors)
            data_month: Tuple of (year, month) for monthly sensors

        Returns:
            True if state should be written, False otherwise
        """

        # Always write if recording is disabled (sensor still needs to update)
        if not self.RECORDING_ENABLED:
            return True

        # Always write if no interval is configured (record all updates)
        if self.RECORDING_INTERVAL is None:
            return True

        # Always write on first update (but only if we have a valid value)
        if self._last_written_value is None:
            # Only write on first update if we have a valid value (not None)
            return new_value is not None

        # Check if value has changed
        value_changed = new_value != self._last_written_value

        # For monthly sensors: write if value changed OR month changed OR date changed
        # Monthly accumulated sensors should record daily to track progression
        # Check monthly first since monthly sensors might have both data_date and data_month
        if data_month is not None:
            month_changed = data_month != self._last_written_month
            # Also check date for monthly accumulated sensors (they should record daily)
            date_changed = False
            if data_date is not None:
                date_changed = data_date != self._last_written_date
            # Only write on month/date change if we have a valid value (don't write None on date/month change)
            if value_changed:
                return True
            if (month_changed or date_changed) and new_value is not None:
                return True
            return False

        # For daily sensors: write if value changed OR date changed (but only if we have a value)
        if data_date is not None:
            date_changed = data_date != self._last_written_date
            # Only write on date change if we have a valid value (don't write None on date change)
            if value_changed:
                return True
            if date_changed and new_value is not None:
                return True
            return False

        # For other sensors: only write if value changed
        return value_changed

    def _async_write_ha_state_if_changed(
        self,
        new_value: Any | None = None,
        data_date: date | None = None,
        data_month: tuple[int, int] | None = None,
    ) -> None:
        """Write state only if value or context has meaningfully changed.

        This method implements value-based state writes to reduce recorder entries
        while maintaining accurate historical data. It's called by subclasses
        after updating sensor values.

        Args:
            new_value: The new sensor value (defaults to self._attr_native_value)
            data_date: The date associated with the data (for daily sensors)
            data_month: Tuple of (year, month) for monthly sensors
        """
        if new_value is None:
            new_value = self._attr_native_value

        # Never write state when value is None/unknown
        # This prevents recording "unknown" states during startup or when data is temporarily unavailable
        # We don't even write transitions from value to None - if data becomes unavailable,
        # it's better to keep the last known value rather than recording "unknown"
        if new_value is None:
            # Skip writing None/unknown states entirely - don't record during startup or when data is missing
            _LOGGER.debug(
                "Skipping state write for %s: value is None/unknown (will not record unknown states)",
                self.entity_id,
            )
            return

        if self._should_write_state(new_value, data_date, data_month):
            # Update tracking variables
            self._last_written_value = new_value
            if data_date is not None:
                self._last_written_date = data_date
            if data_month is not None:
                self._last_written_month = data_month

            # Write state
            self.async_write_ha_state()
            _LOGGER.debug(
                "State written for %s: value=%s, date=%s, month=%s",
                self.entity_id,
                new_value,
                data_date,
                data_month,
            )
        else:
            # Value hasn't changed meaningfully - skip write
            _LOGGER.debug(
                "Skipping state write for %s (no meaningful change: value=%s, date=%s, month=%s, last_written_value=%s, last_written_date=%s, last_written_month=%s)",
                self.entity_id,
                new_value,
                data_date,
                data_month,
                self._last_written_value,
                self._last_written_date,
                self._last_written_month,
            )

    def _get_base_extra_state_attributes(self) -> dict[str, Any]:
        """Get base extra state attributes including description.

        Subclasses should call this and merge with their own attributes.
        The description is also stored in the entity description, but we include it
        in attributes as well to ensure it's visible in the UI.
        """
        attrs: dict[str, Any] = {}

        # Get description from entity description if available, otherwise use stored text
        description = None
        if self._attr_entity_description and isinstance(
            self._attr_entity_description, EcoGuardSensorEntityDescription
        ):
            description = self._attr_entity_description.description

        if not description and self._description_text:
            description = self._description_text

        # Add description to attributes so it's visible in the UI
        if description:
            attrs["description"] = description

        # Add recording configuration metadata (informational only)
        # These attributes help users understand recommended recording settings
        # Actual recording control should be done via recorder configuration or filter sensors
        attrs["recording_enabled"] = self.RECORDING_ENABLED
        if self.RECORDING_INTERVAL is not None:
            attrs["recording_interval_seconds"] = self.RECORDING_INTERVAL
            # Convert to human-readable format
            if self.RECORDING_INTERVAL >= 86400:
                attrs["recording_interval"] = (
                    f"{self.RECORDING_INTERVAL // 86400} day(s)"
                )
            elif self.RECORDING_INTERVAL >= 3600:
                attrs["recording_interval"] = (
                    f"{self.RECORDING_INTERVAL // 3600} hour(s)"
                )
            elif self.RECORDING_INTERVAL >= 60:
                attrs["recording_interval"] = (
                    f"{self.RECORDING_INTERVAL // 60} minute(s)"
                )
            else:
                attrs["recording_interval"] = f"{self.RECORDING_INTERVAL} second(s)"
        else:
            attrs["recording_interval"] = "all updates"

        return attrs

    def _get_device_info(
        self, node_id: int, model: str | None = None
    ) -> dict[str, Any]:
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
                _LOGGER.info(
                    "Updated sensor name from '%s' to '%s'", old_name, new_name
                )
            else:
                _LOGGER.debug(
                    "Updated sensor name from '%s' to '%s'", old_name, new_name
                )

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
