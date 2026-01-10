"""Sensor factory functions for creating EcoGuard sensors."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
import logging

from homeassistant.core import HomeAssistant

from .const import WATER_UTILITIES
from .coordinator import EcoGuardDataUpdateCoordinator

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


def create_installation_sensors(
    hass: HomeAssistant,
    coordinator: EcoGuardDataUpdateCoordinator,
    latest_reception_coordinator: Any,
    active_installations: list[dict[str, Any]],
    measuring_points_with_reception_sensor: set[int],
) -> tuple[list[Any], set[str]]:
    """Create sensors for each installation (daily consumption, daily cost, latest reception).

    Args:
        hass: Home Assistant instance
        coordinator: Main data coordinator
        latest_reception_coordinator: Latest reception coordinator (can be None)
        active_installations: List of active installations
        measuring_points_with_reception_sensor: Set of measuring point IDs that already have reception sensors

    Returns:
        Tuple of (list of created sensors, set of utility codes found)
    """
    # Import here to avoid circular import
    from .sensors import (
        EcoGuardDailyConsumptionSensor,
        EcoGuardDailyCostSensor,
        EcoGuardLatestReceptionSensor,
    )

    sensors: list[Any] = []
    utility_codes = set()

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

        if (
            measuring_point_id not in measuring_points_with_reception_sensor
            and latest_reception_coordinator
        ):
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

            # Create daily cost sensors for each meter: metered and estimated
            daily_cost_metered_sensor = EcoGuardDailyCostSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                cost_type="actual",
            )
            sensors.append(daily_cost_metered_sensor)

            daily_cost_estimated_sensor = EcoGuardDailyCostSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                cost_type="estimated",
            )
            sensors.append(daily_cost_estimated_sensor)

    return sensors, utility_codes


def create_daily_aggregate_sensors(
    hass: HomeAssistant,
    coordinator: EcoGuardDataUpdateCoordinator,
    utility_codes: set[str],
) -> list[Any]:
    """Create daily consumption and cost aggregate sensors for each utility.

    Args:
        hass: Home Assistant instance
        coordinator: Main data coordinator
        utility_codes: Set of utility codes found in installations

    Returns:
        List of created sensors
    """
    # Import here to avoid circular import
    from .sensors import (
        EcoGuardDailyConsumptionAggregateSensor,
        EcoGuardDailyCostAggregateSensor,
    )

    sensors: list[Any] = []

    # Create daily consumption aggregate sensors for each utility (CW, HW)
    for utility_code in utility_codes:
        if utility_code in WATER_UTILITIES:
            daily_aggregate_sensor = EcoGuardDailyConsumptionAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
            )
            sensors.append(daily_aggregate_sensor)

            # Create daily cost aggregate sensors for each utility: metered and estimated
            daily_cost_metered_aggregate_sensor = EcoGuardDailyCostAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                cost_type="actual",
            )
            sensors.append(daily_cost_metered_aggregate_sensor)

            daily_cost_estimated_aggregate_sensor = EcoGuardDailyCostAggregateSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                cost_type="estimated",
            )
            sensors.append(daily_cost_estimated_aggregate_sensor)

    return sensors


def create_daily_combined_water_sensors(
    hass: HomeAssistant,
    coordinator: EcoGuardDataUpdateCoordinator,
    utility_codes: set[str],
) -> list[Any]:
    """Create daily combined water sensors if both HW and CW exist.

    Args:
        hass: Home Assistant instance
        coordinator: Main data coordinator
        utility_codes: Set of utility codes found in installations

    Returns:
        List of created sensors
    """
    # Import here to avoid circular import
    from .sensors import (
        EcoGuardDailyCombinedWaterSensor,
        EcoGuardDailyCombinedWaterCostSensor,
    )

    sensors: list[Any] = []

    # Create daily combined water sensors if both HW and CW exist
    if "CW" in utility_codes and "HW" in utility_codes:
        daily_combined_water_sensor = EcoGuardDailyCombinedWaterSensor(
            hass=hass,
            coordinator=coordinator,
        )
        sensors.append(daily_combined_water_sensor)

        # Create daily combined water cost sensors: metered and estimated
        daily_combined_water_cost_metered_sensor = EcoGuardDailyCombinedWaterCostSensor(
            hass=hass,
            coordinator=coordinator,
            cost_type="actual",
        )
        sensors.append(daily_combined_water_cost_metered_sensor)

        daily_combined_water_cost_estimated_sensor = (
            EcoGuardDailyCombinedWaterCostSensor(
                hass=hass,
                coordinator=coordinator,
                cost_type="estimated",
            )
        )
        sensors.append(daily_combined_water_cost_estimated_sensor)

    return sensors


def create_monthly_accumulated_sensors(
    hass: HomeAssistant,
    coordinator: EcoGuardDataUpdateCoordinator,
    utility_codes: set[str],
) -> list[Any]:
    """Create monthly accumulated sensors for each utility (CW, HW).

    Args:
        hass: Home Assistant instance
        coordinator: Main data coordinator
        utility_codes: Set of utility codes found in installations

    Returns:
        List of created sensors
    """
    # Import here to avoid circular import
    from .sensors import EcoGuardMonthlyAccumulatedSensor

    sensors: list[Any] = []

    # Create monthly accumulated sensors for each utility (CW, HW)
    for utility_code in utility_codes:
        if utility_code in WATER_UTILITIES:
            # Monthly consumption sensor
            monthly_con_sensor = EcoGuardMonthlyAccumulatedSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                aggregate_type="con",
            )
            sensors.append(monthly_con_sensor)

            # Monthly cost sensors: metered and estimated
            monthly_cost_metered_sensor = EcoGuardMonthlyAccumulatedSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                aggregate_type="price",
                cost_type="actual",  # Internal: "actual", Display: "Metered"
            )
            sensors.append(monthly_cost_metered_sensor)

            monthly_cost_estimated_sensor = EcoGuardMonthlyAccumulatedSensor(
                hass=hass,
                coordinator=coordinator,
                utility_code=utility_code,
                aggregate_type="price",
                cost_type="estimated",
            )
            sensors.append(monthly_cost_estimated_sensor)

    return sensors


def create_monthly_meter_sensors(
    hass: HomeAssistant,
    coordinator: EcoGuardDataUpdateCoordinator,
    active_installations: list[dict[str, Any]],
) -> list[Any]:
    """Create monthly sensors per meter (consumption and cost).

    Args:
        hass: Home Assistant instance
        coordinator: Main data coordinator
        active_installations: List of active installations

    Returns:
        List of created sensors
    """
    # Import here to avoid circular import
    from .sensors import EcoGuardMonthlyMeterSensor

    sensors: list[Any] = []

    # Create monthly sensors per meter (consumption and cost)
    for installation in active_installations:
        measuring_point_id = installation.get("MeasuringPointID")
        registers = installation.get("Registers", [])

        # Get measuring point name
        measuring_point_name = None
        for mp in coordinator.get_measuring_points():
            if mp.get("ID") == measuring_point_id:
                measuring_point_name = mp.get("Name")
                break

        for register in registers:
            utility_code = register.get("UtilityCode")
            if not utility_code or utility_code not in ("CW", "HW"):
                continue

            # Monthly consumption sensor per meter
            monthly_con_meter_sensor = EcoGuardMonthlyMeterSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                aggregate_type="con",
            )
            sensors.append(monthly_con_meter_sensor)
            _LOGGER.debug(
                "Created monthly consumption sensor for meter %d (%s): unique_id=%s",
                measuring_point_id,
                measuring_point_name or f"mp{measuring_point_id}",
                monthly_con_meter_sensor._attr_unique_id,
            )

            # Monthly cost sensors per meter: metered and estimated
            # Note: aggregate_type="price" matches API terminology (API uses "[price]" in utility codes),
            # but sensor names use "cost" terminology for user-facing display
            monthly_cost_metered_meter_sensor = EcoGuardMonthlyMeterSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                aggregate_type="price",
                cost_type="actual",
            )
            sensors.append(monthly_cost_metered_meter_sensor)

            monthly_cost_estimated_meter_sensor = EcoGuardMonthlyMeterSensor(
                hass=hass,
                coordinator=coordinator,
                installation=installation,
                utility_code=utility_code,
                measuring_point_id=measuring_point_id,
                measuring_point_name=measuring_point_name,
                aggregate_type="price",
                cost_type="estimated",
            )
            sensors.append(monthly_cost_estimated_meter_sensor)

    return sensors


def create_combined_water_sensors(
    hass: HomeAssistant,
    coordinator: EcoGuardDataUpdateCoordinator,
    utility_codes: set[str],
) -> list[Any]:
    """Create combined water sensors (HW + CW) if both utilities exist.

    Args:
        hass: Home Assistant instance
        coordinator: Main data coordinator
        utility_codes: Set of utility codes found in installations

    Returns:
        List of created sensors
    """
    # Import here to avoid circular import
    from .sensors import EcoGuardCombinedWaterSensor

    sensors: list[Any] = []

    # Create combined water sensors (HW + CW) if both utilities exist
    if "CW" in utility_codes and "HW" in utility_codes:
        # Combined water consumption sensor
        combined_water_con_sensor = EcoGuardCombinedWaterSensor(
            hass=hass,
            coordinator=coordinator,
            aggregate_type="con",
        )
        sensors.append(combined_water_con_sensor)

        # Combined water cost sensors: metered and estimated
        combined_water_cost_metered_sensor = EcoGuardCombinedWaterSensor(
            hass=hass,
            coordinator=coordinator,
            aggregate_type="price",
            cost_type="actual",
        )
        sensors.append(combined_water_cost_metered_sensor)

        combined_water_cost_estimated_sensor = EcoGuardCombinedWaterSensor(
            hass=hass,
            coordinator=coordinator,
            aggregate_type="price",
            cost_type="estimated",
        )
        sensors.append(combined_water_cost_estimated_sensor)

    return sensors


def create_special_sensors(
    hass: HomeAssistant,
    coordinator: EcoGuardDataUpdateCoordinator,
) -> list[Any]:
    """Create special sensors (other items, end-of-month estimate, total monthly cost).

    Args:
        hass: Home Assistant instance
        coordinator: Main data coordinator

    Returns:
        List of created sensors
    """
    # Import here to avoid circular import
    from .sensors import (
        EcoGuardOtherItemsSensor,
        EcoGuardEndOfMonthEstimateSensor,
        EcoGuardTotalMonthlyCostSensor,
    )

    sensors: list[Any] = []

    # Create other items (general fees) sensor
    other_items_sensor = EcoGuardOtherItemsSensor(hass=hass, coordinator=coordinator)
    sensors.append(other_items_sensor)

    # End-of-month estimate sensor
    end_of_month_estimate_sensor = EcoGuardEndOfMonthEstimateSensor(
        hass=hass, coordinator=coordinator
    )
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

    return sensors
