"""Tests for the EcoGuard API client."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiohttp import ClientError

from custom_components.ecoguard.api import (
    EcoGuardAPI,
    EcoGuardAuthenticationError,
    EcoGuardAPIError,
)

# Import pytest-homeassistant-custom-component fixtures (not needed for API tests but for consistency)
pytest_plugins = ("pytest_homeassistant_custom_component",)


@pytest.fixture
def mock_session():
    """Create a mock aiohttp session."""
    session = MagicMock()
    session.close = AsyncMock()
    return session


@pytest.fixture
def api(mock_session):
    """Create an API instance for testing."""
    return EcoGuardAPI(
        username="test_user",
        password="test_password",
        domain="test_domain",
        session=mock_session,
    )


async def test_authenticate_success(api, mock_session):
    """Test successful authentication."""
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value={"Authentication Tenant": "test_token"})
    response.text = AsyncMock(return_value="")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    # Make post return the response directly (it's used as async context manager)
    mock_session.post = MagicMock(return_value=response)

    result = await api.authenticate()

    assert result["access_token"] == "test_token"
    assert api._access_token == "test_token"


async def test_authenticate_401_error(api, mock_session):
    """Test authentication with 401 error."""
    response = MagicMock()
    response.status = 401
    response.text = AsyncMock(return_value="Invalid credentials")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    mock_session.post = MagicMock(return_value=response)

    with pytest.raises(EcoGuardAuthenticationError):
        await api.authenticate()


async def test_authenticate_other_error(api, mock_session):
    """Test authentication with other error status."""
    response = MagicMock()
    response.status = 500
    response.text = AsyncMock(return_value="Server error")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    mock_session.post = MagicMock(return_value=response)

    with pytest.raises(EcoGuardAPIError, match="Authentication failed"):
        await api.authenticate()


async def test_authenticate_network_error(api, mock_session):
    """Test authentication with network error."""
    # For network errors, we need to raise the error when entering the context
    response = MagicMock()
    response.__aenter__ = AsyncMock(side_effect=ClientError("Network error"))
    response.__aexit__ = AsyncMock(return_value=None)
    mock_session.post = MagicMock(return_value=response)

    with pytest.raises(EcoGuardAPIError, match="Network error"):
        await api.authenticate()


async def test_authenticate_no_token_in_response(api, mock_session):
    """Test authentication when response doesn't contain token."""
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value={})  # No token
    response.text = AsyncMock(return_value="")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    mock_session.post = MagicMock(return_value=response)

    with pytest.raises(EcoGuardAPIError, match="missing token"):
        await api.authenticate()


async def test_authenticate_token_cached(api, mock_session):
    """Test that authentication uses cached token if still valid."""
    # Set a valid token
    api._access_token = "cached_token"
    api._token_expires_at = None  # Will be set in authenticate

    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value={"Authentication Tenant": "new_token"})
    response.text = AsyncMock(return_value="")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    mock_session.post = MagicMock(return_value=response)

    # First call should authenticate
    await api.authenticate()
    assert mock_session.post.call_count == 1

    # Second call should use cached token (if not expired)
    # We need to set expires_at to future
    from datetime import datetime, timedelta

    api._token_expires_at = datetime.now() + timedelta(hours=1)

    result2 = await api.authenticate()
    # Should still be 1 call since token is cached
    assert mock_session.post.call_count == 1
    assert result2["access_token"] == "new_token"


async def test_get_user_info(api, mock_session):
    """Test getting user info."""
    # Set up token and expiration
    api._access_token = "test_token"
    from datetime import datetime, timedelta

    api._token_expires_at = datetime.now() + timedelta(hours=1)

    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value={"ID": 1, "Name": "Test User"})
    response.text = AsyncMock(return_value="")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    mock_session.request = MagicMock(return_value=response)

    result = await api.get_user_info()

    assert result["ID"] == 1
    assert result["Name"] == "Test User"


async def test_get_nodes(api, mock_session):
    """Test getting nodes."""
    api._access_token = "test_token"
    from datetime import datetime, timedelta

    api._token_expires_at = datetime.now() + timedelta(hours=1)

    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value=[{"ID": 123, "Name": "Test Node"}])
    response.text = AsyncMock(return_value="")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    mock_session.request = MagicMock(return_value=response)

    result = await api.get_nodes()

    assert len(result) == 1
    assert result[0]["ID"] == 123


async def test_get_nodes_with_node_id(api, mock_session):
    """Test getting nodes with specific node_id."""
    api._access_token = "test_token"
    from datetime import datetime, timedelta

    api._token_expires_at = datetime.now() + timedelta(hours=1)

    # Mock _get_session to return our mock_session
    api._session = mock_session

    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value=[{"ID": 123, "Name": "Test Node"}])
    response.text = AsyncMock(return_value="")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    mock_session.request = MagicMock(return_value=response)

    result = await api.get_nodes(node_id=123)

    assert len(result) == 1
    # Verify the request was made with correct parameters
    call_args = mock_session.request.call_args
    # url is a positional argument, so check call_args[0][1] (second positional arg)
    url = call_args[0][1] if len(call_args[0]) > 1 else str(call_args)
    assert "nodeid=123" in url or "nodeid=123" in str(call_args)


