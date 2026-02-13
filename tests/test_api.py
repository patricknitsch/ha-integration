"""Tests for SolectrusInfluxClient validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from influxdb_client.rest import ApiException
from urllib3.exceptions import HTTPError

from custom_components.solectrus_integration.api import (
    SolectrusAuthError,
    SolectrusConnectionError,
    SolectrusInfluxClient,
    SolectrusInfluxError,
)


@pytest.fixture
def client():
    """Create a client instance for testing."""
    return SolectrusInfluxClient(
        url="http://localhost:8086",
        token="test-token",  # noqa: S106
        org="test-org",
        bucket="test-bucket",
    )


class TestValidateConnection:
    """Tests for async_validate_connection."""

    @pytest.mark.asyncio
    async def test_success(self, client):
        """Successful validation with ping + test write."""
        mock_influx = MagicMock()
        mock_influx.ping.return_value = True
        mock_write_api = MagicMock()
        mock_influx.write_api.return_value = mock_write_api

        with patch.object(client, "_ensure_client", return_value=mock_influx):
            await client.async_validate_connection()

        mock_influx.ping.assert_called_once()
        mock_write_api.write.assert_called_once()

    @pytest.mark.asyncio
    async def test_ping_connection_error(self, client):
        """Ping fails with network error."""
        mock_influx = MagicMock()
        mock_influx.ping.side_effect = OSError("Connection refused")

        with (
            patch.object(client, "_ensure_client", return_value=mock_influx),
            pytest.raises(SolectrusConnectionError, match="Connection failed"),
        ):
            await client.async_validate_connection()

    @pytest.mark.asyncio
    async def test_ping_http_error(self, client):
        """Ping fails with HTTP error."""
        mock_influx = MagicMock()
        mock_influx.ping.side_effect = HTTPError("timeout")

        with (
            patch.object(client, "_ensure_client", return_value=mock_influx),
            pytest.raises(SolectrusConnectionError, match="Connection failed"),
        ):
            await client.async_validate_connection()

    @pytest.mark.asyncio
    async def test_ping_returns_false(self, client):
        """Ping returns False (server unreachable)."""
        mock_influx = MagicMock()
        mock_influx.ping.return_value = False

        with (
            patch.object(client, "_ensure_client", return_value=mock_influx),
            pytest.raises(SolectrusConnectionError, match="not reachable"),
        ):
            await client.async_validate_connection()

    @pytest.mark.asyncio
    async def test_write_unauthorized(self, client):
        """Write fails with 401 (bad token)."""
        mock_influx = MagicMock()
        mock_influx.ping.return_value = True
        mock_write_api = MagicMock()
        mock_write_api.write.side_effect = ApiException(
            status=401, reason="Unauthorized"
        )
        mock_influx.write_api.return_value = mock_write_api

        with (
            patch.object(client, "_ensure_client", return_value=mock_influx),
            pytest.raises(SolectrusAuthError, match="insufficient permissions"),
        ):
            await client.async_validate_connection()

    @pytest.mark.asyncio
    async def test_write_forbidden(self, client):
        """Write fails with 403 (token lacks write permission)."""
        mock_influx = MagicMock()
        mock_influx.ping.return_value = True
        mock_write_api = MagicMock()
        mock_write_api.write.side_effect = ApiException(status=403, reason="Forbidden")
        mock_influx.write_api.return_value = mock_write_api

        with (
            patch.object(client, "_ensure_client", return_value=mock_influx),
            pytest.raises(SolectrusAuthError, match="insufficient permissions"),
        ):
            await client.async_validate_connection()

    @pytest.mark.asyncio
    async def test_write_bucket_not_found(self, client):
        """Write fails with 404 (bucket does not exist)."""
        mock_influx = MagicMock()
        mock_influx.ping.return_value = True
        mock_write_api = MagicMock()
        mock_write_api.write.side_effect = ApiException(status=404, reason="Not Found")
        mock_influx.write_api.return_value = mock_write_api

        with (
            patch.object(client, "_ensure_client", return_value=mock_influx),
            pytest.raises(SolectrusInfluxError, match="Bucket not found"),
        ):
            await client.async_validate_connection()

    @pytest.mark.asyncio
    async def test_write_other_api_error(self, client):
        """Write fails with unexpected API error."""
        mock_influx = MagicMock()
        mock_influx.ping.return_value = True
        mock_write_api = MagicMock()
        mock_write_api.write.side_effect = ApiException(
            status=500, reason="Internal Server Error"
        )
        mock_influx.write_api.return_value = mock_write_api

        with (
            patch.object(client, "_ensure_client", return_value=mock_influx),
            pytest.raises(SolectrusInfluxError, match="API error"),
        ):
            await client.async_validate_connection()

    @pytest.mark.asyncio
    async def test_write_connection_error(self, client):
        """Write fails with network error."""
        mock_influx = MagicMock()
        mock_influx.ping.return_value = True
        mock_write_api = MagicMock()
        mock_write_api.write.side_effect = OSError("Connection reset")
        mock_influx.write_api.return_value = mock_write_api

        with (
            patch.object(client, "_ensure_client", return_value=mock_influx),
            pytest.raises(SolectrusConnectionError, match="Connection failed"),
        ):
            await client.async_validate_connection()
