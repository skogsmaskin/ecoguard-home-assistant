"""Sensor classes for EcoGuard integration."""

from __future__ import annotations

# Daily sensors
from .daily import (
    EcoGuardDailyConsumptionSensor,
    EcoGuardLatestReceptionSensor,
    EcoGuardDailyConsumptionAggregateSensor,
    EcoGuardDailyCombinedWaterSensor,
    EcoGuardDailyCostSensor,
    EcoGuardDailyCostAggregateSensor,
    EcoGuardDailyCombinedWaterCostSensor,
)

# Monthly sensors
from .monthly import (
    EcoGuardMonthlyAggregateSensor,
    EcoGuardMonthlyMeterSensor,
    EcoGuardCombinedWaterSensor,
)

# Special sensors
from .special import (
    EcoGuardOtherItemsSensor,
    EcoGuardTotalMonthlyCostSensor,
    EcoGuardEndOfMonthEstimateSensor,
)

__all__ = [
    # Daily sensors
    "EcoGuardDailyConsumptionSensor",
    "EcoGuardLatestReceptionSensor",
    "EcoGuardDailyConsumptionAggregateSensor",
    "EcoGuardDailyCombinedWaterSensor",
    "EcoGuardDailyCostSensor",
    "EcoGuardDailyCostAggregateSensor",
    "EcoGuardDailyCombinedWaterCostSensor",
    # Monthly sensors
    "EcoGuardMonthlyAggregateSensor",
    "EcoGuardMonthlyMeterSensor",
    "EcoGuardCombinedWaterSensor",
    # Special sensors
    "EcoGuardOtherItemsSensor",
    "EcoGuardTotalMonthlyCostSensor",
    "EcoGuardEndOfMonthEstimateSensor",
]
