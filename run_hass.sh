#!/bin/bash
# Helper script to run Home Assistant from the dev/ subfolder
# This allows development without copying files - uses symlink to custom_components

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Define the dev directory path
DEV_DIR="$SCRIPT_DIR/dev"

# Check if dev directory exists
if [ ! -d "$DEV_DIR" ]; then
    echo "Error: dev/ directory not found."
    echo "Please run this script from the repository root."
    exit 1
fi

# Change to the dev directory
cd "$DEV_DIR"

# Check if symlink exists
if [ ! -L "$DEV_DIR/custom_components/ecoguard" ]; then
    echo "Creating symlink to custom_components..."
    mkdir -p "$DEV_DIR/custom_components"
    ln -sf ../../custom_components/ecoguard "$DEV_DIR/custom_components/ecoguard"
fi

# Check if virtual environment exists
if [ -d "$SCRIPT_DIR/venv" ]; then
    echo "Activating virtual environment..."
    source "$SCRIPT_DIR/venv/bin/activate"
fi

# Check if Home Assistant is installed
if ! command -v hass &> /dev/null; then
    echo "Error: Home Assistant (hass) not found."
    echo "Please install it with: pip install homeassistant"
    exit 1
fi

# Run Home Assistant with config in dev directory
echo "Starting Home Assistant from dev/ directory..."
echo "Config directory: $DEV_DIR"
echo "Custom components symlinked from: $SCRIPT_DIR/custom_components/ecoguard"
echo ""
hass --config .
