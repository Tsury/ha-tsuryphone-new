"""Diagnostics support for TsuryPhone integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN
from .coordinator import TsuryPhoneDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Keys to redact from diagnostics for privacy
REDACT_KEYS = {
    "host",
    "ip_address",
    "mac_address",
    "number",
    "phone_number",
    "caller_id",
    "name",
    "contact_name",
    "device_id",
    "serial_number",
    "webhook_url",
    "url",
    "api_key",
    "password",
    "token",
}

REDACT_PARTIAL_KEYS = {
    "device_name": 4,  # Show first 4 characters
    "ssid": 3,  # Show first 3 characters
    "firmware_version": -4,  # Show last 4 characters
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: TsuryPhoneDataUpdateCoordinator = entry.runtime_data

    # Get device registry info
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_entries = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    entity_entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)

    diagnostics_data = {
        "config_entry": {
            "title": entry.title,
            "version": entry.version,
            "domain": entry.domain,
            "state": entry.state.value,
            "source": entry.source,
            "data": async_redact_data(entry.data, REDACT_KEYS),
            "options": async_redact_data(entry.options, REDACT_KEYS),
            "unique_id": entry.unique_id,
        },
        "coordinator": await _async_get_coordinator_diagnostics(coordinator),
        "devices": [
            await _async_get_device_diagnostics(device_entry, coordinator)
            for device_entry in device_entries
        ],
        "entities": [
            await _async_get_entity_diagnostics(entity_entry, coordinator)
            for entity_entry in entity_entries
        ],
        "integration_info": _get_integration_info(coordinator),
    }

    return async_redact_data(diagnostics_data, REDACT_KEYS, REDACT_PARTIAL_KEYS)


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a specific device."""
    coordinator: TsuryPhoneDataUpdateCoordinator = entry.runtime_data

    device_diagnostics = {
        "device": await _async_get_device_diagnostics(device, coordinator),
        "coordinator": await _async_get_coordinator_diagnostics(coordinator),
        "device_specific_data": await _async_get_device_specific_data(coordinator),
    }

    return async_redact_data(device_diagnostics, REDACT_KEYS, REDACT_PARTIAL_KEYS)


async def _async_get_coordinator_diagnostics(
    coordinator: TsuryPhoneDataUpdateCoordinator,
) -> dict[str, Any]:
    """Get coordinator diagnostics data."""
    return {
        "update_success": coordinator.last_update_success,
        "update_time": (
            coordinator.last_update_time.isoformat()
            if coordinator.last_update_time
            else None
        ),
        "update_interval": coordinator.update_interval.total_seconds(),
        "data_available": coordinator.data is not None,
        "websocket_connected": (
            coordinator._websocket_client.connected
            if coordinator._websocket_client
            else False
        ),
        "websocket_stats": await _get_websocket_stats(coordinator),
        "api_client_stats": await _get_api_client_stats(coordinator),
        "call_timer_active": coordinator._call_timer_task is not None,
        "current_call_duration": coordinator.current_call_duration_seconds,
        "selected_quick_dial": coordinator.selected_quick_dial_code,
        "pending_call_starts": len(coordinator._pending_call_starts),
        "missed_call_detection": coordinator._missed_call_detection,
        "reboot_detected": (
            coordinator.data.reboot_detected if coordinator.data else False
        ),
    }


async def _async_get_device_diagnostics(
    device_entry: DeviceEntry, coordinator: TsuryPhoneDataUpdateCoordinator
) -> dict[str, Any]:
    """Get device diagnostics data."""
    return {
        "device_info": {
            "id": device_entry.id,
            "name": device_entry.name,
            "manufacturer": device_entry.manufacturer,
            "model": device_entry.model,
            "sw_version": device_entry.sw_version,
            "hw_version": device_entry.hw_version,
            "connections": device_entry.connections,
            "identifiers": device_entry.identifiers,
            "configuration_url": device_entry.configuration_url,
            "disabled": device_entry.disabled,
            "disabled_by": device_entry.disabled_by,
        },
        "coordinator_device_info": {
            "device_id": coordinator.device_info.device_id,
            "name": coordinator.device_info.name,
            "host": coordinator.device_info.host,
            "port": coordinator.device_info.port,
            "sw_version": coordinator.device_info.sw_version,
            "hw_version": coordinator.device_info.hw_version,
        },
    }


