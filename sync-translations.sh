#!/bin/bash
# Script to sync strings.json to translations/en.json
# This ensures en.json stays in sync with strings.json

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STRINGS_FILE="${SCRIPT_DIR}/custom_components/ecoguard/strings.json"
EN_FILE="${SCRIPT_DIR}/custom_components/ecoguard/translations/en.json"

if [ ! -f "$STRINGS_FILE" ]; then
    echo "Error: strings.json not found at $STRINGS_FILE"
    exit 1
fi

# Copy strings.json to en.json
cp "$STRINGS_FILE" "$EN_FILE"
echo "Synced strings.json to translations/en.json"
