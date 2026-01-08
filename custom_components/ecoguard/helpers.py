"""Helper functions and utilities for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import zoneinfo
import logging

_LOGGER = logging.getLogger(__name__)


def get_timezone(timezone_str: str | None) -> zoneinfo.ZoneInfo:
    """Get timezone ZoneInfo object from string, with fallback to UTC.
    
    Args:
        timezone_str: IANA timezone string (e.g., "Europe/Oslo")
        
    Returns:
        ZoneInfo object for the timezone, or UTC if invalid
    """
    if not timezone_str:
        return zoneinfo.ZoneInfo("UTC")
    
    try:
        return zoneinfo.ZoneInfo(timezone_str)
    except Exception:
        _LOGGER.warning("Invalid timezone %s, using UTC", timezone_str)
        return zoneinfo.ZoneInfo("UTC")


def get_month_timestamps(year: int, month: int, tz: zoneinfo.ZoneInfo) -> tuple[int, int]:
    """Get start and end timestamps for a month.
    
    Args:
        year: Year
        month: Month (1-12)
        tz: Timezone
        
    Returns:
        Tuple of (from_time, to_time) as Unix timestamps
    """
    from_date = datetime(year, month, 1, tzinfo=tz)
    if month == 12:
        to_date = datetime(year + 1, 1, 1, tzinfo=tz)
    else:
        to_date = datetime(year, month + 1, 1, tzinfo=tz)
    
    return (int(from_date.timestamp()), int(to_date.timestamp()))
