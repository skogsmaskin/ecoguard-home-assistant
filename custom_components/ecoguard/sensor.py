"""Sensor platform for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import logging
import math
import json
import asyncio
from pathlib import Path

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EcoGuardDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Cache for translation files
_translation_cache: dict[str, dict[str, Any]] = {}


def _load_translation_file_sync(lang: str) -> dict[str, Any] | None:
    """Load translation file synchronously (to be run in thread)."""
    try:
        # Check cache first
        if lang in _translation_cache:
            return _translation_cache[lang]

        # Get the integration directory
        integration_dir = Path(__file__).parent
        
        # For English, use strings.json (Home Assistant standard)
        if lang == "en":
            strings_file = integration_dir / "strings.json"
            if strings_file.exists():
                with open(strings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _translation_cache["en"] = data
                    return data
            # Fallback to en.json if strings.json doesn't exist
            translation_file = integration_dir / "translations" / "en.json"
            if translation_file.exists():
                with open(translation_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _translation_cache["en"] = data
                    return data
        else:
            # For other languages, try translations/{lang}.json
            translation_file = integration_dir / "translations" / f"{lang}.json"
            if translation_file.exists():
                with open(translation_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _translation_cache[lang] = data
                    return data
            
            # Fallback to strings.json for English if language file doesn't exist
            strings_file = integration_dir / "strings.json"
            if strings_file.exists():
                with open(strings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _translation_cache["en"] = data
                    return data
    except Exception as e:
        _LOGGER.debug("Failed to load translation file for lang %s: %s", lang, e)

    return None


async def _load_translation_file(hass: HomeAssistant, lang: str) -> dict[str, Any] | None:
    """Load translation file asynchronously to access sensor section."""
    # Check cache first
    if lang in _translation_cache:
        return _translation_cache[lang]

    # Run the blocking file I/O in a thread pool
    try:
        data = await asyncio.to_thread(_load_translation_file_sync, lang)
        return data
    except Exception as e:
        _LOGGER.debug("Failed to load translation file for lang %s: %s", lang, e)
        return None


async def _async_get_translation(hass: HomeAssistant, key: str, **kwargs: Any) -> str:
    """Get a translated string from the integration's translation files."""
    try:
        # Get the current language from hass.config.language
        lang = getattr(hass.config, 'language', 'en')

        # Load translation file directly to access common section
        # (The translation helper only loads config section)
        translation_data = await _load_translation_file(hass, lang)

        if translation_data and "common" in translation_data:
            sensor_data = translation_data["common"]

            # Navigate the nested structure: common.utility.hw or common.name.daily_consumption
            key_parts = key.split(".")
            current = sensor_data

            for part in key_parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    current = None
                    break

            if current and isinstance(current, str):
                text = current
                _LOGGER.debug("Found translation for key %s: %s (lang=%s)", key, text, lang)
                return text.format(**kwargs) if kwargs else text

        # Fallback to English
        if lang != "en":
            translation_data = await _load_translation_file(hass, "en")
            if translation_data and "common" in translation_data:
                sensor_data = translation_data["common"]
                key_parts = key.split(".")
                current = sensor_data

                for part in key_parts:
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    else:
                        current = None
                        break

                if current and isinstance(current, str):
                    text = current
                    return text.format(**kwargs) if kwargs else text
    except Exception as e:
        _LOGGER.warning("Translation lookup failed for key %s (lang=%s): %s", key, getattr(hass.config, 'language', 'en'), e)

    # Fallback to English defaults
    defaults = {
        "utility.hw": "Hot Water",
        "utility.cw": "Cold Water",
        "name.daily_consumption": "Daily Consumption",
        "name.last_update": "Last Update",
        "name.month_to_date_consumption": "Month-to-Date Consumption",
        "name.month_to_date_price": "Month-to-Date Price",
        "name.estimated": "Estimated",
        "name.metered": "Metered",
        "name.other_items_monthly_cost": "Other Items Monthly Cost (Last Bill)",
        "name.month_to_date_total_cost_estimated": "Month-to-Date Total Cost (Estimated)",
        "name.month_to_date_total_cost_metered": "Month-to-Date Total Cost (Metered)",
        "name.end_of_month_estimate": "End-of-Month Bill Estimate",
        "name.measuring_point": "Measuring Point {id}",
        "name.device_name": "EcoGuard Node {node_id}",
    }

    default = defaults.get(key, key)
    return default.format(**kwargs) if kwargs else default


