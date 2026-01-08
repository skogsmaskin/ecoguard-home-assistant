"""Translation utilities for EcoGuard integration."""

from __future__ import annotations

from typing import Any
import logging
import json
import asyncio
from pathlib import Path

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Cache for translation files
_translation_cache: dict[str, dict[str, Any]] = {}

# Track pending translation file loads to prevent duplicate async calls
_pending_translation_loads: dict[str, asyncio.Task] = {}
_translation_load_lock = asyncio.Lock()


def clear_translation_cache() -> None:
    """Clear the translation cache (useful for development/reloads)."""
    global _translation_cache
    _translation_cache.clear()
    _LOGGER.debug("Translation cache cleared")


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


async def load_translation_file(hass: HomeAssistant, lang: str) -> dict[str, Any] | None:
    """Load translation file asynchronously to access sensor section with request deduplication."""
    # Check cache first
    if lang in _translation_cache:
        return _translation_cache[lang]

    # Check if there's already a pending load for this language
    # Acquire lock only to check/update the pending loads dict (never await inside lock)
    task_to_await = None
    async with _translation_load_lock:
        if lang in _pending_translation_loads:
            pending_task = _pending_translation_loads[lang]
            if not pending_task.done():
                # There's a pending task, we'll await it outside the lock
                task_to_await = pending_task
            else:
                # Task completed, remove it
                del _pending_translation_loads[lang]

    # If there's a pending task, wait for it outside the lock
    if task_to_await is not None:
        _LOGGER.debug("Waiting for pending translation file load for lang %s", lang)
        try:
            data = await task_to_await
            return data
        except (asyncio.CancelledError, Exception) as err:
            _LOGGER.debug("Pending translation load failed for lang %s: %s", lang, err)
            # Remove failed/cancelled task
            async with _translation_load_lock:
                if lang in _pending_translation_loads:
                    # Check if it's the same task (might have been replaced)
                    if _pending_translation_loads[lang] is task_to_await:
                        del _pending_translation_loads[lang]
            # Re-raise CancelledError, but for other exceptions, continue to create new task
            if isinstance(err, asyncio.CancelledError):
                raise

    # Create async task for loading
    async def _load_translation_task() -> dict[str, Any] | None:
        try:
            # Run the blocking file I/O in a thread pool
            data = await asyncio.to_thread(_load_translation_file_sync, lang)
            if data:
                _LOGGER.debug("Loaded translation file for lang %s, keys in common: %s", lang, list(data.get("common", {}).keys()))
            return data
        except Exception as e:
            _LOGGER.debug("Failed to load translation file for lang %s: %s", lang, e)
            return None
        finally:
            # Clean up pending load
            async with _translation_load_lock:
                if lang in _pending_translation_loads:
                    # Only remove if it's this task (might have been replaced)
                    if _pending_translation_loads[lang].done():
                        del _pending_translation_loads[lang]

    # Create and track the task (acquire lock only for the dict update)
    async with _translation_load_lock:
        # Double-check in case another task started while we were waiting
        if lang in _pending_translation_loads:
            pending_task = _pending_translation_loads[lang]
            if not pending_task.done():
                # Another task started, use that one instead
                task_to_await = pending_task
            else:
                del _pending_translation_loads[lang]
                task_to_await = None
        else:
            task_to_await = None

        if task_to_await is None:
            # Create new task
            task = asyncio.create_task(_load_translation_task())
            _pending_translation_loads[lang] = task
            task_to_await = task

    # Await the task outside the lock
    try:
        return await task_to_await
    except asyncio.CancelledError:
        # Task was cancelled, clean up
        async with _translation_load_lock:
            if lang in _pending_translation_loads and _pending_translation_loads[lang] is task_to_await:
                del _pending_translation_loads[lang]
        raise
    except Exception as err:
        # Clean up on error
        async with _translation_load_lock:
            if lang in _pending_translation_loads and _pending_translation_loads[lang] is task_to_await and _pending_translation_loads[lang].done():
                del _pending_translation_loads[lang]
        raise