async def _async_get_entity_diagnostics(
    entity_entry: er.RegistryEntry, coordinator: TsuryPhoneDataUpdateCoordinator
) -> dict[str, Any]:
    """Get entity diagnostics data."""
    return {
        "entity_id": entity_entry.entity_id,
        "unique_id": entity_entry.unique_id,
        "platform": entity_entry.platform,
        "device_class": entity_entry.device_class,
        "name": entity_entry.name,
        "original_name": entity_entry.original_name,
        "disabled": entity_entry.disabled,
        "disabled_by": entity_entry.disabled_by,
        "entity_category": entity_entry.entity_category,
        "icon": entity_entry.icon,
        "original_icon": entity_entry.original_icon,
        "unit_of_measurement": entity_entry.unit_of_measurement,
        "capabilities": entity_entry.capabilities,
        "supported_features": entity_entry.supported_features,
        "device_info": entity_entry.device_info,
    }


async def _async_get_device_specific_data(
    coordinator: TsuryPhoneDataUpdateCoordinator,
) -> dict[str, Any]:
    """Get device-specific diagnostic data."""
    if not coordinator.data:
        return {"state": "no_data"}

    state = coordinator.data

    # Get current device diagnostics from API if available
    device_diagnostics = {}
    try:
        device_diagnostics = await coordinator.api_client.get_diagnostics()
    except Exception as err:
        device_diagnostics = {"error": f"Failed to get device diagnostics: {err}"}

    return {
        "device_state": {
            "connected": state.connected,
            "last_seen": state.last_seen,
            "app_state": state.app_state.value,
            "previous_app_state": (
                state.previous_app_state.value if state.previous_app_state else None
            ),
            "last_seq": state.last_seq,
            "reboot_detected": state.reboot_detected,
        },
        "call_state": {
            "is_ringing": state.is_ringing,
            "is_incoming_call": state.is_incoming_call,
            "is_call_active": state.is_call_active,
            "current_call": {
                "active": state.current_call.active,
                "number": "REDACTED" if state.current_call.number else None,
                "name": "REDACTED" if state.current_call.name else None,
                "is_incoming": state.current_call.is_incoming,
                "direction": state.current_call.direction,
                "priority": state.current_call.priority,
                "duration_seconds": state.current_call.duration_seconds,
                "duration_ms": state.current_call.duration_ms,
            },
            "last_call": {
                "available": state.last_call.available,
                "number": "REDACTED" if state.last_call.number else None,
                "name": "REDACTED" if state.last_call.name else None,
                "is_incoming": state.last_call.is_incoming,
                "direction": state.last_call.direction,
                "priority": state.last_call.priority,
                "result": state.last_call.result,
                "duration_seconds": state.last_call.duration_seconds,
                "duration_ms": state.last_call.duration_ms,
            },
        },
        "configuration": {
            "dnd_config": {
                "force": state.dnd_config.force,
                "scheduled": state.dnd_config.scheduled,
                "start_hour": state.dnd_config.start_hour,
                "start_minute": state.dnd_config.start_minute,
                "end_hour": state.dnd_config.end_hour,
                "end_minute": state.dnd_config.end_minute,
            },
            "audio_config": {
                "earpiece_volume": state.audio_config.earpiece_volume,
                "earpiece_gain": state.audio_config.earpiece_gain,
                "speaker_volume": state.audio_config.speaker_volume,
                "speaker_gain": state.audio_config.speaker_gain,
            },
            "ring_pattern": "REDACTED" if state.ring_pattern else None,
            "maintenance_mode": state.maintenance_mode,
        },
        "statistics": {
            "calls_total": state.stats.calls_total,
            "calls_incoming": state.stats.calls_incoming,
            "calls_outgoing": state.stats.calls_outgoing,
            "calls_blocked": state.stats.calls_blocked,
            "talk_time_seconds": state.stats.talk_time_seconds,
            "uptime_seconds": state.stats.uptime_seconds,
            "free_heap_bytes": state.stats.free_heap_bytes,
            "rssi_dbm": state.stats.rssi_dbm,
        },
        "lists": {
            "quick_dial_count": state.quick_dial_count,
            "blocked_count": state.blocked_count,
            "priority_count": (
                state.priority_count
                if hasattr(state, "priority_count")
                else len(getattr(state, "priority_callers", []) or [])
            ),
            "call_history_size": state.call_history_size,
            "webhook_count": len(state.webhooks) if state.webhooks else 0,
        },
        "device_diagnostics": device_diagnostics,
        # Phase P8: Add resilience status
        "resilience": coordinator.get_resilience_status(),
    }


