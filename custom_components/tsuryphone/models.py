"""Data models for the TsuryPhone integration."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from datetime import datetime

from homeassistant.util import dt as dt_util

from .const import AppState, EventCategory, INTEGRATION_EVENT_SCHEMA_VERSION, VolumeMode
from .dialing import DialingContext


class CallDirection(str, Enum):
    """Call direction values used across the integration."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"
    BLOCKED = "blocked"
    MISSED = "missed"


@dataclass
class DeviceInfo:
    """Basic device information."""

    device_id: str
    host: str
    port: int = 8080
    name: str = "TsuryPhone"
    sw_version: str | None = None
    hw_version: str | None = None


@dataclass
class CallInfo:
    """Information about an active or recent call."""

    number: str = ""
    normalized_number: str = ""
    is_incoming: bool = False
    direction: str = ""
    result: str = ""
    is_priority: bool = False
    duration_seconds: int | None = None
    start_time: int = 0  # milliseconds since device boot
    call_start_ts: int = 0  # Device timestamp from firmware (callStartTs)
    duration_ms: int | None = None
    call_id: int = -1
    call_waiting_id: int = -1
    call_type: str = ""
    name: str = ""
    is_on_hold: bool = False
    is_blocked: bool = False
    leg_label: str = ""
    start_received_ts: float | None = None
    end_received_ts: float | None = None


@dataclass
class QuickDialEntry:
    """Quick dial configuration entry."""

    id: str
    number: str  # Normalized E.164 format
    name: str = ""
    code: str = ""  # Optional code for quick dialing
    display_number: str = ""

    def __post_init__(self) -> None:
        """Validate entry after initialization."""
        if not self.id or not self.number:
            raise ValueError("ID and number are required for quick dial entry")
        if not self.display_number:
            self.display_number = self.number


@dataclass
class BlockedNumberEntry:
    """Blocked number configuration entry."""

    id: str
    number: str  # Normalized E.164 format
    name: str = ""
    display_number: str = ""

    def __post_init__(self) -> None:
        """Validate entry after initialization."""
        if not self.id or not self.number:
            raise ValueError("ID and number are required for blocked number entry")
        if not self.display_number:
            self.display_number = self.number


