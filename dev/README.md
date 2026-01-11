# Development Environment

This directory contains the Home Assistant development environment for testing the EcoGuard integration.

## Structure

- `configuration.yaml` - Minimal Home Assistant configuration
- `custom_components/ecoguard/` - Symlink to `../../custom_components/ecoguard`
- `.homeassistant/` - Home Assistant runtime data (created automatically, gitignored)

## Usage

Run Home Assistant from the repository root:

```bash
hass --config dev
```

Or use the helper script:

```bash
./run_hass.sh
```

## Symlink

The `custom_components/ecoguard` directory is a symlink to the actual integration code in the repository root. This means:

- ✅ Edit code directly in `custom_components/ecoguard/` (repo root)
- ✅ Changes are immediately available (just restart Home Assistant)
- ✅ No file copying needed
- ✅ Keeps the repo root clean

If the symlink is missing, it will be automatically created when you run `./run_hass.sh`.
