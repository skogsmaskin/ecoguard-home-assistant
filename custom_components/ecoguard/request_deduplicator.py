"""Request deduplication helper for coordinator."""

from __future__ import annotations

from typing import Any, Callable, Awaitable
import asyncio
import time
import logging

from homeassistant.core import CoreState

_LOGGER = logging.getLogger(__name__)


class RequestDeduplicator:
    """Helper class for deduplicating and caching async requests."""
    
    def __init__(
        self,
        hass: Any,
        cache_ttl: float = 60.0,
        defer_during_startup: bool = True,
    ) -> None:
        """Initialize the deduplicator.
        
        Args:
            hass: Home Assistant instance
            cache_ttl: Cache TTL in seconds
            defer_during_startup: If True, defer requests during HA startup
        """
        self.hass = hass
        self.cache_ttl = cache_ttl
        self.defer_during_startup = defer_during_startup
        self._cache: dict[str, tuple[Any, float]] = {}
        self._pending_requests: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
    
    async def get_or_fetch(
        self,
        cache_key: str,
        fetch_func: Callable[[], Awaitable[Any]],
        use_cache: bool = True,
    ) -> Any:
        """Get cached data or fetch it with deduplication.
        
        This method:
        1. Checks cache first (if enabled)
        2. Checks for pending requests to avoid duplicate calls
        3. Creates a new fetch task if needed
        4. Caches the result
        
        Args:
            cache_key: Unique key for this request
            fetch_func: Async function to fetch the data
            use_cache: Whether to use cache (default: True)
            
        Returns:
            The fetched data
        """
        # Check cache first
        if use_cache and cache_key in self._cache:
            cached_data, cache_timestamp = self._cache[cache_key]
            age = time.time() - cache_timestamp
            
            if age < self.cache_ttl:
                _LOGGER.debug(
                    "Using cached data for key %s (age: %.1f seconds)",
                    cache_key,
                    age,
                )
                return cached_data
            else:
                _LOGGER.debug(
                    "Cache expired for key %s (age: %.1f seconds, TTL: %.1f seconds)",
                    cache_key,
                    age,
                    self.cache_ttl,
                )
                del self._cache[cache_key]
        
        # Defer during startup if configured
        if self.defer_during_startup and self.hass.state == CoreState.starting:
            _LOGGER.debug(
                "Deferring request for key %s (HA is starting)",
                cache_key
            )
            # Return expired cached data if available, or None
            if cache_key in self._cache:
                cached_data, _ = self._cache[cache_key]
                _LOGGER.debug("Using expired cached data during startup")
                return cached_data
            return None
        
        # Check for pending request
        async with self._lock:
            if cache_key in self._pending_requests:
                pending_task = self._pending_requests[cache_key]
                if not pending_task.done():
                    task_to_await = pending_task
                else:
                    # Task completed, remove it
                    del self._pending_requests[cache_key]
                    task_to_await = None
            else:
                task_to_await = None
        
        # Await outside the lock to avoid deadlock
        if task_to_await is not None:
            _LOGGER.debug(
                "Waiting for pending request for key %s",
                cache_key,
            )
            try:
                return await task_to_await
            except Exception as err:
                _LOGGER.debug(
                    "Pending request failed for key %s: %s",
                    cache_key,
                    err,
                )
                # Remove failed task and continue to fetch
                async with self._lock:
                    if cache_key in self._pending_requests and self._pending_requests[cache_key] is task_to_await:
                        del self._pending_requests[cache_key]
        
        # Create async task for fetching
        async def _fetch_with_cache() -> Any:
            try:
                _LOGGER.debug("Fetching data for key %s", cache_key)
                result = await fetch_func()
                
                # Cache the result
                if result is not None and use_cache:
                    self._cache[cache_key] = (result, time.time())
                    _LOGGER.debug(
                        "Cached data for key %s",
                        cache_key,
                    )
                
                return result
            except Exception as err:
                _LOGGER.warning(
                    "Failed to fetch data for key %s: %s",
                    cache_key,
                    err,
                )
                # Return cached data even if expired, as fallback
                if cache_key in self._cache:
                    cached_data, _ = self._cache[cache_key]
                    _LOGGER.debug("Using expired cached data as fallback")
                    return cached_data
                raise
            finally:
                # Clean up pending request
                async with self._lock:
                    if cache_key in self._pending_requests and self._pending_requests[cache_key] is task:
                        del self._pending_requests[cache_key]
        
        # Create and track the task
        async with self._lock:
            # Final check - did another request create a task while we were waiting?
            if cache_key in self._pending_requests:
                pending_task = self._pending_requests[cache_key]
                if not pending_task.done():
                    # Another task exists, use that one
                    task = pending_task
                else:
                    # Task completed, remove it and create new one
                    del self._pending_requests[cache_key]
                    task = asyncio.create_task(_fetch_with_cache())
                    self._pending_requests[cache_key] = task
            else:
                # No pending task, create and add it
                task = asyncio.create_task(_fetch_with_cache())
                self._pending_requests[cache_key] = task
        
        try:
            return await task
        except Exception as err:
            # Clean up on error
            async with self._lock:
                if cache_key in self._pending_requests and self._pending_requests[cache_key] is task:
                    del self._pending_requests[cache_key]
            raise
        finally:
            # Clean up pending request if still there and it's done
            async with self._lock:
                if cache_key in self._pending_requests and self._pending_requests[cache_key].done():
                    if self._pending_requests[cache_key] is task:
                        del self._pending_requests[cache_key]
    
    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
        _LOGGER.debug("Cleared request cache")
