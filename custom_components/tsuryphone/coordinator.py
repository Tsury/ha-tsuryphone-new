"""Data update coordinator for TsuryPhone integration."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .api_client import TsuryPhoneAPIClient, TsuryPhoneAPIError
from .dialing import normalize_phone_number, sanitize_default_dialing_code
from .websocket import TsuryPhoneWebSocketClient
from .storage_cache import TsuryPhoneStorageCache
from .resilience import TsuryPhoneResilience
from .models import (
    TsuryPhoneState,
    TsuryPhoneEvent,
    DeviceInfo,
    CallHistoryEntry,
    PriorityCallerEntry,
    QuickDialEntry,
    BlockedNumberEntry,
    WebhookEntry,
)
from .const import (
    DOMAIN,
    POLLING_FALLBACK_INTERVAL,
    REFETCH_INTERVAL_DEFAULT,
    AppState,
    EventCategory,
    CallEvent,
    PhoneStateEvent,
    SystemEvent,
    ConfigEvent,
    DiagnosticEvent,
    HA_EVENT_CALL_START,
    HA_EVENT_CALL_END,
    HA_EVENT_CALL_BLOCKED,
    HA_EVENT_CALL_MISSED,
    HA_EVENT_PHONE_STATE,
    HA_EVENT_SYSTEM,
    HA_EVENT_CONFIG_DELTA,
    HA_EVENT_DIAGNOSTIC_SNAPSHOT,
)

_LOGGER = logging.getLogger(__name__)


class TsuryPhoneDataUpdateCoordinator(DataUpdateCoordinator[TsuryPhoneState]):
    """Class to manage fetching data from the TsuryPhone device."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: TsuryPhoneAPIClient,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize coordinator."""
        self.api_client = api_client
        self.device_info = device_info

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLLING_FALLBACK_INTERVAL),
        )

        # Initialize state after base class sets default data
        self.data = TsuryPhoneState(device_info=device_info)

        # WebSocket client
        self._websocket_client: TsuryPhoneWebSocketClient | None = None
        self._websocket_enabled = True

        # Call duration timer
        self._call_timer_task: asyncio.Task | None = None
        self._call_start_monotonic: float = 0

        # Event processing state
        self._missed_call_detection: dict[str, Any] = {}
        self._pending_call_starts: dict[str, dict[str, Any]] = {}  # Key: call number

        # Timers and intervals
        self._refetch_timer: Any = None
        self._last_websocket_disconnect: float = 0

        # Phase P4: Hybrid model state
        self.selected_quick_dial_code: str | None = None
        self.selected_blocked_number: str | None = None
        self.selected_priority_number: str | None = None
        self.selected_webhook_code: str | None = None

        # User input buffers for device management actions (exposed via text entities)
        self.quick_dial_input: dict[str, str] = {"code": "", "number": "", "name": ""}
        self.blocked_input: dict[str, str] = {"number": "", "reason": ""}
        self.priority_input: dict[str, str] = {"number": ""}
        self.webhook_input: dict[str, str] = {
            "code": "",
            "webhook_id": "",
            "action_name": "",
        }
        self.dial_digit_input: dict[str, str] = {"digit": ""}

        # Phase P5: Notification manager (will be set during setup)
        self._notification_manager = None

        # Phase P7: Storage cache
        self._storage_cache: TsuryPhoneStorageCache | None = None

        # Phase P8: Resilience management
        self._resilience: TsuryPhoneResilience | None = None

        # State tracking for reboot detection
        self._reboot_detected = False
        self._last_refetch_time: float = 0
        self._invalid_app_state_values: set[str] = set()
        self._invalid_bool_values: set[str] = set()

    def _ensure_state(self) -> TsuryPhoneState:
        """Ensure coordinator state object exists."""
        if self.data is None:
            self.data = TsuryPhoneState(device_info=self.device_info)
        return self.data

    def _coerce_bool(
        self,
        value: Any,
        field: str,
        *,
        default: bool | None = False,
    ) -> bool:
        """Normalize incoming boolean-like values from firmware payloads."""

        if isinstance(value, bool):
            return value

        if value is None:
            return default if default is not None else False

        if isinstance(value, (int, float)):
            return value != 0

        if isinstance(value, str):
            normalized = value.strip().lower()
            if not normalized:
                return default if default is not None else False
            if normalized in {"true", "1", "yes", "on", "y"}:
                return True
            if normalized in {"false", "0", "no", "off", "n"}:
                return False

        key = f"{field}:{value!r}"
        if key not in self._invalid_bool_values:
            self._invalid_bool_values.add(key)
            _LOGGER.debug("Unexpected boolean value for %s: %r", field, value)

        if default is not None:
            return default

        return bool(value)

    async def _async_setup(self) -> None:
        """Set up the coordinator."""
        # Phase P8: Initialize resilience manager
        self._resilience = TsuryPhoneResilience(self.hass, self)

        # Start WebSocket connection
        if self._websocket_enabled:
            await self._start_websocket()

        # Set up periodic refetch timer
        self._refetch_timer = async_track_time_interval(
            self.hass,
            self._periodic_refetch,
            timedelta(minutes=REFETCH_INTERVAL_DEFAULT),
        )

    async def _async_update_data(self) -> TsuryPhoneState:
        """Fetch data from API (used for polling fallback)."""
        _LOGGER.debug("Polling device for state update")

        try:
            state = self._ensure_state()
            # Get current configuration and state
            config_response = await self.api_client.get_tsuryphone_config()

            if not config_response.get("success"):
                raise UpdateFailed("Device returned error response")

            device_data = config_response.get("data", {})

            # Update state from device response
            await self._update_state_from_device_data(device_data)

            # Mark as connected
            state.connected = True
            state.last_seen = time.time()

            return state

        except TsuryPhoneAPIError as err:
            state = self._ensure_state()
            state.connected = False
            # Phase P8: Handle API error through resilience manager
            if self._resilience:
                error_type = (
                    "connection_timeout"
                    if "timeout" in str(err).lower()
                    else "http_error"
                )
                await self._resilience.handle_api_error(error_type, err)
            raise UpdateFailed(f"Error communicating with device: {err}") from err

    async def _start_websocket(self) -> None:
        """Start WebSocket connection."""
        if self._websocket_client:
            return

        websocket_url = self.api_client.websocket_url
        _LOGGER.debug("Starting WebSocket connection to %s", websocket_url)

        self._websocket_client = TsuryPhoneWebSocketClient(
            self.hass,
            websocket_url,
            self._handle_websocket_event,
        )

        # Phase P8: Register resilience callbacks
        if self._resilience:
            self._resilience.register_recovery_callback(
                self._websocket_recovery_callback
            )

        await self._websocket_client.start()

    async def _stop_websocket(self) -> None:
        """Stop WebSocket connection."""
        if self._websocket_client:
            await self._websocket_client.stop()
            self._websocket_client = None

    @callback
    def _handle_websocket_event(self, event: TsuryPhoneEvent) -> None:
        """Handle incoming WebSocket event."""
        _LOGGER.debug(
            "[tsuryphone.event] Processing %s/%s (seq=%d)",
            event.category,
            event.event,
            event.seq,
        )

        # Phase P8: Process event through resilience manager
        if self._resilience:
            self.hass.async_create_task(self._process_event_with_resilience(event))
            return

        # Fallback to direct processing if resilience not available
        self._process_event_directly(event)

    async def _process_event_with_resilience(self, event: TsuryPhoneEvent) -> None:
        """Process event through resilience manager."""
        try:
            # Check sequence validity and handle overflow/reboot detection
            should_process = await self._resilience.handle_event_sequence(event)

            if not should_process:
                _LOGGER.debug("Event dropped by resilience manager (seq=%d)", event.seq)
                return

            # Process the event
            self._process_event_directly(event)

        except Exception as err:
            _LOGGER.error("Error processing event through resilience manager: %s", err)
            # Fallback to direct processing
            self._process_event_directly(event)

    def _process_event_directly(self, event: TsuryPhoneEvent) -> None:
        """Process event directly without resilience checks."""
        self._ensure_state()
        self.data.connected = True
        self.data.last_seen = time.time()
        # Check for reboot detection
        if hasattr(event, "_reboot_detected") and event._reboot_detected:
            self._handle_reboot_detection(event)

        # Update last sequence
        if event.seq > self.data.last_seq:
            self.data.last_seq = event.seq

        # Extract firmware context fields that may be present in any event
        self._extract_firmware_context_fields(event)

        # Dispatch event by category (using string comparison to match firmware)
        if event.category == "call":
            self._handle_call_event(event)
        elif event.category == "phone_state":
            self._handle_phone_state_event(event)
        elif event.category == "system":
            self._handle_system_event(event)
        elif event.category == "config":
            self._handle_config_event(event)
        elif event.category == "diagnostic":
            self._handle_diagnostic_event(event)
        else:
            _LOGGER.debug("Unknown event category: %s", event.category)

        # Fire Home Assistant event
        self._fire_ha_event(event)

        # Update coordinator data and notify listeners
        self.async_set_updated_data(self.data)

        # Phase P5: Check and update notifications after event processing
        if self._notification_manager:
            self.hass.async_create_task(
                self._notification_manager.async_check_and_update_notifications()
            )

        # Phase P7: Update storage cache
        if self._storage_cache:
            self.hass.async_create_task(
                self._storage_cache.async_save_call_history(
                    self.data.call_history or []
                )
            )
            # Save device state backup periodically (every 10 events or important changes)
            if event.seq % 10 == 0 or event.category in ["config", "system"]:
                self.hass.async_create_task(
                    self._storage_cache.async_save_device_state(self.data)
                )

    def _handle_call_event(self, event: TsuryPhoneEvent) -> None:
        """Handle call-related events."""
        if event.event == "start":
            self._handle_call_start(event)
        elif event.event == "end":
            self._handle_call_end(event)
        elif event.event == "blocked":
            self._handle_call_blocked(event)

    def _handle_call_start(self, event: TsuryPhoneEvent) -> None:
        """Handle call start event."""
        # Extract fields directly from event data (firmware schema)
        number = event.data.get("number", "")
        is_incoming = event.data.get("isIncoming", False)
        call_start_ts = event.data.get("callStartTs", event.ts)

        previous_state = self.data.app_state
        if previous_state != AppState.IN_CALL:
            self.data.previous_app_state = previous_state
            self.data.app_state = AppState.IN_CALL

        # Once call starts, ringing/dialing flags should clear immediately
        self.data.ringing = False
        self.data.current_dialing_number = ""

        # Update current call info
        self.data.current_call.number = number
        self.data.current_call.is_incoming = is_incoming
        self.data.current_call.start_time = call_start_ts
        self.data.current_call.call_start_ts = call_start_ts
        self.data.current_call.duration_ms = None
        caller_name = str(
            event.data.get("currentCallName")
            or event.data.get("callerName")
            or ""
        )
        self.data.current_call.name = caller_name

        # Start call duration timer
        self._start_call_timer()

        # Add to call history (provisional entry)
        call_type = "incoming" if is_incoming else "outgoing"
        history_entry = CallHistoryEntry(
            call_type=call_type,
            number=number,
            is_incoming=is_incoming,
            duration_s=None,  # Will be filled on call end
            ts_device=call_start_ts,
            received_ts=event.received_at,
            seq=event.seq,
            call_start_ts=call_start_ts,
            name=caller_name,
        )

        # Store provisional entry (will be finalized on call end)
        self._pending_call_starts[number] = {
            "entry": history_entry,
            "start_monotonic": time.monotonic(),
        }

    def _handle_call_end(self, event: TsuryPhoneEvent) -> None:
        """Handle call end event."""
        number = event.data.get("number") or self.data.current_call.number
        is_incoming = event.data.get("isIncoming")
        if is_incoming is None:
            is_incoming = self.data.current_call.is_incoming

        call_start_ts = (
            event.data.get("callStartTs")
            or self.data.current_call.call_start_ts
            or self.data.current_call.start_time
        )

        duration_ms = event.data.get("durationMs")
        if duration_ms is None and self._call_start_monotonic > 0:
            duration_ms = int((time.monotonic() - self._call_start_monotonic) * 1000)

        caller_name = str(
            event.data.get("currentCallName")
            or event.data.get("callerName")
            or self.data.current_call.name
            or ""
        )

        # Clear transient flags so HA reflects idle state without delay
        self.data.ringing = False
        self.data.current_dialing_number = ""
        self.data.current_call_is_priority = False

        # Calculate duration (prefer device duration, fallback to local)
        duration_s = None
        if duration_ms is not None:
            duration_s = duration_ms // 1000

        # Update last call info
        resolved_incoming = (
            bool(is_incoming)
            if is_incoming is not None
            else self.data.current_call.is_incoming
        )
        call_type = "incoming_answered" if resolved_incoming else "outgoing_answered"

        self._update_last_call_info(
            number,
            is_incoming=resolved_incoming,
            call_start_ts=call_start_ts,
            duration_ms=duration_ms,
            call_type=call_type,
            name=caller_name,
        )

        # Finalize call history entry
        pending_call = self._pending_call_starts.pop(number, None) if number else None
        if pending_call:
            # Update existing provisional entry
            entry = pending_call["entry"]
            entry.duration_s = duration_s
            entry.name = caller_name or entry.name
            self.data.add_call_history_entry(entry)
        else:
            # Synthesize call start (R45 - handle end-only scenario)
            _LOGGER.debug("Synthesizing call start for end-only event")
            call_type = "incoming" if is_incoming else "outgoing"
            history_entry = CallHistoryEntry(
                call_type=call_type,
                number=number,
                is_incoming=is_incoming,
                duration_s=duration_s,
                ts_device=call_start_ts,
                received_ts=event.received_at,
                seq=event.seq,
                synthetic=True,
                name=caller_name,
            )
            self.data.add_call_history_entry(history_entry)

        # Clear remaining current call state
        self._reset_current_call_state(number=number)

    def _handle_call_blocked(self, event: TsuryPhoneEvent) -> None:
        """Handle blocked call event."""
        number = event.data.get("number", "")

        caller_name = str(
            event.data.get("currentCallName")
            or event.data.get("callerName")
            or ""
        )

        # Add to call history immediately (blocked calls are complete events)
        history_entry = CallHistoryEntry(
            call_type="blocked",
            number=number,
            is_incoming=True,  # Blocked calls are always incoming
            duration_s=None,
            ts_device=event.ts,
            received_ts=event.received_at,
            seq=event.seq,
            name=caller_name,
        )

        self.data.add_call_history_entry(history_entry)

        # Update blocked call statistics
        self.data.stats.calls_blocked += 1

        # Update last call snapshot
        self._update_last_call_info(
            number,
            is_incoming=True,
            call_start_ts=event.ts,
            duration_ms=None,
            call_type="incoming_blocked",
            name=caller_name,
        )

    def _update_last_call_info(
        self,
        number: str,
        *,
        is_incoming: bool | None = None,
        call_start_ts: int | None = None,
        duration_ms: int | None = None,
        call_type: str | None = None,
        name: str | None = None,
    ) -> None:
        """Update last call metadata before clearing current call state."""
        if not number:
            return

        normalized_call_type = self._normalize_call_type(
            call_type,
            is_incoming,
            duration_ms,
        )

        if is_incoming is None:
            is_incoming = self._infer_is_incoming_from_call_type(normalized_call_type)
        if is_incoming is None:
            is_incoming = self.data.current_call.is_incoming

        if not call_start_ts:
            call_start_ts = (
                self.data.current_call.call_start_ts
                or self.data.current_call.start_time
                or 0
            )

        self.data.last_call.number = number
        if is_incoming is not None:
            self.data.last_call.is_incoming = bool(is_incoming)
        self.data.last_call.start_time = call_start_ts
        self.data.last_call.call_start_ts = call_start_ts
        self.data.last_call.duration_ms = duration_ms

        if normalized_call_type:
            self.data.last_call.call_type = normalized_call_type
        elif not self.data.last_call.call_type and is_incoming is not None:
            self.data.last_call.call_type = (
                "incoming_answered"
                if self.data.last_call.is_incoming
                else "outgoing_answered"
            )

        if name is None:
            name = self.data.current_call.name or self.data.last_call.name

        if name is not None:
            self.data.last_call.name = name or ""

    def _reset_current_call_state(self, *, number: str | None = None) -> None:
        """Clear current call-related state and associated helpers."""
        active_number = number or self.data.current_call.number

        if active_number:
            self._pending_call_starts.pop(active_number, None)

        self._stop_call_timer()

        # Reset transient call state fields
        self.data.current_call = type(self.data.current_call)()
        self.data.current_dialing_number = ""
        self.data.current_call_is_priority = False
        self.data.ringing = False

    def _normalize_call_type(
        self,
        call_type: str | None,
        is_incoming: bool | None,
        duration_ms: int | None,
    ) -> str | None:
        if call_type:
            return str(call_type).strip().lower()

        if is_incoming is None:
            return None

        if duration_ms is not None and duration_ms <= 0:
            return "incoming_missed" if is_incoming else "outgoing_unanswered"

        return "incoming_answered" if is_incoming else "outgoing_answered"

    def _infer_is_incoming_from_call_type(self, call_type: str | None) -> bool | None:
        if not call_type:
            return None

        normalized = str(call_type).strip().lower()
        mapping: dict[str, bool] = {
            "incoming_answered": True,
            "incoming_missed": True,
            "incoming_blocked": True,
            "outgoing_answered": False,
            "outgoing_unanswered": False,
        }
        return mapping.get(normalized)

    def _handle_system_event(self, event: TsuryPhoneEvent) -> None:
        """Handle system events from firmware."""
        if event.event == SystemEvent.STATS:
            self._handle_stats_update(event)
        elif event.event == SystemEvent.STATUS:
            self._handle_status_update(event)
        elif event.event == SystemEvent.ERROR:
            self._handle_system_error(event)
        elif event.event == SystemEvent.SHUTDOWN:
            self._handle_system_shutdown(event)
        else:
            _LOGGER.debug("Unhandled system event type: %s", event.event)

    def _handle_phone_state_event(self, event: TsuryPhoneEvent) -> None:
        """Handle phone state events."""
        if event.event == "state":
            self._handle_phone_state_change(event)
        elif event.event == "dialing":
            self._handle_dialing_update(event)
        elif event.event == "ring":
            self._handle_ring_state(event)
        elif event.event == "dnd":
            self._handle_dnd_state(event)
        elif event.event == "call_info":
            self._handle_call_info_update(event)

    def _handle_phone_state_change(self, event: TsuryPhoneEvent) -> None:
        """Handle phone state change."""
        previous_state_value = event.data.get("previousState")
        new_state_value = event.data.get("state")

        previous_state = self._parse_app_state_value(
            previous_state_value, "event.previousState"
        )
        if previous_state is None and "previousStateName" in event.data:
            previous_state = self._parse_app_state_value(
                event.data.get("previousStateName"), "event.previousStateName"
            )

        if previous_state is not None:
            self.data.previous_app_state = previous_state
        else:
            previous_state = self.data.previous_app_state

        new_state = self._parse_app_state_value(new_state_value, "event.state")
        if new_state is None and "stateName" in event.data:
            new_state = self._parse_app_state_value(
                event.data.get("stateName"), "event.stateName"
            )

        if new_state is not None:
            self.data.app_state = new_state
        else:
            new_state = self.data.app_state

        # Extract additional firmware fields per schema
        if "dndActive" in event.data:
            self.data.dnd_active = self._coerce_bool(
                event.data["dndActive"],
                "event.dndActive",
                default=self.data.dnd_active,
            )

        if "isMaintenanceMode" in event.data:
            self.data.maintenance_mode = self._coerce_bool(
                event.data["isMaintenanceMode"],
                "event.isMaintenanceMode",
                default=self.data.maintenance_mode,
            )

        if "isHookOff" in event.data:
            self.data.hook_off = self._coerce_bool(
                event.data["isHookOff"],
                "event.isHookOff",
                default=self.data.hook_off,
            )

        # Update current call number if provided
        current_call_number = event.data.get("currentCallNumber", "")
        if current_call_number:
            self.data.current_call.number = current_call_number
        elif new_state == AppState.DIALING and not self.data.current_call.number:
            dial_buffer = (
                event.data.get("currentDialingNumber")
                or self.data.current_dialing_number
                or ""
            )
            if dial_buffer:
                self.data.current_call.number = dial_buffer

        if "currentCallName" in event.data:
            self.data.current_call.name = str(event.data.get("currentCallName") or "")

        # Update dialing number if provided
        if "currentDialingNumber" in event.data:
            current_dialing_number = event.data.get("currentDialingNumber") or ""
            self.data.current_dialing_number = current_dialing_number

        # Handle incoming call direction
        if event.data.get("isIncomingCall") is not None:
            self.data.current_call.is_incoming = self._coerce_bool(
                event.data["isIncomingCall"],
                "event.isIncomingCall",
                default=self.data.current_call.is_incoming,
            )

        if new_state in (AppState.INCOMING_CALL, AppState.INCOMING_CALL_RING):
            self.data.current_call.is_incoming = True
        elif new_state == AppState.DIALING:
            self.data.current_call.is_incoming = False

        # Update derived states
        if "isRinging" in event.data:
            self.data.ringing = self._coerce_bool(
                event.data["isRinging"],
                "event.isRinging",
                default=self.data.ringing,
            )
        else:
            self.data.ringing = new_state == AppState.INCOMING_CALL_RING

        reset_call_state = False

        if (
            previous_state
            and new_state
            and previous_state
            in (
                AppState.INCOMING_CALL,
                AppState.INCOMING_CALL_RING,
            )
            and new_state == AppState.IDLE
        ):
            self._detect_missed_call(event)
            reset_call_state = True

        if previous_state == AppState.DIALING and new_state == AppState.IDLE:
            self._detect_unanswered_outgoing(event)
            reset_call_state = True

        if new_state == AppState.IDLE:
            if previous_state == AppState.IN_CALL and self.data.current_call.number:
                self._update_last_call_info(
                    self.data.current_call.number,
                    is_incoming=self.data.current_call.is_incoming,
                    call_start_ts=self.data.current_call.call_start_ts
                    or self.data.current_call.start_time,
                    duration_ms=0,
                    call_type=(
                        "incoming_answered"
                        if self.data.current_call.is_incoming
                        else "outgoing_answered"
                    ),
                    name=self.data.current_call.name,
                )
            if (
                self.data.current_call.number
                or self.data.current_dialing_number
                or self.data.ringing
            ):
                reset_call_state = True

        if reset_call_state:
            self._reset_current_call_state()

    def _handle_dialing_update(self, event: TsuryPhoneEvent) -> None:
        """Handle dialing number update."""
        self.data.current_dialing_number = event.data.get("currentDialingNumber") or ""

    def _handle_ring_state(self, event: TsuryPhoneEvent) -> None:
        """Handle ring state change."""
        if "isRinging" in event.data:
            self.data.ringing = self._coerce_bool(
                event.data["isRinging"],
                "event.isRinging",
                default=self.data.ringing,
            )

    def _handle_dnd_state(self, event: TsuryPhoneEvent) -> None:
        """Handle DND state change."""
        if "dndActive" in event.data:
            self.data.dnd_active = self._coerce_bool(
                event.data["dndActive"],
                "event.dndActive",
                default=self.data.dnd_active,
            )

    def _handle_call_info_update(self, event: TsuryPhoneEvent) -> None:
        """Handle supplementary call info."""
        # Update current call with additional information
        current_number = event.data.get("currentCallNumber", "")
        if current_number:
            self.data.current_call.number = current_number

            if "currentCallName" in event.data:
                self.data.current_call.name = str(
                    event.data.get("currentCallName") or ""
                )

            if "dndActive" in event.data:
                self.data.dnd_active = self._coerce_bool(
                    event.data["dndActive"],
                    "event.dndActive",
                )
            self._handle_status_update(event)
            if "isMaintenanceMode" in event.data:
                self.data.maintenance_mode = self._coerce_bool(
                    event.data["isMaintenanceMode"],
                    "event.isMaintenanceMode",
                )
        elif event.event == "error":
            self._handle_system_error(event)
        elif event.event == "shutdown":
            self._handle_system_shutdown(event)

    def _handle_stats_update(self, event: TsuryPhoneEvent) -> None:
        """Handle statistics update."""
        calls_section = event.data.get("calls")
        totals_data: dict[str, Any] | None = None
        last_call_data: dict[str, Any] | None = None

        if isinstance(calls_section, dict):
            totals_candidate = calls_section.get("totals")
            if isinstance(totals_candidate, dict):
                totals_data = totals_candidate
            last_call_candidate = calls_section.get("lastCall")
            if isinstance(last_call_candidate, dict):
                last_call_data = last_call_candidate
        else:
            if any(
                key in event.data
                for key in ("total", "incoming", "outgoing", "blocked")
            ):
                totals_data = event.data
            last_call_candidate = event.data.get("lastCall")
            if isinstance(last_call_candidate, dict):
                last_call_data = last_call_candidate

        def _as_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        if totals_data:
            self.data.stats.calls_total = _as_int(totals_data.get("total"))
            self.data.stats.calls_incoming = _as_int(totals_data.get("incoming"))
            self.data.stats.calls_outgoing = _as_int(totals_data.get("outgoing"))
            self.data.stats.calls_blocked = _as_int(totals_data.get("blocked"))
            self.data.stats.talk_time_seconds = _as_int(
                totals_data.get("talkTimeSeconds")
            )

        if last_call_data:
            number = str(last_call_data.get("number", ""))
            call_type = str(last_call_data.get("type", ""))
            is_incoming = self._infer_is_incoming_from_call_type(call_type)
            self._update_last_call_info(
                number,
                is_incoming=is_incoming,
                call_start_ts=None,
                duration_ms=None,
                call_type=call_type or None,
                name=str(
                    last_call_data.get("name")
                    or last_call_data.get("currentCallName")
                    or ""
                ),
            )

    def _handle_status_update(self, event: TsuryPhoneEvent) -> None:
        """Handle system status update."""
        self.data.stats.uptime_seconds = event.data.get("uptime", 0)
        self.data.stats.free_heap_bytes = event.data.get("freeHeap", 0)
        self.data.stats.rssi_dbm = event.data.get("rssi", 0)

    def _handle_system_error(self, event: TsuryPhoneEvent) -> None:
        """Handle system error."""
        error_message = event.data.get("error", "Unknown system error")
        _LOGGER.error("Device system error: %s", error_message)

        # TODO: Create persistent notification (Phase P5)

    def _handle_system_shutdown(self, event: TsuryPhoneEvent) -> None:
        """Handle system shutdown notification."""
        reason = event.data.get("reason", "unknown")
        _LOGGER.info("Device shutdown: %s", reason)

        # Maintenance mode is a transient runtime flag. After a shutdown the
        # firmware boots in normal mode, so clear the cached flag proactively
        # to avoid stale UI state while we wait for fresh telemetry.
        if self.data.maintenance_mode:
            _LOGGER.debug(
                "Clearing maintenance mode due to shutdown event (%s)", reason
            )
            self.data.maintenance_mode = False

    def _handle_config_event(self, event: TsuryPhoneEvent) -> None:
        """Handle configuration change events."""
        if event.event == ConfigEvent.CONFIG_DELTA:
            self._handle_config_delta(event)

    def _handle_config_delta(self, event: TsuryPhoneEvent) -> None:
        """Handle configuration delta event."""
        # Handle single key change
        if "key" in event.data:
            key = event.data["key"]
            new_value = event.data.get("newValue")
            self._apply_config_change(key, new_value)

        # Handle aggregated changes
        elif "changes" in event.data:
            for change in event.data["changes"]:
                key = change.get("key")
                new_value = change.get("newValue")
                if key:
                    self._apply_config_change(key, new_value)

    def _update_default_dialing_metadata(
        self,
        *,
        code: Any | None = None,
        prefix: Any | None = None,
    ) -> None:
        """Update cached dialing metadata and refresh normalized entries if needed."""

        state = self._ensure_state()

        current_code = state.default_dialing_code or ""
        if code is not None:
            sanitized_code = sanitize_default_dialing_code(str(code))
        else:
            sanitized_code = current_code

        current_prefix = state.default_dialing_prefix or ""
        if prefix is not None:
            sanitized_prefix = str(prefix or "").strip()
        else:
            sanitized_prefix = current_prefix

        if sanitized_code:
            if not sanitized_prefix or not sanitized_prefix.startswith("+"):
                sanitized_prefix = f"+{sanitized_code}"
        else:
            sanitized_prefix = ""

        code_changed = sanitized_code != current_code
        prefix_changed = sanitized_prefix != current_prefix

        if not code_changed and not prefix_changed:
            return

        state.default_dialing_code = sanitized_code
        state.default_dialing_prefix = sanitized_prefix

        if code_changed:
            for entry in state.quick_dials:
                entry.normalized_number = normalize_phone_number(
                    entry.number, sanitized_code
                )
            for entry in state.blocked_numbers:
                entry.normalized_number = normalize_phone_number(
                    entry.number, sanitized_code
                )
            for entry in state.priority_callers:
                entry.normalized_number = normalize_phone_number(
                    entry.number, sanitized_code
                )

    def _apply_config_change(self, key: str, value: Any) -> None:
        """Apply a single configuration change."""
        _LOGGER.debug("Applying config change: %s = %s", key, value)

        # Map configuration keys to state fields
        if key == "ring.pattern":
            self.data.ring_pattern = str(value)
        elif key.startswith("audio."):
            # Audio configuration changes
            audio_field = key.split(".", 1)[1]
            # Map API field names to model field names
            field_mapping = {
                "earpieceVolume": "earpiece_volume",
                "earpieceGain": "earpiece_gain",
                "speakerVolume": "speaker_volume",
                "speakerGain": "speaker_gain",
            }
            model_field = field_mapping.get(audio_field, audio_field)
            if hasattr(self.data.audio_config, model_field):
                setattr(self.data.audio_config, model_field, value)
        elif key.startswith("dnd."):
            # DND configuration changes
            dnd_field = key.split(".", 1)[1]
            field_mapping = {
                "force": "force",
                "scheduled": "scheduled",
                "startHour": "start_hour",
                "startMinute": "start_minute",
                "endHour": "end_hour",
                "endMinute": "end_minute",
            }
            model_field = field_mapping.get(dnd_field, dnd_field)
            if hasattr(self.data.dnd_config, model_field):
                setattr(self.data.dnd_config, model_field, value)
                # Update active DND status if needed
                if dnd_field == "force":
                    forced = self._coerce_bool(
                        value,
                        "config.delta.dnd.force",
                        default=self.data.dnd_active,
                    )
                    self.data.dnd_active = forced or self.data.dnd_active
        elif key.startswith("dialing."):
            dial_field = key.split(".", 1)[1]
            if dial_field == "defaultCode":
                self._update_default_dialing_metadata(code=value)
            elif dial_field == "defaultPrefix":
                self._update_default_dialing_metadata(prefix=value)
            else:
                _LOGGER.debug("Unhandled dialing config delta key: %s", key)
        elif key.startswith("quick_dial."):
            # Quick dial list changes
            action = key.split(".", 1)[1]
            if action == "add" and isinstance(value, dict):
                # Add new quick dial entry
                try:
                    normalized = value.get("normalizedNumber")
                    if not normalized:
                        normalized = normalize_phone_number(
                            value.get("number"), self.data.default_dialing_code
                        )
                    entry = QuickDialEntry(
                        code=value.get("code", ""),
                        number=value.get("number", ""),
                        name=value.get("name", ""),
                        normalized_number=str(normalized or ""),
                    )
                    # Remove any existing entry with same code
                    self.data.quick_dials = [
                        q for q in self.data.quick_dials if q.code != entry.code
                    ]
                    self.data.quick_dials.append(entry)
                    self._ensure_quick_dial_selection()
                except (ValueError, KeyError) as err:
                    _LOGGER.warning("Invalid quick dial entry in config delta: %s", err)
            elif action == "remove" and isinstance(value, str):
                # Remove quick dial by code
                self.data.quick_dials = [
                    q for q in self.data.quick_dials if q.code != value
                ]
                self._ensure_quick_dial_selection()
        elif key.startswith("blocked."):
            # Blocked numbers list changes
            action = key.split(".", 1)[1]
            if action == "add" and isinstance(value, dict):
                try:
                    normalized = value.get("normalizedNumber")
                    if not normalized:
                        normalized = normalize_phone_number(
                            value.get("number"), self.data.default_dialing_code
                        )
                    entry = BlockedNumberEntry(
                        number=value.get("number", ""),
                        reason=value.get("reason", ""),
                        normalized_number=str(normalized or ""),
                    )
                    # Remove any existing entry with same number
                    self.data.blocked_numbers = [
                        b for b in self.data.blocked_numbers if b.number != entry.number
                    ]
                    self.data.blocked_numbers.append(entry)
                    self._ensure_blocked_selection()
                except (ValueError, KeyError) as err:
                    _LOGGER.warning(
                        "Invalid blocked number entry in config delta: %s", err
                    )
            elif action == "remove" and isinstance(value, str):
                # Remove blocked number
                self.data.blocked_numbers = [
                    b for b in self.data.blocked_numbers if b.number != value
                ]
                self._ensure_blocked_selection()
        elif key.startswith("webhook."):
            # Webhook configuration changes
            action = key.split(".", 1)[1]
            if action == "add" and isinstance(value, dict):
                try:
                    raw_events = value.get("events") or value.get("eventTypes") or []
                    if isinstance(raw_events, (list, tuple, set)):
                        events = [str(event) for event in raw_events if event]
                    elif raw_events:
                        events = [str(raw_events)]
                    else:
                        events = []
                    entry = WebhookEntry(
                        code=value.get("code", ""),
                        webhook_id=value.get("id", ""),
                        action_name=value.get("actionName", ""),
                        active=True,  # New webhooks are active by default
                        events=events,
                    )
                    # Remove any existing entry with same code
                    self.data.webhooks = [
                        w for w in self.data.webhooks if w.code != entry.code
                    ]
                    self.data.webhooks.append(entry)
                    self._ensure_webhook_selection()
                except (ValueError, KeyError) as err:
                    _LOGGER.warning("Invalid webhook entry in config delta: %s", err)
            elif action == "remove" and isinstance(value, str):
                # Remove webhook by code
                self.data.webhooks = [w for w in self.data.webhooks if w.code != value]
                self._ensure_webhook_selection()
        elif key.startswith("priority."):
            # Priority callers list changes (firmware emits priority.add / priority.remove)
            action = key.split(".", 1)[1]
            if action == "add" and isinstance(value, dict):
                try:
                    normalized = value.get("normalizedNumber")
                    if not normalized:
                        normalized = normalize_phone_number(
                            value.get("number"), self.data.default_dialing_code
                        )
                    entry = PriorityCallerEntry(
                        number=value.get("number", ""),
                        normalized_number=str(normalized or ""),
                    )
                    # Remove existing duplicate
                    self.data.priority_callers = [
                        p
                        for p in self.data.priority_callers
                        if p.number != entry.number
                    ]
                    self.data.priority_callers.append(entry)
                    self._ensure_priority_selection()
                except (ValueError, KeyError) as err:
                    _LOGGER.warning(
                        "Invalid priority caller entry in config delta: %s", err
                    )
            elif action == "remove" and isinstance(value, str):
                self.data.priority_callers = [
                    p for p in self.data.priority_callers if p.number != value
                ]
                self._ensure_priority_selection()
        elif key == "maintenance.enabled":
            # Maintenance mode changes
            self.data.maintenance_mode = self._coerce_bool(
                value,
                "config.delta.maintenance.enabled",
                default=self.data.maintenance_mode,
            )
        else:
            _LOGGER.debug("Unhandled config delta key: %s", key)

    def _handle_diagnostic_event(self, event: TsuryPhoneEvent) -> None:
        """Handle diagnostic events."""
        if event.event == "snapshot":
            self._handle_diagnostic_snapshot(event)

    def _handle_diagnostic_snapshot(self, event: TsuryPhoneEvent) -> None:
        """Handle diagnostic snapshot event."""
        _LOGGER.debug("Processing diagnostic snapshot for full state hydrate")
        data = event.data
        try:
            # Basic device info
            self.data.device_name = data.get("deviceName", self.data.device_name)
            self.data.firmware_version = data.get(
                "firmwareVersion", self.data.firmware_version
            )
            self.data.hardware_model = data.get(
                "hardwareModel", self.data.hardware_model
            )

            # Phone state info
            if "dndActive" in data:
                self.data.dnd_active = self._coerce_bool(
                    data["dndActive"],
                    "snapshot.dndActive",
                    default=self.data.dnd_active,
                )
            if "isRinging" in data:
                self.data.ringing = self._coerce_bool(
                    data["isRinging"],
                    "snapshot.isRinging",
                    default=self.data.ringing,
                )
            if "isMaintenanceMode" in data:
                self.data.maintenance_mode = self._coerce_bool(
                    data["isMaintenanceMode"],
                    "snapshot.isMaintenanceMode",
                    default=self.data.maintenance_mode,
                )

            # Call info
            call_number = data.get("currentCallNumber", "")
            if call_number:
                self.data.current_call.number = call_number
            if "currentCallName" in data:
                self.data.current_call.name = str(
                    data.get("currentCallName") or ""
                )
            if "isIncomingCall" in data:
                self.data.current_call.is_incoming = self._coerce_bool(
                    data["isIncomingCall"],
                    "snapshot.isIncomingCall",
                    default=self.data.current_call.is_incoming,
                )

            if "callWaitingId" in data:
                try:
                    call_waiting_id = int(data["callWaitingId"])
                except (TypeError, ValueError):
                    call_waiting_id = -1

                self.data.current_call.call_waiting_id = call_waiting_id
                self.data.call_waiting_available = call_waiting_id != -1

                if call_waiting_id == -1:
                    self.data.call_waiting_on_hold = False

            if "callWaitingAvailable" in data:
                available = self._coerce_bool(
                    data["callWaitingAvailable"],
                    "snapshot.callWaitingAvailable",
                    default=self.data.call_waiting_available,
                )
                self.data.call_waiting_available = available
                if not available:
                    self.data.current_call.call_waiting_id = -1
                    self.data.call_waiting_on_hold = False

            if "callWaitingOnHold" in data:
                self.data.call_waiting_on_hold = self._coerce_bool(
                    data["callWaitingOnHold"],
                    "snapshot.callWaitingOnHold",
                    default=self.data.call_waiting_on_hold,
                )
            if data.get("callStartTs"):
                self.data.current_call.start_ts = data.get("callStartTs")

            # Stats/system info
            self.data.stats.uptime_seconds = data.get(
                "uptime", self.data.stats.uptime_seconds
            )
            self.data.stats.free_heap_bytes = data.get(
                "freeHeap", self.data.stats.free_heap_bytes
            )
            self.data.stats.rssi_dbm = data.get("rssi", self.data.stats.rssi_dbm)

            # Lists (quick dials, blocked, priority, webhooks)
            phone_section = data.get("phone") or {}
            if not isinstance(phone_section, dict):
                phone_section = {}

            config_section = data.get("config") or {}
            if not isinstance(config_section, dict):
                config_section = {}

            quick_dial_source = (
                phone_section.get("quickDial")
                or phone_section.get("quickDialEntries")
                or data.get("quickDial")
                or data.get("quickDials")
                or []
            )
            qd_list: list[QuickDialEntry] = []
            if isinstance(quick_dial_source, list):
                for q in quick_dial_source:
                    if not isinstance(q, dict):
                        _LOGGER.debug(
                            "Skipping quick dial snapshot entry with invalid type: %s",
                            q,
                        )
                        continue
                    try:
                        code = (
                            q.get("code")
                            or q.get("entry")
                            or q.get("key")
                            or q.get("id")
                            or ""
                        )
                        number = (
                            q.get("number") or q.get("value") or q.get("phone") or ""
                        )
                        name = q.get("name") or q.get("label") or ""
                        qd_list.append(
                            QuickDialEntry(
                                code=str(code),
                                number=str(number),
                                name=str(name),
                            )
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug(
                            "Skipping invalid quick dial snapshot entry: %s", q
                        )
            self.data.quick_dials = qd_list
            self._ensure_quick_dial_selection()

            blocked_source = (
                phone_section.get("blocked")
                or phone_section.get("blockedNumbers")
                or data.get("blocked")
                or data.get("blockedNumbers")
                or []
            )
            blocked_list: list[BlockedNumberEntry] = []
            if isinstance(blocked_source, list):
                for b in blocked_source:
                    if not isinstance(b, dict):
                        _LOGGER.debug(
                            "Skipping blocked snapshot entry with invalid type: %s", b
                        )
                        continue
                    try:
                        number = (
                            b.get("number") or b.get("value") or b.get("phone") or ""
                        )
                        reason = b.get("reason") or b.get("note") or ""
                        blocked_list.append(
                            BlockedNumberEntry(
                                number=str(number),
                                reason=str(reason),
                            )
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("Skipping invalid blocked snapshot entry: %s", b)
            self.data.blocked_numbers = blocked_list
            self._ensure_blocked_selection()

            priority_source = (
                phone_section.get("priorityCallers")
                or data.get("priorityCallers")
                or []
            )
            priority_list: list[PriorityCallerEntry] = []
            if isinstance(priority_source, list):
                for p in priority_source:
                    try:
                        number = p.get("number") if isinstance(p, dict) else p
                        priority_list.append(PriorityCallerEntry(number=str(number)))
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("Skipping invalid priority snapshot entry: %s", p)
            self.data.priority_callers = priority_list
            self._ensure_priority_selection()

            webhook_source = phone_section.get("webhooks") or data.get("webhooks") or []
            webhook_list: list[WebhookEntry] = []
            if isinstance(webhook_source, list):
                for w in webhook_source:
                    if not isinstance(w, dict):
                        _LOGGER.debug(
                            "Skipping webhook snapshot entry with invalid type: %s", w
                        )
                        continue
                    try:
                        code = w.get("code") or w.get("entry") or w.get("key") or ""
                        webhook_id = (
                            w.get("id")
                            or w.get("webhook_id")
                            or w.get("webhookId")
                            or ""
                        )
                        action_name = w.get("actionName") or w.get("name") or ""
                        active = w.get("active") if "active" in w else True
                        raw_events = w.get("events") or w.get("eventTypes") or []
                        if isinstance(raw_events, (list, tuple, set)):
                            events = [str(event) for event in raw_events if event]
                        elif raw_events:
                            events = [str(raw_events)]
                        else:
                            events = []
                        webhook_list.append(
                            WebhookEntry(
                                code=str(code),
                                webhook_id=str(webhook_id),
                                action_name=str(action_name),
                                active=self._coerce_bool(
                                    active,
                                    "snapshot.webhooks.active",
                                    default=True,
                                ),
                                events=events,
                            )
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("Skipping invalid webhook snapshot entry: %s", w)
            self.data.webhooks = webhook_list
            self._ensure_webhook_selection()

            # Audio config
            audio = data.get("audioConfig") or config_section.get("audio") or {}
            if audio:
                for fw_key, model_attr in {
                    "earpieceVolume": "earpiece_volume",
                    "earpieceGain": "earpiece_gain",
                    "speakerVolume": "speaker_volume",
                    "speakerGain": "speaker_gain",
                }.items():
                    if fw_key in audio and hasattr(self.data.audio_config, model_attr):
                        setattr(self.data.audio_config, model_attr, audio[fw_key])

            # DND config
            dnd_sources: tuple[dict[str, Any] | None, ...] = (
                data.get("dndConfig"),
                (
                    config_section.get("dnd")
                    if isinstance(config_section.get("dnd"), dict)
                    else None
                ),
                (
                    config_section.get("dndConfig")
                    if isinstance(config_section.get("dndConfig"), dict)
                    else None
                ),
                data.get("dnd") if isinstance(data.get("dnd"), dict) else None,
            )
            dnd = next((section for section in dnd_sources if section), None)
            if dnd:
                mapping = {
                    "force": "force",
                    "scheduled": "scheduled",
                    "startHour": "start_hour",
                    "startMinute": "start_minute",
                    "endHour": "end_hour",
                    "endMinute": "end_minute",
                }
                for fw_key, attr in mapping.items():
                    if fw_key not in dnd or not hasattr(self.data.dnd_config, attr):
                        continue

                    value = dnd[fw_key]
                    if attr in {"force", "scheduled"}:
                        coerced = self._coerce_bool(
                            value,
                            f"snapshot.dnd.{fw_key}",
                            default=getattr(self.data.dnd_config, attr),
                        )
                        setattr(self.data.dnd_config, attr, coerced)
                    else:
                        try:
                            setattr(self.data.dnd_config, attr, int(value))
                        except (TypeError, ValueError):
                            _LOGGER.debug(
                                "Skipping invalid DND value for %s: %r", fw_key, value
                            )

            # Ring pattern
            if "ringPattern" in data:
                self.data.ring_pattern = data.get("ringPattern", self.data.ring_pattern)

            # Fire internal event to alert listeners of snapshot hydrate
            self.hass.bus.async_fire(
                f"{DOMAIN}_diagnostic_snapshot_applied",
                {"device_id": self.device_info.device_id},
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to process diagnostic snapshot: %s", err)

    def _handle_reboot_detection(self, event: TsuryPhoneEvent) -> None:
        """Handle device reboot detection."""
        _LOGGER.warning("Device reboot detected (seq regression)")
        self._reboot_detected = True
        self.data.reboot_detected = True

        if self.data.maintenance_mode:
            _LOGGER.debug("Clearing maintenance mode as part of reboot handling")
            self.data.maintenance_mode = False

        # Cancel any active call timer
        self._stop_call_timer()

        # Clear transient state
        self.data.current_dialing_number = ""
        self._pending_call_starts.clear()

        # Schedule refetch (rate limited)
        current_time = time.time()
        if current_time - self._last_refetch_time > 10:  # Max once per 10s
            self.hass.async_create_task(self._refetch_after_reboot())
            self._last_refetch_time = current_time

    def _extract_firmware_context_fields(self, event: TsuryPhoneEvent) -> None:
        """Extract additional context fields that firmware helper methods may inject."""
        # Based on firmware analysis, events may contain additional fields from:
        # - addBasicDeviceInfo(): device metadata
        # - addPhoneStateInfo(): current state context
        # - addCallInfo(): current call details
        # - addSystemInfo(): system metrics

        if isinstance(event.data.get("dialing"), dict):
            dialing_info = event.data.get("dialing")
            self._update_default_dialing_metadata(
                code=dialing_info.get("defaultCode"),
                prefix=dialing_info.get("defaultPrefix"),
            )

        if "defaultDialingCode" in event.data:
            self._update_default_dialing_metadata(
                code=event.data.get("defaultDialingCode")
            )

        if "defaultDialingPrefix" in event.data:
            self._update_default_dialing_metadata(
                prefix=event.data.get("defaultDialingPrefix")
            )

        # Extract current call number if present (from addCallInfo/addPhoneStateInfo)
        if "currentCallNumber" in event.data and event.data["currentCallNumber"]:
            self.data.current_call.number = event.data["currentCallNumber"]

        if "currentCallName" in event.data:
            self.data.current_call.name = str(event.data.get("currentCallName") or "")

        # Extract dialing number if present
        if "currentDialingNumber" in event.data:
            self.data.current_dialing_number = event.data.get("currentDialingNumber") or ""

        # Extract state information if present
        parsed_state = None
        if "state" in event.data:
            parsed_state = self._parse_app_state_value(
                event.data["state"], "event.context.state"
            )

        if parsed_state is None and "stateName" in event.data:
            parsed_state = self._parse_app_state_value(
                event.data["stateName"], "event.context.stateName"
            )

        if parsed_state is not None:
            self.data.app_state = parsed_state

        parsed_prev_state = None
        if "previousState" in event.data:
            parsed_prev_state = self._parse_app_state_value(
                event.data["previousState"], "event.context.previousState"
            )

        if parsed_prev_state is None and "previousStateName" in event.data:
            parsed_prev_state = self._parse_app_state_value(
                event.data["previousStateName"], "event.context.previousStateName"
            )

        if parsed_prev_state is not None:
            self.data.previous_app_state = parsed_prev_state

        # Extract DND status if present
        if "dndActive" in event.data:
            self.data.dnd_active = self._coerce_bool(
                event.data["dndActive"],
                "event.context.dndActive",
                default=self.data.dnd_active,
            )

        # Extract maintenance mode if present
        if "isMaintenanceMode" in event.data:
            self.data.maintenance_mode = self._coerce_bool(
                event.data["isMaintenanceMode"],
                "event.context.isMaintenanceMode",
                default=self.data.maintenance_mode,
            )

        # Extract hook state if present
        if "isHookOff" in event.data:
            self.data.hook_off = self._coerce_bool(
                event.data["isHookOff"],
                "event.context.isHookOff",
                default=self.data.hook_off,
            )

        # Extract system metrics if present (from addSystemInfo)
        if "freeHeap" in event.data:
            self.data.stats.free_heap_bytes = event.data["freeHeap"]
        if "rssi" in event.data:
            self.data.stats.rssi_dbm = event.data["rssi"]
        if "uptime" in event.data:
            self.data.stats.uptime_seconds = event.data["uptime"]

        # Extract call info if present
        if "isIncomingCall" in event.data:
            self.data.current_call.is_incoming = self._coerce_bool(
                event.data["isIncomingCall"],
                "event.context.isIncomingCall",
                default=self.data.current_call.is_incoming,
            )

        if "currentCallIsPriority" in event.data:
            self.data.current_call_is_priority = self._coerce_bool(
                event.data["currentCallIsPriority"],
                "event.context.currentCallIsPriority",
                default=self.data.current_call_is_priority,
            )

        # Extract call waiting info if available (firmware debt R61)
        if "callWaitingId" in event.data:
            try:
                call_waiting_id = int(event.data["callWaitingId"])
            except (TypeError, ValueError):
                call_waiting_id = None

            if call_waiting_id is None:
                call_waiting_id = -1

            self.data.current_call.call_waiting_id = call_waiting_id
            self.data.call_waiting_available = call_waiting_id != -1

            if call_waiting_id == -1:
                self.data.call_waiting_on_hold = False

        if "callWaitingAvailable" in event.data:
            available = self._coerce_bool(
                event.data["callWaitingAvailable"],
                "event.context.callWaitingAvailable",
                default=self.data.call_waiting_available,
            )
            self.data.call_waiting_available = available
            if not available:
                self.data.current_call.call_waiting_id = -1
                self.data.call_waiting_on_hold = False

        if "callWaitingOnHold" in event.data:
            self.data.call_waiting_on_hold = self._coerce_bool(
                event.data["callWaitingOnHold"],
                "event.context.callWaitingOnHold",
                default=self.data.call_waiting_on_hold,
            )

    def _detect_unanswered_outgoing(self, event: TsuryPhoneEvent) -> None:
        """Detect and record unanswered outgoing calls."""
        number = (
            event.data.get("currentDialingNumber")
            or event.data.get("number", "")
            or self.data.current_dialing_number
            or self.data.current_call.number
        )

        if not number:
            return

        # Avoid duplicates if we already logged an outgoing answered call
        if any(
            entry.call_type == "outgoing" and entry.number == number
            for entry in self.data.call_history[-5:]
        ):
            return

        self._pending_call_starts.pop(number, None)

        history_entry = CallHistoryEntry(
            call_type="outgoing",
            number=number,
            is_incoming=False,
            duration_s=None,
            ts_device=event.ts,
            received_ts=event.received_at,
            seq=event.seq,
            reason="unanswered",
            synthetic=True,
        )

        self.data.add_call_history_entry(history_entry)

        self._update_last_call_info(
            number,
            is_incoming=False,
            call_start_ts=event.ts,
            duration_ms=None,
            call_type="outgoing_unanswered",
        )

    def _detect_missed_call(self, event: TsuryPhoneEvent) -> None:
        """Detect and record missed calls."""
        # Look for current call number from recent state
        number = event.data.get("currentCallNumber", "")
        if not number and hasattr(self.data, "current_call"):
            number = self.data.current_call.number

        if not number:
            return  # Can't record missed call without number

        # Check if this was actually a blocked call
        if any(
            entry.call_type == "blocked" and entry.number == number
            for entry in self.data.call_history[-5:]
        ):  # Check recent history
            return  # Don't record as missed if it was blocked

        # Record missed call
        history_entry = CallHistoryEntry(
            call_type="missed",
            number=number,
            is_incoming=True,
            duration_s=None,
            ts_device=event.ts,
            received_ts=event.received_at,
            seq=event.seq,
        )

        self.data.add_call_history_entry(history_entry)

        self._update_last_call_info(
            number,
            is_incoming=True,
            call_start_ts=event.ts,
            duration_ms=None,
            call_type="incoming_missed",
        )

        # Fire missed call event
        self.hass.bus.async_fire(
            HA_EVENT_CALL_MISSED,
            {
                "number": number,
                "detected_seq": event.seq,
                "ts": event.ts,
                "device_id": self.device_info.device_id,
            },
        )

        # Phase P5: Fire device trigger event for missed call
        self.hass.bus.async_fire(
            "tsuryphone_missed_call",
            {
                "device_id": self.device_info.device_id,
                "number": number,
                "name": event.data.get("name", ""),
                "call_id": event.data.get("callId", ""),
                "timestamp": self._event_timestamp_iso(event),
            },
        )

    def _fire_ha_event(self, event: TsuryPhoneEvent) -> None:
        """Fire Home Assistant event for device event."""
        base_data = event.to_ha_event_data()
        event_timestamp = self._event_timestamp_iso(event)

        # Determine event name and fire
        if event.category == EventCategory.CALL:
            if event.event == CallEvent.START:
                self.hass.bus.async_fire(HA_EVENT_CALL_START, base_data)
                # Phase P5: Fire device trigger event
                self.hass.bus.async_fire(
                    "tsuryphone_incoming_call",
                    {
                        "device_id": self.device_info.device_id,
                        "number": event.data.get("number", ""),
                        "name": event.data.get("name", ""),
                        "call_id": event.data.get("callId", ""),
                        "timestamp": event_timestamp,
                    },
                )
            elif event.event == CallEvent.END:
                self.hass.bus.async_fire(HA_EVENT_CALL_END, base_data)
                # Phase P5: Fire device trigger event
                self.hass.bus.async_fire(
                    "tsuryphone_call_ended",
                    {
                        "device_id": self.device_info.device_id,
                        "number": event.data.get("number", ""),
                        "name": event.data.get("name", ""),
                        "duration": event.data.get("durationMs", 0) // 1000,
                        "direction": (
                            "incoming"
                            if event.data.get("isIncoming", False)
                            else "outgoing"
                        ),
                        "timestamp": event_timestamp,
                        "call_id": event.data.get("callId", ""),
                    },
                )
            elif event.event == CallEvent.BLOCKED:
                self.hass.bus.async_fire(HA_EVENT_CALL_BLOCKED, base_data)

        elif event.category == EventCategory.PHONE_STATE:
            event_name = HA_EVENT_PHONE_STATE.format(event.event)
            self.hass.bus.async_fire(event_name, base_data)

            # Phase P5: Fire specific device trigger events for state changes
            if event.event == PhoneStateEvent.RINGING:
                # This is when call is answered
                self.hass.bus.async_fire(
                    "tsuryphone_call_answered",
                    {
                        "device_id": self.device_info.device_id,
                        "number": event.data.get("number", ""),
                        "name": event.data.get("name", ""),
                        "call_id": event.data.get("callId", ""),
                        "timestamp": event_timestamp,
                    },
                )
            elif event.event == PhoneStateEvent.IDLE:
                # Check if this was a disconnect or device state change
                old_state = self.data.previous_app_state
                if old_state in [AppState.RINGING, AppState.IN_CALL]:
                    # This might be a missed call or call end - handled by call events
                    pass
                elif not self.data.connected:
                    self.hass.bus.async_fire(
                        "tsuryphone_device_disconnected",
                        {
                            "device_id": self.device_info.device_id,
                            "previous_state": (
                                old_state.value if old_state else "unknown"
                            ),
                            "new_state": "disconnected",
                            "timestamp": event_timestamp,
                        },
                    )
                else:
                    self.hass.bus.async_fire(
                        "tsuryphone_device_connected",
                        {
                            "device_id": self.device_info.device_id,
                            "previous_state": (
                                old_state.value if old_state else "unknown"
                            ),
                            "new_state": "idle",
                            "timestamp": event_timestamp,
                        },
                    )

        # Phase P5: Fire DND and maintenance mode triggers
        elif event.category == EventCategory.CONFIG:
            config_section = event.data.get("section", "")
            changes = event.data.get("changes", {})

            # Check for DND changes
            if config_section == "dnd" or "dnd" in changes:
                dnd_active = self.data.dnd_active
                if "force" in changes or "scheduled" in changes:
                    if dnd_active:
                        self.hass.bus.async_fire(
                            "tsuryphone_dnd_enabled",
                            {
                                "device_id": self.device_info.device_id,
                                "timestamp": event_timestamp,
                            },
                        )
                    else:
                        self.hass.bus.async_fire(
                            "tsuryphone_dnd_disabled",
                            {
                                "device_id": self.device_info.device_id,
                                "timestamp": event_timestamp,
                            },
                        )

            # Check for maintenance mode changes
            if config_section == "maintenance" or "maintenance_mode" in changes:
                maintenance_active = self.data.maintenance_mode
                if maintenance_active:
                    self.hass.bus.async_fire(
                        "tsuryphone_maintenance_enabled",
                        {
                            "device_id": self.device_info.device_id,
                            "timestamp": event_timestamp,
                        },
                    )
                else:
                    self.hass.bus.async_fire(
                        "tsuryphone_maintenance_disabled",
                        {
                            "device_id": self.device_info.device_id,
                            "timestamp": event_timestamp,
                        },
                    )

        elif event.category == EventCategory.SYSTEM:
            event_name = HA_EVENT_SYSTEM.format(event.event)
            self.hass.bus.async_fire(event_name, base_data)

            # Phase P5: Fire reboot detection trigger
            if event.event == SystemEvent.STATUS and self.data.reboot_detected:
                self.hass.bus.async_fire(
                    "tsuryphone_device_rebooted",
                    {
                        "device_id": self.device_info.device_id,
                        "timestamp": event_timestamp,
                    },
                )

        elif event.category == EventCategory.CONFIG:
            self.hass.bus.async_fire(HA_EVENT_CONFIG_DELTA, base_data)

            # Phase P5: Fire config change trigger
            self.hass.bus.async_fire(
                "tsuryphone_config_changed",
                {
                    "device_id": self.device_info.device_id,
                    "config_section": event.data.get("section", "unknown"),
                    "changes": event.data.get("changes", {}),
                    "timestamp": event_timestamp,
                },
            )

        elif event.category == EventCategory.DIAGNOSTIC:
            self.hass.bus.async_fire(HA_EVENT_DIAGNOSTIC_SNAPSHOT, base_data)

    def _start_call_timer(self) -> None:
        """Start real-time call duration timer."""
        if self._call_timer_task is not None:
            return  # Timer already running

        self._call_start_monotonic = time.monotonic()
        self._call_timer_task = asyncio.create_task(self._call_timer_loop())

    def _stop_call_timer(self) -> None:
        """Stop call duration timer."""
        if self._call_timer_task:
            self._call_timer_task.cancel()
            self._call_timer_task = None
        self._call_start_monotonic = 0

    async def _call_timer_loop(self) -> None:
        """Call duration timer loop (updates every second)."""
        try:
            while True:
                await asyncio.sleep(1)
                if self._call_start_monotonic > 0:
                    # Update call duration in state (triggers entity updates)
                    current_duration = int(
                        time.monotonic() - self._call_start_monotonic
                    )
                    # Duration will be read by call duration sensor
                    self.async_set_updated_data(self.data)
        except asyncio.CancelledError:
            pass

    @property
    def current_call_duration_seconds(self) -> int:
        """Get current call duration in seconds."""
        if self._call_start_monotonic > 0 and self.data.is_call_active:
            return int(time.monotonic() - self._call_start_monotonic)
        return 0

    async def _update_state_from_device_data(self, device_data: dict[str, Any]) -> None:
        """Update state model from device API response."""
        # This method would parse the full device response and update self.data
        # Implementation would be similar to config delta handling but for full state
        _LOGGER.debug("Updating state from device data")

        config_section = device_data.get("config") or {}
        if not isinstance(config_section, dict):
            config_section = {}

        dialing_sections: tuple[dict[str, Any] | None, ...] = (
            config_section.get("dialing")
            if isinstance(config_section.get("dialing"), dict)
            else None,
            device_data.get("dialing")
            if isinstance(device_data.get("dialing"), dict)
            else None,
        )
        for dialing_section in dialing_sections:
            if dialing_section:
                self._update_default_dialing_metadata(
                    code=dialing_section.get("defaultCode"),
                    prefix=dialing_section.get("defaultPrefix"),
                )

        # Update phone state and related lists
        if "phone" in device_data:
            phone_data = device_data["phone"]
            parsed_state = None

            if isinstance(phone_data, dict) and isinstance(
                phone_data.get("dialing"), dict
            ):
                dialing_info = phone_data.get("dialing", {})
                self._update_default_dialing_metadata(
                    code=dialing_info.get("defaultCode"),
                    prefix=dialing_info.get("defaultPrefix"),
                )
            if "state" in phone_data:
                parsed_state = self._parse_app_state_value(
                    phone_data["state"], "device.phone.state"
                )

            if parsed_state is None and "stateName" in phone_data:
                parsed_state = self._parse_app_state_value(
                    phone_data["stateName"], "device.phone.stateName"
                )

            if parsed_state is not None:
                self.data.app_state = parsed_state
            elif "state" in phone_data:
                _LOGGER.error("Invalid app state: %s", phone_data["state"])

            # Previous state if provided
            parsed_prev = None
            if "previousState" in phone_data:
                parsed_prev = self._parse_app_state_value(
                    phone_data["previousState"], "device.phone.previousState"
                )

            if parsed_prev is None and "previousStateName" in phone_data:
                parsed_prev = self._parse_app_state_value(
                    phone_data["previousStateName"], "device.phone.previousStateName"
                )

            if parsed_prev is not None:
                self.data.previous_app_state = parsed_prev

            # Priority callers list
            if isinstance(phone_data.get("priorityCallers"), list):
                pr_list: list[PriorityCallerEntry] = []
                detail_map: dict[str, str] = {}
                if isinstance(phone_data.get("priorityCallerDetails"), list):
                    for detail in phone_data.get("priorityCallerDetails", []):
                        if not isinstance(detail, dict):
                            continue
                        number_value = str(detail.get("number") or "")
                        normalized_value = str(detail.get("normalizedNumber") or "")
                        if number_value:
                            detail_map[number_value] = normalized_value
                for item in phone_data.get("priorityCallers", []):
                    if isinstance(item, str) and item:
                        try:
                            normalized_value = detail_map.get(item)
                            if not normalized_value:
                                normalized_value = normalize_phone_number(
                                    item, self.data.default_dialing_code
                                )
                            pr_list.append(
                                PriorityCallerEntry(
                                    number=item,
                                    normalized_number=str(normalized_value or ""),
                                )
                            )
                        except ValueError:
                            pass
                self.data.priority_callers = pr_list
                self._ensure_priority_selection()

            # Quick dial entries
            quick_dial_source = (
                phone_data.get("quickDial")
                or phone_data.get("quickDialEntries")
                or device_data.get("quickDial")
                or device_data.get("quickDials")
            )
            if isinstance(quick_dial_source, list):
                qd_list: list[QuickDialEntry] = []
                for q in quick_dial_source:
                    if not isinstance(q, dict):
                        continue
                    try:
                        code_value = str(
                            q.get("code")
                            or q.get("entry")
                            or q.get("key")
                            or q.get("id")
                            or ""
                        )
                        number_value = str(
                            q.get("number")
                            or q.get("value")
                            or q.get("phone")
                            or ""
                        )
                        name_value = str(q.get("name") or q.get("label") or "")
                        normalized_value = q.get("normalizedNumber")
                        if not normalized_value and number_value:
                            normalized_value = normalize_phone_number(
                                number_value, self.data.default_dialing_code
                            )
                        qd_list.append(
                            QuickDialEntry(
                                code=code_value,
                                number=number_value,
                                name=name_value,
                                normalized_number=str(normalized_value or ""),
                            )
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("Skipping invalid quick dial config entry: %s", q)
                self.data.quick_dials = qd_list
                self._ensure_quick_dial_selection()

            # Blocked number entries
            blocked_source = (
                phone_data.get("blocked")
                or phone_data.get("blockedNumbers")
                or device_data.get("blocked")
                or device_data.get("blockedNumbers")
            )
            if isinstance(blocked_source, list):
                blocked_list: list[BlockedNumberEntry] = []
                for b in blocked_source:
                    if not isinstance(b, dict):
                        continue
                    try:
                        number_value = str(
                            b.get("number")
                            or b.get("value")
                            or b.get("phone")
                            or ""
                        )
                        reason_value = str(b.get("reason") or b.get("note") or "")
                        normalized_value = b.get("normalizedNumber")
                        if not normalized_value and number_value:
                            normalized_value = normalize_phone_number(
                                number_value, self.data.default_dialing_code
                            )
                        blocked_list.append(
                            BlockedNumberEntry(
                                number=number_value,
                                reason=reason_value,
                                normalized_number=str(normalized_value or ""),
                            )
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("Skipping invalid blocked config entry: %s", b)
                self.data.blocked_numbers = blocked_list
                self._ensure_blocked_selection()

            # Webhook entries
            webhook_source = phone_data.get("webhooks") or device_data.get("webhooks")
            if isinstance(webhook_source, list):
                webhook_list: list[WebhookEntry] = []
                for w in webhook_source:
                    if not isinstance(w, dict):
                        continue
                    try:
                        raw_events = w.get("events") or w.get("eventTypes") or []
                        if isinstance(raw_events, (list, tuple, set)):
                            events = [str(event) for event in raw_events if event]
                        elif raw_events:
                            events = [str(raw_events)]
                        else:
                            events = []
                        webhook_list.append(
                            WebhookEntry(
                                code=str(
                                    w.get("code")
                                    or w.get("entry")
                                    or w.get("key")
                                    or ""
                                ),
                                webhook_id=str(
                                    w.get("id")
                                    or w.get("webhook_id")
                                    or w.get("webhookId")
                                    or ""
                                ),
                                action_name=str(
                                    w.get("actionName") or w.get("name") or ""
                                ),
                                active=self._coerce_bool(
                                    w.get("active", True),
                                    "config.webhooks.active",
                                    default=True,
                                ),
                                events=events,
                            )
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("Skipping invalid webhook config entry: %s", w)
                self.data.webhooks = webhook_list
                self._ensure_webhook_selection()

            # Current call priority flag if exposed in full phone state context
            if "currentCallIsPriority" in phone_data:
                self.data.current_call_is_priority = self._coerce_bool(
                    phone_data.get("currentCallIsPriority"),
                    "config.phone.currentCallIsPriority",
                    default=self.data.current_call_is_priority,
                )

            if "isMaintenanceMode" in phone_data:
                self.data.maintenance_mode = self._coerce_bool(
                    phone_data.get("isMaintenanceMode"),
                    "config.phone.isMaintenanceMode",
                    default=self.data.maintenance_mode,
                )
            elif isinstance(phone_data.get("maintenance"), dict):
                maintenance_info = phone_data.get("maintenance", {})
                if "enabled" in maintenance_info:
                    self.data.maintenance_mode = self._coerce_bool(
                        maintenance_info["enabled"],
                        "config.phone.maintenance.enabled",
                        default=self.data.maintenance_mode,
                    )

            if "isHookOff" in phone_data:
                self.data.hook_off = self._coerce_bool(
                    phone_data.get("isHookOff"),
                    "config.phone.isHookOff",
                    default=self.data.hook_off,
                )

            if "callWaitingId" in phone_data:
                try:
                    call_waiting_id = int(phone_data["callWaitingId"])
                except (TypeError, ValueError):
                    call_waiting_id = -1

                self.data.current_call.call_waiting_id = call_waiting_id
                self.data.call_waiting_available = call_waiting_id != -1

                if call_waiting_id == -1:
                    self.data.call_waiting_on_hold = False

            if "callWaitingAvailable" in phone_data:
                available = self._coerce_bool(
                    phone_data["callWaitingAvailable"],
                    "config.phone.callWaitingAvailable",
                    default=self.data.call_waiting_available,
                )
                self.data.call_waiting_available = available
                if not available:
                    self.data.current_call.call_waiting_id = -1
                    self.data.call_waiting_on_hold = False

            if "callWaitingOnHold" in phone_data:
                self.data.call_waiting_on_hold = self._coerce_bool(
                    phone_data["callWaitingOnHold"],
                    "config.phone.callWaitingOnHold",
                    default=self.data.call_waiting_on_hold,
                )

        # Extract global fields that may appear outside phone section
        audio_section = (
            device_data.get("audioConfig")
            or config_section.get("audio")
            or device_data.get("audio")
        )
        if isinstance(audio_section, dict):
            for fw_key, model_attr in {
                "earpieceVolume": "earpiece_volume",
                "earpieceGain": "earpiece_gain",
                "speakerVolume": "speaker_volume",
                "speakerGain": "speaker_gain",
            }.items():
                if fw_key in audio_section and hasattr(
                    self.data.audio_config, model_attr
                ):
                    setattr(self.data.audio_config, model_attr, audio_section[fw_key])

        dnd_sources: tuple[dict[str, Any] | None, ...] = (
            (
                device_data.get("dndConfig")
                if isinstance(device_data.get("dndConfig"), dict)
                else None
            ),
            (
                config_section.get("dnd")
                if isinstance(config_section.get("dnd"), dict)
                else None
            ),
            (
                config_section.get("dndConfig")
                if isinstance(config_section.get("dndConfig"), dict)
                else None
            ),
            (
                device_data.get("dnd")
                if isinstance(device_data.get("dnd"), dict)
                else None
            ),
        )
        dnd_section = next((section for section in dnd_sources if section), None)
        if dnd_section:
            for fw_key, attr in {
                "force": "force",
                "scheduled": "scheduled",
                "startHour": "start_hour",
                "startMinute": "start_minute",
                "endHour": "end_hour",
                "endMinute": "end_minute",
            }.items():
                if fw_key not in dnd_section or not hasattr(self.data.dnd_config, attr):
                    continue

                value = dnd_section[fw_key]
                if attr in {"force", "scheduled"}:
                    coerced = self._coerce_bool(
                        value,
                        f"config.dnd.{fw_key}",
                        default=getattr(self.data.dnd_config, attr),
                    )
                    setattr(self.data.dnd_config, attr, coerced)
                else:
                    try:
                        setattr(self.data.dnd_config, attr, int(value))
                    except (TypeError, ValueError):
                        _LOGGER.debug(
                            "Skipping invalid DND value for %s: %r", fw_key, value
                        )

        if "currentCallIsPriority" in device_data:
            self.data.current_call_is_priority = self._coerce_bool(
                device_data.get("currentCallIsPriority"),
                "config.device.currentCallIsPriority",
                default=self.data.current_call_is_priority,
            )

        if "isMaintenanceMode" in device_data:
            self.data.maintenance_mode = self._coerce_bool(
                device_data.get("isMaintenanceMode"),
                "config.device.isMaintenanceMode",
                default=self.data.maintenance_mode,
            )
        elif "maintenanceMode" in device_data:
            self.data.maintenance_mode = self._coerce_bool(
                device_data.get("maintenanceMode"),
                "config.device.maintenanceMode",
                default=self.data.maintenance_mode,
            )
        elif isinstance(device_data.get("maintenance"), dict):
            maintenance_info = device_data.get("maintenance", {})
            if "enabled" in maintenance_info:
                self.data.maintenance_mode = self._coerce_bool(
                    maintenance_info["enabled"],
                    "config.device.maintenance.enabled",
                    default=self.data.maintenance_mode,
                )

        if "isHookOff" in device_data:
            self.data.hook_off = self._coerce_bool(
                device_data.get("isHookOff"),
                "config.device.isHookOff",
                default=self.data.hook_off,
            )

        if "callWaitingId" in device_data:
            try:
                call_waiting_id = int(device_data["callWaitingId"])
            except (TypeError, ValueError):
                call_waiting_id = -1

            self.data.current_call.call_waiting_id = call_waiting_id
            self.data.call_waiting_available = call_waiting_id != -1

            if call_waiting_id == -1:
                self.data.call_waiting_on_hold = False

        if "callWaitingAvailable" in device_data:
            available = self._coerce_bool(
                device_data["callWaitingAvailable"],
                "config.device.callWaitingAvailable",
                default=self.data.call_waiting_available,
            )
            self.data.call_waiting_available = available
            if not available:
                self.data.current_call.call_waiting_id = -1
                self.data.call_waiting_on_hold = False

        if "callWaitingOnHold" in device_data:
            self.data.call_waiting_on_hold = self._coerce_bool(
                device_data["callWaitingOnHold"],
                "config.device.callWaitingOnHold",
                default=self.data.call_waiting_on_hold,
            )

        # Validate tracked selections after bulk update
        self._ensure_quick_dial_selection()
        self._ensure_blocked_selection()
        self._ensure_priority_selection()
        self._ensure_webhook_selection()

    def _parse_app_state_value(self, value: Any, source: str) -> AppState | None:
        """Normalize various state encodings to AppState."""
        if isinstance(value, AppState):
            return value

        if isinstance(value, int):
            try:
                return AppState(value)
            except ValueError:
                self._log_invalid_app_state(value, source)
                return None

        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return None

            if candidate.isdigit() or (
                candidate.startswith("-") and candidate[1:].isdigit()
            ):
                try:
                    return AppState(int(candidate))
                except ValueError:
                    self._log_invalid_app_state(value, source)
                    return None

            normalized = re.sub(r"[^A-Z0-9]+", "", candidate.upper())
            for state in AppState:
                state_normalized = re.sub(r"[^A-Z0-9]+", "", state.name.upper())
                if normalized == state_normalized:
                    return state

        if value is not None:
            self._log_invalid_app_state(value, source)

        return None

    def _log_invalid_app_state(self, value: Any, source: str) -> None:
        """Log invalid app state values once per unique representation."""
        key = f"{source}:{value!r}"
        if key in self._invalid_app_state_values:
            return

        self._invalid_app_state_values.add(key)
        _LOGGER.warning("Unknown app state value %s from %s", value, source)

    def _ensure_quick_dial_selection(self) -> None:
        """Clear quick dial selection if entry no longer exists."""
        if not self.selected_quick_dial_code:
            return

        if not any(
            entry.code == self.selected_quick_dial_code
            for entry in self.data.quick_dials
        ):
            self.selected_quick_dial_code = None

    def _ensure_blocked_selection(self) -> None:
        """Clear blocked number selection if entry no longer exists."""
        if not self.selected_blocked_number:
            return

        if not any(
            entry.number == self.selected_blocked_number
            for entry in self.data.blocked_numbers
        ):
            self.selected_blocked_number = None

    def _ensure_priority_selection(self) -> None:
        """Clear priority number selection if entry no longer exists."""
        if not self.selected_priority_number:
            return

        if not any(
            entry.number == self.selected_priority_number
            for entry in self.data.priority_callers
        ):
            self.selected_priority_number = None

    def _ensure_webhook_selection(self) -> None:
        """Clear webhook selection if entry no longer exists."""
        if not self.selected_webhook_code:
            return

        if not any(
            entry.code == self.selected_webhook_code for entry in self.data.webhooks
        ):
            self.selected_webhook_code = None

    def _event_timestamp_iso(self, event: TsuryPhoneEvent) -> str:
        """Return ISO 8601 timestamp for an event's reception time."""
        received = getattr(event, "received_at", None)

        if isinstance(received, datetime):
            dt_value = received
        else:
            try:
                dt_value = dt_util.utc_from_timestamp(float(received))
            except (TypeError, ValueError):
                dt_value = dt_util.utcnow()

        return dt_value.isoformat()

    async def _refetch_after_reboot(self) -> None:
        """Refetch device state after reboot detection."""
        try:
            _LOGGER.info("Refetching device state after reboot detection")
            await self.api_client.refetch_all()

            # Clear reboot flag after successful refetch
            self.data.reboot_detected = False
            self._reboot_detected = False

        except TsuryPhoneAPIError as err:
            _LOGGER.error("Failed to refetch state after reboot: %s", err)

    async def _periodic_refetch(self, now=None) -> None:
        """Periodic device state refetch."""
        try:
            await self.api_client.refetch_all()
        except TsuryPhoneAPIError as err:
            _LOGGER.debug("Periodic refetch failed: %s", err)

    async def async_shutdown(self) -> None:
        """Shutdown coordinator."""
        # Phase P8: Cleanup resilience manager
        if self._resilience:
            await self._resilience.cleanup()
            self._resilience = None

        # Phase P7: Final storage cache save and cleanup
        if self._storage_cache:
            try:
                if self.data:
                    await self._storage_cache.async_save_call_history(
                        self.data.call_history or []
                    )
                    await self._storage_cache.async_save_device_state(self.data)
                await self._storage_cache.async_cleanup_storage()
                _LOGGER.debug("Storage cache saved and cleaned up")
            except Exception as err:
                _LOGGER.error("Failed to save storage cache during shutdown: %s", err)

        # Stop WebSocket
        await self._stop_websocket()

        # Stop call timer
        self._stop_call_timer()

        # Cancel refetch timer
        if self._refetch_timer:
            self._refetch_timer()
            self._refetch_timer = None

    async def _websocket_recovery_callback(self) -> None:
        """Recovery callback for WebSocket reconnection after errors."""
        _LOGGER.info("Executing WebSocket recovery callback")

        if self._websocket_client:
            try:
                # Force WebSocket reconnection
                await self._websocket_client.reconnect()
                _LOGGER.info("WebSocket recovery completed successfully")
            except Exception as err:
                _LOGGER.error("WebSocket recovery failed: %s", err)

    def get_resilience_status(self) -> dict[str, Any]:
        """Get comprehensive resilience status for diagnostics."""
        if not self._resilience:
            return {"resilience_enabled": False}

        # Get resilience stats
        resilience_stats = self._resilience.get_resilience_stats()

        # Add WebSocket health info
        websocket_health = {"connected": False, "healthy": False, "issues": []}
        if self._websocket_client:
            websocket_health["connected"] = self._websocket_client.connected
            ws_healthy, ws_issues = self._websocket_client.is_healthy()
            websocket_health["healthy"] = ws_healthy
            websocket_health["issues"] = ws_issues
            websocket_health.update(self._websocket_client.statistics)

        return {
            "resilience_enabled": True,
            "resilience": resilience_stats,
            "websocket": websocket_health,
            "api_client": {
                "base_url": self.api_client.base_url,
                "websocket_url": self.api_client.websocket_url,
                "timeout": getattr(self.api_client, "_timeout", "unknown"),
            },
        }
