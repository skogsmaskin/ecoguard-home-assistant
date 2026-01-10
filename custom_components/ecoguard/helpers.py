"""Helper functions and utilities for EcoGuard integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable
import zoneinfo
import logging
import math

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


def get_month_timestamps(
    year: int, month: int, tz: zoneinfo.ZoneInfo
) -> tuple[int, int]:
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


def get_date_range_timestamps(
    days: int,
    get_setting: Callable[[str], str | None],
) -> tuple[int, int]:
    """Get timestamps for a date range looking back N days from today.

    Args:
        days: Number of days to look back
        get_setting: Function to get setting value (for timezone)

    Returns:
        Tuple of (from_time, to_time) as Unix timestamps
    """
    timezone_str = get_setting("TimeZoneIANA") if get_setting else None
    if not timezone_str:
        timezone_str = "UTC"

    tz = get_timezone(timezone_str)
    now_tz = datetime.now(tz)

    # Align to start of tomorrow in the timezone (to include all of today)
    tomorrow_start = datetime.combine(
        (now_tz.date() + timedelta(days=1)), datetime.min.time(), tz
    )
    to_time = int(tomorrow_start.timestamp())

    # Calculate from_time as start of day N days ago
    from_date = now_tz.date() - timedelta(days=days)
    from_start = datetime.combine(from_date, datetime.min.time(), tz)
    from_time = int(from_start.timestamp())

    return (from_time, to_time)


def format_cache_key(
    prefix: str,
    utility_code: str | None = None,
    measuring_point_id: int | None = None,
    from_time: int | None = None,
    to_time: int | None = None,
    aggregate_type: str | None = None,
    cost_type: str | None = None,
    year: int | None = None,
    month: int | None = None,
    **kwargs: Any,
) -> str:
    """Generate a standardized cache key from components.

    Args:
        prefix: Key prefix (e.g., "price", "data", "consumption")
        utility_code: Optional utility code
        measuring_point_id: Optional measuring point ID
        from_time: Optional start timestamp
        to_time: Optional end timestamp
        aggregate_type: Optional aggregate type (e.g., "con", "price")
        cost_type: Optional cost type (e.g., "actual", "estimated")
        year: Optional year
        month: Optional month
        **kwargs: Additional key components

    Returns:
        Formatted cache key string
    """
    parts = [prefix]

    if utility_code:
        parts.append(utility_code)

    if measuring_point_id is not None:
        parts.append(str(measuring_point_id))
    elif measuring_point_id is None and utility_code:
        parts.append("all")

    if from_time is not None:
        parts.append(str(from_time))
    if to_time is not None:
        parts.append(str(to_time))

    if aggregate_type:
        parts.append(aggregate_type)
    if cost_type:
        parts.append(cost_type)

    if year is not None:
        parts.append(str(year))
    if month is not None:
        parts.append(f"{month:02d}")

    # Add any additional kwargs
    for key, value in sorted(kwargs.items()):
        if value is not None:
            parts.append(f"{key}_{value}")

    return "_".join(parts)


def log_static_info_summary(
    node_data: dict[str, Any] | None,
    settings: list[dict[str, Any]],
    installations: list[dict[str, Any]],
    measuring_points: list[dict[str, Any]],
    latest_reception: list[dict[str, Any]],
    node_id: int,
    domain: str,
) -> None:
    """Log a summary of all static information for debugging.

    Args:
        node_data: Node data dictionary
        settings: List of settings
        installations: List of installations
        measuring_points: List of measuring points
        latest_reception: List of latest reception data
        node_id: Node ID
        domain: Domain name
    """
    if not node_data and not settings:
        return

    _LOGGER.debug("=" * 80)
    _LOGGER.debug("ECOGUARD STATIC DATA SUMMARY")
    _LOGGER.debug("=" * 80)

    # Node Information
    if node_data:
        _LOGGER.debug("NODE DATA:")
        _LOGGER.debug("  Node ID: %s", node_id)
        _LOGGER.debug("  Domain: %s", domain)

        # Properties
        properties = node_data.get("Properties", [])
        if properties:
            _LOGGER.debug("  Properties:")
            for prop in properties:
                _LOGGER.debug(
                    "    - %s: %s",
                    prop.get("Name", "Unknown"),
                    prop.get("Value", "N/A"),
                )

        # Measuring Points
        measuring_points_data = node_data.get("MeasuringPoints", [])
        if measuring_points_data:
            _LOGGER.debug("  Measuring Points (%d):", len(measuring_points_data))
            for mp in measuring_points_data:
                _LOGGER.debug("    - ID: %s, Name: %s", mp.get("ID"), mp.get("Name"))

        # SubNodes
        sub_nodes = node_data.get("SubNodes", [])
        if sub_nodes:
            _LOGGER.debug("  SubNodes (%d):", len(sub_nodes))
            for sub in sub_nodes:
                _LOGGER.debug("    - ID: %s, Name: %s", sub.get("ID"), sub.get("Name"))

        # Rental Contracts
        contracts = node_data.get("RentalContracts", [])
        if contracts:
            _LOGGER.debug("  Rental Contracts (%d):", len(contracts))
            for contract in contracts:
                contract_date = contract.get("Date")
                if contract_date:
                    date_str = datetime.fromtimestamp(contract_date).strftime(
                        "%Y-%m-%d"
                    )
                else:
                    date_str = "N/A"
                _LOGGER.debug(
                    "    - ID: %s, Date: %s, Code: %s",
                    contract.get("ID"),
                    date_str,
                    contract.get("ContractCode"),
                )
    else:
        _LOGGER.debug("NODE DATA: Not available")

    # Settings
    if settings:
        _LOGGER.debug("SETTINGS (%d):", len(settings))
        for setting in settings:
            _LOGGER.debug("  - %s: %s", setting.get("Name"), setting.get("Value"))
    else:
        _LOGGER.debug("SETTINGS: Not available")

    # Installations
    if installations:
        _LOGGER.debug("INSTALLATIONS (%d):", len(installations))
        for inst in installations:
            mp_id = inst.get("MeasuringPointID")
            device_type = inst.get("DeviceTypeDisplay", "Unknown")
            external_key = inst.get("ExternalKey", "N/A")

            # Installation lifespan
            from_date = inst.get("From")
            to_date = inst.get("To")
            if from_date:
                from_str = datetime.fromtimestamp(from_date).strftime("%Y-%m-%d")
            else:
                from_str = "N/A"
            if to_date:
                to_str = datetime.fromtimestamp(to_date).strftime("%Y-%m-%d")
                status = "Ended"
            else:
                to_str = "Active"
                status = "Active"

            _LOGGER.debug(
                "  - MeasuringPointID: %s, DeviceType: %s, ExternalKey: %s",
                mp_id,
                device_type,
                external_key,
            )
            _LOGGER.debug("    Status: %s, From: %s, To: %s", status, from_str, to_str)

            # Registers (utility codes)
            registers = inst.get("Registers", [])
            if registers:
                _LOGGER.debug("    Registers:")
                for reg in registers:
                    util_code = reg.get("UtilityCode", "Unknown")
                    _LOGGER.debug("      - UtilityCode: %s", util_code)
    else:
        _LOGGER.debug("INSTALLATIONS: Not available")

    # Measuring Points (from cache)
    if measuring_points:
        _LOGGER.debug("MEASURING POINTS CACHE (%d):", len(measuring_points))
        for mp in measuring_points:
            _LOGGER.debug("  - ID: %s, Name: %s", mp.get("ID"), mp.get("Name"))

    # Latest Reception
    if latest_reception:
        _LOGGER.debug("LATEST RECEPTION (%d):", len(latest_reception))
        for reception in latest_reception:
            pos_id = reception.get("PositionID")
            latest = reception.get("LatestReception")
            if latest:
                date_str = datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M:%S")
            else:
                date_str = "N/A"
            _LOGGER.debug("  - PositionID: %s, LatestReception: %s", pos_id, date_str)
    else:
        _LOGGER.debug("LATEST RECEPTION: Not available")

    _LOGGER.debug("=" * 80)


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


def find_last_data_date(
    daily_cache: list[dict[str, Any]],
    tz: zoneinfo.ZoneInfo | None = None,
) -> datetime | None:
    """Find the actual last date with data from daily consumption cache.

    This finds the most recent date where the API returned actual consumption data
    (non-None value). This represents the actual last date we successfully retrieved
    data from the API, not just the latest timestamp in the cache.

    Args:
        daily_cache: List of daily consumption entries with 'time' and 'value' keys
            (populated from API responses in data_processor)
        tz: Optional timezone for date conversion (defaults to UTC)

    Returns:
        Datetime of the last entry with actual data from the API, or None if no data found
    """
    if not daily_cache:
        return None

    if tz is None:
        tz = zoneinfo.ZoneInfo("UTC")

    # Sort by time (descending) to find the latest entry with actual data
    sorted_cache = sorted(
        daily_cache,
        key=lambda x: x.get("time", 0),
        reverse=True,
    )

    # Find the last entry with a non-None, non-negative value
    for entry in sorted_cache:
        value = entry.get("value")
        time_stamp = entry.get("time")
        if value is not None and time_stamp is not None and value >= 0:
            return datetime.fromtimestamp(time_stamp, tz=tz)

    return None


def find_last_price_date(
    daily_cache: list[dict[str, Any]],
    tz: zoneinfo.ZoneInfo | None = None,
) -> datetime | None:
    """Find the actual last date with price data from daily price cache.

    This finds the most recent date where the API returned actual price data
    (non-None value), preferring non-zero values but accepting zero as a fallback
    since 0 values might indicate missing data for some utilities (like HW).

    Args:
        daily_cache: List of daily price entries with 'time' and 'value' keys
            (populated from API responses in data_processor)
        tz: Optional timezone for date conversion (defaults to UTC)

    Returns:
        Datetime of the last entry with actual price data from the API, or None if no data found
    """
    if not daily_cache:
        return None

    if tz is None:
        tz = zoneinfo.ZoneInfo("UTC")

    # Sort by time (descending) to find the latest entry with actual data
    sorted_cache = sorted(
        daily_cache,
        key=lambda x: x.get("time", 0),
        reverse=True,
    )

    # Single pass: prefer the most recent non-zero value,
    # but remember the most recent zero value as a fallback.
    zero_timestamp: int | None = None

    for entry in sorted_cache:
        value = entry.get("value")
        time_stamp = entry.get("time")
        if value is None or time_stamp is None or value < 0:
            continue

        if value > 0:
            # First non-zero value in descending order is the most recent
            return datetime.fromtimestamp(time_stamp, tz=tz)

        # Value is 0: remember the first (most recent) zero as fallback
        if zero_timestamp is None:
            zero_timestamp = time_stamp

    if zero_timestamp is not None:
        return datetime.fromtimestamp(zero_timestamp, tz=tz)

    return None


def detect_data_lag(
    last_data_date: datetime | None,
    tz: zoneinfo.ZoneInfo,
    expected_delay_days: int = 1,
) -> tuple[bool, int | None]:
    """Detect if data is lagging behind expected date.

    Args:
        last_data_date: The last date with actual data
        tz: Timezone for date calculations
        expected_delay_days: Expected delay in days (default: 1, since data is typically delayed by 1 day)

    Returns:
        Tuple of (is_lagging: bool, lag_days: int | None)
    """
    if last_data_date is None:
        return (True, None)

    now_tz = datetime.now(tz)
    today = now_tz.date()

    # Expected last data date is yesterday (or expected_delay_days ago)
    expected_date = today - timedelta(days=expected_delay_days)
    last_data_date_only = last_data_date.date()

    if last_data_date_only > today:
        # Data appears to be from the future relative to the current date.
        # This is treated as "not lagging" but logged as a potential anomaly.
        _LOGGER.warning(
            "Last data date %s is in the future relative to timezone %s; "
            "treating as not lagging",
            last_data_date_only,
            tz,
        )
        return (False, 0)

    if last_data_date_only < expected_date:
        lag_days = (expected_date - last_data_date_only).days
        return (True, lag_days)

    return (False, 0)
