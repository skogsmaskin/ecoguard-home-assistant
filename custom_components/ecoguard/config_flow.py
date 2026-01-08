"""Config flow for EcoGuard integration."""

from __future__ import annotations

from typing import Any, Optional
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

from .const import NORD_POOL_AREA_CODES
from .const import NORD_POOL_MAP_URL

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("username"): str,
        vol.Required("password"): str,
        vol.Required("domain"): str,
        vol.Optional("nord_pool_area"): vol.In(list(NORD_POOL_AREA_CODES.keys())),
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    # Lazy import to avoid circular dependency
    from .api import EcoGuardAPI, EcoGuardAuthenticationError, EcoGuardAPIError
    from .storage import save_cached_data

    _LOGGER.debug("Starting validation for domain: %s", data["domain"])

    api = EcoGuardAPI(
        username=data["username"],
        password=data["password"],
        domain=data["domain"],
    )

    try:
        # Try to authenticate
        _LOGGER.debug("Authenticating...")
        auth_result = await api.authenticate()
        if not auth_result.get("access_token"):
            await api.async_close()
            raise CannotConnect("Authentication succeeded but no access token received")
        _LOGGER.debug("Authentication successful")

        # Get user info to verify connection
        try:
            await api.get_user_info()
        except Exception:
            # User info might not be available, but that's okay if auth worked
            pass

        # Get nodes to find the primary node
        _LOGGER.debug("Fetching nodes...")
        nodes = await api.get_nodes()

        if not nodes:
            await api.async_close()
            raise CannotConnect("No nodes found for this account")

        # Use the first node as the primary node
        primary_node = nodes[0]
        node_id = primary_node.get("ID")
        _LOGGER.debug("Found primary node with ID: %s", node_id)

        if not node_id:
            await api.async_close()
            raise CannotConnect("Primary node has no ID")

        # Fetch installations, measuring points, and settings before closing API
        # This allows sensors to be created immediately on next HA load
        installations = []
        measuring_points = []
        node_data = None
        settings = []

        try:
            # Get node data (includes measuring points)
            try:
                node_data = await api.get_node(node_id)
                measuring_points = node_data.get("MeasuringPoints", [])
            except Exception:
                # Fallback to separate measuring points endpoint
                try:
                    measuring_points = await api.get_measuring_points(node_id)
                except Exception:
                    pass  # Will be empty list

            # Get installations
            try:
                installations = await api.get_installations(node_id)
            except Exception:
                pass  # Will be empty list

            # Get settings
            try:
                settings = await api.get_settings()
            except Exception:
                pass  # Will be empty list
        finally:
            # Always close the API session before doing any other async operations
            # This ensures the session is properly closed and prevents "unclosed session" errors
            await api.async_close()

        # Save to cache using domain as key (after API is closed)
        # Will be migrated to entry_id in async_setup_entry
        try:
            _LOGGER.debug("Saving data to cache for domain: %s", data["domain"])
            await save_cached_data(
                hass,
                data["domain"],
                installations=installations,
                measuring_points=measuring_points,
                node_data=node_data,
                settings=settings,
            )
            _LOGGER.debug(
                "Cached %d installations, %d measuring points, and %d settings during config flow",
                len(installations),
                len(measuring_points),
                len(settings),
            )
        except Exception as err:
            # Don't fail the config flow if caching fails
            _LOGGER.warning("Failed to cache data during config flow: %s", err)

        _LOGGER.debug("Validation successful, returning info")
        return {
            "title": f"EcoGuard - {primary_node.get('Name', data['domain'])}",
            "node_id": node_id,
        }
    except EcoGuardAuthenticationError as err:
        # Ensure API is closed even on error
        try:
            await api.async_close()
        except Exception:
            pass
        raise InvalidAuth(str(err)) from err
    except EcoGuardAPIError as err:
        # Ensure API is closed even on error
        try:
            await api.async_close()
        except Exception:
            pass
        raise CannotConnect(f"API error: {str(err)}") from err
    except Exception as err:
        # Ensure API is closed even on error
        try:
            await api.async_close()
        except Exception:
            pass
        raise CannotConnect(
            f"Unexpected error: {type(err).__name__}: {str(err)}"
        ) from err


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EcoGuard."""

    VERSION = 1

    async def async_step_user(self, user_input: Optional[dict[str, Any]] = None):
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                description_placeholders={"nord_pool_link": NORD_POOL_MAP_URL},
            )

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect as err:
            _LOGGER.error("Cannot connect during config flow: %s", err)
            errors["base"] = "cannot_connect"
        except InvalidAuth as err:
            _LOGGER.error("Invalid auth during config flow: %s", err)
            errors["base"] = "invalid_auth"
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected error during config flow validation: %s", err)
            errors["base"] = "unknown"
        else:
            # Check if already configured
            await self.async_set_unique_id(user_input["domain"])
            self._abort_if_unique_id_configured()

            _LOGGER.debug(
                "Creating config entry with title: %s, node_id: %s",
                info["title"],
                info["node_id"],
            )
            result = self.async_create_entry(
                title=info["title"],
                data={
                    **user_input,
                    "node_id": info["node_id"],
                },
            )

            # Trigger data fetching after entry is created and setup completes
            # This happens in the background and doesn't block the config flow
            domain = user_input["domain"]  # Use domain as unique_id to find the entry

            async def _trigger_data_fetch_after_setup():
                """Trigger data fetch after entry setup completes."""
                import asyncio

                # Wait a bit for async_setup_entry to complete
                await asyncio.sleep(1.0)
                try:
                    # Find the entry by unique_id (domain)
                    entries = self.hass.config_entries.async_entries(DOMAIN)
                    entry = None
                    for e in entries:
                        if e.unique_id == domain:
                            entry = e
                            break

                    if entry:
                        from . import trigger_data_fetch_for_entry

                        await trigger_data_fetch_for_entry(self.hass, entry.entry_id)
                    else:
                        _LOGGER.warning(
                            "Could not find entry for domain %s after creation", domain
                        )
                except Exception as err:
                    _LOGGER.warning(
                        "Failed to trigger data fetch after config flow: %s",
                        err,
                        exc_info=True,
                    )

            # Schedule data fetch as a background task
            self.hass.async_create_task(_trigger_data_fetch_after_setup())

            return result

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"nord_pool_link": NORD_POOL_MAP_URL},
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