async def _get_websocket_stats(
    coordinator: TsuryPhoneDataUpdateCoordinator,
) -> dict[str, Any]:
    """Get WebSocket connection statistics."""
    if not coordinator._websocket_client:
        return {"status": "not_initialized"}

    websocket = coordinator._websocket_client
    return {
        "connected": websocket.connected,
        "connection_attempts": getattr(websocket, "_connection_attempts", 0),
        "last_disconnect_time": coordinator._last_websocket_disconnect,
        "current_url": getattr(websocket, "_url", None),
        "events_processed": getattr(websocket, "_events_processed", 0),
        "reconnect_delay": getattr(websocket, "_reconnect_delay", 0),
        "queue_size": (
            getattr(websocket, "_message_queue_size", 0)
            if hasattr(websocket, "_message_queue_size")
            else 0
        ),
    }


async def _get_api_client_stats(
    coordinator: TsuryPhoneDataUpdateCoordinator,
) -> dict[str, Any]:
    """Get API client statistics."""
    api_client = coordinator.api_client
    return {
        "host": "REDACTED",
        "port": api_client.port,
        "base_url": "REDACTED",
        "requests_made": getattr(api_client, "_requests_made", 0),
        "last_request_time": getattr(api_client, "_last_request_time", None),
        "request_errors": getattr(api_client, "_request_errors", 0),
        "timeout_seconds": getattr(api_client, "_timeout", 10),
    }


def _get_integration_info(
    coordinator: TsuryPhoneDataUpdateCoordinator,
) -> dict[str, Any]:
    """Get integration-specific information."""
    return {
        "domain": DOMAIN,
        "platforms": [
            "binary_sensor",
            "sensor",
            "switch",
            "number",
            "select",
            "button",
        ],
        "services": [
            "dial",
            "answer",
            "hangup",
            "ring_device",
            "set_ring_pattern",
            "reset_device",
            "set_dnd",
            "set_audio",
            "get_call_history",
            "clear_call_history",
            "quick_dial_add",
            "quick_dial_remove",
            "quick_dial_clear",
            "blocked_add",
            "blocked_remove",
            "blocked_clear",
            "priority_add",
            "priority_remove",
            "refetch_all",
            "get_diagnostics",
            "webhook_add",
            "webhook_remove",
            "webhook_clear",
            "webhook_test",
            "switch_call_waiting",
            "set_maintenance_mode",
            "get_missed_calls",
            "quick_dial_import",
            "quick_dial_export",
            "blocked_import",
            "blocked_export",
        ],
        "features": {
            "websocket_support": True,
            "device_triggers": True,
            "device_conditions": True,
            "persistent_notifications": True,
            "options_flow": True,
            "diagnostics": True,
            "call_history_tracking": True,
            "missed_call_detection": True,
            "webhook_management": True,
            "bulk_import_export": True,
            "hybrid_list_management": True,
        },
        "notification_manager": {
            "active": hasattr(coordinator, "_notification_manager"),
            "notification_types": [
                "maintenance_mode",
                "device_offline",
                "device_reboot",
                "missed_calls",
            ],
        },
    }


