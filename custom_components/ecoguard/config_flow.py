"""Config flow for EcoGuard integration."""

from __future__ import annotations

from typing import Any, Optional
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

from .const import NORD_POOL_AREA_CODES
from .const import NORD_POOL_MAP_URL

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

    api = EcoGuardAPI(
        username=data["username"],
        password=data["password"],
        domain=data["domain"],
    )

    try:
        # Try to authenticate
        auth_result = await api.authenticate()
        if not auth_result.get("access_token"):
            raise CannotConnect("Authentication succeeded but no access token received")

        # Get user info to verify connection
        try:
            await api.get_user_info()
        except Exception:
            # User info might not be available, but that's okay if auth worked
            pass

        # Get nodes to find the primary node
        nodes = await api.get_nodes()
        await api.async_close()

        if not nodes:
            raise CannotConnect("No nodes found for this account")

        # Use the first node as the primary node
        primary_node = nodes[0]

        return {
            "title": f"EcoGuard - {primary_node.get('Name', data['domain'])}",
            "node_id": primary_node.get("ID"),
        }
    except EcoGuardAuthenticationError as err:
        raise InvalidAuth(str(err)) from err
    except EcoGuardAPIError as err:
        raise CannotConnect(f"API error: {str(err)}") from err
    except Exception as err:
        raise CannotConnect(f"Unexpected error: {type(err).__name__}: {str(err)}") from err


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EcoGuard."""

    VERSION = 1

    async def async_step_user(
        self, user_input: Optional[dict[str, Any]] = None
    ):
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                description_placeholders={
                    "nord_pool_link": NORD_POOL_MAP_URL
                }
            )

        errors = {}

        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            errors["base"] = "unknown"
        else:
            # Check if already configured
            await self.async_set_unique_id(user_input["domain"])
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=info["title"],
                data={
                    **user_input,
                    "node_id": info["node_id"],
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "nord_pool_link": NORD_POOL_MAP_URL
            }
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""

