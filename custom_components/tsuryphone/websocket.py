"""WebSocket client for TsuryPhone device communication."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable
import time

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    WEBSOCKET_RECONNECT_DELAY,
    WEBSOCKET_MAX_BACKOFF,
    EVENT_QUEUE_MAX_SIZE,
    INTEGRATION_EVENT_SCHEMA_VERSION,
)
from .models import TsuryPhoneEvent

_LOGGER = logging.getLogger(__name__)

EventHandler = Callable[[TsuryPhoneEvent], None]
ConnectionStateHandler = Callable[[str, dict[str, Any]], None]


class TsuryPhoneWebSocketError(Exception):
    """WebSocket specific error."""


class TsuryPhoneWebSocketClient:
    """WebSocket client for TsuryPhone real-time events."""

    def __init__(
        self,
        hass: HomeAssistant,
        url: str,
        event_handler: EventHandler,
        connection_state_handler: ConnectionStateHandler | None = None,
    ) -> None:
        """Initialize WebSocket client."""
        self._hass = hass
        self._url = url
        self._event_handler = event_handler
        self._connection_state_handler = connection_state_handler
        self._session = async_get_clientsession(hass)

        # Connection state
        self._websocket: aiohttp.ClientWebSocketResponse | None = None
        self._connected = False
        self._reconnect_task: asyncio.Task | None = None
        self._listen_task: asyncio.Task | None = None
        self._should_reconnect = True

        # Backoff and retry
        self._current_backoff = WEBSOCKET_RECONNECT_DELAY / 1000  # Convert to seconds
        self._last_seq = 0
        self._connection_attempts = 0

        # Event processing
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=EVENT_QUEUE_MAX_SIZE
        )
        self._queue_overflows = 0

        # Statistics
        self._connect_time: float = 0
        self._disconnect_time: float = 0
        self._events_received = 0
        self._events_processed = 0
        self._schema_mismatches = 0

        # Phase P8: Resilience improvements
        self._consecutive_failures = 0
        self._last_pong_time = 0.0
        self._ping_task: asyncio.Task | None = None
        self._ping_interval = 30  # seconds
        self._ping_timeout = 10  # seconds
        self._last_reboot_warning = 0.0
        self._reboot_warning_interval = 60  # seconds

    def _notify_connection_state(self, state: str) -> None:
        if self._connection_state_handler:
            try:
                self._connection_state_handler(state, self.statistics)
            except Exception as err:
                _LOGGER.debug("Connection state handler error: %s", err)

    @property
    def connected(self) -> bool:
        """Return if WebSocket is connected."""
        return self._connected

    @property
    def last_seq(self) -> int:
        """Return the last processed sequence number."""
        return self._last_seq

    @property
    def statistics(self) -> dict[str, Any]:
        """Return WebSocket statistics."""
        return {
            "connected": self._connected,
            "last_seq": self._last_seq,
            "events_received": self._events_received,
            "events_processed": self._events_processed,
            "queue_overflows": self._queue_overflows,
            "schema_mismatches": self._schema_mismatches,
            "connection_attempts": self._connection_attempts,
            "current_backoff_s": self._current_backoff,
            "connect_time": self._connect_time,
            "disconnect_time": self._disconnect_time,
            "consecutive_failures": self._consecutive_failures,
            "last_pong_time": self._last_pong_time,
        }

    async def start(self) -> None:
        """Start WebSocket connection."""
        if self._reconnect_task is not None:
            return  # Already started

        _LOGGER.debug("Starting WebSocket client for %s", self._url)
        self._should_reconnect = True
        self._reconnect_task = asyncio.create_task(self._connection_manager())

    async def stop(self) -> None:
        """Stop WebSocket connection."""
        _LOGGER.debug("Stopping WebSocket client")
        self._should_reconnect = False

        # Cancel reconnection attempts
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            self._reconnect_task = None

        # Cancel listening task
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        # Phase P8: Cancel ping task
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
            self._ping_task = None

        # Close WebSocket connection
        await self._disconnect()

    async def _connection_manager(self) -> None:
        """Manage WebSocket connection with automatic reconnection."""
        while self._should_reconnect:
            try:
                await self._connect()
                if self._connected:
                    # Reset backoff on successful connection
                    self._current_backoff = WEBSOCKET_RECONNECT_DELAY / 1000
                    self._connection_attempts = 0
                    self._consecutive_failures = 0

                    # Phase P8: Start ping/pong keepalive
                    self._ping_task = asyncio.create_task(self._ping_keepalive())

                    # Start listening for events
                    self._listen_task = asyncio.create_task(self._listen_for_events())
                    await self._listen_task

            except Exception as err:
                _LOGGER.error("WebSocket connection error: %s", err)
                self._consecutive_failures += 1

            if not self._should_reconnect:
                break

            # Exponential backoff
            self._connection_attempts += 1
            _LOGGER.debug(
                "Reconnecting in %s seconds (attempt %d)",
                self._current_backoff,
                self._connection_attempts,
            )

            try:
                await asyncio.sleep(self._current_backoff)
            except asyncio.CancelledError:
                break

            # Increase backoff, max 60 seconds
            self._current_backoff = min(
                self._current_backoff * 2, WEBSOCKET_MAX_BACKOFF / 1000
            )

    async def _connect(self) -> None:
        """Establish WebSocket connection."""
        if self._connected:
            return

        _LOGGER.debug("Connecting to WebSocket at %s", self._url)

        try:
            self._websocket = await self._session.ws_connect(
                self._url,
                timeout=aiohttp.ClientTimeout(total=10),
                heartbeat=30,  # Send ping every 30 seconds
            )
            self._connected = True
            self._connect_time = time.time()
            self._disconnect_time = 0
            self._last_seq = 0
            self._last_reboot_warning = 0.0
            _LOGGER.info("WebSocket connected successfully")
            self._notify_connection_state("connected")

        except Exception as err:
            _LOGGER.error("Failed to connect WebSocket: %s", err)
            self._connected = False
            raise TsuryPhoneWebSocketError(f"Connection failed: {err}") from err

    async def _disconnect(self) -> None:
        """Close WebSocket connection."""
        if not self._connected:
            return

        self._connected = False
        self._disconnect_time = time.time()

        if self._websocket and not self._websocket.closed:
            await self._websocket.close()
            _LOGGER.debug("WebSocket disconnected")

        self._websocket = None
        self._notify_connection_state("disconnected")

    async def _listen_for_events(self) -> None:
        """Listen for incoming WebSocket messages."""
        if not self._websocket:
            return

        _LOGGER.debug("Starting to listen for WebSocket events")

        try:
            async for msg in self._websocket:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", self._websocket.exception())
                    break
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                    _LOGGER.debug("WebSocket closed by server")
                    break

        except asyncio.CancelledError:
            _LOGGER.debug("WebSocket listening cancelled")
        except Exception as err:
            _LOGGER.error("WebSocket listening error: %s", err)
        finally:
            await self._disconnect()

    async def _handle_message(self, data: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            message = json.loads(data)
            self._events_received += 1

            # Add to processing queue
            try:
                self._event_queue.put_nowait(message)
            except asyncio.QueueFull:
                self._queue_overflows += 1
                _LOGGER.warning(
                    "Event queue full, dropping event (overflows: %d)",
                    self._queue_overflows,
                )
                # Try to make room by dropping oldest event
                try:
                    self._event_queue.get_nowait()
                    self._event_queue.put_nowait(message)
                except asyncio.QueueEmpty:
                    pass

            # Process events from queue
            await self._process_event_queue()

        except json.JSONDecodeError as err:
            _LOGGER.error("Invalid JSON received from WebSocket: %s", err)
        except Exception as err:
            _LOGGER.exception("Error handling WebSocket message: %s", err)

    async def _process_event_queue(self) -> None:
        """Process all events in the queue."""
        while not self._event_queue.empty():
            try:
                raw_event = self._event_queue.get_nowait()
                await self._process_event(raw_event)
                self._events_processed += 1
            except asyncio.QueueEmpty:
                break
            except Exception as err:
                _LOGGER.exception("Error processing event: %s", err)

    async def _process_event(self, raw_event: dict[str, Any]) -> None:
        """Process a single event from the device."""
        try:
            # Validate basic event structure
            if not self._validate_event_structure(raw_event):
                return

            # Check schema version
            schema_version = raw_event.get("schemaVersion")
            if schema_version != INTEGRATION_EVENT_SCHEMA_VERSION:
                self._schema_mismatches += 1
                if self._schema_mismatches == 1:  # Only log first mismatch
                    _LOGGER.warning(
                        "Schema version mismatch: expected %d, got %d",
                        INTEGRATION_EVENT_SCHEMA_VERSION,
                        schema_version,
                    )
                # Continue processing despite version mismatch

            # Check sequence number for reboot detection
            seq = raw_event.get("seq", 0)
            previous_seq = self._last_seq
            if seq <= previous_seq and previous_seq > 0:
                now = time.time()
                if now - self._last_reboot_warning >= self._reboot_warning_interval:
                    _LOGGER.warning(
                        "Sequence regression detected: %d <= %d (possible device reboot)",
                        seq,
                        previous_seq,
                    )
                    self._last_reboot_warning = now
                else:
                    _LOGGER.debug(
                        "Sequence regression detected (suppressed warning): %d <= %d",
                        seq,
                        previous_seq,
                    )
                # Don't drop the event, but signal potential reboot
                raw_event["_reboot_detected"] = True

            self._last_seq = max(seq, previous_seq)
            if seq > previous_seq:
                self._last_reboot_warning = 0.0

            # Convert to structured event
            event = TsuryPhoneEvent.from_json(raw_event)

            # Log event for debugging (first 300 seconds or if verbose)
            _LOGGER.debug(
                "[tsuryphone.event] %s/%s seq=%d ts=%d",
                event.category,
                event.event,
                event.seq,
                event.ts,
            )

            # Pass to event handler
            if self._event_handler:
                self._event_handler(event)

        except Exception as err:
            _LOGGER.exception("Error processing event: %s", err)

    def _validate_event_structure(self, event: dict[str, Any]) -> bool:
        """Validate basic event structure."""
        required_fields = ["schemaVersion", "seq", "ts", "category", "event"]

        for field in required_fields:
            if field not in event:
                _LOGGER.error("Missing required field '%s' in event", field)
                return False

        return True

    def reset_sequence(self) -> None:
        """Reset sequence tracking (for testing or reboot recovery)."""
        _LOGGER.debug("Resetting WebSocket sequence tracking")
        self._last_seq = 0

    async def _ping_keepalive(self) -> None:
        """Send periodic ping to keep connection alive and detect stale connections."""
        while self._connected and self._websocket:
            try:
                await asyncio.sleep(self._ping_interval)

                if not self._connected or not self._websocket:
                    break

                # Send ping frame
                _LOGGER.debug("Sending WebSocket ping")
                pong_waiter = await self._websocket.ping()

                try:
                    await asyncio.wait_for(pong_waiter, timeout=self._ping_timeout)
                    self._last_pong_time = time.time()
                    _LOGGER.debug("Received WebSocket pong")

                except asyncio.TimeoutError:
                    _LOGGER.warning("WebSocket ping timeout - connection may be stale")
                    # Let the connection manager handle reconnection
                    break

            except Exception as err:
                _LOGGER.debug("Ping keepalive error: %s", err)
                break

    async def disconnect(self) -> None:
        """Public method to disconnect WebSocket."""
        await self.stop()

    async def reconnect(self) -> None:
        """Public method to force reconnection."""
        _LOGGER.info("Forcing WebSocket reconnection")
        await self._disconnect()

        # Reset failure counter for immediate reconnection
        self._consecutive_failures = 0
        self._current_backoff = WEBSOCKET_RECONNECT_DELAY / 1000

        if not self._reconnect_task or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._connection_manager())

    def is_healthy(self) -> tuple[bool, list[str]]:
        """Check WebSocket health status."""
        issues = []

        # Check connection status
        if not self._connected:
            issues.append("WebSocket not connected")

        # Check consecutive failures
        if self._consecutive_failures > 3:
            issues.append(f"High consecutive failures: {self._consecutive_failures}")

        # Check stale pong
        if self._last_pong_time > 0:
            time_since_pong = time.time() - self._last_pong_time
            if time_since_pong > self._ping_interval * 3:
                issues.append(f"Stale pong response: {time_since_pong:.1f}s ago")

        # Check queue overflows
        if self._queue_overflows > 0:
            issues.append(f"Event queue overflows: {self._queue_overflows}")

        is_healthy = len(issues) == 0
        return is_healthy, issues
