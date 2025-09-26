"""Data update coordinator for TsuryPhone integration."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta

from .api_client import TsuryPhoneAPIClient, TsuryPhoneAPIError
from .websocket import TsuryPhoneWebSocketClient
from .storage_cache import TsuryPhoneStorageCache
from .resilience import TsuryPhoneResilience
from .models import (
    TsuryPhoneState,
    TsuryPhoneEvent,
    DeviceInfo,
    CallHistoryEntry,
    PriorityCallerEntry,
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

    def _ensure_state(self) -> TsuryPhoneState:
        """Ensure coordinator state object exists."""
        if self.data is None:
            self.data = TsuryPhoneState(device_info=self.device_info)
        return self.data

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

        # Update current call info
        self.data.current_call.number = number
        self.data.current_call.is_incoming = is_incoming
        self.data.current_call.start_time = call_start_ts
        self.data.current_call.call_start_ts = call_start_ts
        self.data.current_call.duration_ms = None

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
        )

        # Store provisional entry (will be finalized on call end)
        self._pending_call_starts[number] = {
            "entry": history_entry,
            "start_monotonic": time.monotonic(),
        }

    def _handle_call_end(self, event: TsuryPhoneEvent) -> None:
        """Handle call end event."""
        number = event.data.get("number", "")
        is_incoming = event.data.get("isIncoming", False)
        call_start_ts = event.data.get("callStartTs", 0)
        duration_ms = event.data.get("durationMs")

        # Stop call timer
        self._stop_call_timer()

        # Calculate duration (prefer device duration, fallback to local)
        duration_s = None
        if duration_ms is not None:
            duration_s = duration_ms // 1000
        elif self._call_start_monotonic > 0:
            duration_s = int(time.monotonic() - self._call_start_monotonic)

        # Update last call info
        self.data.last_call.number = number
        self.data.last_call.is_incoming = is_incoming
        self.data.last_call.start_time = call_start_ts
        self.data.last_call.call_start_ts = call_start_ts
        self.data.last_call.duration_ms = duration_ms

        # Clear current call
        self.data.current_call = type(self.data.current_call)()

        # Finalize call history entry
        pending_call = self._pending_call_starts.get(number)
        if pending_call:
            # Update existing provisional entry
            entry = pending_call["entry"]
            entry.duration_s = duration_s
            self.data.add_call_history_entry(entry)
            del self._pending_call_starts[number]
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
            )
            self.data.add_call_history_entry(history_entry)

    def _handle_call_blocked(self, event: TsuryPhoneEvent) -> None:
        """Handle blocked call event."""
        number = event.data.get("number", "")

        # Add to call history immediately (blocked calls are complete events)
        history_entry = CallHistoryEntry(
            call_type="blocked",
            number=number,
            is_incoming=True,  # Blocked calls are always incoming
            duration_s=None,
            ts_device=event.ts,
            received_ts=event.received_at,
            seq=event.seq,
        )

        self.data.add_call_history_entry(history_entry)

        # Update blocked call statistics
        self.data.stats.calls_blocked += 1

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
        if previous_state is not None:
            self.data.previous_app_state = previous_state
        else:
            previous_state = self.data.previous_app_state

        new_state = self._parse_app_state_value(new_state_value, "event.state")
        if new_state is not None:
            self.data.app_state = new_state
        else:
            new_state = self.data.app_state

        # Extract additional firmware fields per schema
        self.data.dnd_active = event.data.get("dndActive", False)
        self.data.maintenance_mode = event.data.get("isMaintenanceMode", False)

        # Update current call number if provided
        current_call_number = event.data.get("currentCallNumber", "")
        if current_call_number:
            self.data.current_call.number = current_call_number

        # Update dialing number if provided
        current_dialing_number = event.data.get("currentDialingNumber", "")
        if current_dialing_number:
            self.data.current_dialing_number = current_dialing_number

        # Handle incoming call direction
        if event.data.get("isIncomingCall") is not None:
            self.data.current_call.is_incoming = event.data.get("isIncomingCall")

        # Update derived states
        self.data.ringing = event.data.get(
            "isRinging", new_state == AppState.INCOMING_CALL_RING
        )

        # Detect missed calls (R52)
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

    def _handle_dialing_update(self, event: TsuryPhoneEvent) -> None:
        """Handle dialing number update."""
        self.data.current_dialing_number = event.data.get("currentDialingNumber", "")

    def _handle_ring_state(self, event: TsuryPhoneEvent) -> None:
        """Handle ring state change."""
        self.data.ringing = event.data.get("isRinging", False)

    def _handle_dnd_state(self, event: TsuryPhoneEvent) -> None:
        """Handle DND state change."""
        self.data.dnd_active = event.data.get("dndActive", False)

    def _handle_call_info_update(self, event: TsuryPhoneEvent) -> None:
        """Handle supplementary call info."""
        # Update current call with additional information
        current_number = event.data.get("currentCallNumber", "")
        if current_number:
            self.data.current_call.number = current_number

    def _handle_system_event(self, event: TsuryPhoneEvent) -> None:
        """Handle system events."""
        if event.event == "stats":
            self._handle_stats_update(event)
        elif event.event == "status":
            self._handle_status_update(event)
        elif event.event == "error":
            self._handle_system_error(event)
        elif event.event == "shutdown":
            self._handle_system_shutdown(event)

    def _handle_stats_update(self, event: TsuryPhoneEvent) -> None:
        """Handle statistics update."""
        # Based on firmware analysis: stats can be in nested structure or flat
        # Try nested structure first (addStatsInfo creates calls.totals nested structure)
        if "calls" in event.data and "totals" in event.data["calls"]:
            totals = event.data["calls"]["totals"]
            self.data.stats.calls_total = totals.get("total", 0)
            self.data.stats.calls_incoming = totals.get("incoming", 0)
            self.data.stats.calls_outgoing = totals.get("outgoing", 0)
            self.data.stats.calls_blocked = totals.get("blocked", 0)
            self.data.stats.talk_time_seconds = totals.get("talkTimeSeconds", 0)
        # Fallback to flat structure for backwards compatibility
        elif "total" in event.data:
            self.data.stats.calls_total = event.data.get("total", 0)
            self.data.stats.calls_incoming = event.data.get("incoming", 0)
            self.data.stats.calls_outgoing = event.data.get("outgoing", 0)
            self.data.stats.calls_blocked = event.data.get("blocked", 0)
            self.data.stats.talk_time_seconds = event.data.get("talkTimeSeconds", 0)

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
                    self.data.dnd_active = bool(value) or self.data.dnd_active
        elif key.startswith("quick_dial."):
            # Quick dial list changes
            action = key.split(".", 1)[1]
            if action == "add" and isinstance(value, dict):
                # Add new quick dial entry
                try:
                    from .models import QuickDialEntry

                    entry = QuickDialEntry(
                        code=value.get("code", ""),
                        number=value.get("number", ""),
                        name=value.get("name", ""),
                    )
                    # Remove any existing entry with same code
                    self.data.quick_dials = [
                        q for q in self.data.quick_dials if q.code != entry.code
                    ]
                    self.data.quick_dials.append(entry)
                except (ValueError, KeyError) as err:
                    _LOGGER.warning("Invalid quick dial entry in config delta: %s", err)
            elif action == "remove" and isinstance(value, str):
                # Remove quick dial by code
                self.data.quick_dials = [
                    q for q in self.data.quick_dials if q.code != value
                ]
        elif key.startswith("blocked."):
            # Blocked numbers list changes
            action = key.split(".", 1)[1]
            if action == "add" and isinstance(value, dict):
                try:
                    from .models import BlockedNumberEntry

                    entry = BlockedNumberEntry(
                        number=value.get("number", ""), reason=value.get("reason", "")
                    )
                    # Remove any existing entry with same number
                    self.data.blocked_numbers = [
                        b for b in self.data.blocked_numbers if b.number != entry.number
                    ]
                    self.data.blocked_numbers.append(entry)
                except (ValueError, KeyError) as err:
                    _LOGGER.warning(
                        "Invalid blocked number entry in config delta: %s", err
                    )
            elif action == "remove" and isinstance(value, str):
                # Remove blocked number
                self.data.blocked_numbers = [
                    b for b in self.data.blocked_numbers if b.number != value
                ]
        elif key.startswith("webhook."):
            # Webhook configuration changes
            action = key.split(".", 1)[1]
            if action == "add" and isinstance(value, dict):
                try:
                    from .models import WebhookEntry

                    entry = WebhookEntry(
                        code=value.get("code", ""),
                        webhook_id=value.get("id", ""),
                        action_name=value.get("actionName", ""),
                        active=True,  # New webhooks are active by default
                    )
                    # Remove any existing entry with same code
                    self.data.webhooks = [
                        w for w in self.data.webhooks if w.code != entry.code
                    ]
                    self.data.webhooks.append(entry)
                except (ValueError, KeyError) as err:
                    _LOGGER.warning("Invalid webhook entry in config delta: %s", err)
            elif action == "remove" and isinstance(value, str):
                # Remove webhook by code
                self.data.webhooks = [w for w in self.data.webhooks if w.code != value]
        elif key.startswith("priority."):
            # Priority callers list changes (firmware emits priority.add / priority.remove)
            action = key.split(".", 1)[1]
            if action == "add" and isinstance(value, dict):
                try:
                    entry = PriorityCallerEntry(number=value.get("number", ""))
                    # Remove existing duplicate
                    self.data.priority_callers = [
                        p
                        for p in self.data.priority_callers
                        if p.number != entry.number
                    ]
                    self.data.priority_callers.append(entry)
                except (ValueError, KeyError) as err:
                    _LOGGER.warning(
                        "Invalid priority caller entry in config delta: %s", err
                    )
            elif action == "remove" and isinstance(value, str):
                self.data.priority_callers = [
                    p for p in self.data.priority_callers if p.number != value
                ]
        elif key == "maintenance.enabled":
            # Maintenance mode changes
            self.data.maintenance_mode = bool(value)
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
            self.data.dnd_active = data.get("dndActive", self.data.dnd_active)
            self.data.ringing = data.get("isRinging", self.data.ringing)
            self.data.maintenance_mode = data.get(
                "isMaintenanceMode", self.data.maintenance_mode
            )

            # Call info
            call_number = data.get("currentCallNumber", "")
            if call_number:
                self.data.current_call.number = call_number
            self.data.current_call.is_incoming = data.get(
                "isIncomingCall", self.data.current_call.is_incoming
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
            from .models import (
                QuickDialEntry,
                BlockedNumberEntry,
                PriorityCallerEntry,
                WebhookEntry,
            )

            qd_list = []
            for q in data.get("quickDials", []):
                try:
                    qd_list.append(
                        QuickDialEntry(
                            code=q.get("code", ""),
                            number=q.get("number", ""),
                            name=q.get("name", ""),
                        )
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Skipping invalid quick dial snapshot entry: %s", q)
            self.data.quick_dials = qd_list

            blocked_list = []
            for b in data.get("blockedNumbers", []):
                try:
                    blocked_list.append(
                        BlockedNumberEntry(
                            number=b.get("number", ""), reason=b.get("reason", "")
                        )
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Skipping invalid blocked snapshot entry: %s", b)
            self.data.blocked_numbers = blocked_list

            priority_list = []
            for p in data.get("priorityCallers", []):
                try:
                    priority_list.append(
                        PriorityCallerEntry(
                            number=p.get("number", "") if isinstance(p, dict) else p
                        )
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Skipping invalid priority snapshot entry: %s", p)
            self.data.priority_callers = priority_list

            webhook_list = []
            for w in data.get("webhooks", []):
                try:
                    webhook_list.append(
                        WebhookEntry(
                            code=w.get("code", ""),
                            webhook_id=w.get("id", ""),
                            action_name=w.get("actionName", ""),
                            active=True,
                        )
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Skipping invalid webhook snapshot entry: %s", w)
            self.data.webhooks = webhook_list

            # Audio config
            audio = data.get("audioConfig", {})
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
            dnd = data.get("dndConfig", {})
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
                    if fw_key in dnd and hasattr(self.data.dnd_config, attr):
                        setattr(self.data.dnd_config, attr, dnd[fw_key])

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

        # Extract current call number if present (from addCallInfo/addPhoneStateInfo)
        if "currentCallNumber" in event.data and event.data["currentCallNumber"]:
            self.data.current_call.number = event.data["currentCallNumber"]

        # Extract dialing number if present
        if "currentDialingNumber" in event.data:
            self.data.current_dialing_number = event.data["currentDialingNumber"]

        # Extract state information if present
        if "state" in event.data:
            parsed_state = self._parse_app_state_value(
                event.data["state"], "event.context.state"
            )
            if parsed_state is not None:
                self.data.app_state = parsed_state

        if "previousState" in event.data:
            parsed_prev_state = self._parse_app_state_value(
                event.data["previousState"], "event.context.previousState"
            )
            if parsed_prev_state is not None:
                self.data.previous_app_state = parsed_prev_state

        # Extract DND status if present
        if "dndActive" in event.data:
            self.data.dnd_active = bool(event.data["dndActive"])

        # Extract maintenance mode if present
        if "isMaintenanceMode" in event.data:
            self.data.maintenance_mode = bool(event.data["isMaintenanceMode"])

        # Extract system metrics if present (from addSystemInfo)
        if "freeHeap" in event.data:
            self.data.stats.free_heap_bytes = event.data["freeHeap"]
        if "rssi" in event.data:
            self.data.stats.rssi_dbm = event.data["rssi"]
        if "uptime" in event.data:
            self.data.stats.uptime_seconds = event.data["uptime"]

        # Extract call info if present
        if "isIncomingCall" in event.data:
            self.data.current_call.is_incoming = bool(event.data["isIncomingCall"])

        # Extract call waiting info if available (firmware debt R61)
        if "callWaitingId" in event.data:
            # This would indicate firmware now exposes call waiting
            self.data.call_waiting_available = event.data["callWaitingId"] != -1

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
                "timestamp": event.received_at.isoformat(),
            },
        )

    def _fire_ha_event(self, event: TsuryPhoneEvent) -> None:
        """Fire Home Assistant event for device event."""
        base_data = event.to_ha_event_data()

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
                        "timestamp": event.received_at.isoformat(),
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
                        "timestamp": event.received_at.isoformat(),
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
                        "timestamp": event.received_at.isoformat(),
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
                            "timestamp": event.received_at.isoformat(),
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
                            "timestamp": event.received_at.isoformat(),
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
                                "timestamp": event.received_at.isoformat(),
                            },
                        )
                    else:
                        self.hass.bus.async_fire(
                            "tsuryphone_dnd_disabled",
                            {
                                "device_id": self.device_info.device_id,
                                "timestamp": event.received_at.isoformat(),
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
                            "timestamp": event.received_at.isoformat(),
                        },
                    )
                else:
                    self.hass.bus.async_fire(
                        "tsuryphone_maintenance_disabled",
                        {
                            "device_id": self.device_info.device_id,
                            "timestamp": event.received_at.isoformat(),
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
                        "timestamp": event.received_at.isoformat(),
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
                    "timestamp": event.received_at.isoformat(),
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

        # Update phone state and related lists
        if "phone" in device_data:
            phone_data = device_data["phone"]
            if "state" in phone_data:
                parsed_state = self._parse_app_state_value(
                    phone_data["state"], "device.phone.state"
                )
                if parsed_state is not None:
                    self.data.app_state = parsed_state
                else:
                    _LOGGER.error("Invalid app state: %s", phone_data["state"])

            # Previous state if provided
            if "previousState" in phone_data:
                parsed_prev = self._parse_app_state_value(
                    phone_data["previousState"], "device.phone.previousState"
                )
                if parsed_prev is not None:
                    self.data.previous_app_state = parsed_prev

            # Priority callers list
            if isinstance(phone_data.get("priorityCallers"), list):
                pr_list: list[PriorityCallerEntry] = []
                for item in phone_data.get("priorityCallers", []):
                    if isinstance(item, str) and item:
                        try:
                            pr_list.append(PriorityCallerEntry(number=item))
                        except ValueError:
                            pass
                self.data.priority_callers = pr_list

            # Current call priority flag if exposed in full phone state context
            if "currentCallIsPriority" in phone_data:
                self.data.current_call_is_priority = bool(
                    phone_data.get("currentCallIsPriority")
                )

        # Extract global fields that may appear outside phone section
        if "currentCallIsPriority" in device_data:
            self.data.current_call_is_priority = bool(
                device_data.get("currentCallIsPriority")
            )

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
