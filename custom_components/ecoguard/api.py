"""API client for EcoGuard integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Any
from urllib.parse import quote
import aiohttp
from aiohttp import ClientSession, ClientError

from .const import (
    API_BASE_URL,
    API_TOKEN_ENDPOINT,
    API_USERS_SELF,
    API_NODES,
    API_MEASURING_POINTS,
    API_DATA,
    API_LATEST_RECEPTION,
    API_BILLING_RESULTS,
    API_INSTALLATIONS,
    API_SETTINGS,
)

_LOGGER = logging.getLogger(__name__)

class EcoGuardAuthenticationError(Exception):
    """Exception raised for authentication errors."""

    pass


class EcoGuardAPIError(Exception):
    """Exception raised for API errors."""

    pass


class EcoGuardAPI:
    """EcoGuard API client."""

    def __init__(
        self,
        username: str,
        password: str,
        domain: str,
        session: Optional[ClientSession] = None,
    ) -> None:
        """Initialize the EcoGuard API client."""
        self._username = username
        self._password = password
        self._domain = domain
        self._session = session
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        self._lock = asyncio.Lock()
        self._request_semaphore = asyncio.Semaphore(3)  # Limit to 3 concurrent requests
        self._last_request_time: Optional[datetime] = None
        self._min_request_interval = timedelta(milliseconds=200)  # Minimum 200ms between requests

    async def _get_session(self) -> ClientSession:
        """Get or create aiohttp session."""
        if self._session is None:
            self._session = ClientSession()
        return self._session

    async def async_close(self) -> None:
        """Close the aiohttp session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def authenticate(self) -> dict[str, Any]:
        """Authenticate with EcoGuard API and get access token."""
        async with self._lock:
            # Check if token is still valid
            if (
                self._access_token
                and self._token_expires_at
                and datetime.now() < self._token_expires_at - timedelta(minutes=5)
            ):
                return {"access_token": self._access_token}

            session = await self._get_session()
            url = f"{API_BASE_URL}{API_TOKEN_ENDPOINT}"

            # API expects form-encoded data, not JSON
            form_data = aiohttp.FormData()
            form_data.add_field("grant_type", "password")
            form_data.add_field("username", self._username)
            form_data.add_field("password", self._password)
            form_data.add_field("domain", self._domain)
            form_data.add_field("issue_refresh_token", "true")

            try:
                async with session.post(
                    url,
                    data=form_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ) as response:
                    if response.status == 200:
                        response_data = await response.json()
                        # Try different possible response field names
                        self._access_token = (
                            response_data.get("Authentication Tenant")
                            or response_data.get("access_token")
                            or response_data.get("AccessToken")
                            or response_data.get("token")
                        )
                        self._refresh_token = (
                            response_data.get("Refresh Token")
                            or response_data.get("refresh_token")
                            or response_data.get("RefreshToken")
                        )

                        if not self._access_token:
                            # Log the actual response for debugging
                            raise EcoGuardAPIError(
                                f"Authentication response missing token. Response: {response_data}"
                            )

                        # Parse token expiration from JWT if available
                        # Tokens typically expire in 12 days based on examples
                        if self._access_token:
                            # Set expiration to 12 days from now as default
                            self._token_expires_at = datetime.now() + timedelta(
                                days=12
                            )

                        return {
                            "access_token": self._access_token,
                            "refresh_token": self._refresh_token,
                        }
                    elif response.status == 401:
                        error_text = await response.text()
                        raise EcoGuardAuthenticationError(
                            f"Invalid credentials. Please check your username, password, and domain. Response: {error_text}"
                        )
                    else:
                        error_text = await response.text()
                        raise EcoGuardAPIError(
                            f"Authentication failed with status {response.status}: {error_text}"
                        )
            except ClientError as err:
                raise EcoGuardAPIError(f"Network error during authentication: {err}")

    async def refresh_token(self) -> dict[str, Any]:
        """Refresh the access token using refresh token."""
        if not self._refresh_token:
            # If no refresh token, re-authenticate
            return await self.authenticate()

        async with self._lock:
            session = await self._get_session()
            url = f"{API_BASE_URL}{API_TOKEN_ENDPOINT}"

            # API expects form-encoded data
            form_data = aiohttp.FormData()
            form_data.add_field("grant_type", "refresh_token")
            form_data.add_field("refresh_token", self._refresh_token)

            try:
                async with session.post(
                    url,
                    data=form_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        self._access_token = data.get("Refresh Token")
                        # Update expiration
                        self._token_expires_at = datetime.now() + timedelta(days=12)
                        return {"access_token": self._access_token}
                    else:
                        # Refresh failed, try full authentication
                        return await self.authenticate()
            except ClientError:
                # Network error, try full authentication
                return await self.authenticate()

    async def _get_token(self) -> str:
        """Get a valid access token, refreshing if necessary."""
        if (
            not self._access_token
            or not self._token_expires_at
            or datetime.now() >= self._token_expires_at - timedelta(minutes=5)
        ):
            if self._refresh_token:
                await self.refresh_token()
            else:
                await self.authenticate()

        if not self._access_token:
            raise EcoGuardAuthenticationError("Failed to obtain access token")

        return self._access_token

    async def _request(
        self, method: str, endpoint: str, **kwargs: Any
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Make an authenticated API request with rate limiting and retry logic."""
        # Rate limiting: ensure minimum interval between requests
        if self._last_request_time:
            time_since_last = datetime.now() - self._last_request_time
            if time_since_last < self._min_request_interval:
                wait_time = (self._min_request_interval - time_since_last).total_seconds()
                await asyncio.sleep(wait_time)

        # Use semaphore to limit concurrent requests
        async with self._request_semaphore:
            max_retries = 3
            base_delay = 1.0  # Start with 1 second delay

            for attempt in range(max_retries):
                try:
                    token = await self._get_token()
                    session = await self._get_session()
                    url = f"{API_BASE_URL}{endpoint}"

                    headers = kwargs.pop("headers", {})
                    headers["Authorization"] = f"Bearer {token}"
                    headers["accept"] = "*/*"
                    headers["content-type"] = "application/json"

                    self._last_request_time = datetime.now()

                    async with session.request(method, url, headers=headers, **kwargs) as response:
                        if response.status == 401:
                            # Token expired, try to refresh
                            await self.refresh_token()
                            token = await self._get_token()
                            headers["Authorization"] = f"Bearer {token}"
                            async with session.request(
                                method, url, headers=headers, **kwargs
                            ) as retry_response:
                                if retry_response.status != 200:
                                    error_text = await retry_response.text()
                                    raise EcoGuardAPIError(
                                        f"API request failed with status {retry_response.status}: {error_text}"
                                    )
                                return await retry_response.json()

                        if response.status == 429:
                            # Rate limited - retry with exponential backoff
                            if attempt < max_retries - 1:
                                delay = base_delay * (2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
                                error_text = await response.text()
                                _LOGGER.warning(
                                    "Rate limited (429) on attempt %d/%d for %s %s. Retrying in %.1f seconds...",
                                    attempt + 1,
                                    max_retries,
                                    method,
                                    endpoint,
                                    delay,
                                )
                                await asyncio.sleep(delay)
                                continue
                            else:
                                error_text = await response.text()
                                raise EcoGuardAPIError(
                                    f"API request failed with status 429 (Too Many Requests) after {max_retries} attempts: {error_text}"
                                )

                        if response.status != 200:
                            error_text = await response.text()
                            raise EcoGuardAPIError(
                                f"API request failed with status {response.status}: {error_text}"
                            )

                        return await response.json()
                except ClientError as err:
                    # Network errors - retry if we have attempts left
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        _LOGGER.warning(
                            "Network error on attempt %d/%d for %s %s: %s. Retrying in %.1f seconds...",
                            attempt + 1,
                            max_retries,
                            method,
                            endpoint,
                            err,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise EcoGuardAPIError(f"Network error during API request after {max_retries} attempts: {err}")

            # If we get here, all retries failed
            raise EcoGuardAPIError(f"API request failed after {max_retries} attempts")

    async def get_user_info(self) -> dict[str, Any]:
        """Get current user information."""
        return await self._request("GET", API_USERS_SELF)

    async def get_nodes(
        self, node_id: Optional[int] = None, include_sub_nodes: bool = False
    ) -> list[dict[str, Any]]:
        """Get nodes information."""
        endpoint = API_NODES.format(domaincode=self._domain)
        params = {}
        if node_id:
            params["nodeid"] = str(node_id)
        if include_sub_nodes:
            params["includesubnodes"] = "true"
        if params:
            endpoint += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return await self._request("GET", endpoint)

    async def get_node(self, node_id: int) -> dict[str, Any]:
        """Get a single node by ID (includes MeasuringPoints in response)."""
        endpoint = f"/api/{self._domain}/nodes/{node_id}"
        return await self._request("GET", endpoint)

    async def get_measuring_points(
        self, node_id: int, include_sub_nodes: bool = False
    ) -> list[dict[str, Any]]:
        """Get measuring points for a node."""
        endpoint = API_MEASURING_POINTS.format(domaincode=self._domain)
        params = {"nodeid": str(node_id)}
        if include_sub_nodes:
            params["includesubnodes"] = "true"
        if params:
            endpoint += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return await self._request("GET", endpoint)

    async def get_data(
        self,
        node_id: int,
        from_time: int,
        to_time: int,
        interval: str = "d",
        grouping: str = "apartment",
        utilities: Optional[list[str]] = None,
        include_sub_nodes: bool = True,
        measuring_point_id: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Get consumption or price data for a node.

        Args:
            node_id: Node ID
            from_time: Start timestamp (Unix timestamp)
            to_time: End timestamp (Unix timestamp)
            interval: Data interval (e.g., "d" for daily)
            grouping: Grouping type (e.g., "apartment")
            utilities: List of utility codes (e.g., ["HW[con]", "CW[price]"])
            include_sub_nodes: Whether to include sub-nodes
            measuring_point_id: Optional measuring point ID to filter by specific meter
        """
        endpoint = API_DATA.format(domaincode=self._domain)
        params = {
            "from": str(from_time),
            "to": str(to_time),
            "interval": interval,
            "grouping": grouping,
        }

        # When measuringpointid is specified, don't include nodeID or includeSubNodes
        # The API doesn't allow multiple selection parameters together
        if measuring_point_id is not None:
            params["measuringpointid"] = str(measuring_point_id)
        else:
            params["nodeID"] = str(node_id)  # Note: webapp uses nodeID (capital ID)
            if include_sub_nodes:
                params["includeSubNodes"] = "true"  # Note: webapp uses camelCase

        # Build query string with proper URL encoding for utilities
        query_parts = []
        for key, value in params.items():
            query_parts.append(f"{key}={value}")

        # Add utilities with proper URL encoding (e.g., HW[con] -> HW%5Bcon%5D)
        if utilities:
            for util in utilities:
                query_parts.append(f"utl={quote(util, safe='')}")

        endpoint += "?" + "&".join(query_parts)
        return await self._request("GET", endpoint)

    async def get_latest_reception(
        self, node_id: int, include_sub_nodes: bool = True
    ) -> list[dict[str, Any]]:
        """Get latest reception timestamps for a node."""
        endpoint = API_LATEST_RECEPTION.format(domaincode=self._domain)
        params = {"nodeid": str(node_id)}
        if include_sub_nodes:
            params["includesubnodes"] = "true"
        if params:
            endpoint += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return await self._request("GET", endpoint)

    async def get_billing_results(
        self,
        node_id: int,
        start_from: Optional[int] = None,
        start_to: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Get billing results for a node.

        Args:
            node_id: Node ID
            start_from: Optional start timestamp (Unix timestamp)
            start_to: Optional end timestamp (Unix timestamp)
        """
        endpoint = API_BILLING_RESULTS.format(domaincode=self._domain)
        params = {"nodeID": str(node_id)}
        if start_from is not None:
            params["startFrom"] = str(start_from)
        if start_to is not None:
            params["startTo"] = str(start_to)
        if params:
            endpoint += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return await self._request("GET", endpoint)

    async def get_installations(
        self, node_id: int, include_latest_reception_alarm: bool = False
    ) -> list[dict[str, Any]]:
        """Get installations for a node."""
        endpoint = API_INSTALLATIONS.format(domaincode=self._domain)
        params = {"nodeID": str(node_id)}  # Note: webapp uses nodeID (capital ID)
        # Only add includeLatestReceptionAlarm if explicitly requested
        # The webapp doesn't include this parameter by default
        if include_latest_reception_alarm:
            params["includeLatestReceptionAlarm"] = "true"
        if params:
            endpoint += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return await self._request("GET", endpoint)

    async def get_settings(self) -> list[dict[str, Any]]:
        """Get domain settings."""
        endpoint = API_SETTINGS.format(domaincode=self._domain)
        return await self._request("GET", endpoint)

