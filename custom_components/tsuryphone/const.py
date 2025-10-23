"""Constants for the TsuryPhone integration."""

from enum import IntEnum, StrEnum
from typing import Final

# Domain and basic config
DOMAIN: Final = "tsuryphone"
MANUFACTURER: Final = "TsuryTech"
MODEL: Final = "TsuryPhone"

# Network configuration
DEFAULT_PORT: Final = 8080
WEBSOCKET_PATH: Final = "/ws"

# Integration event schema
INTEGRATION_EVENT_SCHEMA_VERSION: Final = 3
INTEGRATION_TAG: Final = "ha"

# mDNS discovery
MDNS_SERVICE_TYPE: Final = "_http._tcp.local."
MDNS_DEVICE_TYPE: Final = "tsuryphone"

# Timing constants (milliseconds)
WEBSOCKET_RECONNECT_DELAY: Final = 5000  # 5 seconds
WEBSOCKET_MAX_BACKOFF: Final = 60000  # 60 seconds
POLLING_FALLBACK_INTERVAL: Final = 30  # 30 seconds
REFETCH_INTERVAL_DEFAULT: Final = 300  # 5 minutes

# Event processing
EVENT_HISTORY_SIZE_DEFAULT: Final = 300
EVENT_QUEUE_MAX_SIZE: Final = 500
SERVICE_CONCURRENCY_LIMIT: Final = 4

# Validation limits (from firmware Validation.h)
MAX_CODE_LENGTH: Final = 16
MAX_NUMBER_LENGTH: Final = 24
MAX_PATTERN_LENGTH: Final = 32
MAX_BLOCKED_NAME_LENGTH: Final = 64
MAX_NAME_LENGTH: Final = 32
MAX_DIALING_CODE_LENGTH: Final = 6

# Ring pattern defaults
DEFAULT_RING_DURATION: Final = 2000  # milliseconds
RING_CYCLE_DURATION: Final = 30  # milliseconds (from firmware)

# Audio configuration ranges
AUDIO_MIN_LEVEL: Final = 1
AUDIO_MAX_LEVEL: Final = 7

# Service attribute keys
ATTR_DEVICE_ID: Final = "device_id"


# Device state enum (matches firmware state.h)
class AppState(IntEnum):
    """Device application states (matches firmware AppState enum)."""

    STARTUP = 0
    CHECK_HARDWARE = 1
    CHECK_LINE = 2
    IDLE = 3
    INVALID_NUMBER = 4
    INCOMING_CALL = 5
    INCOMING_CALL_RING = 6
    IN_CALL = 7
    DIALING = 8


class VolumeMode(StrEnum):
    """Audio routing modes exposed by the firmware."""

    EARPIECE = "earpiece"
    SPEAKER = "speaker"
    UNKNOWN = "unknown"


# Event categories (from firmware IntegrationService)
class EventCategory(StrEnum):
    """Event categories for device events."""

    CALL = "call"
    PHONE_STATE = "phone_state"
    SYSTEM = "system"
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


# Event types for each category
class CallEvent(StrEnum):
    """Call event types."""

    START = "start"
    END = "end"
    BLOCKED = "blocked"


class PhoneStateEvent(StrEnum):
    """Phone state event types."""

    STATE = "state"
    DIALING = "dialing"
    RING = "ring"
    RINGING = "ringing"
    IDLE = "idle"
    DND = "dnd"
    CALL_INFO = "call_info"


class SystemEvent(StrEnum):
    """System event types."""

    STATS = "stats"
    STATUS = "status"
    ERROR = "error"
    SHUTDOWN = "shutdown"


class ConfigEvent(StrEnum):
    """Config event types."""

    CONFIG_DELTA = "config_delta"


class DiagnosticEvent(StrEnum):
    """Diagnostic event types."""

    SNAPSHOT = "snapshot"


