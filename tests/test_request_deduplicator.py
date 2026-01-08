"""Tests for request deduplication."""

import pytest
import asyncio

from homeassistant.core import HomeAssistant

from custom_components.ecoguard.request_deduplicator import RequestDeduplicator


@pytest.fixture
def deduplicator(hass: HomeAssistant) -> RequestDeduplicator:
    """Create a request deduplicator instance for testing."""
    cache = {}
    pending_requests = {}
    lock = asyncio.Lock()

    return RequestDeduplicator(
        hass=hass,
        cache_ttl=60.0,
        defer_during_startup=False,
        cache=cache,
        pending_requests=pending_requests,
        lock=lock,
    )


async def test_get_or_fetch_single_request(
    deduplicator: RequestDeduplicator, hass: HomeAssistant
):
    """Test that a single request is executed."""
    call_count = 0

    async def mock_fetch():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)  # Simulate async work
        return {"data": "test"}

    result = await deduplicator.get_or_fetch("test_key", mock_fetch)

    assert call_count == 1
    assert result == {"data": "test"}


async def test_get_or_fetch_concurrent_requests(
    deduplicator: RequestDeduplicator, hass: HomeAssistant
):
    """Test that concurrent requests for the same key are deduplicated."""
    call_count = 0

    async def mock_fetch():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.1)  # Simulate async work
        return {"data": "test", "call": call_count}

    # Make 5 concurrent requests with the same key
    results = await asyncio.gather(
        deduplicator.get_or_fetch("test_key", mock_fetch),
        deduplicator.get_or_fetch("test_key", mock_fetch),
        deduplicator.get_or_fetch("test_key", mock_fetch),
        deduplicator.get_or_fetch("test_key", mock_fetch),
        deduplicator.get_or_fetch("test_key", mock_fetch),
    )

    # Should only call once
    assert call_count == 1
    # All results should be the same
    assert all(r == results[0] for r in results)


async def test_get_or_fetch_different_keys(
    deduplicator: RequestDeduplicator, hass: HomeAssistant
):
    """Test that requests with different keys are not deduplicated."""
    call_count = 0

    async def mock_fetch():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return {"data": "test"}

    # Make requests with different keys
    await asyncio.gather(
        deduplicator.get_or_fetch("key1", mock_fetch),
        deduplicator.get_or_fetch("key2", mock_fetch),
        deduplicator.get_or_fetch("key3", mock_fetch),
    )

    # Should call 3 times (once per key)
    assert call_count == 3


async def test_get_or_fetch_cache_hit(
    deduplicator: RequestDeduplicator, hass: HomeAssistant
):
    """Test that cached results are returned without calling fetch."""
    call_count = 0

    async def mock_fetch():
        nonlocal call_count
        call_count += 1
        return {"data": "test"}

    # First call
    result1 = await deduplicator.get_or_fetch("test_key", mock_fetch)
    assert call_count == 1

    # Second call should use cache
    result2 = await deduplicator.get_or_fetch("test_key", mock_fetch)
    assert call_count == 1  # Still 1
    assert result2 == result1
