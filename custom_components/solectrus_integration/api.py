"""InfluxDB access layer for the SOLECTRUS integration."""

from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING, Any

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS, WriteApi
from influxdb_client.rest import ApiException
from urllib3.exceptions import HTTPError

from .const import LOGGER

if TYPE_CHECKING:
    from datetime import datetime

# HTTP status codes
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404


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
    def _handle_api_exception(err: ApiException) -> None:
        """Translate InfluxDB API errors into domain exceptions."""
        if err.status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
            LOGGER.error("InfluxDB authentication failed (HTTP %s)", err.status)
            raise SolectrusAuthError("Authentication failed") from err
        LOGGER.error("InfluxDB API error (HTTP %s)", err.status)
        raise SolectrusInfluxError(f"API error (HTTP {err.status})") from err

    @staticmethod
    def _handle_connection_exception(err: HTTPError | OSError) -> None:
        """Translate network errors into domain exceptions."""
        LOGGER.warning("InfluxDB connection failed: %s", type(err).__name__)
        raise SolectrusConnectionError(
            f"Connection failed: {type(err).__name__}"
        ) from err

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
