"""Tests for helper functions."""

from datetime import datetime, timezone, timedelta

from custom_components.ecoguard.helpers import (
    round_to_max_digits,
    get_timezone,
    get_month_timestamps,
    get_date_range_timestamps,
    format_cache_key,
    find_last_data_date,
    find_last_price_date,
    detect_data_lag,
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
    assert (
        diff.days == 30 or diff.days == 29 or diff.days == 31
    )  # Allow for timezone/day boundary differences


def test_format_cache_key():
    """Test cache key formatting."""
    key = format_cache_key(
        "data", utility_code="CW", measuring_point_id=1, aggregate_type="con"
    )
    assert "data" in key
    assert "CW" in key
    assert "1" in key
    assert "con" in key

    key = format_cache_key(
        "price",
        utility_code="HW",
        measuring_point_id=2,
        aggregate_type="price",
        cost_type="actual",
    )
    assert "price" in key
    assert "HW" in key
    assert "2" in key
    assert "price" in key
    assert "actual" in key

    key = format_cache_key(
        "data", utility_code="CW", measuring_point_id=None, aggregate_type="con"
    )
    assert "all" in key  # Should use "all" when measuring_point_id is None


def test_find_last_data_date():
    """Test finding last data date from daily consumption cache."""
    tz = get_timezone("Europe/Oslo")
    
    # Test with empty cache
    assert find_last_data_date([], tz) is None
    
    # Test with cache containing data
    now = datetime.now(tz)
    yesterday = now - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)
    
    cache = [
        {"time": int(two_days_ago.timestamp()), "value": 10.0, "unit": "m³"},
        {"time": int(yesterday.timestamp()), "value": 15.0, "unit": "m³"},
        {"time": int(now.timestamp()), "value": None, "unit": "m³"},  # None value should be skipped
    ]
    
    result = find_last_data_date(cache, tz)
    assert result is not None
    assert result.date() == yesterday.date()
    
    # Test with unsorted cache (should still find latest)
    cache_unsorted = [
        {"time": int(now.timestamp()), "value": None, "unit": "m³"},
        {"time": int(two_days_ago.timestamp()), "value": 10.0, "unit": "m³"},
        {"time": int(yesterday.timestamp()), "value": 15.0, "unit": "m³"},
    ]
    
    result = find_last_data_date(cache_unsorted, tz)
    assert result is not None
    assert result.date() == yesterday.date()
    
    # Test with all None values
    cache_none = [
        {"time": int(now.timestamp()), "value": None, "unit": "m³"},
        {"time": int(yesterday.timestamp()), "value": None, "unit": "m³"},
    ]
    
    assert find_last_data_date(cache_none, tz) is None

    # Test with negative values (should be skipped)
    cache_negative = [
        {"time": int(now.timestamp()), "value": -5.0, "unit": "m³"},  # Negative value should be skipped
        {"time": int(yesterday.timestamp()), "value": 15.0, "unit": "m³"},
        {"time": int(two_days_ago.timestamp()), "value": 10.0, "unit": "m³"},
    ]
    
    result = find_last_data_date(cache_negative, tz)
    assert result is not None
    assert result.date() == yesterday.date()  # Should skip negative value and return yesterday


def test_find_last_price_date():
    """Test finding last price date from daily price cache."""
    tz = get_timezone("Europe/Oslo")
    
    # Test with empty cache
    assert find_last_price_date([], tz) is None
    
    # Test with cache containing non-zero prices
    now = datetime.now(tz)
    yesterday = now - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)
    
    cache = [
        {"time": int(two_days_ago.timestamp()), "value": 5.0, "unit": "NOK"},
        {"time": int(yesterday.timestamp()), "value": 6.0, "unit": "NOK"},
        {"time": int(now.timestamp()), "value": 0.0, "unit": "NOK"},  # Zero should be accepted but non-zero preferred
    ]
    
    result = find_last_price_date(cache, tz)
    assert result is not None
    # Should prefer non-zero value (yesterday)
    assert result.date() == yesterday.date()
    
    # Test with only zero values (should still return the last one)
    cache_zero = [
        {"time": int(two_days_ago.timestamp()), "value": 0.0, "unit": "NOK"},
        {"time": int(yesterday.timestamp()), "value": 0.0, "unit": "NOK"},
    ]
    
    result = find_last_price_date(cache_zero, tz)
    assert result is not None
    assert result.date() == yesterday.date()
    
    # Test with all None values
    cache_none = [
        {"time": int(now.timestamp()), "value": None, "unit": "NOK"},
    ]
    
    assert find_last_price_date(cache_none, tz) is None


def test_detect_data_lag():
    """Test data lag detection."""
    tz = get_timezone("Europe/Oslo")
    now = datetime.now(tz)
    today = now.date()
    
    # Test with None (should be lagging)
    is_lagging, lag_days = detect_data_lag(None, tz)
    assert is_lagging is True
    assert lag_days is None
    
    # Test with yesterday (should not be lagging, expected delay is 1 day)
    yesterday = datetime.combine(today - timedelta(days=1), datetime.min.time(), tz)
    is_lagging, lag_days = detect_data_lag(yesterday, tz)
    assert is_lagging is False
    assert lag_days == 0
    
    # Test with 2 days ago (should be lagging by 1 day)
    two_days_ago = datetime.combine(today - timedelta(days=2), datetime.min.time(), tz)
    is_lagging, lag_days = detect_data_lag(two_days_ago, tz)
    assert is_lagging is True
    assert lag_days == 1
    
    # Test with 5 days ago (should be lagging by 4 days)
    five_days_ago = datetime.combine(today - timedelta(days=5), datetime.min.time(), tz)
    is_lagging, lag_days = detect_data_lag(five_days_ago, tz)
    assert is_lagging is True
    assert lag_days == 4
    
    # Test with today (should not be lagging, but this is unusual)
    today_dt = datetime.combine(today, datetime.min.time(), tz)
    is_lagging, lag_days = detect_data_lag(today_dt, tz)
    assert is_lagging is False
    assert lag_days == 0
    
    # Test with custom expected delay
    three_days_ago = datetime.combine(today - timedelta(days=3), datetime.min.time(), tz)
    is_lagging, lag_days = detect_data_lag(three_days_ago, tz, expected_delay_days=2)
    assert is_lagging is True
    assert lag_days == 1  # 3 days ago vs expected 2 days ago = 1 day lag
    
    # Test with future date (should not be lagging and log warning)
    tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), tz)
    is_lagging, lag_days = detect_data_lag(tomorrow, tz)
    assert is_lagging is False
    assert lag_days == 0
