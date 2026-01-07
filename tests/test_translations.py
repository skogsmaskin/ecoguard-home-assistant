"""Tests for translation functionality."""

from unittest.mock import patch
import pytest
from pathlib import Path

from homeassistant.core import HomeAssistant

from custom_components.ecoguard.sensor import (
    _async_get_translation,
    _load_translation_file,
    _get_translation_default,
)


@pytest.fixture
def mock_translation_data_en():
    """Mock English translation data."""
    return {
        "config": {},
        "common": {
            "utility_hw": "Hot Water",
            "utility_cw": "Cold Water",
            "name_consumption_daily": "Consumption Daily",
            "name_reception_last_update": "Reception Last Update",
            "name_estimated": "Estimated",
            "name_metered": "Metered",
            "name_measuring_point": "Measuring Point {id}",
            "name_device_name": "EcoGuard Node {node_id}",
        },
    }


@pytest.fixture
def mock_translation_data_nb():
    """Mock Norwegian Bokmål translation data."""
    return {
        "config": {},
        "common": {
            "utility_hw": "Varmt vann",
            "utility_cw": "Kaldt vann",
            "name_consumption_daily": "Forbruk Daglig",
            "name_reception_last_update": "Mottak Siste Oppdatering",
            "name_estimated": "Estimert",
            "name_metered": "Avlest",
            "name_measuring_point": "Målepunkt {id}",
            "name_device_name": "EcoGuard Node {node_id}",
        },
    }


async def test_get_translation_default():
    """Test default translation fallback."""
    # Test utility translations
    assert _get_translation_default("utility.hw") == "Hot Water"
    assert _get_translation_default("utility.cw") == "Cold Water"
    
    # Test name translations
    assert _get_translation_default("name.consumption_daily") == "Consumption Daily"
    assert _get_translation_default("name.reception_last_update") == "Reception Last Update"
    assert _get_translation_default("name.estimated") == "Estimated"
    assert _get_translation_default("name.metered") == "Metered"
    
    # Test with formatting
    assert _get_translation_default("name.measuring_point", id=123) == "Measuring Point 123"
    assert _get_translation_default("name.device_name", node_id=456) == "EcoGuard Node 456"
    
    # Test unknown key returns key itself
    assert _get_translation_default("unknown.key") == "unknown.key"


async def test_async_get_translation_english(
    hass: HomeAssistant, mock_translation_data_en
):
    """Test getting English translations."""
    with patch(
        "custom_components.ecoguard.sensor._load_translation_file",
        return_value=mock_translation_data_en,
    ):
        # Set language to English
        hass.config.language = "en"
        
        # Test utility translations
        result = await _async_get_translation(hass, "utility.hw")
        assert result == "Hot Water"
        
        result = await _async_get_translation(hass, "utility.cw")
        assert result == "Cold Water"
        
        # Test name translations
        result = await _async_get_translation(hass, "name.consumption_daily")
        assert result == "Consumption Daily"
        
        result = await _async_get_translation(hass, "name.reception_last_update")
        assert result == "Reception Last Update"
        
        # Test with formatting
        result = await _async_get_translation(hass, "name.measuring_point", id=123)
        assert result == "Measuring Point 123"
        
        result = await _async_get_translation(hass, "name.device_name", node_id=456)
        assert result == "EcoGuard Node 456"


async def test_async_get_translation_norwegian(
    hass: HomeAssistant, mock_translation_data_nb
):
    """Test getting Norwegian translations."""
    def load_translation_side_effect(hass, lang):
        return mock_translation_data_nb if lang == "nb" else None
    
    with patch(
        "custom_components.ecoguard.sensor._load_translation_file",
        side_effect=load_translation_side_effect,
    ):
        # Set language to Norwegian
        hass.config.language = "nb"
        
        # Test utility translations
        result = await _async_get_translation(hass, "utility.hw")
        assert result == "Varmt vann"
        
        result = await _async_get_translation(hass, "utility.cw")
        assert result == "Kaldt vann"
        
        # Test name translations
        result = await _async_get_translation(hass, "name.consumption_daily")
        assert result == "Forbruk Daglig"
        
        result = await _async_get_translation(hass, "name.reception_last_update")
        assert result == "Mottak Siste Oppdatering"


