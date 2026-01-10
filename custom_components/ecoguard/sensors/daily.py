"""Daily sensors for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import logging

from homeassistant.components.sensor import SensorStateClass, SensorDeviceClass
from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from ..coordinator import EcoGuardDataUpdateCoordinator
from ..helpers import (
    round_to_max_digits,
    find_last_data_date,
    find_last_price_date,
    detect_data_lag,
    get_timezone,
)
from ..translations import (
    async_get_translation,
    get_translation_default,
)
from ..sensor_helpers import (
    slugify_name,
    utility_code_to_slug,
)
from ..sensor_base import EcoGuardBaseSensor

_LOGGER = logging.getLogger(__name__)


class EcoGuardDailyConsumptionSensor(EcoGuardBaseSensor):
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
        super().__init__(
            coordinator,
            hass=hass,
            description_key="description.consumption_daily_meter",
        )
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
            measuring_point_display = get_translation_default(
                "name.measuring_point", id=measuring_point_id
            )

        # Use English defaults here; will be updated in async_added_to_hass with proper translations
        utility_name = get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Consumption Daily - Meter "Measuring Point" (Utility)"
        # This ensures entity_id starts with "consumption_daily_" when slugified
        # The name will be updated in async_added_to_hass with proper translations
        # but the entity_id is already generated from this initial name
        consumption_daily = get_translation_default("name.consumption_daily")
        meter = get_translation_default("name.meter")
        self._attr_name = f'{consumption_daily} - {meter} "{measuring_point_display}" ({utility_name})'

        # Build unique_id following pattern: purpose_group_utility_meter_sensor
        # Home Assistant strips the domain prefix, so we want: consumption_daily_metered_cold_water_meter_kaldtvann_bad
        # Use measuring_point_id to ensure uniqueness across nodes
        utility_slug = utility_code_to_slug(utility_code)
        sensor_name = slugify_name(measuring_point_name) or f"mp{measuring_point_id}"
        unique_id = (
            f"{DOMAIN}_consumption_daily_metered_{utility_slug}_meter_{sensor_name}"
        )
        self._attr_unique_id = unique_id
        _LOGGER.debug("Daily consumption sensor unique_id: %s", unique_id)

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        self._attr_device_info = self._get_device_info(
            coordinator.node_id,
            model=installation.get("DeviceTypeDisplay", "Unknown"),
        )

        # Disable individual meter sensors by default (users can enable if needed)
        self._attr_entity_registry_enabled_default = False

        # Set state class and unit (will be updated when we get data)
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = None
        self._last_data_date: datetime | None = None
        self._data_lagging: bool = False
        self._data_lag_days: int | None = None

        # Set icon based on utility type
        if utility_code == "HW":
            self._attr_icon = "mdi:water-thermometer"
        elif utility_code == "CW":
            self._attr_icon = "mdi:water"
        elif utility_code == "E":
            self._attr_icon = "mdi:lightning-bolt"
        elif utility_code == "HE":
            self._attr_icon = "mdi:radiator"
        else:
            self._attr_icon = "mdi:gauge"

        # Set entity description (must be called after name and unique_id are set)
        self._set_entity_description()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = self._get_base_extra_state_attributes()
        attrs.update(
            {
                "measuring_point_id": self._measuring_point_id,
                "utility_code": self._utility_code,
                "external_key": self._installation.get("ExternalKey"),
                "device_type": self._installation.get("DeviceTypeDisplay"),
                "sensor_type": "daily_consumption",
            }
        )

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()

        # Add lag detection attributes
        attrs["data_lagging"] = self._data_lagging
        if self._data_lag_days is not None:
            attrs["data_lag_days"] = self._data_lag_days

        return attrs

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            lang = getattr(self._hass.config, "language", "en")
            _LOGGER.debug("Updating sensor name for lang=%s", lang)

            # Rebuild sensor name with translations
            if self._measuring_point_name:
                measuring_point_display = self._measuring_point_name
            else:
                measuring_point_display = await async_get_translation(
                    self._hass, "name.measuring_point", id=self._measuring_point_id
                )
                _LOGGER.debug("Measuring point display: %s", measuring_point_display)

            utility_name = await self._get_translated_utility_name(self._utility_code)
            _LOGGER.debug("Utility name: %s", utility_name)

            consumption_daily = await async_get_translation(
                self._hass, "name.consumption_daily"
            )
            _LOGGER.debug("Consumption daily: %s", consumption_daily)

            # Update the name (this is the display name, not the entity_id)
            # Format: "Consumption Daily - Meter "Measuring Point" (Utility)"
            # Keep "Consumption Daily" format to maintain entity_id starting with "consumption_daily_"
            meter = await async_get_translation(self._hass, "name.meter")
            new_name = f'{consumption_daily} - {meter} "{measuring_point_display}" ({utility_name})'
            await self._update_name_and_registry(new_name, log_level="info")

            # Also update device name
            device_name = await self._get_translated_device_name(
                self.coordinator.node_id
            )
            self._update_device_name(device_name)

            # Update description
            await self._async_update_description()
        except Exception as e:
            _LOGGER.warning("Failed to update translated name: %s", e, exc_info=True)

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.debug("No coordinator data for %s", self.entity_id)
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = None
            self._last_data_date = None
            self._data_lagging = False
            self._data_lag_days = None
            self._attr_available = True
            self.async_write_ha_state()
            return

        # Get consumption cache from coordinator data
        consumption_cache = coordinator_data.get("latest_consumption_cache", {})
        daily_consumption_cache = coordinator_data.get("daily_consumption_cache", {})

        # Build cache key
        if self._measuring_point_id:
            cache_key = f"{self._utility_code}_{self._measuring_point_id}"
        else:
            cache_key = f"{self._utility_code}_all"

        consumption_data = consumption_cache.get(cache_key)

        # Find actual last data date from daily consumption cache
        daily_cache = daily_consumption_cache.get(cache_key, [])
        timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
        tz = get_timezone(timezone_str)
        actual_last_data_date = find_last_data_date(daily_cache, tz)

        # Use actual last data date if available, otherwise fall back to latest cache timestamp
        if actual_last_data_date:
            self._last_data_date = actual_last_data_date
        elif consumption_data:
            time_stamp = consumption_data.get("time")
            if time_stamp:
                self._last_data_date = datetime.fromtimestamp(time_stamp, tz=tz)
            else:
                self._last_data_date = None
        else:
            self._last_data_date = None

        # Detect lag
        if self._last_data_date:
            is_lagging, lag_days = detect_data_lag(self._last_data_date, tz)
            self._data_lagging = is_lagging
            self._data_lag_days = lag_days
        else:
            self._data_lagging = True
            self._data_lag_days = None

        if consumption_data:
            raw_value = consumption_data.get("value")
            new_value = (
                round_to_max_digits(raw_value)
                if isinstance(raw_value, (int, float))
                else raw_value
            )
            old_value = self._attr_native_value

            self._attr_native_value = new_value
            self._attr_native_unit_of_measurement = consumption_data.get("unit")

            # Mark sensor as available when we have data
            self._attr_available = True

            # Log update for debugging
            if old_value != new_value:
                lag_info = (
                    f" (lagging {self._data_lag_days} days)"
                    if self._data_lagging
                    else ""
                )
                _LOGGER.info(
                    "Updated %s: %s -> %s %s (cache key: %s, last data: %s)%s",
                    self.entity_id,
                    old_value,
                    new_value,
                    self._attr_native_unit_of_measurement,
                    cache_key,
                    (
                        self._last_data_date.strftime("%Y-%m-%d")
                        if self._last_data_date
                        else "None"
                    ),
                    lag_info,
                )
        else:
            # Log missing cache key for debugging (only once to avoid spam)
            if not hasattr(self, "_cache_miss_logged") or not self._cache_miss_logged:
                available_keys = sorted(consumption_cache.keys())
                _LOGGER.warning(
                    "Cache miss for %s: Looking for key '%s', available keys: %s",
                    self.entity_id,
                    cache_key,
                    available_keys[:10] if len(available_keys) > 10 else available_keys,
                )
                self._cache_miss_logged = True

            # Keep sensor available even if no data (shows as "unknown" not "unavailable")
            # This allows sensors to appear in UI immediately
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = None
            self._attr_available = True  # Keep available, just show None/unknown

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()


class EcoGuardLatestReceptionSensor(EcoGuardBaseSensor):
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
        super().__init__(
            coordinator, hass=hass, description_key="description.reception_last_update"
        )
        self._hass = hass
        self._measuring_point_id = measuring_point_id
        self._measuring_point_name = measuring_point_name
        self._utility_code = utility_code

        # Build sensor name
        if measuring_point_name:
            measuring_point_display = measuring_point_name
        else:
            measuring_point_display = get_translation_default(
                "name.measuring_point", id=measuring_point_id
            )

        # Use English defaults here; will be updated in async_added_to_hass
        utility_suffix = ""
        if utility_code:
            utility_name = get_translation_default(f"utility.{utility_code.lower()}")
            if (
                utility_name == f"utility.{utility_code.lower()}"
            ):  # Fallback if not found
                utility_name = utility_code
            utility_suffix = f" ({utility_name})"

        # Format: "Reception Last Update - Meter "Measuring Point" (Utility)"
        # This ensures entity_id starts with "reception_last_update_" when slugified
        # This will be updated in async_added_to_hass with proper translations
        reception_last_update = get_translation_default("name.reception_last_update")
        meter = get_translation_default("name.meter")
        if utility_suffix:
            # utility_suffix already includes parentheses, so we need to extract just the utility name
            utility_name = get_translation_default(f"utility.{utility_code.lower()}")
            if (
                utility_name == f"utility.{utility_code.lower()}"
            ):  # Fallback if not found
                utility_name = utility_code
            self._attr_name = f'{reception_last_update} - {meter} "{measuring_point_display}" ({utility_name})'
        else:
            self._attr_name = (
                f'{reception_last_update} - {meter} "{measuring_point_display}"'
            )

        # Build unique_id following pattern: purpose_group_utility_meter_sensor
        # Home Assistant strips the domain prefix, so we want: reception_last_update_cold_water_meter_kaldtvann_bad
        sensor_name = slugify_name(measuring_point_name) or f"mp{measuring_point_id}"
        if utility_code:
            utility_slug = utility_code_to_slug(utility_code)
            self._attr_unique_id = (
                f"{DOMAIN}_reception_last_update_{utility_slug}_meter_{sensor_name}"
            )
        else:
            self._attr_unique_id = f"{DOMAIN}_reception_last_update_meter_{sensor_name}"

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        self._attr_device_info = self._get_device_info(coordinator.node_id)

        # Disable individual meter sensors by default (users can enable if needed)
        self._attr_entity_registry_enabled_default = False

        # Set device class to timestamp
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_native_value = None

        # Set icon for latest reception sensor
        self._attr_icon = "mdi:clock-outline"

        # Set entity description (must be called after name and unique_id are set)
        self._set_entity_description()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = self._get_base_extra_state_attributes()
        attrs.update(
            {
                "measuring_point_id": self._measuring_point_id,
            }
        )

        if self._measuring_point_name:
            attrs["measuring_point_name"] = self._measuring_point_name

        if self._utility_code:
            attrs["utility_code"] = self._utility_code

        return attrs

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            if self._measuring_point_name:
                measuring_point_display = self._measuring_point_name
            else:
                measuring_point_display = await async_get_translation(
                    self._hass, "name.measuring_point", id=self._measuring_point_id
                )

            # Keep "Reception Last Update" format to maintain entity_id starting with "reception_last_update_"
            # Format: "Reception Last Update - Meter "Measuring Point" (Utility)"
            # This groups similar sensors together when sorted alphabetically
            reception_last_update = await async_get_translation(
                self._hass, "name.reception_last_update"
            )
            meter = await async_get_translation(self._hass, "name.meter")
            if self._utility_code:
                utility_name = await async_get_translation(
                    self._hass, f"utility.{self._utility_code.lower()}"
                )
                if (
                    utility_name == f"utility.{self._utility_code.lower()}"
                ):  # Fallback if not found
                    utility_name = self._utility_code
                new_name = f'{reception_last_update} - {meter} "{measuring_point_display}" ({utility_name})'
            else:
                new_name = (
                    f'{reception_last_update} - {meter} "{measuring_point_display}"'
                )
            await self._update_name_and_registry(new_name, log_level="debug")

            # Update description
            await self._async_update_description()
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        # The coordinator data is a list of reception entries
        latest_reception_data = self.coordinator.data

        if not latest_reception_data:
            self._attr_native_value = None
            self._attr_available = True
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
                    self._attr_native_value = datetime.fromtimestamp(
                        latest_timestamp, tz=timezone.utc
                    )
                else:
                    self._attr_native_value = None
                break

        if latest_timestamp is None:
            # No reception data found for this measuring point
            self._attr_native_value = None

        self._attr_available = True
        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()

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
                    self._attr_native_value = datetime.fromtimestamp(
                        latest_timestamp, tz=timezone.utc
                    )
                else:
                    self._attr_native_value = None
                break

        if latest_timestamp is None:
            # No reception data found for this measuring point
            self._attr_native_value = None

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()


class EcoGuardDailyConsumptionAggregateSensor(EcoGuardBaseSensor):
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
        super().__init__(
            coordinator,
            hass=hass,
            description_key="description.consumption_daily_aggregated",
        )
        self._hass = hass
        self._utility_code = utility_code

        # Build sensor name
        utility_name = get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Consumption Daily Metered - Utility"
        consumption_daily = get_translation_default("name.consumption_daily")
        metered = get_translation_default("name.metered")
        self._attr_name = f"{consumption_daily} {metered} - {utility_name}"

        # Build unique_id following pattern: consumption_daily_metered_utility
        utility_slug = utility_code_to_slug(utility_code)
        self._attr_unique_id = f"{DOMAIN}_consumption_daily_metered_{utility_slug}"

        # Sensor attributes
        self._attr_device_info = self._get_device_info(coordinator.node_id)

        # Set state class
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = None
        self._last_data_date: datetime | None = None
        self._meters_with_data: list[dict[str, Any]] = []
        self._data_lagging: bool = False
        self._data_lag_days: int | None = None

        # Set icon based on utility type
        if utility_code == "HW":
            self._attr_icon = "mdi:water-thermometer"
        elif utility_code == "CW":
            self._attr_icon = "mdi:water"
        elif utility_code == "E":
            self._attr_icon = "mdi:lightning-bolt"
        elif utility_code == "HE":
            self._attr_icon = "mdi:radiator"
        else:
            self._attr_icon = "mdi:gauge"

        # Set entity description (must be called after name and unique_id are set)
        self._set_entity_description()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = self._get_base_extra_state_attributes()
        attrs.update(
            {
                "utility_code": self._utility_code,
                "sensor_type": "daily_consumption_aggregate",
                "meter_count": len(self._meters_with_data),
            }
        )

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()

        # Add lag detection attributes
        attrs["data_lagging"] = self._data_lagging
        if self._data_lag_days is not None:
            attrs["data_lag_days"] = self._data_lag_days

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

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            utility_name = await async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            consumption_daily = await async_get_translation(
                self._hass, "name.consumption_daily"
            )
            metered = await async_get_translation(self._hass, "name.metered")
            new_name = f"{consumption_daily} {metered} - {utility_name}"
            await self._update_name_and_registry(new_name, log_level="debug")

            # Update description
            await self._async_update_description()
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.debug("No coordinator data for %s", self.entity_id)
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = None
            self._attr_available = True
            self.async_write_ha_state()
            return

        # Get consumption cache from coordinator data
        consumption_cache = coordinator_data.get("latest_consumption_cache", {})
        daily_consumption_cache = coordinator_data.get("daily_consumption_cache", {})

        # Try to read from "all" cache first (aggregated across all meters)
        cache_key_all = f"{self._utility_code}_all"
        consumption_data = consumption_cache.get(cache_key_all)

        # Find actual last data date from daily consumption cache
        daily_cache = daily_consumption_cache.get(cache_key_all, [])
        timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
        tz = get_timezone(timezone_str)
        actual_last_data_date = find_last_data_date(daily_cache, tz)

        if consumption_data:
            # Use aggregated data directly
            raw_value = consumption_data.get("value")
            new_value = (
                round_to_max_digits(raw_value)
                if isinstance(raw_value, (int, float))
                else raw_value
            )
            old_value = self._attr_native_value

            self._attr_native_value = new_value
            self._attr_native_unit_of_measurement = consumption_data.get("unit")

            # Use actual last data date if available, otherwise fall back to latest cache timestamp
            if actual_last_data_date:
                self._last_data_date = actual_last_data_date
            else:
                time_stamp = consumption_data.get("time")
                if time_stamp:
                    self._last_data_date = datetime.fromtimestamp(time_stamp, tz=tz)
                else:
                    self._last_data_date = None

            # Detect lag
            if self._last_data_date:
                is_lagging, lag_days = detect_data_lag(self._last_data_date, tz)
                self._data_lagging = is_lagging
                self._data_lag_days = lag_days
            else:
                self._data_lagging = True
                self._data_lag_days = None

            # Mark sensor as available when we have data
            self._attr_available = True

            # Log update for debugging
            if old_value != new_value:
                lag_info = (
                    f" (lagging {self._data_lag_days} days)"
                    if self._data_lagging
                    else ""
                )
                _LOGGER.info(
                    "Updated %s: %s -> %s %s (cache key: %s, last data: %s)%s",
                    self.entity_id,
                    old_value,
                    new_value,
                    self._attr_native_unit_of_measurement,
                    cache_key_all,
                    (
                        self._last_data_date.strftime("%Y-%m-%d")
                        if self._last_data_date
                        else "None"
                    ),
                    lag_info,
                )

            self.async_write_ha_state()
            return

        # Fallback: Sum consumption across all meters for this utility
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

            # Read consumption from cache (no API call)
            cache_key = f"{self._utility_code}_{measuring_point_id}"
            consumption_data = consumption_cache.get(cache_key)

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

                meters_with_data.append(
                    {
                        "measuring_point_id": measuring_point_id,
                        "measuring_point_name": measuring_point_name,
                        "value": value,
                    }
                )

        # Find actual last data date from daily consumption cache (fallback case)
        if not actual_last_data_date and latest_timestamp:
            # Check individual meter caches
            for installation in active_installations:
                measuring_point_id = installation.get("MeasuringPointID")
                cache_key = f"{self._utility_code}_{measuring_point_id}"
                meter_daily_cache = daily_consumption_cache.get(cache_key, [])
                meter_last_date = find_last_data_date(meter_daily_cache, tz)
                if meter_last_date:
                    if (
                        actual_last_data_date is None
                        or meter_last_date > actual_last_data_date
                    ):
                        actual_last_data_date = meter_last_date

        if total_value > 0:
            self._attr_native_value = round_to_max_digits(total_value)
            self._attr_native_unit_of_measurement = unit
            # Use actual last data date if available, otherwise fall back to latest timestamp
            if actual_last_data_date:
                self._last_data_date = actual_last_data_date
            elif latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp, tz=tz)
            else:
                self._last_data_date = None

            # Detect lag
            if self._last_data_date:
                is_lagging, lag_days = detect_data_lag(self._last_data_date, tz)
                self._data_lagging = is_lagging
                self._data_lag_days = lag_days
            else:
                self._data_lagging = True
                self._data_lag_days = None

            self._meters_with_data = meters_with_data
        else:
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = unit
            self._last_data_date = None
            self._data_lagging = True
            self._data_lag_days = None
            self._meters_with_data = []

        self.async_write_ha_state()


class EcoGuardDailyCombinedWaterSensor(EcoGuardBaseSensor):
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
        super().__init__(
            coordinator,
            hass=hass,
            description_key="description.consumption_daily_combined_water",
        )
        self._hass = hass

        # Format: "Consumption Daily Metered - Combined Water"
        consumption_daily = get_translation_default("name.consumption_daily")
        metered = get_translation_default("name.metered")
        water_name = get_translation_default("name.combined_water")
        if water_name == "name.combined_water":  # Fallback if not found
            water_name = "Combined Water"
        self._attr_name = f"{consumption_daily} {metered} - {water_name}"

        # Build unique_id
        self._attr_unique_id = f"{DOMAIN}_consumption_daily_metered_combined_water"

        # Sensor attributes
        self._attr_device_info = self._get_device_info(coordinator.node_id)

        # Set state class
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = None
        self._last_data_date: datetime | None = None
        self._hw_meters_with_data: list[dict[str, Any]] = []
        self._cw_meters_with_data: list[dict[str, Any]] = []
        self._data_lagging: bool = False
        self._data_lag_days: int | None = None

        # Set icon for combined water sensor
        self._attr_icon = "mdi:water-circle"

        # Set entity description (must be called after name and unique_id are set)
        self._set_entity_description()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = self._get_base_extra_state_attributes()
        attrs.update(
            {
                "sensor_type": "daily_consumption_combined_water",
                "utilities": ["HW", "CW"],
                "hw_meter_count": len(self._hw_meters_with_data),
                "cw_meter_count": len(self._cw_meters_with_data),
            }
        )

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()

        # Add lag detection attributes
        attrs["data_lagging"] = self._data_lagging
        if self._data_lag_days is not None:
            attrs["data_lag_days"] = self._data_lag_days

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

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            consumption_daily = await async_get_translation(
                self._hass, "name.consumption_daily"
            )
            metered = await async_get_translation(self._hass, "name.metered")
            water_name = await async_get_translation(self._hass, "name.combined_water")
            if water_name == "name.combined_water":
                water_name = "Combined Water"
            new_name = f"{consumption_daily} {metered} - {water_name}"
            await self._update_name_and_registry(new_name, log_level="debug")

            # Update description
            await self._async_update_description()
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = None
            self._attr_available = True  # Keep available even if no data
            self.async_write_ha_state()
            return

        # Get consumption cache from coordinator data
        consumption_cache = coordinator_data.get("latest_consumption_cache", {})
        daily_consumption_cache = coordinator_data.get("daily_consumption_cache", {})

        timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
        tz = get_timezone(timezone_str)

        active_installations = self.coordinator.get_active_installations()
        hw_total = 0.0
        cw_total = 0.0
        unit = None
        latest_timestamp = None
        hw_meters_with_data = []
        cw_meters_with_data = []
        hw_last_data_date: datetime | None = None
        cw_last_data_date: datetime | None = None

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

                # Read consumption from cache (no API call)
                cache_key = f"{utility_code}_{measuring_point_id}"
                consumption_data = consumption_cache.get(cache_key)

                # Find actual last data date from daily consumption cache
                meter_daily_cache = daily_consumption_cache.get(cache_key, [])
                meter_last_date = find_last_data_date(meter_daily_cache, tz)

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

                    # Track last data date per utility
                    if meter_last_date:
                        if utility_code == "HW":
                            if (
                                hw_last_data_date is None
                                or meter_last_date > hw_last_data_date
                            ):
                                hw_last_data_date = meter_last_date
                        elif utility_code == "CW":
                            if (
                                cw_last_data_date is None
                                or meter_last_date > cw_last_data_date
                            ):
                                cw_last_data_date = meter_last_date

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
        old_value = self._attr_native_value

        # Use the earliest of HW and CW last data dates (most conservative)
        # This ensures we show lag if either utility is lagging
        if hw_last_data_date and cw_last_data_date:
            self._last_data_date = min(hw_last_data_date, cw_last_data_date)
        elif hw_last_data_date:
            self._last_data_date = hw_last_data_date
        elif cw_last_data_date:
            self._last_data_date = cw_last_data_date
        elif latest_timestamp:
            self._last_data_date = datetime.fromtimestamp(latest_timestamp, tz=tz)
        else:
            self._last_data_date = None

        # Detect lag
        if self._last_data_date:
            is_lagging, lag_days = detect_data_lag(self._last_data_date, tz)
            self._data_lagging = is_lagging
            self._data_lag_days = lag_days
        else:
            self._data_lagging = True
            self._data_lag_days = None

        if total_value > 0:
            self._attr_native_value = round_to_max_digits(total_value)
            self._attr_native_unit_of_measurement = unit
            self._hw_meters_with_data = hw_meters_with_data
            self._cw_meters_with_data = cw_meters_with_data
            self._attr_available = True

            # Log update for debugging
            if old_value != self._attr_native_value:
                lag_info = (
                    f" (lagging {self._data_lag_days} days)"
                    if self._data_lagging
                    else ""
                )
                _LOGGER.debug(
                    "Updated %s: %s -> %s %s (HW: %s, CW: %s, last data: %s)%s",
                    self.entity_id,
                    old_value,
                    self._attr_native_value,
                    unit,
                    hw_total,
                    cw_total,
                    (
                        self._last_data_date.strftime("%Y-%m-%d")
                        if self._last_data_date
                        else "None"
                    ),
                    lag_info,
                )
        else:
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = unit
            self._hw_meters_with_data = []
            self._cw_meters_with_data = []
            # Keep sensor available even if no data (shows as "unknown" not "unavailable")
            self._attr_available = True
            if old_value is not None:
                _LOGGER.debug(
                    "Updated %s: %s -> None (no data found)", self.entity_id, old_value
                )

        self.async_write_ha_state()


class EcoGuardDailyCostSensor(EcoGuardBaseSensor):
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
        description_key = (
            "description.cost_daily_estimated"
            if cost_type == "estimated"
            else "description.cost_daily_metered"
        )
        super().__init__(coordinator, hass=hass, description_key=description_key)
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
            measuring_point_display = get_translation_default(
                "name.measuring_point", id=measuring_point_id
            )

        utility_name = get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Cost Daily Metered/Estimated - Meter "Measuring Point" (Utility)"
        cost_daily = get_translation_default("name.cost_daily")
        meter = get_translation_default("name.meter")
        if cost_type == "estimated":
            estimated = get_translation_default("name.estimated")
            self._attr_name = f'{cost_daily} {estimated} - {meter} "{measuring_point_display}" ({utility_name})'
        else:
            metered = get_translation_default("name.metered")
            self._attr_name = f'{cost_daily} {metered} - {meter} "{measuring_point_display}" ({utility_name})'

        # Build unique_id following pattern: cost_daily_metered/estimated_utility_sensor
        utility_slug = utility_code_to_slug(utility_code)
        sensor_name = slugify_name(measuring_point_name) or f"mp{measuring_point_id}"
        if cost_type == "estimated":
            unique_id_suffix = (
                f"cost_daily_estimated_{utility_slug}_meter_{sensor_name}"
            )
        else:
            unique_id_suffix = f"cost_daily_metered_{utility_slug}_meter_{sensor_name}"
        self._attr_unique_id = f"{DOMAIN}_{unique_id_suffix}"

        # Sensor attributes
        self._attr_device_info = self._get_device_info(
            coordinator.node_id,
            model=installation.get("DeviceTypeDisplay", "Unknown"),
        )

        # Disable individual meter sensors by default (users can enable if needed)
        self._attr_entity_registry_enabled_default = False

        # Set state class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = None
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._last_data_date: datetime | None = None
        self._data_lagging: bool = False
        self._data_lag_days: int | None = None

        # Set icon for cost sensor (all money units use dollar icon)
        self._attr_icon = "mdi:currency-usd"

        # Set entity description (must be called after name and unique_id are set)
        self._set_entity_description()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = self._get_base_extra_state_attributes()
        attrs.update(
            {
                "measuring_point_id": self._measuring_point_id,
                "utility_code": self._utility_code,
                "external_key": self._installation.get("ExternalKey"),
                "device_type": self._installation.get("DeviceTypeDisplay"),
                "sensor_type": "daily_cost",
                "cost_type": "metered" if self._cost_type == "actual" else "estimated",
            }
        )

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()
            attrs["data_lagging"] = self._data_lagging
            attrs["data_lag_days"] = self._data_lag_days

        # Add lag detection attributes
        attrs["data_lagging"] = self._data_lagging
        if self._data_lag_days is not None:
            attrs["data_lag_days"] = self._data_lag_days

        return attrs

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            if self._measuring_point_name:
                measuring_point_display = self._measuring_point_name
            else:
                measuring_point_display = await async_get_translation(
                    self._hass, "name.measuring_point", id=self._measuring_point_id
                )

            utility_name = await async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            cost_daily = await async_get_translation(self._hass, "name.cost_daily")
            meter = await async_get_translation(self._hass, "name.meter")
            if self._cost_type == "estimated":
                estimated = await async_get_translation(self._hass, "name.estimated")
                new_name = f'{cost_daily} {estimated} - {meter} "{measuring_point_display}" ({utility_name})'
            else:
                metered = await async_get_translation(self._hass, "name.metered")
                new_name = f'{cost_daily} {metered} - {meter} "{measuring_point_display}" ({utility_name})'

            await self._update_name_and_registry(new_name, log_level="debug")

            # Update description
            await self._async_update_description()
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        _LOGGER.debug(
            "_update_from_coordinator_data called for %s (cost_type=%s, utility=%s, meter=%s)",
            self.entity_id,
            self._cost_type,
            self._utility_code,
            self._measuring_point_id,
        )
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.debug("No coordinator data for %s", self.entity_id)
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None
            self.async_write_ha_state()
            return

        # Get cost cache from coordinator data
        cost_cache = coordinator_data.get("latest_cost_cache", {})
        daily_price_cache = coordinator_data.get("daily_price_cache", {})
        daily_consumption_cache = coordinator_data.get("daily_consumption_cache", {})

        # Build cache key - always use metered cache key
        # For estimated costs, use metered cost if available (estimated = metered when metered exists)
        if self._measuring_point_id:
            cache_key = f"{self._utility_code}_{self._measuring_point_id}_metered"
            consumption_cache_key = f"{self._utility_code}_{self._measuring_point_id}"
        else:
            cache_key = f"{self._utility_code}_all_metered"
            consumption_cache_key = f"{self._utility_code}_all"

        cost_data = cost_cache.get(cache_key)

        # Find actual last data date
        # For metered costs: use daily_price_cache
        # For estimated costs: use daily_consumption_cache (since they're calculated from consumption)
        timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
        tz = get_timezone(timezone_str)
        actual_last_data_date = None

        if self._cost_type == "actual":
            # Metered costs: use price cache
            price_daily_cache = daily_price_cache.get(cache_key, [])
            actual_last_data_date = find_last_price_date(price_daily_cache, tz)
        else:
            # Estimated costs: use consumption cache (since they're calculated from consumption)
            consumption_daily_cache = daily_consumption_cache.get(
                consumption_cache_key, []
            )
            actual_last_data_date = find_last_data_date(consumption_daily_cache, tz)

        _LOGGER.debug(
            "_update_from_coordinator_data for %s: cost_type=%s, cache_key=%s, cost_data=%s",
            self.entity_id,
            self._cost_type,
            cache_key,
            cost_data is not None,
        )

        # For estimated costs: if we have metered cost data, use it (estimated = metered when available)
        # Only calculate from consumption if metered cost is not available
        if not cost_data and self._cost_type == "estimated":
            # No metered cost available, trigger async fetch to calculate estimated cost
            # This is needed for HW where metered cost is often not available
            from homeassistant.core import CoreState

            if (
                self.hass
                and not self.hass.is_stopping
                and self.hass.state != CoreState.starting
            ):
                # Trigger async fetch in background (non-blocking)
                async def _fetch_estimated_cost():
                    try:
                        _LOGGER.debug(
                            "Starting async fetch for estimated cost: %s",
                            self.entity_id,
                        )
                        await self._async_fetch_value()
                    except Exception as err:
                        _LOGGER.warning(
                            "Error in async fetch for %s: %s",
                            self.entity_id,
                            err,
                            exc_info=True,
                        )

                self.hass.async_create_task(_fetch_estimated_cost())
                _LOGGER.debug(
                    "Created async task for estimated cost fetch: %s", self.entity_id
                )
            else:
                _LOGGER.debug(
                    "Skipping async fetch for %s: hass=%s, is_stopping=%s, state=%s",
                    self.entity_id,
                    self.hass is not None,
                    self.hass.is_stopping if self.hass else None,
                    self.hass.state if self.hass else None,
                )
            cost_data = None

        if cost_data:
            raw_value = cost_data.get("value")
            self._attr_native_value = (
                round_to_max_digits(raw_value)
                if isinstance(raw_value, (int, float))
                else raw_value
            )
            self._attr_native_unit_of_measurement = (
                cost_data.get("unit") or self.coordinator.get_setting("Currency") or ""
            )

            # Use actual last data date if available, otherwise fall back to latest cache timestamp
            if actual_last_data_date:
                self._last_data_date = actual_last_data_date
            else:
                time_stamp = cost_data.get("time")
                if time_stamp:
                    self._last_data_date = datetime.fromtimestamp(time_stamp, tz=tz)
                else:
                    self._last_data_date = None
        else:
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None

        # Detect lag
        if self._last_data_date:
            is_lagging, lag_days = detect_data_lag(self._last_data_date, tz)
            self._data_lagging = is_lagging
            self._data_lag_days = lag_days
        else:
            self._data_lagging = True
            self._data_lag_days = None

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()

    async def _async_fetch_value(self) -> None:
        """Fetch estimated cost when metered cost is not available."""
        if self._cost_type != "estimated":
            # Only fetch for estimated costs
            return

        _LOGGER.debug(
            "Fetching estimated cost for %s (utility: %s, meter: %s)",
            self.entity_id,
            self._utility_code,
            self._measuring_point_id,
        )

        # Get estimated cost from coordinator
        cost_data = await self.coordinator.get_latest_estimated_cost(
            utility_code=self._utility_code,
            measuring_point_id=self._measuring_point_id,
            external_key=self._installation.get("ExternalKey"),
        )

        if cost_data:
            raw_value = cost_data.get("value")
            self._attr_native_value = (
                round_to_max_digits(raw_value)
                if isinstance(raw_value, (int, float))
                else raw_value
            )
            self._attr_native_unit_of_measurement = (
                cost_data.get("unit") or self.coordinator.get_setting("Currency") or ""
            )

            # For estimated costs, use consumption cache to find actual last data date
            # since estimated costs are calculated from consumption data
            coordinator_data = self.coordinator.data
            if coordinator_data and self._cost_type == "estimated":
                daily_consumption_cache = coordinator_data.get(
                    "daily_consumption_cache", {}
                )
                if self._measuring_point_id:
                    consumption_cache_key = (
                        f"{self._utility_code}_{self._measuring_point_id}"
                    )
                else:
                    consumption_cache_key = f"{self._utility_code}_all"
                consumption_daily_cache = daily_consumption_cache.get(
                    consumption_cache_key, []
                )
                timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
                tz = get_timezone(timezone_str)
                actual_last_data_date = find_last_data_date(consumption_daily_cache, tz)
                if actual_last_data_date:
                    self._last_data_date = actual_last_data_date
                else:
                    # Fall back to timestamp from cost_data
                    time_stamp = cost_data.get("time")
                    if time_stamp:
                        self._last_data_date = datetime.fromtimestamp(time_stamp, tz=tz)
                    else:
                        self._last_data_date = None
            else:
                # For metered costs, use timestamp from cost_data
                time_stamp = cost_data.get("time")
                if time_stamp:
                    timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
                    tz = get_timezone(timezone_str)
                    self._last_data_date = datetime.fromtimestamp(time_stamp, tz=tz)
                else:
                    self._last_data_date = None

            _LOGGER.info(
                "Updated %s (estimated): %s %s",
                self.entity_id,
                self._attr_native_value,
                self._attr_native_unit_of_measurement,
            )
        else:
            _LOGGER.debug(
                "No estimated cost data returned for %s (utility: %s, meter: %s)",
                self.entity_id,
                self._utility_code,
                self._measuring_point_id,
            )
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()


class EcoGuardDailyCostAggregateSensor(EcoGuardBaseSensor):
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
        description_key = (
            "description.cost_daily_aggregated_estimated"
            if cost_type == "estimated"
            else "description.cost_daily_aggregated_metered"
        )
        super().__init__(coordinator, hass=hass, description_key=description_key)
        self._hass = hass
        self._utility_code = utility_code
        self._cost_type = cost_type

        # Build sensor name
        utility_name = get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        # Format: "Cost Daily Metered/Estimated - Utility"
        cost_daily = get_translation_default("name.cost_daily")
        if cost_type == "estimated":
            estimated = get_translation_default("name.estimated")
            self._attr_name = f"{cost_daily} {estimated} - {utility_name}"
        else:
            metered = get_translation_default("name.metered")
            self._attr_name = f"{cost_daily} {metered} - {utility_name}"

        # Build unique_id following pattern: cost_daily_metered/estimated_utility
        utility_slug = utility_code_to_slug(utility_code)
        if cost_type == "estimated":
            self._attr_unique_id = f"{DOMAIN}_cost_daily_estimated_{utility_slug}"
        else:
            self._attr_unique_id = f"{DOMAIN}_cost_daily_metered_{utility_slug}"

        # Sensor attributes
        self._attr_device_info = self._get_device_info(coordinator.node_id)

        # Set state class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = None
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._last_data_date: datetime | None = None
        self._meters_with_data: list[dict[str, Any]] = []
        self._data_lagging: bool = False
        self._data_lag_days: int | None = None

        # Set icon for cost sensor (all money units use dollar icon)
        self._attr_icon = "mdi:currency-usd"

        # Set entity description (must be called after name and unique_id are set)
        self._set_entity_description()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = self._get_base_extra_state_attributes()
        attrs.update(
            {
                "utility_code": self._utility_code,
                "sensor_type": "daily_cost_aggregate",
                "cost_type": "metered" if self._cost_type == "actual" else "estimated",
                "meter_count": len(self._meters_with_data),
            }
        )

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()

        # Add lag detection attributes
        attrs["data_lagging"] = self._data_lagging
        if self._data_lag_days is not None:
            attrs["data_lag_days"] = self._data_lag_days

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

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            utility_name = await async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            cost_daily = await async_get_translation(self._hass, "name.cost_daily")
            if self._cost_type == "estimated":
                estimated = await async_get_translation(self._hass, "name.estimated")
                new_name = f"{cost_daily} {estimated} - {utility_name}"
            else:
                metered = await async_get_translation(self._hass, "name.metered")
                new_name = f"{cost_daily} {metered} - {utility_name}"

            await self._update_name_and_registry(new_name, log_level="debug")

            # Update description
            await self._async_update_description()
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            _LOGGER.debug("No coordinator data for %s", self.entity_id)
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None
            self._data_lagging = False
            self._data_lag_days = None
            self._attr_available = True
            self.async_write_ha_state()
            return

        # Get cost cache from coordinator data
        cost_cache = coordinator_data.get("latest_cost_cache", {})
        daily_price_cache = coordinator_data.get("daily_price_cache", {})
        daily_consumption_cache = coordinator_data.get("daily_consumption_cache", {})

        timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
        tz = get_timezone(timezone_str)

        # Sum costs across all meters for this utility
        active_installations = self.coordinator.get_active_installations()
        total_value = 0.0
        latest_timestamp = None
        meters_with_data = []
        actual_last_data_dates: list[datetime] = []

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

            # Read cost from cache (no API call)
            # Always use metered cache key
            # For estimated costs, use metered cost if available (estimated = metered when metered exists)
            cache_key = f"{self._utility_code}_{measuring_point_id}_metered"
            consumption_cache_key = f"{self._utility_code}_{measuring_point_id}"
            cost_data = cost_cache.get(cache_key)

            # Find actual last data date for this meter
            if self._cost_type == "actual":
                # Metered costs: use price cache
                price_daily_cache = daily_price_cache.get(cache_key, [])
                meter_last_date = find_last_price_date(price_daily_cache, tz)
            else:
                # Estimated costs: use consumption cache
                consumption_daily_cache = daily_consumption_cache.get(
                    consumption_cache_key, []
                )
                meter_last_date = find_last_data_date(consumption_daily_cache, tz)

            if meter_last_date:
                actual_last_data_dates.append(meter_last_date)

            if cost_data and cost_data.get("value") is not None:
                value = cost_data.get("value", 0.0)
                total_value += value

                # Track latest timestamp
                time_stamp = cost_data.get("time")
                if time_stamp:
                    if latest_timestamp is None or time_stamp > latest_timestamp:
                        latest_timestamp = time_stamp

                meters_with_data.append(
                    {
                        "measuring_point_id": measuring_point_id,
                        "measuring_point_name": measuring_point_name,
                        "value": value,
                    }
                )

        if total_value > 0 or (self._cost_type == "actual" and meters_with_data):
            # Even if total is 0, update if we have meter data (shows 0 is valid)
            self._attr_native_value = (
                round_to_max_digits(total_value) if total_value > 0 else 0.0
            )
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            # Use actual last data date if available (most recent across all meters), otherwise fall back to latest timestamp
            if actual_last_data_dates:
                self._last_data_date = max(actual_last_data_dates)
            elif latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp, tz=tz)
            else:
                self._last_data_date = None
            # Detect lag based on the last data date, similar to consumption sensors
            self._data_lagging, self._data_lag_days = detect_data_lag(
                self._last_data_date,
                tz,
            )
            self._meters_with_data = meters_with_data
            self._attr_available = True

            # Detect lag
            if self._last_data_date:
                is_lagging, lag_days = detect_data_lag(self._last_data_date, tz)
                self._data_lagging = is_lagging
                self._data_lag_days = lag_days
            else:
                self._data_lagging = True
                self._data_lag_days = None

            _LOGGER.info(
                "Updated %s: %s %s (from %d meters)",
                self.entity_id,
                self._attr_native_value,
                currency,
                len(meters_with_data),
            )
        else:
            # No metered cost data available
            # For estimated costs, trigger async fetch to calculate from consumption + rate/spot prices
            if self._cost_type == "estimated":
                from homeassistant.core import CoreState

                if (
                    self.hass
                    and not self.hass.is_stopping
                    and self.hass.state != CoreState.starting
                ):
                    # Trigger async fetch in background (non-blocking)
                    async def _fetch_estimated_cost():
                        try:
                            _LOGGER.debug(
                                "Starting async fetch for estimated cost aggregate: %s",
                                self.entity_id,
                            )
                            await self._async_fetch_value()
                        except Exception as err:
                            _LOGGER.warning(
                                "Error in async fetch for %s: %s",
                                self.entity_id,
                                err,
                                exc_info=True,
                            )

                    self.hass.async_create_task(_fetch_estimated_cost())
                    _LOGGER.debug(
                        "Created async task for estimated cost aggregate fetch: %s",
                        self.entity_id,
                    )
                else:
                    _LOGGER.debug(
                        "Skipping async fetch for %s: hass=%s, is_stopping=%s, state=%s",
                        self.entity_id,
                        self.hass is not None,
                        self.hass.is_stopping if self.hass else None,
                        self.hass.state if self.hass else None,
                    )

            # No data available yet, but keep sensor available
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None
            self._meters_with_data = []
            self._data_lagging = False
            self._data_lag_days = None
            self._attr_available = True

        self.async_write_ha_state()

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
            if self._cost_type == "estimated":
                cost_data = await self.coordinator.get_latest_estimated_cost(
                    utility_code=self._utility_code,
                    measuring_point_id=measuring_point_id,
                    external_key=installation.get("ExternalKey"),
                )
            else:
                cost_data = await self.coordinator.get_latest_metered_cost(
                    utility_code=self._utility_code,
                    measuring_point_id=measuring_point_id,
                    external_key=installation.get("ExternalKey"),
                )

            if cost_data and cost_data.get("value") is not None:
                value = cost_data.get("value", 0.0)
                total_value += value

                # Track latest timestamp
                time_stamp = cost_data.get("time")
                if time_stamp:
                    if latest_timestamp is None or time_stamp > latest_timestamp:
                        latest_timestamp = time_stamp

                meters_with_data.append(
                    {
                        "measuring_point_id": measuring_point_id,
                        "measuring_point_name": measuring_point_name,
                        "value": value,
                    }
                )

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


class EcoGuardDailyCombinedWaterCostSensor(EcoGuardBaseSensor):
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
        description_key = (
            "description.cost_daily_combined_water_estimated"
            if cost_type == "estimated"
            else "description.cost_daily_combined_water_metered"
        )
        super().__init__(coordinator, hass=hass, description_key=description_key)
        self._hass = hass
        self._cost_type = cost_type

        # Format: "Cost Daily Metered/Estimated - Combined Water"
        cost_daily = get_translation_default("name.cost_daily")
        water_name = get_translation_default("name.combined_water")
        if water_name == "name.combined_water":  # Fallback if not found
            water_name = "Combined Water"

        if cost_type == "estimated":
            estimated = get_translation_default("name.estimated")
            self._attr_name = f"{cost_daily} {estimated} - {water_name}"
        else:
            metered = get_translation_default("name.metered")
            self._attr_name = f"{cost_daily} {metered} - {water_name}"

        # Build unique_id
        if cost_type == "estimated":
            self._attr_unique_id = f"{DOMAIN}_cost_daily_estimated_combined_water"
        else:
            self._attr_unique_id = f"{DOMAIN}_cost_daily_metered_combined_water"

        # Sensor attributes
        self._attr_device_info = self._get_device_info(coordinator.node_id)

        # Set state class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = None
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._last_data_date: datetime | None = None
        self._hw_meters_with_data: list[dict[str, Any]] = []
        self._cw_meters_with_data: list[dict[str, Any]] = []

        # Set icon for cost sensor (all money units use dollar icon)
        self._attr_icon = "mdi:currency-usd"

        # Set entity description (must be called after name and unique_id are set)
        self._set_entity_description()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = self._get_base_extra_state_attributes()
        attrs.update(
            {
                "sensor_type": "daily_cost_combined_water",
                "cost_type": "metered" if self._cost_type == "actual" else "estimated",
                "utilities": ["HW", "CW"],
                "hw_meter_count": len(self._hw_meters_with_data),
                "cw_meter_count": len(self._cw_meters_with_data),
            }
        )

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()

        # Add lag detection attributes, consistent with other daily sensors
        timezone = get_timezone(self.hass)
        data_lagging, data_lag_days = detect_data_lag(self._last_data_date, timezone)
        attrs["data_lagging"] = data_lagging
        attrs["data_lag_days"] = data_lag_days
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

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            cost_daily = await async_get_translation(self._hass, "name.cost_daily")
            water_name = await async_get_translation(self._hass, "name.combined_water")
            if water_name == "name.combined_water":
                water_name = "Combined Water"

            if self._cost_type == "estimated":
                estimated = await async_get_translation(self._hass, "name.estimated")
                new_name = f"{cost_daily} {estimated} - {water_name}"
            else:
                metered = await async_get_translation(self._hass, "name.metered")
                new_name = f"{cost_daily} {metered} - {water_name}"

            await self._update_name_and_registry(new_name, log_level="debug")

            # Update description
            await self._async_update_description()
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _update_from_coordinator_data(self) -> None:
        """Update sensor state from coordinator's cached data (no API calls)."""
        # Read from coordinator.data cache (populated by batch fetch)
        coordinator_data = self.coordinator.data
        if not coordinator_data:
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._attr_available = True  # Keep available even if no data
            self.async_write_ha_state()
            return

        # Get cost cache from coordinator data
        cost_cache = coordinator_data.get("latest_cost_cache", {})
        daily_price_cache = coordinator_data.get("daily_price_cache", {})
        daily_consumption_cache = coordinator_data.get("daily_consumption_cache", {})

        timezone_str = self.coordinator.get_setting("TimeZoneIANA") or "UTC"
        tz = get_timezone(timezone_str)

        active_installations = self.coordinator.get_active_installations()
        hw_total = 0.0
        cw_total = 0.0
        latest_timestamp = None
        hw_meters_with_data = []
        cw_meters_with_data = []
        hw_last_data_dates: list[datetime] = []
        cw_last_data_dates: list[datetime] = []

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

                # Read cost from cache (no API call)
                cache_key = f"{utility_code}_{measuring_point_id}_metered"
                consumption_cache_key = f"{utility_code}_{measuring_point_id}"
                cost_data = cost_cache.get(cache_key)

                # Find actual last data date for this meter
                if self._cost_type == "actual":
                    # Metered costs: use price cache
                    price_daily_cache = daily_price_cache.get(cache_key, [])
                    meter_last_date = find_last_price_date(price_daily_cache, tz)
                else:
                    # Estimated costs: use consumption cache
                    consumption_daily_cache = daily_consumption_cache.get(
                        consumption_cache_key, []
                    )
                    meter_last_date = find_last_data_date(consumption_daily_cache, tz)

                if meter_last_date:
                    if utility_code == "HW":
                        hw_last_data_dates.append(meter_last_date)
                    elif utility_code == "CW":
                        cw_last_data_dates.append(meter_last_date)

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
                elif utility_code == "HW" and self._cost_type == "actual":
                    # For metered HW costs: if data is missing (Unknown), we need to know about it
                    # This happens when all HW price values are 0 (no metered price data from API)
                    # We track this so we can show Unknown for the combined sensor
                    _LOGGER.debug(
                        "HW cost data is Unknown (missing from cache) for meter %d in combined sensor %s",
                        measuring_point_id,
                        self.entity_id,
                    )

        total_value = hw_total + cw_total

        # For estimated costs: if we don't have data for both HW and CW, trigger async fetch
        # This is needed because HW estimated costs are calculated using spot prices, not from cache
        # We need both utilities to show a value, so fetch if either is missing
        if self._cost_type == "estimated":
            # Check if we're missing data for either utility
            has_hw_data = len(hw_meters_with_data) > 0
            has_cw_data = len(cw_meters_with_data) > 0

            # If we're missing data for either utility, trigger async fetch
            # This ensures we get the complete combined cost with both HW and CW
            if not has_hw_data or not has_cw_data:
                from homeassistant.core import CoreState

                if (
                    self.hass
                    and not self.hass.is_stopping
                    and self.hass.state != CoreState.starting
                ):
                    # Trigger async fetch in background (non-blocking)
                    async def _fetch_estimated_cost():
                        try:
                            _LOGGER.debug(
                                "Starting async fetch for estimated combined water cost: %s",
                                self.entity_id,
                            )
                            await self._async_fetch_value()
                        except Exception as err:
                            _LOGGER.warning(
                                "Error in async fetch for %s: %s",
                                self.entity_id,
                                err,
                                exc_info=True,
                            )

                    self.hass.async_create_task(_fetch_estimated_cost())
                    _LOGGER.debug(
                        "Created async task for estimated combined water cost fetch: %s (has_hw_data=%s, has_cw_data=%s)",
                        self.entity_id,
                        has_hw_data,
                        has_cw_data,
                    )

        # Only set a value if we have data for BOTH HW and CW
        # This ensures the combined sensor only shows a value when both utilities are available
        # If HW is Unknown (missing from cache), we show Unknown rather than just CW cost
        # (showing partial data would be misleading - it looks like total combined cost but is missing HW)
        has_hw_data = len(hw_meters_with_data) > 0
        has_cw_data = len(cw_meters_with_data) > 0

        if has_hw_data and has_cw_data:
            self._attr_native_value = round_to_max_digits(total_value)
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            # Use the earliest of HW and CW last data dates (most conservative)
            # This ensures we show lag if either utility is lagging
            all_last_dates = []
            if hw_last_data_dates:
                all_last_dates.extend(hw_last_data_dates)
            if cw_last_data_dates:
                all_last_dates.extend(cw_last_data_dates)
            if all_last_dates:
                self._last_data_date = min(all_last_dates)
            elif latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp, tz=tz)
            else:
                self._last_data_date = None
            self._hw_meters_with_data = hw_meters_with_data
            self._cw_meters_with_data = cw_meters_with_data
            self._attr_available = True
            _LOGGER.debug(
                "Updated %s: HW=%.2f, CW=%.2f, Total=%.2f",
                self.entity_id,
                hw_total,
                cw_total,
                total_value,
            )
        else:
            # Missing data for one or both utilities - show Unknown
            # This is especially important for metered costs: if HW is Unknown (all 0 values),
            # we show Unknown rather than just CW cost (which would be misleading)
            if self._cost_type == "estimated":
                _LOGGER.debug(
                    "Waiting for both utilities: %s (has_hw_data=%s, has_cw_data=%s)",
                    self.entity_id,
                    has_hw_data,
                    has_cw_data,
                )
            elif self._cost_type == "actual":
                _LOGGER.debug(
                    "Missing data for combined water cost: %s (has_hw_data=%s, has_cw_data=%s) - showing Unknown",
                    self.entity_id,
                    has_hw_data,
                    has_cw_data,
                )
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None
            self._hw_meters_with_data = []
            self._cw_meters_with_data = []
            # Keep sensor available even if no data (shows as "unknown" not "unavailable")
            self._attr_available = True

        self.async_write_ha_state()

    async def _async_fetch_value(self) -> None:
        """Fetch estimated combined water cost by fetching costs for all HW and CW meters."""
        if self._cost_type != "estimated":
            # Only fetch for estimated costs
            return

        _LOGGER.debug("Fetching estimated combined water cost for %s", self.entity_id)

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

                # Fetch estimated cost for this meter
                cost_data = await self.coordinator.get_latest_estimated_cost(
                    utility_code=utility_code,
                    measuring_point_id=measuring_point_id,
                    external_key=installation.get("ExternalKey"),
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

        # Only set a value if we have data for BOTH HW and CW
        # This ensures the combined sensor only shows a value when both utilities are available
        has_hw_data = len(hw_meters_with_data) > 0
        has_cw_data = len(cw_meters_with_data) > 0

        if has_hw_data and has_cw_data and total_value > 0:
            self._attr_native_value = round_to_max_digits(total_value)
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            if latest_timestamp:
                self._last_data_date = datetime.fromtimestamp(latest_timestamp)
            self._hw_meters_with_data = hw_meters_with_data
            self._cw_meters_with_data = cw_meters_with_data
            self._attr_available = True

            _LOGGER.info(
                "Updated %s (estimated): HW=%.2f, CW=%.2f, Total=%.2f %s",
                self.entity_id,
                hw_total,
                cw_total,
                total_value,
                currency,
            )
        else:
            # Missing data for one or both utilities - don't show a value yet
            self._attr_native_value = None
            currency = self.coordinator.get_setting("Currency") or ""
            self._attr_native_unit_of_measurement = currency
            self._last_data_date = None
            self._hw_meters_with_data = []
            self._cw_meters_with_data = []
            self._attr_available = True
            _LOGGER.debug(
                "Waiting for both utilities in %s (has_hw_data=%s, has_cw_data=%s)",
                self.entity_id,
                has_hw_data,
                has_cw_data,
            )

        self.async_write_ha_state()