def _get_translation_default(key: str, **kwargs: Any) -> str:
    """Get English default translation (for use in __init__ to avoid blocking I/O).

    Actual translations will be loaded in async_added_to_hass.
    """
    defaults = {
        "utility.hw": "Hot Water",
        "utility.cw": "Cold Water",
        "name.daily_consumption": "Daily Consumption",
        "name.last_update": "Last Update",
        "name.month_to_date_consumption": "Month-to-Date Consumption",
        "name.month_to_date_price": "Month-to-Date Price",
        "name.estimated": "Estimated",
        "name.metered": "Metered",
        "name.other_items_monthly_cost": "Other Items Monthly Cost (Last Bill)",
        "name.month_to_date_total_cost_estimated": "Month-to-Date Total Cost (Estimated)",
        "name.month_to_date_total_cost_metered": "Month-to-Date Total Cost (Metered)",
        "name.end_of_month_estimate": "End-of-Month Bill Estimate",
        "name.measuring_point": "Measuring Point {id}",
        "name.device_name": "EcoGuard Node {node_id}",
    }

    default = defaults.get(key, key)
    return default.format(**kwargs) if kwargs else default


def round_to_max_digits(value: float | None, max_digits: int = 3) -> float | None:
    """Round a value to a maximum number of significant digits.

    Args:
        value: The value to round
        max_digits: Maximum number of significant digits (default: 3)

    Returns:
        Rounded value, or None if input is None
    """
    if value is None:
        return None

    if value == 0:
        return 0.0

    # Calculate the number of decimal places needed for max_digits significant digits
    magnitude = math.floor(math.log10(abs(value)))
    decimal_places = max(0, max_digits - 1 - magnitude)

    # Round to the calculated decimal places
    return round(value, decimal_places)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EcoGuard sensors from a config entry."""
    coordinator: EcoGuardDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    latest_reception_coordinator = hass.data[DOMAIN][entry.entry_id].get(
        "latest_reception_coordinator"
    )

    # Wait for initial data
    try:
        await coordinator.async_config_entry_first_refresh()
        if latest_reception_coordinator:
            await latest_reception_coordinator.async_config_entry_first_refresh()
    except Exception as err:
        _LOGGER.error("Failed to refresh coordinator data: %s", err)

    # Create sensors for each active installation
    sensors: list[SensorEntity] = []
    active_installations = coordinator.get_active_installations()

    # Track unique utility codes across all installations
    utility_codes = set()

    # Track measuring points for which we've already created latest reception sensors
    measuring_points_with_reception_sensor = set()

    for installation in active_installations:
        measuring_point_id = installation.get("MeasuringPointID")
        registers = installation.get("Registers", [])

        # Get measuring point name for better sensor naming
        measuring_point_name = None
        for mp in coordinator.get_measuring_points():
            if mp.get("ID") == measuring_point_id:
                measuring_point_name = mp.get("Name")
                break

        # Create a latest reception sensor for each meter (only once per measuring point)
        # Find the primary utility code for this measuring point
        primary_utility_code = None
        for register in registers:
            utility_code = register.get("UtilityCode")
            if utility_code:
                primary_utility_code = utility_code
                break  # Use the first utility found
        
        if measuring_point_id not in measuring_points_with_reception_sensor and latest_reception_coordinator:
            latest_reception_sensor = EcoGuardLatestReceptionSensor(
                hass=hass,
                coordinator=latest_reception_coordinator,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                utility_code=primary_utility_code,
            )
            sensors.append(latest_reception_sensor)
            measuring_points_with_reception_sensor.add(measuring_point_id)

        # Create a sensor for each register (utility) in the installation
        for register in registers:
            utility_code = register.get("UtilityCode")
            if not utility_code:
                continue

            utility_codes.add(utility_code)

            # Create a daily consumption sensor for each meter
            daily_sensor = EcoGuardDailyConsumptionSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
            )
            sensors.append(daily_sensor)

    # Create monthly aggregate sensors for each utility (CW, HW)
    for utility_code in utility_codes:
        if utility_code in ("CW", "HW"):
            # Monthly consumption sensor
            monthly_con_sensor = EcoGuardMonthlyAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                aggregate_type="con",
            )
            sensors.append(monthly_con_sensor)

            # Monthly price sensors: metered and estimated
            monthly_price_metered_sensor = EcoGuardMonthlyAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                aggregate_type="price",
                cost_type="actual",  # Internal: "actual", Display: "Metered"
            )
            sensors.append(monthly_price_metered_sensor)

            monthly_price_estimated_sensor = EcoGuardMonthlyAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                aggregate_type="price",
                cost_type="estimated",
            )
            sensors.append(monthly_price_estimated_sensor)

    # Create other items (general fees) sensor
    other_items_sensor = EcoGuardOtherItemsSensor(hass=hass, coordinator=coordinator)
    sensors.append(other_items_sensor)

    # End-of-month estimate sensor
    end_of_month_estimate_sensor = EcoGuardEndOfMonthEstimateSensor(hass=hass, coordinator=coordinator)
    sensors.append(end_of_month_estimate_sensor)

    # Create total monthly cost sensors (metered and estimated)
    # These sum the individual utility costs
    metered_cost_sensor = EcoGuardTotalMonthlyCostSensor(
        hass=hass,
        coordinator=coordinator,
        cost_type="actual",  # Internal: "actual", Display: "Metered"
    )
    sensors.append(metered_cost_sensor)

    estimated_cost_sensor = EcoGuardTotalMonthlyCostSensor(
        hass=hass,
        coordinator=coordinator,
        cost_type="estimated",
    )
    sensors.append(estimated_cost_sensor)

    _LOGGER.info("Creating %d EcoGuard sensors", len(sensors))
    async_add_entities(sensors)


class EcoGuardSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Base class for EcoGuard sensors."""

    def __init__(
        self,
        coordinator: EcoGuardDataUpdateCoordinator,
        unique_id_suffix: str,
        name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.node_id}_{unique_id_suffix}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": f"EcoGuard Node {coordinator.node_id}",
            "manufacturer": "EcoGuard",
        }


class EcoGuardDailyConsumptionSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
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
        super().__init__(coordinator)
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
            measuring_point_display = _get_translation_default("name.measuring_point", id=measuring_point_id)

        # Use English defaults here; will be updated in async_added_to_hass with proper translations
        utility_name = _get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        daily_consumption = _get_translation_default("name.daily_consumption")

        # Format: "Daily Consumption (Utility) - Measuring Point"
        # This groups similar sensors together when sorted alphabetically
        # This will be updated in async_added_to_hass with proper async translations
        self._attr_name = f'{daily_consumption} ({utility_name}) - "{measuring_point_display}"'
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.node_id}_"
            f"mp{measuring_point_id}_{utility_code.lower()}_daily"
        )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
            "model": installation.get("DeviceTypeDisplay", "Unknown"),
        }

        # Set state class and unit (will be updated when we get data)
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_native_value = None
        self._attr_native_unit_of_measurement = None
        self._last_data_date: datetime | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "measuring_point_id": self._measuring_point_id,
            "utility_code": self._utility_code,
            "external_key": self._installation.get("ExternalKey"),
            "device_type": self._installation.get("DeviceTypeDisplay"),
            "sensor_type": "daily_consumption",
        }

        if self._last_data_date:
            attrs["last_data_date"] = self._last_data_date.isoformat()
            attrs["last_data_date_readable"] = self._last_data_date.strftime("%Y-%m-%d")

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        # Update sensor name with translations now that we're in hass
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            lang = getattr(self._hass.config, 'language', 'en')
            _LOGGER.debug("Updating sensor name for lang=%s", lang)

            # Rebuild sensor name with translations
            if self._measuring_point_name:
                measuring_point_display = self._measuring_point_name
            else:
                measuring_point_display = await _async_get_translation(
                    self._hass, "name.measuring_point", id=self._measuring_point_id
                )
                _LOGGER.debug("Measuring point display: %s", measuring_point_display)

            utility_name = await _async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":  # Fallback if not found
                utility_name = self._utility_code
            _LOGGER.debug("Utility name: %s", utility_name)

            daily_consumption = await _async_get_translation(self._hass, "name.daily_consumption")
            _LOGGER.debug("Daily consumption: %s", daily_consumption)

            # Update the name (this is the display name, not the entity_id)
            # Format: "Daily Consumption (Utility) - Measuring Point"
            # This groups similar sensors together when sorted alphabetically
            new_name = f'{daily_consumption} ({utility_name}) - "{measuring_point_display}"'
            if self._attr_name != new_name:
                old_name = self._attr_name
                self._attr_name = new_name
                self.async_write_ha_state()
                _LOGGER.info("Updated sensor name from '%s' to '%s' (lang=%s)", old_name, new_name, lang)
            else:
                _LOGGER.debug("Sensor name unchanged: %s", new_name)

            # Also update device name
            device_name = await _async_get_translation(
                self._hass, "name.device_name", node_id=self.coordinator.node_id
            )
            if self._attr_device_info.get("name") != device_name:
                self._attr_device_info["name"] = device_name
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.warning("Failed to update translated name: %s", e, exc_info=True)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching latest daily consumption value."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch last known daily consumption value asynchronously."""
        consumption_data = await self.coordinator.get_latest_consumption_value(
            utility_code=self._utility_code,
            measuring_point_id=self._measuring_point_id,
            external_key=self._installation.get("ExternalKey"),
        )

        if consumption_data:
            raw_value = consumption_data.get("value")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            self._attr_native_unit_of_measurement = consumption_data.get("unit")

            # Update last data date
            time_stamp = consumption_data.get("time")
            if time_stamp:
                self._last_data_date = datetime.fromtimestamp(time_stamp)
        else:
            self._attr_native_value = None
            self._attr_native_unit_of_measurement = None
            self._last_data_date = None

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()