# Ring pattern presets (from design document)
RING_PATTERN_PRESETS: Final = {
    "Default": "",  # Device native
    "Pulse Short": "300,300x2",
    "Classic": "500,500,500",
    "Long Gap": "800,400,800",
    "Triple": "300,300,300",
    "Stagger": "500,250,500",
    "Alarm": "200,200x5",
    "Slow": "1000",
    "Burst": "150,150x3",
}

RING_PATTERN_PRESET_LABELS: Final = {
    name: f"{name} ({pattern})" if pattern else f"{name} (device default)"
    for name, pattern in RING_PATTERN_PRESETS.items()
}

# Home Assistant event names
HA_EVENT_CALL_START: Final = "tsuryphone_call_start"
HA_EVENT_CALL_END: Final = "tsuryphone_call_end"
HA_EVENT_CALL_BLOCKED: Final = "tsuryphone_call_blocked"
HA_EVENT_CALL_MISSED: Final = "tsuryphone_call_missed"
HA_EVENT_PHONE_STATE: Final = "tsuryphone_phone_state_{}"  # Format with event type
HA_EVENT_SYSTEM: Final = "tsuryphone_system_{}"  # Format with event type
HA_EVENT_CONFIG_DELTA: Final = "tsuryphone_config_delta"
HA_EVENT_DIAGNOSTIC_SNAPSHOT: Final = "tsuryphone_diagnostic_snapshot"
HA_EVENT_WEBHOOK_ACTION: Final = "tsuryphone_webhook_action"

# Configuration keys for options flow
CONF_HOST_OVERRIDE: Final = "host_override"
CONF_POLLING_FALLBACK_SECONDS: Final = "polling_fallback_seconds"
CONF_REFETCH_INTERVAL_MINUTES: Final = "refetch_interval_minutes"
CONF_VERBOSE_EVENTS: Final = "verbose_events"
CONF_EVENT_HISTORY_SIZE: Final = "event_history_size"
CONF_MISSED_CALL_NOTIFICATION: Final = "missed_call_notification"

# Audio configuration keys
CONF_EARPIECE_VOLUME: Final = "earpiece_volume"
CONF_EARPIECE_GAIN: Final = "earpiece_gain"
CONF_SPEAKER_VOLUME: Final = "speaker_volume"
CONF_SPEAKER_GAIN: Final = "speaker_gain"

# Ring pattern configuration
CONF_RING_PATTERN_MODE: Final = "ring_pattern_mode"
CONF_RING_PATTERN_VALUE: Final = "ring_pattern_value"

# DND configuration
CONF_DND_FORCE: Final = "dnd_force"
CONF_DND_SCHEDULE_ENABLED: Final = "dnd_schedule_enabled"
CONF_DND_START_HOUR: Final = "dnd_start_hour"
CONF_DND_START_MINUTE: Final = "dnd_start_minute"
CONF_DND_END_HOUR: Final = "dnd_end_hour"
CONF_DND_END_MINUTE: Final = "dnd_end_minute"

# Maintenance mode
CONF_MAINTENANCE_MODE_DEFAULT: Final = "maintenance_mode_default"