def get_diagnostic_summary(
    coordinator: TsuryPhoneDataUpdateCoordinator,
) -> dict[str, Any]:
    """Get a summary of diagnostic information for quick troubleshooting."""
    if not coordinator.data:
        return {"status": "no_data", "coordinator_available": True}

    state = coordinator.data

    # Calculate health score based on various factors
    health_factors = {
        "device_connected": state.connected,
        "coordinator_success": coordinator.last_update_success,
        "websocket_connected": (
            coordinator._websocket_client.connected
            if coordinator._websocket_client
            else False
        ),
        "no_recent_errors": True,  # Would be calculated from error logs
        "reasonable_uptime": state.stats.uptime_seconds > 60,
        "good_signal": (
            state.stats.rssi_dbm > -80 if state.stats.rssi_dbm != 0 else None
        ),
    }

    health_score = sum(1 for factor in health_factors.values() if factor is True)
    max_score = sum(1 for factor in health_factors.values() if factor is not None)
    health_percentage = (health_score / max_score * 100) if max_score > 0 else 0

    return {
        "status": (
            "healthy"
            if health_percentage >= 80
            else "warning" if health_percentage >= 60 else "error"
        ),
        "health_percentage": health_percentage,
        "health_factors": health_factors,
        "quick_stats": {
            "device_connected": state.connected,
            "app_state": state.app_state.value,
            "call_active": state.is_call_active,
            "dnd_active": state.dnd_active,
            "maintenance_mode": state.maintenance_mode,
            "uptime_hours": round(state.stats.uptime_seconds / 3600, 1),
            "signal_dbm": state.stats.rssi_dbm if state.stats.rssi_dbm != 0 else None,
            "total_calls": state.stats.calls_total,
            "missed_calls": len([c for c in (state.call_history or []) if c.missed]),
            "websocket_connected": (
                coordinator._websocket_client.connected
                if coordinator._websocket_client
                else False
            ),
            "last_update": (
                coordinator.last_update_time.isoformat()
                if coordinator.last_update_time
                else None
            ),
        },
        "recommendations": _get_diagnostic_recommendations(state, coordinator),
    }


def _get_diagnostic_recommendations(
    state, coordinator: TsuryPhoneDataUpdateCoordinator
) -> list[str]:
    """Get recommendations based on diagnostic data."""
    recommendations = []

    if not state.connected:
        recommendations.append(
            "Device is offline - check power and network connectivity"
        )

    if not coordinator.last_update_success:
        recommendations.append("Coordinator updates failing - check API connectivity")

    if coordinator._websocket_client and not coordinator._websocket_client.connected:
        recommendations.append(
            "WebSocket disconnected - real-time updates may be delayed"
        )

    if state.stats.rssi_dbm != 0 and state.stats.rssi_dbm < -80:
        recommendations.append(
            "Poor WiFi signal strength - consider improving network coverage"
        )

    if state.stats.free_heap_bytes < 10000:
        recommendations.append(
            "Low device memory - consider reboot or reduced polling frequency"
        )

    if state.maintenance_mode:
        recommendations.append(
            "Device in maintenance mode - normal operations may be affected"
        )

    recent_missed_calls = len([c for c in (state.call_history or []) if c.missed])
    if recent_missed_calls > 5:
        recommendations.append(
            f"{recent_missed_calls} recent missed calls - check DND settings and device availability"
        )

    if not recommendations:
        recommendations.append("All systems appear to be functioning normally")

    return recommendations