class EcoGuardLatestReceptionSensor(CoordinatorEntity, SensorEntity):
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
        super().__init__(coordinator)
        self._hass = hass
        self._measuring_point_id = measuring_point_id
        self._measuring_point_name = measuring_point_name
        self._utility_code = utility_code

        # Build sensor name
        if measuring_point_name:
            measuring_point_display = measuring_point_name
        else:
            measuring_point_display = _get_translation_default("name.measuring_point", id=measuring_point_id)

        # Use English defaults here; will be updated in async_added_to_hass
        utility_suffix = ""
        if utility_code:
            utility_name = _get_translation_default(f"utility.{utility_code.lower()}")
            if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
                utility_name = utility_code
            utility_suffix = f" ({utility_name})"
        
        # Format: "Last Update (Utility) - Measuring Point"
        # This groups similar sensors together when sorted alphabetically
        last_update = _get_translation_default("name.last_update")
        if utility_suffix:
            self._attr_name = f'{last_update}{utility_suffix} - "{measuring_point_display}"'
        else:
            self._attr_name = f'{last_update} - "{measuring_point_display}"'
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.node_id}_mp{measuring_point_id}_last_update"
        )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

        # Set device class to timestamp
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_native_value = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "measuring_point_id": self._measuring_point_id,
        }

        if self._measuring_point_name:
            attrs["measuring_point_name"] = self._measuring_point_name
        
        if self._utility_code:
            attrs["utility_code"] = self._utility_code

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            if self._measuring_point_name:
                measuring_point_display = self._measuring_point_name
            else:
                measuring_point_display = await _async_get_translation(
                    self._hass, "name.measuring_point", id=self._measuring_point_id
                )

            utility_suffix = ""
            if self._utility_code:
                utility_name = await _async_get_translation(
                    self._hass, f"utility.{self._utility_code.lower()}"
                )
                if utility_name == f"utility.{self._utility_code.lower()}":  # Fallback if not found
                    utility_name = self._utility_code
                utility_suffix = f" ({utility_name})"

            last_update = await _async_get_translation(self._hass, "name.last_update")
            # Format: "Last Update (Utility) - Measuring Point"
            # This groups similar sensors together when sorted alphabetically
            if utility_suffix:
                self._attr_name = f'{last_update}{utility_suffix} - "{measuring_point_display}"'
            else:
                self._attr_name = f'{last_update} - "{measuring_point_display}"'
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching latest reception."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

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
                    self._attr_native_value = datetime.fromtimestamp(latest_timestamp, tz=timezone.utc)
                else:
                    self._attr_native_value = None
                break

        if latest_timestamp is None:
            # No reception data found for this measuring point
            self._attr_native_value = None

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()


class EcoGuardMonthlyAggregateSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Sensor for monthly aggregate consumption or price."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        utility_code: str,
        aggregate_type: str,
        cost_type: str = "actual",
    ) -> None:
        """Initialize the monthly aggregate sensor.

        Args:
            hass: Home Assistant instance
            coordinator: The coordinator instance
            utility_code: Utility code (e.g., "HW", "CW")
            aggregate_type: "con" for consumption, "price" for price
            cost_type: "actual" for metered API data, "estimated" for estimated (only for price)
        """
        super().__init__(coordinator)
        self._hass = hass
        self._utility_code = utility_code
        self._aggregate_type = aggregate_type
        self._cost_type = cost_type

        # Build sensor name
        # Use English defaults here; will be updated in async_added_to_hass
        utility_name = _get_translation_default(f"utility.{utility_code.lower()}")
        if utility_name == f"utility.{utility_code.lower()}":  # Fallback if not found
            utility_name = utility_code

        if aggregate_type == "con":
            aggregate_name = _get_translation_default("name.month_to_date_consumption")
        else:
            aggregate_name = _get_translation_default("name.month_to_date_price")

        # Add cost type suffix for price sensors
        if aggregate_type == "price" and cost_type == "estimated":
            estimated = _get_translation_default("name.estimated")
            aggregate_name = f"{aggregate_name} ({estimated})"
        elif aggregate_type == "price" and cost_type == "actual":
            metered = _get_translation_default("name.metered")
            aggregate_name = f"{aggregate_name} ({metered})"

        # Format: "Aggregate Name - Utility"
        # This groups similar sensors together when sorted alphabetically
        self._attr_name = f"{aggregate_name} - {utility_name}"

        # Build unique ID
        if aggregate_type == "price" and cost_type == "estimated":
            unique_id_suffix = f"{utility_code.lower()}_monthly_{aggregate_type}_estimated"
        else:
            # For "actual" cost_type, use "metered" in the ID for clarity
            unique_id_suffix = f"{utility_code.lower()}_monthly_{aggregate_type}_metered"

        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.node_id}_{unique_id_suffix}"
        )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

        # Set state class
        if aggregate_type == "con":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        # Always set a default unit from the start to prevent statistics issues
        # Use currency from settings as default for price sensors
        # If currency is not available, use "NOK" as fallback to ensure unit is never empty
        if aggregate_type == "price":
            default_unit = coordinator.get_setting("Currency") or "NOK"
            self._attr_native_unit_of_measurement = default_unit
        else:
            self._attr_native_unit_of_measurement = None
        self._current_year: int | None = None
        self._current_month: int | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "utility_code": self._utility_code,
            "aggregate_type": self._aggregate_type,
            "cost_type": "metered" if self._cost_type == "actual" else self._cost_type,
        }

        if self._current_year is not None and self._current_month is not None:
            attrs["year"] = self._current_year
            attrs["month"] = self._current_month
            attrs["period"] = f"{self._current_year}-{self._current_month:02d}"

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            utility_name = await _async_get_translation(
                self._hass, f"utility.{self._utility_code.lower()}"
            )
            if utility_name == f"utility.{self._utility_code.lower()}":
                utility_name = self._utility_code

            if self._aggregate_type == "con":
                aggregate_name = await _async_get_translation(
                    self._hass, "name.month_to_date_consumption"
                )
            else:
                aggregate_name = await _async_get_translation(
                    self._hass, "name.month_to_date_price"
                )

            if self._aggregate_type == "price" and self._cost_type == "estimated":
                estimated = await _async_get_translation(self._hass, "name.estimated")
                aggregate_name = f"{aggregate_name} ({estimated})"
            elif self._aggregate_type == "price" and self._cost_type == "actual":
                metered = await _async_get_translation(self._hass, "name.metered")
                aggregate_name = f"{aggregate_name} ({metered})"

            # Format: "Aggregate Name - Utility"
            # This groups similar sensors together when sorted alphabetically
            self._attr_name = f"{aggregate_name} - {utility_name}"
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching monthly aggregate."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch current month's aggregate value."""
        now = datetime.now()
        year = now.year
        month = now.month

        aggregate_data = await self.coordinator.get_monthly_aggregate(
            utility_code=self._utility_code,
            year=year,
            month=month,
            aggregate_type=self._aggregate_type,
            cost_type=self._cost_type if self._aggregate_type == "price" else "actual",
        )

        # Always set a default unit to prevent statistics issues
        # Use currency from settings as default for price sensors
        # If currency is not available, use "NOK" as fallback to ensure unit is never empty
        default_unit = ""
        if self._aggregate_type == "price":
            default_unit = self.coordinator.get_setting("Currency") or "NOK"

        if aggregate_data:
            raw_value = aggregate_data.get("value")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            # Use unit from data, or fall back to default
            self._attr_native_unit_of_measurement = aggregate_data.get("unit") or default_unit
            self._current_year = aggregate_data.get("year")
            self._current_month = aggregate_data.get("month")
        else:
            self._attr_native_value = None
            # Always set unit even when no data to maintain consistency for statistics
            self._attr_native_unit_of_measurement = default_unit
            self._current_year = None
            self._current_month = None

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()


class EcoGuardOtherItemsSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Sensor for other items (general fees) from billing results.

    Uses the most recent billing data as the source of truth.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
    ) -> None:
        """Initialize the other items sensor."""
        super().__init__(coordinator)
        self._hass = hass

        # Use English default here; will be updated in async_added_to_hass
        self._attr_name = _get_translation_default("name.other_items_monthly_cost")
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.node_id}_other_items_monthly_cost"
        )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

        # Set state class
        self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        # Always set currency unit from the start to prevent statistics issues
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._current_year: int | None = None
        self._current_month: int | None = None
        self._item_count: int | None = None
        self._items: list[dict[str, Any]] = []

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "sensor_type": "other_items_cost",
            "note": "Uses last bill's values (most recent billing period available)",
        }

        if self._current_year is not None and self._current_month is not None:
            attrs["year"] = self._current_year
            attrs["month"] = self._current_month
            attrs["period"] = f"{self._current_year}-{self._current_month:02d}"

        if self._item_count is not None:
            attrs["item_count"] = self._item_count

        if self._items:
            attrs["items"] = self._items

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            self._attr_name = await _async_get_translation(
                self._hass, "name.other_items_monthly_cost"
            )
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching other items cost."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch current month's other items cost."""
        now = datetime.now()
        year = now.year
        month = now.month

        cost_data = await self.coordinator.get_monthly_other_items_cost(
            year=year,
            month=month,
        )

        # Always set currency unit to prevent statistics issues
        default_currency = self.coordinator.get_setting("Currency") or ""

        if cost_data:
            raw_value = cost_data.get("value")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            # Use unit from data, or fall back to default currency
            self._attr_native_unit_of_measurement = cost_data.get("unit") or default_currency
            self._current_year = cost_data.get("year")
            self._current_month = cost_data.get("month")
            self._item_count = cost_data.get("item_count")
            self._items = cost_data.get("items", [])
        else:
            self._attr_native_value = None
            # Always set unit even when no data to maintain consistency for statistics
            self._attr_native_unit_of_measurement = default_currency
            self._current_year = None
            self._current_month = None
            self._item_count = None
            self._items = []

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()


class EcoGuardTotalMonthlyCostSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Sensor for total monthly cost across all utilities.

    Always outputs the pure (pre-VAT) value. If VAT is detected in billing results,
    it is automatically removed from the API prices to ensure consistency.

    Can be configured as "metered" (only metered API data) or "estimated" (includes
    estimated HW costs when price data is missing).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
        cost_type: str = "actual",
    ) -> None:
        """Initialize the total monthly cost sensor.

        Args:
            hass: Home Assistant instance
            coordinator: The coordinator instance
            cost_type: "actual" for metered API data, "estimated" for estimated costs
        """
        super().__init__(coordinator)
        self._hass = hass

        self._cost_type = cost_type  # "actual" (displayed as "Metered") or "estimated"

        # Use English defaults here; will be updated in async_added_to_hass
        if cost_type == "estimated":
            self._attr_name = _get_translation_default("name.month_to_date_total_cost_estimated")
            self._attr_unique_id = (
                f"{DOMAIN}_{coordinator.node_id}_total_monthly_cost_estimated"
            )
        else:
            self._attr_name = _get_translation_default("name.month_to_date_total_cost_metered")
            self._attr_unique_id = (
                f"{DOMAIN}_{coordinator.node_id}_total_monthly_cost_metered"
            )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

        # Set state class
        self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        # Always set currency unit from the start to prevent statistics issues
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._current_year: int | None = None
        self._current_month: int | None = None
        self._utilities: list[str] = []

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "sensor_type": "total_monthly_cost",
        }

        if self._current_year is not None and self._current_month is not None:
            attrs["year"] = self._current_year
            attrs["month"] = self._current_month
            attrs["period"] = f"{self._current_year}-{self._current_month:02d}"

        if self._utilities:
            attrs["utilities"] = self._utilities

        attrs["cost_type"] = "metered" if self._cost_type == "actual" else self._cost_type

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            if self._cost_type == "estimated":
                self._attr_name = await _async_get_translation(
                    self._hass, "name.month_to_date_total_cost_estimated"
                )
            else:
                self._attr_name = await _async_get_translation(
                    self._hass, "name.month_to_date_total_cost_metered"
                )
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching total monthly cost."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch current month's total cost by summing individual utility costs."""
        now = datetime.now()
        year = now.year
        month = now.month

        # Get all utility codes
        utility_codes = set()
        active_installations = self.coordinator.get_active_installations()
        for installation in active_installations:
            registers = installation.get("Registers", [])
            for register in registers:
                utility_code = register.get("UtilityCode")
                if utility_code and utility_code in ("HW", "CW", "E", "HE"):
                    utility_codes.add(utility_code)

        # Sum costs from individual utilities
        total_cost = 0.0
        utilities_with_data = []

        for utility_code in sorted(utility_codes):
            if utility_code in ("CW", "HW"):
                price_data = await self.coordinator.get_monthly_aggregate(
                    utility_code=utility_code,
                    year=year,
                    month=month,
                    aggregate_type="price",
                    cost_type=self._cost_type,
                )

                if price_data and price_data.get("value") is not None:
                    cost = price_data.get("value", 0.0)
                    total_cost += cost
                    utilities_with_data.append(utility_code)

        # Always set currency unit to prevent statistics issues
        currency = self.coordinator.get_setting("Currency") or ""

        if utilities_with_data:
            raw_value = total_cost

            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            self._attr_native_unit_of_measurement = currency
            self._current_year = year
            self._current_month = month
            self._utilities = utilities_with_data
        else:
            self._attr_native_value = None
            # Always set unit even when no data to maintain consistency for statistics
            self._attr_native_unit_of_measurement = currency
            self._current_year = None
            self._current_month = None
            self._utilities = []

        # Notify Home Assistant that the state has changed
        self.async_write_ha_state()