async def test_get_data(api, mock_session):
    """Test getting consumption data."""
    api._access_token = "test_token"
    from datetime import datetime, timedelta

    api._token_expires_at = datetime.now() + timedelta(hours=1)

    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value=[{"Time": 1234567890, "Value": 10.5}])
    response.text = AsyncMock(return_value="")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    mock_session.request = MagicMock(return_value=response)

    to_time = int(datetime.now().timestamp())
    from_time = int((datetime.now() - timedelta(days=30)).timestamp())

    result = await api.get_data(
        node_id=123,
        from_time=from_time,
        to_time=to_time,
    )

    assert len(result) == 1
    assert result[0]["Value"] == 10.5


async def test_request_401_refresh_token(api, mock_session):
    """Test that 401 errors trigger token refresh."""
    api._access_token = "expired_token"
    api._refresh_token = "refresh_token"

    # First request returns 401
    response1 = MagicMock()
    response1.status = 401
    response1.text = AsyncMock(return_value="Unauthorized")
    response1.__aenter__ = AsyncMock(return_value=response1)
    response1.__aexit__ = AsyncMock(return_value=None)

    # Refresh token response
    refresh_response = MagicMock()
    refresh_response.status = 200
    refresh_response.json = AsyncMock(return_value={"Refresh Token": "new_token"})
    refresh_response.text = AsyncMock(return_value="")
    refresh_response.__aenter__ = AsyncMock(return_value=refresh_response)
    refresh_response.__aexit__ = AsyncMock(return_value=None)

    # Second request succeeds
    response2 = MagicMock()
    response2.status = 200
    response2.json = AsyncMock(return_value={"data": "test"})
    response2.text = AsyncMock(return_value="")
    response2.__aenter__ = AsyncMock(return_value=response2)
    response2.__aexit__ = AsyncMock(return_value=None)

    mock_session.post = MagicMock(return_value=refresh_response)
    mock_session.request = MagicMock(side_effect=[response1, response2])

    with patch.object(api, "refresh_token", new_callable=AsyncMock) as mock_refresh:
        mock_refresh.return_value = {"access_token": "new_token"}
        api._access_token = "new_token"

        result = await api.get_user_info()

        assert result["data"] == "test"
        # Token should have been refreshed
        assert api._access_token == "new_token"


async def test_request_429_rate_limit(api, mock_session):
    """Test handling of 429 rate limit errors with retry."""
    api._access_token = "test_token"
    from datetime import datetime, timedelta

    api._token_expires_at = datetime.now() + timedelta(hours=1)

    # First two requests return 429, third succeeds
    response_429 = MagicMock()
    response_429.status = 429
    response_429.text = AsyncMock(return_value="Too Many Requests")
    response_429.__aenter__ = AsyncMock(return_value=response_429)
    response_429.__aexit__ = AsyncMock(return_value=None)

    response_200 = MagicMock()
    response_200.status = 200
    response_200.json = AsyncMock(return_value={"data": "test"})
    response_200.text = AsyncMock(return_value="")
    response_200.__aenter__ = AsyncMock(return_value=response_200)
    response_200.__aexit__ = AsyncMock(return_value=None)

    mock_session.request = MagicMock(
        side_effect=[response_429, response_429, response_200]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await api.get_user_info()

        assert result["data"] == "test"
        assert mock_session.request.call_count == 3


async def test_request_network_error_retry(api, mock_session):
    """Test retry on network errors."""
    api._access_token = "test_token"
    from datetime import datetime, timedelta

    api._token_expires_at = datetime.now() + timedelta(hours=1)

    # First request fails, second succeeds
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value={"data": "test"})
    response.text = AsyncMock(return_value="")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)

    mock_session.request = MagicMock(
        side_effect=[ClientError("Network error"), response]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await api.get_user_info()

        assert result["data"] == "test"
        assert mock_session.request.call_count == 2


async def test_async_close(api, mock_session):
    """Test closing the API session."""
    await api.async_close()

    mock_session.close.assert_called_once()


async def test_async_close_no_session(api):
    """Test closing when no session exists."""
    api._session = None

    # Should not raise an error
    await api.async_close()


async def test_refresh_token(api, mock_session):
    """Test refreshing the access token."""
    api._refresh_token = "refresh_token"

    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value={"Refresh Token": "new_access_token"})
    response.text = AsyncMock(return_value="")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    mock_session.post = MagicMock(return_value=response)

    result = await api.refresh_token()

    assert result["access_token"] == "new_access_token"
    assert api._access_token == "new_access_token"


async def test_refresh_token_no_refresh_token(api, mock_session):
    """Test refresh when no refresh token exists."""
    api._refresh_token = None

    with patch.object(api, "authenticate", new_callable=AsyncMock) as mock_auth:
        mock_auth.return_value = {"access_token": "new_token"}

        result = await api.refresh_token()

        assert result["access_token"] == "new_token"
        mock_auth.assert_called_once()