async def test_async_get_translation_fallback_to_english(
    hass: HomeAssistant, mock_translation_data_en
):
    """Test fallback to English when translation is missing."""
    def load_translation_side_effect(hass, lang):
        if lang == "nb":
            # Norwegian file exists but missing some keys
            return {
                "config": {},
                "common": {
                    "utility_hw": "Varmt vann",
                    "utility_cw": "Kaldt vann",
                    # Missing name keys
                },
            }
        elif lang == "en":
            return mock_translation_data_en
        return None
    
    with patch(
        "custom_components.ecoguard.sensor._load_translation_file",
        side_effect=load_translation_side_effect,
    ):
        # Set language to Norwegian
        hass.config.language = "nb"
        
        # This should fallback to English since Norwegian doesn't have name.consumption_daily
        result = await _async_get_translation(hass, "name.consumption_daily")
        assert result == "Consumption Daily"


async def test_async_get_translation_missing_key_fallback(
    hass: HomeAssistant, mock_translation_data_en
):
    """Test fallback to default when key is missing."""
    with patch(
        "custom_components.ecoguard.sensor._load_translation_file",
        return_value=mock_translation_data_en,
    ):
        hass.config.language = "en"
        
        # Test unknown key falls back to default
        result = await _async_get_translation(hass, "name.unknown_key")
        assert result == "name.unknown_key"  # Falls back to key itself


async def test_async_get_translation_missing_file_fallback(hass: HomeAssistant):
    """Test fallback to default when translation file is missing."""
    with patch(
        "custom_components.ecoguard.sensor._load_translation_file",
        return_value=None,
    ):
        hass.config.language = "en"
        
        # Should fall back to default translations
        result = await _async_get_translation(hass, "utility.hw")
        assert result == "Hot Water"
        
        result = await _async_get_translation(hass, "name.consumption_daily")
        assert result == "Consumption Daily"


async def test_async_get_translation_missing_common_key(hass: HomeAssistant):
    """Test fallback when common key is missing from translation file."""
    # Translation file exists but doesn't have common key
    translation_data = {
        "config": {
            "step": {"user": {"title": "Test"}},
        },
        # Missing "common" key
    }
    
    with patch(
        "custom_components.ecoguard.sensor._load_translation_file",
        return_value=translation_data,
    ):
        hass.config.language = "en"
        
        # Should fall back to default translations
        result = await _async_get_translation(hass, "utility.hw")
        assert result == "Hot Water"


async def test_load_translation_file(hass: HomeAssistant):
    """Test loading translation file."""
    # This test verifies the file loading mechanism works
    # We'll mock the file system access
    Path(__file__).parent.parent / "custom_components" / "ecoguard"
    
    # Test loading English (should try strings.json first, then en.json)
    result = await _load_translation_file(hass, "en")
    
    # Should successfully load the actual translation file
    assert result is not None
    assert "config" in result
    assert "common" in result
    # Verify flattened keys exist
    assert "utility_hw" in result["common"]
    assert "utility_cw" in result["common"]
    assert "name_consumption_daily" in result["common"]


async def test_translation_key_structure(hass: HomeAssistant):
    """Test that translation keys follow the expected structure."""
    result = await _load_translation_file(hass, "en")
    
    assert result is not None
    assert "common" in result, "Translation file should have 'common' key"
    
    common_data = result["common"]
    
    # Verify utility translations (flattened keys)
    assert "utility_hw" in common_data
    assert "utility_cw" in common_data
    
    # Verify name translations (flattened keys)
    assert "name_consumption_daily" in common_data
    assert "name_reception_last_update" in common_data
    assert "name_measuring_point" in common_data
    assert "name_device_name" in common_data


async def test_translation_formatting_with_kwargs(hass: HomeAssistant):
    """Test that translation formatting works with kwargs."""
    result = await _async_get_translation(
        hass, "name.measuring_point", id=42
    )
    assert "42" in result
    assert result == "Measuring Point 42"
    
    result = await _async_get_translation(
        hass, "name.device_name", node_id=999
    )
    assert "999" in result
    assert result == "EcoGuard Node 999"
