"""InfluxDB access layer for the SOLECTRUS integration."""

from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING, Any, NoReturn

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS, WriteApi
from influxdb_client.rest import ApiException
from urllib3.exceptions import HTTPError

from .const import (
    DATA_TYPE_BOOL,
    DATA_TYPE_FLOAT,
    DATA_TYPE_INT,
    DATA_TYPE_STRING,
    LOGGER,
)

if TYPE_CHECKING:
    from datetime import datetime

# HTTP status codes
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404


def _column_type_to_label(data_type: str) -> str:
    """Map a Flux column data_type annotation to our internal label."""
    if data_type == "boolean":
        return DATA_TYPE_BOOL
    if data_type in ("long", "unsignedLong"):
        return DATA_TYPE_INT
    if data_type == "double":
        return DATA_TYPE_FLOAT
    return DATA_TYPE_STRING


class SolectrusInfluxError(Exception):
    """Base exception for InfluxDB issues."""


class SolectrusConnectionError(SolectrusInfluxError):
    """Raised when connection to InfluxDB fails."""


class SolectrusAuthError(SolectrusInfluxError):
    """Raised when authentication fails."""


class SolectrusInfluxClient:
    """Thin wrapper around the sync InfluxDB client, executed off the event loop."""

    def __init__(
        self, url: str, token: str, org: str, bucket: str, *, verify_ssl: bool = True
    ) -> None:
        """Create the Influx client wrapper."""
        self._url = url
        self._token = token
        self._org = org
        self._bucket = bucket
        self._client: InfluxDBClient | None = None
        self._write_api: WriteApi | None = None
        self._ssl = not url.lower().startswith("http://")
        self._verify_ssl = bool(verify_ssl) and self._ssl

    async def async_validate_connection(self) -> None:
        """Validate connectivity, auth, and write access."""
        client = await self._ensure_client()
        loop = asyncio.get_running_loop()

        # Verify basic connectivity (no auth required)
        try:
            reachable = await loop.run_in_executor(None, client.ping)
        except (HTTPError, OSError) as err:
            msg = f"Connection failed: {err}"
            raise SolectrusConnectionError(msg) from err

        if not reachable:
            msg = "InfluxDB server is not reachable"
            raise SolectrusConnectionError(msg)

        # Validate write access by writing a test point
        write_api = await loop.run_in_executor(
            None, lambda: client.write_api(write_options=SYNCHRONOUS)
        )
        ok = True
        point = Point("_solectrus_test").field("ok", ok)
        try:
            await loop.run_in_executor(
                None,
                lambda: write_api.write(
                    bucket=self._bucket,
                    org=self._org,
                    record=point,
                ),
            )
        except ApiException as err:
            if err.status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
                msg = "Invalid token or insufficient permissions"
                raise SolectrusAuthError(msg) from err
            if err.status == _HTTP_NOT_FOUND:
                msg = "Bucket not found"
                raise SolectrusInfluxError(msg) from err
            msg = f"API error (HTTP {err.status})"
            raise SolectrusInfluxError(msg) from err
        except (HTTPError, OSError) as err:
            msg = f"Connection failed: {type(err).__name__}"
            raise SolectrusConnectionError(msg) from err

    async def async_connect(self) -> None:
        """Prepare the write API."""
        client = await self._ensure_client()
        if self._write_api is None:
            loop = asyncio.get_running_loop()
            self._write_api = await loop.run_in_executor(
                None, lambda: client.write_api(write_options=SYNCHRONOUS)
            )

    async def async_write(
        self,
        measurement: str,
        field: str,
        value: Any,
        timestamp: datetime | None = None,
    ) -> None:
        """Write a point to InfluxDB."""
        if self._write_api is None:
            await self.async_connect()

        point = Point(measurement)
        point.field(field, value)
        if timestamp is not None:
            point.time(timestamp, WritePrecision.S)

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self._write_api.write(
                    bucket=self._bucket,
                    org=self._org,
                    record=point,
                    write_precision=WritePrecision.S,
                ),
            )
        except ApiException as err:
            self._handle_api_exception(err)
        except (HTTPError, OSError) as err:
            self._handle_connection_exception(err)

    async def async_write_batch(self, points: list[Point]) -> None:
        """Write multiple points to InfluxDB in a single request."""
        if not points:
            return

        if self._write_api is None:
            await self.async_connect()

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self._write_api.write(
                    bucket=self._bucket,
                    org=self._org,
                    record=points,
                    write_precision=WritePrecision.S,
                ),
            )
        except ApiException as err:
            self._handle_api_exception(err)
        except (HTTPError, OSError) as err:
            self._handle_connection_exception(err)

    @staticmethod
    def _handle_api_exception(err: ApiException) -> NoReturn:
        """Translate InfluxDB API errors into domain exceptions."""
        if err.status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
            LOGGER.error("InfluxDB authentication failed (HTTP %s)", err.status)
            raise SolectrusAuthError("Authentication failed") from err
        LOGGER.error("InfluxDB API error (HTTP %s)", err.status)
        raise SolectrusInfluxError(f"API error (HTTP {err.status})") from err

    @staticmethod
    def _handle_connection_exception(err: HTTPError | OSError) -> NoReturn:
        """Translate network errors into domain exceptions."""
        LOGGER.warning("InfluxDB connection failed: %s", type(err).__name__)
        raise SolectrusConnectionError(
            f"Connection failed: {type(err).__name__}"
        ) from err

    async def async_get_field_types(
        self, fields: list[tuple[str, str]]
    ) -> dict[tuple[str, str], str]:
        """Return existing field types for the given (measurement, field) tuples."""
        if not fields:
            return {}

        client = await self._ensure_client()
        loop = asyncio.get_running_loop()

        def _esc(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')

        bucket = _esc(self._bucket)
        # Filter to only the series we care about so a shared bucket doesn't
        # slow down the schema lookup. first() suffices because the field type
        # is frozen at first write and reading the oldest block is cheaper.
        predicates = " or ".join(
            f'(r._measurement == "{_esc(m)}" and r._field == "{_esc(f)}")'
            for m, f in fields
        )
        flux = (
            f'from(bucket: "{bucket}")'
            " |> range(start: 0)"
            f" |> filter(fn: (r) => {predicates})"
            " |> first()"
            ' |> keep(columns: ["_measurement", "_field", "_value"])'
        )

        def _query() -> dict[tuple[str, str], str]:
            query_api = client.query_api()
            tables = query_api.query(query=flux, org=self._org)
            result: dict[tuple[str, str], str] = {}
            for table in tables:
                # The Flux column annotation is the source of truth for the
                # field type; deriving it from the deserialized Python value
                # would be ambiguous (e.g. bool is a subclass of int).
                value_column = next(
                    (c for c in table.columns if c.label == "_value"), None
                )
                if value_column is None:
                    continue
                label = _column_type_to_label(value_column.data_type)
                for record in table.records:
                    measurement = record.values.get("_measurement")
                    field = record.values.get("_field")
                    if not isinstance(measurement, str) or not isinstance(field, str):
                        continue
                    result[(measurement, field)] = label
            return result

        try:
            return await loop.run_in_executor(None, _query)
        except ApiException as err:
            if err.status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
                # Token lacks READ permission — degrade gracefully, since this
                # needs the user to fix the token, not a setup retry.
                LOGGER.warning(
                    "InfluxDB schema query forbidden (HTTP %s); the configured "
                    "token needs READ access to the bucket so existing field "
                    "types can be detected. Falling back to built-in defaults, "
                    "which may cause field type conflicts.",
                    err.status,
                )
                return {}
            self._handle_api_exception(err)
        except (HTTPError, OSError) as err:
            self._handle_connection_exception(err)

    async def async_close(self) -> None:
        """Close the client."""
        client = self._client
        self._client = None
        self._write_api = None
        if client is None:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.close)

    async def _ensure_client(self) -> InfluxDBClient:
        """Create the client off the event loop to avoid blocking."""
        if self._client is not None:
            return self._client

        loop = asyncio.get_running_loop()

        def _build_client() -> InfluxDBClient:
            ssl_param: bool | ssl.SSLContext
            if not self._ssl:
                ssl_param = False
            elif self._verify_ssl:
                ssl_param = ssl.create_default_context()
            else:
                insecure_context = ssl.create_default_context()
                insecure_context.check_hostname = False
                insecure_context.verify_mode = ssl.CERT_NONE
                ssl_param = insecure_context
            return InfluxDBClient(
                url=self._url,
                token=self._token,
                org=self._org,
                ssl=ssl_param,
                verify_ssl=self._verify_ssl,
            )

        try:
            self._client = await loop.run_in_executor(None, _build_client)
        except (HTTPError, OSError) as err:
            msg = f"Connection failed: {err}"
            raise SolectrusConnectionError(msg) from err
        except (ValueError, TypeError) as err:
            msg = f"Invalid configuration: {err}"
            raise SolectrusInfluxError(msg) from err
        return self._client