# API endpoints
API_CONFIG_TSURYPHONE: Final = "/api/config/tsuryphone"
API_REFETCH_ALL: Final = "/api/refetch_all"
API_DIAGNOSTICS: Final = "/api/diagnostics"
API_CALL_DIAL: Final = "/api/call/dial"
API_CALL_DIAL_DIGIT: Final = "/api/call/dial_digit"
API_CALL_ANSWER: Final = "/api/call/answer"
API_CALL_HANGUP: Final = "/api/call/hangup"
API_CALL_SWITCH_CALL_WAITING: Final = "/api/call/switch_call_waiting"
API_CALL_TOGGLE_VOLUME_MODE: Final = "/api/call/toggle_volume_mode"
API_SYSTEM_RESET: Final = "/api/system/reset"
API_SYSTEM_FACTORY_RESET: Final = "/api/system/factory_reset"
API_SYSTEM_RING: Final = "/api/system/ring"
API_CONFIG_DND: Final = "/api/config/dnd"
API_CONFIG_MAINTENANCE: Final = "/api/config/maintenance"
API_CONFIG_AUDIO: Final = "/api/config/audio"
API_CONFIG_RING_PATTERN: Final = "/api/config/ring_pattern"
API_CONFIG_DIALING: Final = "/api/config/dialing"
API_CALL_DIAL_QUICK_DIAL: Final = "/api/call/dial_quick_dial"
API_CONFIG_QUICK_DIAL_ADD: Final = "/api/config/quick_dial_add"
API_CONFIG_QUICK_DIAL_REMOVE: Final = "/api/config/quick_dial_remove"
API_CONFIG_WEBHOOK_ADD: Final = "/api/config/webhook_add"
API_CONFIG_WEBHOOK_REMOVE: Final = "/api/config/webhook_remove"
API_CONFIG_BLOCKED_ADD: Final = "/api/config/blocked_add"
API_CONFIG_BLOCKED_REMOVE: Final = "/api/config/blocked_remove"
API_CONFIG_HA_URL: Final = "/api/config/ha_url"
API_CONFIG_PRIORITY_ADD: Final = "/api/config/priority_add"
API_CONFIG_PRIORITY_REMOVE: Final = "/api/config/priority_remove"

# Error codes (from firmware)
ERROR_CODE_INVALID_NUMBER: Final = "WEB_INVALID_NUMBER"
ERROR_CODE_PHONE_NOT_READY: Final = "PHONE_NOT_READY"
ERROR_CODE_NO_INCOMING_CALL: Final = "NO_INCOMING_CALL"
ERROR_CODE_NO_ACTIVE_CALL: Final = "NO_ACTIVE_CALL"
ERROR_CODE_INVALID_PATTERN: Final = "INVALID_PATTERN"
ERROR_CODE_CALL_WAITING_NOT_AVAILABLE: Final = "CALL_WAITING_NOT_AVAILABLE"
ERROR_CODE_INVALID_JSON: Final = "WEB_INVALID_JSON"
ERROR_CODE_MISSING_CODE: Final = "WEB_MISSING_CODE"
ERROR_CODE_INVALID_CODE: Final = "WEB_INVALID_CODE"
ERROR_CODE_CODE_CONFLICT: Final = "WEB_CODE_CONFLICT"
ERROR_CODE_MISSING_NUMBER: Final = "WEB_MISSING_NUMBER"
ERROR_CODE_MISSING_DEFAULT_CODE: Final = "WEB_MISSING_DEFAULT_CODE"
ERROR_CODE_INVALID_DEFAULT_CODE: Final = "WEB_INVALID_DEFAULT_CODE"
ERROR_CODE_SERVICE_UNAVAILABLE: Final = "WEB_SERVICE_UNAVAILABLE"
ERROR_CODE_MISSING_ENABLED: Final = "WEB_MISSING_ENABLED"
ERROR_CODE_INVALID_PATTERN: Final = "WEB_INVALID_PATTERN"
ERROR_CODE_AUDIO_PARAM_REQUIRED: Final = "WEB_AUDIO_PARAM_REQUIRED"
ERROR_CODE_INVALID_URL: Final = "WEB_INVALID_URL"
ERROR_CODE_MISSING_DIGIT: Final = "WEB_MISSING_DIGIT"
ERROR_CODE_INVALID_DIGIT: Final = "WEB_INVALID_DIGIT"
ERROR_CODE_DIAL_BUFFER_FULL: Final = "WEB_DIAL_BUFFER_FULL"

