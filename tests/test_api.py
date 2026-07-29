"""Tests for SolectrusInfluxClient validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from influxdb_client.rest import ApiException
from urllib3.exceptions import HTTPError

from custom_components.solectrus.api import (
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


class TestUrlNormalization:
    """Tests for scheme handling in SolectrusInfluxClient.__init__."""

    def test_bare_host_gets_https_scheme(self):
        client = SolectrusInfluxClient(
            url="localhost:8086",
            token="test-token",  # noqa: S106
            org="test-org",
            bucket="test-bucket",
        )
        assert client._url == "https://localhost:8086"
        assert client._ssl is True

    def test_http_scheme_preserved(self):
        client = SolectrusInfluxClient(
            url="http://localhost:8086",
            token="test-token",  # noqa: S106
            org="test-org",
            bucket="test-bucket",
        )
        assert client._url == "http://localhost:8086"
        assert client._ssl is False

    def test_https_scheme_preserved(self):
        client = SolectrusInfluxClient(
            url="https://influx.example.com",
            token="test-token",  # noqa: S106
            org="test-org",
            bucket="test-bucket",
        )
        assert client._url == "https://influx.example.com"
        assert client._ssl is True

    def test_strips_whitespace(self):
        client = SolectrusInfluxClient(
            url="  localhost:8086  ",
            token="test-token",  # noqa: S106
            org="test-org",
            bucket="test-bucket",
        )
        assert client._url == "https://localhost:8086"


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


_SAMPLE_FIELDS = [
    ("inverter", "power"),
    ("battery", "soc"),
    ("wallbox", "connected"),
    ("system", "status"),
    ("ok_meas", "ok_field"),
]


def _record(measurement: str, field: str):
    record = MagicMock()
    record.values = {"_measurement": measurement, "_field": field}
    return record


def _table(records, value_type: str):
    """Build a mock Flux table with a typed _value column.

    Flux returns one table per distinct column-type schema, so the
    `data_type` annotation lives on the column, not on individual records.
    """
    value_column = MagicMock()
    value_column.label = "_value"
    value_column.data_type = value_type
    table = MagicMock()
    table.columns = [value_column]
    table.records = records
    return table


class TestGetFieldTypes:
    """Tests for async_get_field_types."""

    @pytest.mark.asyncio
    async def test_maps_column_annotations_to_labels(self, client):
        """Each Flux column annotation is mapped to its label."""
        mock_influx = MagicMock()
        mock_query_api = MagicMock()
        # Flux groups records by column schema, so different types arrive
        # in separate tables.
        mock_query_api.query.return_value = [
            _table([_record("inverter", "power")], value_type="long"),
            _table([_record("battery", "soc")], value_type="double"),
            _table([_record("wallbox", "connected")], value_type="boolean"),
            _table([_record("system", "status")], value_type="string"),
            _table([_record("counter", "total")], value_type="unsignedLong"),
        ]
        mock_influx.query_api.return_value = mock_query_api

        with patch.object(client, "_ensure_client", return_value=mock_influx):
            result = await client.async_get_field_types(_SAMPLE_FIELDS)

        assert result[("inverter", "power")] == "int"
        assert result[("battery", "soc")] == "float"
        assert result[("wallbox", "connected")] == "bool"
        assert result[("system", "status")] == "string"
        assert result[("counter", "total")] == "int"

    @pytest.mark.asyncio
    async def test_returns_empty_on_auth_error(self, client):
        """401/403 degrade to empty so fallbacks apply (user must fix the token)."""
        mock_influx = MagicMock()
        mock_query_api = MagicMock()
        mock_query_api.query.side_effect = ApiException(status=403, reason="Forbidden")
        mock_influx.query_api.return_value = mock_query_api

        with patch.object(client, "_ensure_client", return_value=mock_influx):
            result = await client.async_get_field_types(_SAMPLE_FIELDS)

        assert result == {}

    @pytest.mark.asyncio
    async def test_raises_on_other_api_error(self, client):
        """5xx propagates so HA can retry setup with ConfigEntryNotReady."""
        mock_influx = MagicMock()
        mock_query_api = MagicMock()
        mock_query_api.query.side_effect = ApiException(status=500, reason="boom")
        mock_influx.query_api.return_value = mock_query_api

        with (
            patch.object(client, "_ensure_client", return_value=mock_influx),
            pytest.raises(SolectrusInfluxError),
        ):
            await client.async_get_field_types(_SAMPLE_FIELDS)

    @pytest.mark.asyncio
    async def test_raises_on_connection_error(self, client):
        """Network errors propagate so HA can retry setup."""
        mock_influx = MagicMock()
        mock_query_api = MagicMock()
        mock_query_api.query.side_effect = OSError("connection lost")
        mock_influx.query_api.return_value = mock_query_api

        with (
            patch.object(client, "_ensure_client", return_value=mock_influx),
            pytest.raises(SolectrusConnectionError),
        ):
            await client.async_get_field_types(_SAMPLE_FIELDS)

    @pytest.mark.asyncio
    async def test_empty_fields_skips_query(self, client):
        """No configured fields means no need to query at all."""
        with patch.object(client, "_ensure_client") as ensure:
            result = await client.async_get_field_types([])
        assert result == {}
        ensure.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_records_without_string_keys(self, client):
        mock_influx = MagicMock()
        mock_query_api = MagicMock()
        bad = MagicMock()
        bad.values = {"_measurement": None, "_field": "power"}
        mock_query_api.query.return_value = [
            _table([bad, _record("ok_meas", "ok_field")], value_type="double"),
        ]
        mock_influx.query_api.return_value = mock_query_api

        with patch.object(client, "_ensure_client", return_value=mock_influx):
            result = await client.async_get_field_types(_SAMPLE_FIELDS)

        assert result == {("ok_meas", "ok_field"): "float"}

    @pytest.mark.asyncio
    async def test_skips_table_without_value_column(self, client):
        """Tables missing the _value column are silently skipped."""
        mock_influx = MagicMock()
        mock_query_api = MagicMock()
        bare_table = MagicMock()
        bare_table.columns = []
        bare_table.records = [_record("inverter", "power")]
        mock_query_api.query.return_value = [
            bare_table,
            _table([_record("battery", "soc")], value_type="double"),
        ]
        mock_influx.query_api.return_value = mock_query_api

        with patch.object(client, "_ensure_client", return_value=mock_influx):
            result = await client.async_get_field_types(_SAMPLE_FIELDS)

        assert result == {("battery", "soc"): "float"}