@dataclass
class WebhookEntry:
    """Webhook action configuration entry."""

    code: str
    webhook_id: str
    action_name: str = ""
    active: bool = True  # Whether webhook is active/reachable
    events: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate entry after initialization."""
        if not self.code or not self.webhook_id:
            raise ValueError("Code and webhook_id are required for webhook entry")
        # Normalize events to strings
        normalized_events: list[str] = []
        for event in self.events:
            if not event:
                continue
            normalized_events.append(str(event))
        self.events = normalized_events


@dataclass
class PriorityCallerEntry:
    """Priority caller entry."""

    id: str
    number: str  # Normalized E.164 format
    display_number: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.number:
            raise ValueError("ID and number are required for priority caller entry")
        if not self.display_number:
            self.display_number = self.number
        if not self.display_number:
            self.display_number = self.number


@dataclass
class AudioConfig:
    """Audio configuration settings."""

    earpiece_volume: int = 4
    earpiece_gain: int = 4
    speaker_volume: int = 4
    speaker_gain: int = 4

    def __post_init__(self) -> None:
        """Validate audio levels are in valid range (1-7)."""
        for field_name, value in [
            ("earpiece_volume", self.earpiece_volume),
            ("earpiece_gain", self.earpiece_gain),
            ("speaker_volume", self.speaker_volume),
            ("speaker_gain", self.speaker_gain),
        ]:
            if not 1 <= value <= 7:
                raise ValueError(f"{field_name} must be between 1 and 7, got {value}")


@dataclass
class DNDConfig:
    """Do Not Disturb configuration."""

    force: bool = False
    scheduled: bool = False
    start_hour: int = 22
    start_minute: int = 0
    end_hour: int = 7
    end_minute: int = 0

    def __post_init__(self) -> None:
        """Validate time fields."""
        if not 0 <= self.start_hour <= 23:
            raise ValueError(f"start_hour must be 0-23, got {self.start_hour}")
        if not 0 <= self.end_hour <= 23:
            raise ValueError(f"end_hour must be 0-23, got {self.end_hour}")
        if not 0 <= self.start_minute <= 59:
            raise ValueError(f"start_minute must be 0-59, got {self.start_minute}")
        if not 0 <= self.end_minute <= 59:
            raise ValueError(f"end_minute must be 0-59, got {self.end_minute}")


@dataclass
class DeviceStats:
    """Device statistics from firmware."""

    calls_total: int = 0
    calls_incoming: int = 0
    calls_outgoing: int = 0
    calls_blocked: int = 0
    talk_time_seconds: int = 0
    uptime_seconds: int = 0
    free_heap_bytes: int = 0
    rssi_dbm: int = 0


@dataclass
class CallHistoryEntry:
    """Single call history entry.
    
    IMPORTANT: ts_device and call_start_ts are device uptime in milliseconds (from millis())
    NOT Unix epoch timestamps! For age/retention logic, always use received_ts.
    The timestamp property converts ts_device incorrectly and should not be used for cleanup.
    """

    call_type: str  # "incoming", "outgoing", "blocked", "missed"
    number: str
    is_incoming: bool
    duration_s: int | None
    ts_device: int  # Device uptime in milliseconds (NOT epoch timestamp!)
    received_ts: float  # HA timestamp when received (Unix epoch, use this for retention!)
    seq: int
    call_start_ts: int = 0  # Firmware callStartTs field (device uptime in ms)
    duration_ms: int | None = None  # Firmware durationMs field
    reason: str | None = None
    synthetic: bool = False  # True if start was synthesized from end-only
    name: str = ""

    def __post_init__(self) -> None:
        """Set received timestamp if not provided."""
        if self.received_ts == 0:
            self.received_ts = time.time()

    @property
    def timestamp(self) -> datetime | None:
        """Get timestamp as datetime object."""
        if not self.ts_device:
            return None

        try:
            ts_value = float(self.ts_device)
        except (TypeError, ValueError):
            return None

        # Firmware timestamps are typically in seconds, but handle millisecond inputs.
        if ts_value > 1_000_000_000_000:  # larger than year 33658 in seconds
            ts_value /= 1000.0

        try:
            return dt_util.utc_from_timestamp(ts_value)
        except (ValueError, OSError):
            return None

    @property
    def missed(self) -> bool:
        """Check if this call was missed."""
        return self.call_type == "missed"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "call_type": self.call_type,
            "number": self.number,
            "is_incoming": self.is_incoming,
            "duration_s": self.duration_s,
            "ts_device": self.ts_device,
            "received_ts": self.received_ts,
            "seq": self.seq,
            "call_start_ts": self.call_start_ts,
            "duration_ms": self.duration_ms,
            "reason": self.reason,
            "synthetic": self.synthetic,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CallHistoryEntry:
        """Create from dictionary."""
        return cls(
            call_type=data["call_type"],
            number=data["number"],
            is_incoming=data["is_incoming"],
            duration_s=data.get("duration_s"),
            ts_device=data["ts_device"],
            received_ts=data["received_ts"],
            seq=data["seq"],
            call_start_ts=data.get("call_start_ts", 0),
            duration_ms=data.get("duration_ms"),
            reason=data.get("reason"),
            synthetic=data.get("synthetic", False),
            name=data.get("name", ""),
        )


@dataclass
class TsuryPhoneState:
    """Complete state model for the TsuryPhone device."""

    # Device identification
    device_info: DeviceInfo

    # Connection state
    connected: bool = False
    last_seen: float = field(default_factory=time.time)

    # App state
    app_state: AppState = AppState.STARTUP
    previous_app_state: AppState = AppState.STARTUP

    # Call information
    current_call: CallInfo = field(default_factory=CallInfo)
    waiting_call: CallInfo = field(default_factory=CallInfo)
    last_call: CallInfo = field(default_factory=CallInfo)
    current_dialing_number: str = ""

    # Phone state flags
    ringing: bool = False
    dnd_active: bool = False
    maintenance_mode: bool = False
    hook_off: bool = False
    call_waiting_available: bool = False
    call_waiting_on_hold: bool = False
    volume_mode: str = VolumeMode.EARPIECE.value
    volume_mode_code: int = 0
    is_speaker_mode: bool = False
    is_muted: bool = False

    # Device stats
    stats: DeviceStats = field(default_factory=DeviceStats)

    # Configuration
    audio_config: AudioConfig = field(default_factory=AudioConfig)
    dnd_config: DNDConfig = field(default_factory=DNDConfig)
    ring_pattern: str = ""
    default_dialing_code: str = ""
    default_dialing_prefix: str = ""

    # Lists (managed via services and options)
    quick_dials: list[QuickDialEntry] = field(default_factory=list)
    blocked_numbers: list[BlockedNumberEntry] = field(default_factory=list)
    webhooks: list[WebhookEntry] = field(default_factory=list)
    priority_callers: list[PriorityCallerEntry] = field(default_factory=list)
    current_call_is_priority: bool = False

    # Event processing
    last_seq: int = 0
    reboot_detected: bool = False

    # Call history (rolling buffer)
    call_history: list[CallHistoryEntry] = field(default_factory=list)
    call_history_capacity: int = 500
    call_state_revision: int = 0
    call_state_updated_at: float = field(default_factory=time.time)

    # State derived properties
    @property
    def is_call_active(self) -> bool:
        """True if device is in an active call."""
        return self.app_state == AppState.IN_CALL

    @property
    def is_incoming_call(self) -> bool:
        """True if device has an incoming call."""
        return self.app_state in (AppState.INCOMING_CALL, AppState.INCOMING_CALL_RING)

    @property
    def is_dialing(self) -> bool:
        """True if device is dialing."""
        return self.app_state == AppState.DIALING

    @property
    def volume_mode_enum(self) -> VolumeMode:
        """Return volume mode as VolumeMode enum."""
        try:
            return VolumeMode(self.volume_mode)
        except ValueError:
            return VolumeMode.UNKNOWN

    @property
    def volume_mode_label(self) -> str:
        """Human-friendly label for current audio routing mode."""
        mode = self.volume_mode_enum
        if mode is VolumeMode.SPEAKER:
            return "Speaker"
        if mode is VolumeMode.EARPIECE:
            return "Earpiece"
        return "Unknown"

    @property
    def quick_dial_count(self) -> int:
        """Number of configured quick dial entries."""
        return len(self.quick_dials)

    @property
    def blocked_count(self) -> int:
        """Number of blocked numbers."""
        return len(self.blocked_numbers)

    @property
    def priority_count(self) -> int:
        """Number of priority caller numbers."""
        return len(self.priority_callers)

    @property
    def last_blocked_number(self) -> str:
        """Most recently blocked number from history."""
        for entry in reversed(self.call_history):
            if entry.call_type == "blocked":
                return entry.number
        return ""

    @property
    def call_history_size(self) -> int:
        """Current call history size."""
        return len(self.call_history)

    @property
    def current_call_direction(self) -> str | None:
        """Return the direction of the current call or dialing session."""
        if self.current_call.direction:
            return self.current_call.direction
        if self.app_state in (AppState.INCOMING_CALL, AppState.INCOMING_CALL_RING):
            return "incoming"
        if self.app_state in (AppState.DIALING, AppState.IN_CALL):
            return "incoming" if self.current_call.is_incoming else "outgoing"
        return None

    def add_call_history_entry(self, entry: CallHistoryEntry) -> None:
        """Add entry to call history with capacity management."""
        self.call_history.append(entry)

        # Enforce capacity limit (newest entries kept)
        if len(self.call_history) > self.call_history_capacity:
            self.call_history = self.call_history[-self.call_history_capacity :]

    def get_quick_dial_by_code(self, code: str) -> QuickDialEntry | None:
        """Find quick dial entry by code."""
        for entry in self.quick_dials:
            if entry.code == code:
                return entry
        return None

    def get_blocked_number(self, number: str) -> BlockedNumberEntry | None:
        """Find blocked number entry."""
        for entry in self.blocked_numbers:
            if entry.number == number:
                return entry
        return None

    def get_webhook_by_code(self, code: str) -> WebhookEntry | None:
        """Find webhook entry by code."""
        for entry in self.webhooks:
            if entry.code == code:
                return entry
        return None

    def mark_call_state_changed(self) -> None:
        """Register that current/last call data has changed."""
        self.call_state_revision += 1
        self.call_state_updated_at = time.time()

    @property
    def dialing_context(self) -> DialingContext:
        """Return the dialing context for helper utilities."""
        return DialingContext(
            default_code=self.default_dialing_code or "",
            default_prefix=self.default_dialing_prefix
            or (f"+{self.default_dialing_code}" if self.default_dialing_code else ""),
        )


@dataclass
class TsuryPhoneEvent:
    """Represents a device event from WebSocket or generated internally."""

    schema_version: int
    seq: int
    ts: int  # Device timestamp
    integration: str
    device_id: str
    category: str  # String category from firmware (not enum)
    event: str
    data: dict[str, Any] = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TsuryPhoneEvent:
        """Create event from JSON data."""
        return cls(
            schema_version=data.get("schemaVersion", INTEGRATION_EVENT_SCHEMA_VERSION),
            seq=data.get("seq", 0),
            ts=data.get("ts", 0),
            integration=data.get("integration", "ha"),
            device_id=data.get("deviceId", ""),
            category=data.get("category", ""),  # Keep as string per firmware
            event=data.get("event", ""),
            data={
                k: v
                for k, v in data.items()
                if k
                not in {
                    "schemaVersion",
                    "seq",
                    "ts",
                    "integration",
                    "deviceId",
                    "category",
                    "event",
                }
            },
        )

    def to_ha_event_data(self) -> dict[str, Any]:
        """Convert to Home Assistant event data format."""
        return {
            "seq": self.seq,
            "ts": self.ts,
            "device_id": self.device_id,
            "category": self.category,
            "event": self.event,
            **self.data,
        }