# Service names
SERVICE_DIAL: Final = "dial"
SERVICE_DIAL_DIGIT: Final = "dial_digit"
SERVICE_ANSWER: Final = "answer"
SERVICE_HANGUP: Final = "hangup"
SERVICE_RING_DEVICE: Final = "ring_device"
SERVICE_SET_RING_PATTERN: Final = "set_ring_pattern"
SERVICE_RESET_DEVICE: Final = "reset_device"
SERVICE_FACTORY_RESET_DEVICE: Final = "factory_reset_device"
SERVICE_SET_DND: Final = "set_dnd"
SERVICE_SET_AUDIO: Final = "set_audio"
SERVICE_SET_DIALING_CONFIG: Final = "set_dialing_config"
SERVICE_GET_CALL_HISTORY: Final = "get_call_history"
SERVICE_CLEAR_CALL_HISTORY: Final = "clear_call_history"
SERVICE_GET_TSURYPHONE_CONFIG: Final = "get_tsuryphone_config"
SERVICE_QUICK_DIAL_ADD: Final = "quick_dial_add"
SERVICE_QUICK_DIAL_REMOVE: Final = "quick_dial_remove"
SERVICE_QUICK_DIAL_CLEAR: Final = "quick_dial_clear"
SERVICE_BLOCKED_ADD: Final = "blocked_add"
SERVICE_BLOCKED_REMOVE: Final = "blocked_remove"
SERVICE_BLOCKED_CLEAR: Final = "blocked_clear"
SERVICE_PRIORITY_ADD: Final = "priority_add"
SERVICE_PRIORITY_REMOVE: Final = "priority_remove"
SERVICE_REFETCH_ALL: Final = "refetch_all"
SERVICE_GET_DIAGNOSTICS: Final = "get_diagnostics"
SERVICE_WEBHOOK_ADD: Final = "webhook_add"
SERVICE_WEBHOOK_REMOVE: Final = "webhook_remove"
SERVICE_WEBHOOK_CLEAR: Final = "webhook_clear"
SERVICE_WEBHOOK_TEST: Final = "webhook_test"
SERVICE_SWITCH_CALL_WAITING: Final = "switch_call_waiting"
SERVICE_TOGGLE_VOLUME_MODE: Final = "toggle_volume_mode"
SERVICE_SET_MAINTENANCE_MODE: Final = "set_maintenance_mode"
SERVICE_GET_MISSED_CALLS: Final = "get_missed_calls"
SERVICE_DIAL_QUICK_DIAL: Final = "dial_quick_dial"
SERVICE_SET_HA_URL: Final = "set_ha_url"

# Phase P4: Bulk import/export services
SERVICE_QUICK_DIAL_IMPORT: Final = "quick_dial_import"
SERVICE_QUICK_DIAL_EXPORT: Final = "quick_dial_export"
SERVICE_BLOCKED_IMPORT: Final = "blocked_import"
SERVICE_BLOCKED_EXPORT: Final = "blocked_export"

# Phase P8: Resilience and testing services
SERVICE_RESILIENCE_STATUS: Final = "resilience_status"
SERVICE_RESILIENCE_TEST: Final = "resilience_test"
SERVICE_WEBSOCKET_RECONNECT: Final = "websocket_reconnect"
SERVICE_RUN_HEALTH_CHECK: Final = "run_health_check"

# Notification IDs
NOTIFICATION_ID_SYSTEM_ERROR: Final = "tsuryphone_sys_error"
NOTIFICATION_ID_MAINTENANCE: Final = "tsuryphone_maint"
NOTIFICATION_ID_REBOOT: Final = "tsuryphone_reboot"
NOTIFICATION_ID_MISSED_CALLS: Final = "tsuryphone_missed_calls"


# State machine derived states
def is_call_active(app_state: AppState) -> bool:
    """Check if device is in an active call state."""
    return app_state == AppState.IN_CALL


def is_ringing(app_state: AppState) -> bool:
    """Check if device is in a ringing state."""
    return app_state == AppState.INCOMING_CALL_RING


def is_dialing(app_state: AppState) -> bool:
    """Check if device is in a dialing state."""
    return app_state == AppState.DIALING


def is_incoming_call(app_state: AppState) -> bool:
    """Check if device has an incoming call."""
    return app_state in (AppState.INCOMING_CALL, AppState.INCOMING_CALL_RING)