async def async_get_translation(hass: HomeAssistant, key: str, **kwargs: Any) -> str:
    """Get a translated string from the integration's translation files."""
    try:
        # Get the current language from hass.config.language
        lang = getattr(hass.config, 'language', 'en')

        # Load translation file directly to access common section
        # (The translation helper only loads config section)
        translation_data = await load_translation_file(hass, lang)

        if translation_data and "common" in translation_data:
            common_data = translation_data["common"]

            # Convert key from "utility.hw" to "utility_hw" format
            translation_key = key.replace(".", "_")

            if translation_key in common_data:
                text = common_data[translation_key]
                if isinstance(text, str):
                    _LOGGER.debug("Found translation for key %s (as %s): %s (lang=%s)", key, translation_key, text, lang)
                    return text.format(**kwargs) if kwargs else text
            else:
                _LOGGER.debug("Translation key %s (as %s) not found in common section (lang=%s). Available keys: %s",
                             key, translation_key, lang, list(common_data.keys())[:10])

        # Fallback to English
        if lang != "en":
            translation_data = await load_translation_file(hass, "en")
            if translation_data and "common" in translation_data:
                common_data = translation_data["common"]
                translation_key = key.replace(".", "_")

                if translation_key in common_data:
                    text = common_data[translation_key]
                    if isinstance(text, str):
                        return text.format(**kwargs) if kwargs else text
    except Exception as e:
        _LOGGER.warning("Translation lookup failed for key %s (lang=%s): %s", key, getattr(hass.config, 'language', 'en'), e)

    # Fallback to English defaults
    defaults = {
        "utility.hw": "Hot Water",
        "utility.cw": "Cold Water",
        "name.estimated": "Estimated",
        "name.metered": "Metered",
        "name.measuring_point": "Measuring Point {id}",
        "name.meter": "Meter",
        "name.device_name": "EcoGuard Node {node_id}",
        "name.combined_water": "Combined Water",
        "name.consumption_daily": "Consumption Daily",
        "name.cost_daily": "Cost Daily",
        "name.consumption_monthly_aggregated": "Consumption Monthly Aggregated",
        "name.cost_monthly_aggregated": "Cost Monthly Aggregated",
        "name.cost_monthly_other_items": "Cost Monthly Other Items",
        "name.combined": "Combined",
        "name.all_utilities": "All Utilities",
        "name.cost_monthly_estimated_final_settlement": "Cost Monthly Estimated Final Settlement",
        "name.reception_last_update": "Reception Last Update",
    }

    default = defaults.get(key, key)
    if default == key:
        _LOGGER.debug("Translation key %s not found in defaults dictionary, returning key as-is", key)
    return default.format(**kwargs) if kwargs else default


def get_translation_default(key: str, **kwargs: Any) -> str:
    """Get English default translation (for use in __init__ to avoid blocking I/O).

    Actual translations will be loaded in async_added_to_hass.
    """
    defaults = {
        "utility.hw": "Hot Water",
        "utility.cw": "Cold Water",
        "name.estimated": "Estimated",
        "name.metered": "Metered",
        "name.measuring_point": "Measuring Point {id}",
        "name.meter": "Meter",
        "name.device_name": "EcoGuard Node {node_id}",
        "name.combined_water": "Combined Water",
        "name.consumption_daily": "Consumption Daily",
        "name.cost_daily": "Cost Daily",
        "name.consumption_monthly_aggregated": "Consumption Monthly Aggregated",
        "name.cost_monthly_aggregated": "Cost Monthly Aggregated",
        "name.cost_monthly_other_items": "Cost Monthly Other Items",
        "name.combined": "Combined",
        "name.all_utilities": "All Utilities",
        "name.cost_monthly_estimated_final_settlement": "Cost Monthly Estimated Final Settlement",
        "name.reception_last_update": "Reception Last Update",
    }

    default = defaults.get(key, key)
    return default.format(**kwargs) if kwargs else default
