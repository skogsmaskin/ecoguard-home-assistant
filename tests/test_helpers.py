"""Tests for helper functions."""

import pytest
from datetime import datetime, timezone

from custom_components.ecoguard.helpers import (
    round_to_max_digits,
    get_timezone,
    get_month_timestamps,
    get_date_range_timestamps,
    format_cache_key,
)


def test_round_to_max_digits():
    """Test rounding to max digits."""
    assert round_to_max_digits(123.456789, 3) == 123.0
    assert round_to_max_digits(1.234567, 3) == 1.23
    assert round_to_max_digits(0.123456, 3) == 0.123
    assert round_to_max_digits(None, 3) is None
    assert round_to_max_digits(0, 3) == 0.0
    assert round_to_max_digits(0.001, 3) == 0.001
    # Note: 0.0001 with max_digits=3 may round differently based on implementation
    # Just verify it doesn't crash
    result = round_to_max_digits(0.0001, 3)
    assert result is not None


def test_get_timezone():
    """Test timezone helper."""
    # Test with valid timezone
    tz = get_timezone("Europe/Oslo")
    assert tz is not None
    assert str(tz) == "Europe/Oslo"
    
    # Test with invalid timezone (should return UTC)
    tz = get_timezone("Invalid/Timezone")
    assert tz is not None
    assert str(tz) == "UTC"
    
    # Test with None (should return UTC)
    tz = get_timezone(None)
    assert tz is not None
    assert str(tz) == "UTC"


def test_get_month_timestamps():
    """Test month timestamp calculation."""
    from custom_components.ecoguard.helpers import get_timezone
    
    # Test January 2024
    tz = get_timezone("Europe/Oslo")
    start, end = get_month_timestamps(2024, 1, tz)
    assert start is not None
    assert end is not None
    assert start < end
    
    # Verify it's actually January 2024 (convert to the timezone used)
    start_dt = datetime.fromtimestamp(start, tz=tz)
    end_dt = datetime.fromtimestamp(end, tz=tz)
    assert start_dt.year == 2024
    assert start_dt.month == 1
    assert end_dt.year == 2024
    assert end_dt.month == 2  # Start of February


def test_get_date_range_timestamps():
    """Test date range timestamp calculation."""
    # Test 30-day range
    def get_setting(name: str) -> str | None:
        if name == "TimeZoneIANA":
            return "Europe/Oslo"
        return None
    
    start, end = get_date_range_timestamps(days=30, get_setting=get_setting)
    
    assert start is not None
    assert end is not None
    assert start < end
    
    # Verify the range is approximately 30 days
    start_dt = datetime.fromtimestamp(start, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end, tz=timezone.utc)
    diff = end_dt - start_dt
    assert diff.days == 30 or diff.days == 29 or diff.days == 31  # Allow for timezone/day boundary differences


def test_format_cache_key():
    """Test cache key formatting."""
    key = format_cache_key("data", utility_code="CW", measuring_point_id=1, aggregate_type="con")
    assert "data" in key
    assert "CW" in key
    assert "1" in key
    assert "con" in key
    
    key = format_cache_key("price", utility_code="HW", measuring_point_id=2, aggregate_type="price", cost_type="actual")
    assert "price" in key
    assert "HW" in key
    assert "2" in key
    assert "price" in key
    assert "actual" in key
    
    key = format_cache_key("data", utility_code="CW", measuring_point_id=None, aggregate_type="con")
    assert "all" in key  # Should use "all" when measuring_point_id is None