class EcoGuardEndOfMonthEstimateSensor(CoordinatorEntity[EcoGuardDataUpdateCoordinator], SensorEntity):
    """Sensor for end-of-month bill estimate.

    Estimates the total bill for the current month based on mean daily consumption
    and price so far, projected to the end of the month.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EcoGuardDataUpdateCoordinator,
    ) -> None:
        """Initialize the end-of-month estimate sensor."""
        super().__init__(coordinator)
        self._hass = hass

        # Use English default here; will be updated in async_added_to_hass
        self._attr_name = _get_translation_default("name.end_of_month_estimate")
        self._attr_unique_id = (
            f"{DOMAIN}_{coordinator.node_id}_end_of_month_estimate"
        )

        # Sensor attributes
        # Use English default here; will be updated in async_added_to_hass
        device_name = _get_translation_default("name.device_name", node_id=coordinator.node_id)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(coordinator.node_id))},
            "name": device_name,
            "manufacturer": "EcoGuard",
        }

        # Set state class
        self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        # Always set currency unit from the start to prevent statistics issues
        currency = coordinator.get_setting("Currency") or ""
        self._attr_native_unit_of_measurement = currency
        self._current_year: int | None = None
        self._current_month: int | None = None
        self._days_elapsed_calendar: int | None = None
        self._days_with_data: int | None = None
        self._days_remaining: int | None = None
        self._total_days_in_month: int | None = None
        self._latest_data_timestamp: int | None = None
        self._hw_consumption_estimate: float | None = None
        self._hw_price_estimate: float | None = None
        self._cw_consumption_estimate: float | None = None
        self._cw_price_estimate: float | None = None
        self._other_items_cost: float | None = None
        self._hw_mean_daily_consumption: float | None = None
        self._hw_mean_daily_price: float | None = None
        self._cw_mean_daily_consumption: float | None = None
        self._cw_mean_daily_price: float | None = None
        self._hw_consumption_so_far: float | None = None
        self._hw_price_so_far: float | None = None
        self._cw_consumption_so_far: float | None = None
        self._cw_price_so_far: float | None = None
        self._hw_price_is_estimated: bool = False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            "sensor_type": "end_of_month_estimate",
        }

        if self._current_year is not None and self._current_month is not None:
            attrs["year"] = self._current_year
            attrs["month"] = self._current_month
            attrs["period"] = f"{self._current_year}-{self._current_month:02d}"

        if self._days_elapsed_calendar is not None:
            attrs["days_elapsed_calendar"] = self._days_elapsed_calendar
        if self._days_with_data is not None:
            attrs["days_with_data"] = self._days_with_data
        if self._days_remaining is not None:
            attrs["days_remaining"] = self._days_remaining
        if self._total_days_in_month is not None:
            attrs["total_days_in_month"] = self._total_days_in_month
        if self._latest_data_timestamp is not None:
            attrs["latest_data_timestamp"] = self._latest_data_timestamp
            # Also add a human-readable date
            try:
                from datetime import datetime
                latest_date = datetime.fromtimestamp(self._latest_data_timestamp)
                attrs["latest_data_date"] = latest_date.isoformat()
            except Exception:
                pass

        if self._hw_consumption_estimate is not None:
            attrs["hw_consumption_estimate"] = round_to_max_digits(self._hw_consumption_estimate)
        if self._hw_price_estimate is not None:
            attrs["hw_price_estimate"] = round_to_max_digits(self._hw_price_estimate)
        if self._cw_consumption_estimate is not None:
            attrs["cw_consumption_estimate"] = round_to_max_digits(self._cw_consumption_estimate)
        if self._cw_price_estimate is not None:
            attrs["cw_price_estimate"] = round_to_max_digits(self._cw_price_estimate)
        if self._other_items_cost is not None:
            attrs["other_items_cost"] = round_to_max_digits(self._other_items_cost)

        if self._hw_mean_daily_consumption is not None:
            attrs["hw_mean_daily_consumption"] = round_to_max_digits(self._hw_mean_daily_consumption)
        if self._hw_mean_daily_price is not None:
            attrs["hw_mean_daily_price"] = round_to_max_digits(self._hw_mean_daily_price)
        if self._cw_mean_daily_consumption is not None:
            attrs["cw_mean_daily_consumption"] = round_to_max_digits(self._cw_mean_daily_consumption)
        if self._cw_mean_daily_price is not None:
            attrs["cw_mean_daily_price"] = round_to_max_digits(self._cw_mean_daily_price)

        if self._hw_consumption_so_far is not None:
            attrs["hw_consumption_so_far"] = round_to_max_digits(self._hw_consumption_so_far)
        if self._hw_price_so_far is not None:
            attrs["hw_price_so_far"] = round_to_max_digits(self._hw_price_so_far)
        if self._cw_consumption_so_far is not None:
            attrs["cw_consumption_so_far"] = round_to_max_digits(self._cw_consumption_so_far)
        if self._cw_price_so_far is not None:
            attrs["cw_price_so_far"] = round_to_max_digits(self._cw_price_so_far)

        attrs["hw_price_is_estimated"] = self._hw_price_is_estimated

        return attrs

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass, fetch initial value and update translations."""
        await super().async_added_to_hass()
        await self._async_update_translated_name()
        await self._async_fetch_value()

    async def _async_update_translated_name(self) -> None:
        """Update the sensor name with translated strings."""
        if not self.hass or not self._hass:
            return

        try:
            self._attr_name = await _async_get_translation(
                self._hass, "name.end_of_month_estimate"
            )
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.debug("Failed to update translated name: %s", e)

    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update by fetching end-of-month estimate."""
        if self.hass:
            self.hass.async_create_task(self._async_fetch_value())

    async def _async_fetch_value(self) -> None:
        """Fetch end-of-month estimate asynchronously."""
        estimate_data = await self.coordinator.get_end_of_month_estimate()

        # Always set currency unit to prevent statistics issues
        default_currency = self.coordinator.get_setting("Currency") or ""

        if estimate_data:
            raw_value = estimate_data.get("total_bill_estimate")
            self._attr_native_value = round_to_max_digits(raw_value) if isinstance(raw_value, (int, float)) else raw_value
            # Use currency from data, or fall back to default
            self._attr_native_unit_of_measurement = estimate_data.get("currency") or default_currency

            self._current_year = estimate_data.get("year")
            self._current_month = estimate_data.get("month")
            self._days_elapsed_calendar = estimate_data.get("days_elapsed_calendar")
            self._days_with_data = estimate_data.get("days_with_data")
            self._days_remaining = estimate_data.get("days_remaining")
            self._total_days_in_month = estimate_data.get("total_days_in_month")
            self._latest_data_timestamp = estimate_data.get("latest_data_timestamp")
            self._hw_consumption_estimate = estimate_data.get("hw_consumption_estimate")
            self._hw_price_estimate = estimate_data.get("hw_price_estimate")
            self._cw_consumption_estimate = estimate_data.get("cw_consumption_estimate")
            self._cw_price_estimate = estimate_data.get("cw_price_estimate")
            self._other_items_cost = estimate_data.get("other_items_cost")
            self._hw_mean_daily_consumption = estimate_data.get("hw_mean_daily_consumption")
            self._hw_mean_daily_price = estimate_data.get("hw_mean_daily_price")
            self._cw_mean_daily_consumption = estimate_data.get("cw_mean_daily_consumption")
            self._cw_mean_daily_price = estimate_data.get("cw_mean_daily_price")
            self._hw_consumption_so_far = estimate_data.get("hw_consumption_so_far")
            self._hw_price_so_far = estimate_data.get("hw_price_so_far")
            self._cw_consumption_so_far = estimate_data.get("cw_consumption_so_far")
            self._cw_price_so_far = estimate_data.get("cw_price_so_far")
            self._hw_price_is_estimated = estimate_data.get("hw_price_is_estimated", False)
        else:
            self._attr_native_value = None
            # Always set unit even when no data to maintain consistency for statistics
            self._attr_native_unit_of_measurement = default_currency
            self._current_year = None
            self._current_month = None
            self._days_elapsed_calendar = None
            self._days_with_data = None
            self._days_remaining = None
            self._total_days_in_month = None
            self._latest_data_timestamp = None
            self._hw_consumption_estimate = None
            self._hw_price_estimate = None
            self._cw_consumption_estimate = None
            self._cw_price_estimate = None
            self._other_items_cost = None
            self._hw_mean_daily_consumption = None
            self._hw_mean_daily_price = None
            self._cw_mean_daily_consumption = None
            self._cw_mean_daily_price = None
            self._hw_consumption_so_far = None
            self._hw_price_so_far = None
            self._cw_consumption_so_far = None
            self._cw_price_so_far = None
            self._hw_price_is_estimated = False

        self.async_write_ha_state()
