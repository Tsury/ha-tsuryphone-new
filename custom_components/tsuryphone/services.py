"""Services for TsuryPhone integration."""

from __future__ import annotations

import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.exceptions import ServiceValidationError, HomeAssistantError
from homeassistant.helpers.entity_component import EntityComponent

from .const import (
    DOMAIN,
    SERVICE_DIAL,
    SERVICE_ANSWER,
    SERVICE_HANGUP,
    SERVICE_RING_DEVICE,
    SERVICE_SET_RING_PATTERN,
    SERVICE_RESET_DEVICE,
    SERVICE_SET_DND,
    SERVICE_SET_AUDIO,
    SERVICE_GET_CALL_HISTORY,
    SERVICE_CLEAR_CALL_HISTORY,
    SERVICE_QUICK_DIAL_ADD,
    SERVICE_QUICK_DIAL_REMOVE,
    SERVICE_QUICK_DIAL_CLEAR,
    SERVICE_BLOCKED_ADD,
    SERVICE_BLOCKED_REMOVE,
    SERVICE_BLOCKED_CLEAR,
    SERVICE_PRIORITY_ADD,
    SERVICE_PRIORITY_REMOVE,
    SERVICE_REFETCH_ALL,
    SERVICE_GET_DIAGNOSTICS,
    SERVICE_WEBHOOK_ADD,
    SERVICE_WEBHOOK_REMOVE,
    SERVICE_WEBHOOK_CLEAR,
    SERVICE_WEBHOOK_TEST,
    SERVICE_SWITCH_CALL_WAITING,
    SERVICE_SET_MAINTENANCE_MODE,
    SERVICE_GET_MISSED_CALLS,
    SERVICE_QUICK_DIAL_IMPORT,
    SERVICE_QUICK_DIAL_EXPORT,
    SERVICE_BLOCKED_IMPORT,
    SERVICE_BLOCKED_EXPORT,
    SERVICE_RESILIENCE_STATUS,
    SERVICE_RESILIENCE_TEST,
    SERVICE_WEBSOCKET_RECONNECT,
    SERVICE_RUN_HEALTH_CHECK,
    AUDIO_MIN_LEVEL,
    AUDIO_MAX_LEVEL,
    RING_PATTERN_PRESETS,
    ATTR_DEVICE_ID,
)
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .api_client import TsuryPhoneAPIError

_LOGGER = logging.getLogger(__name__)

# Service schemas
DIAL_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("number"): cv.string,
    }
)

RING_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("pattern", default=""): cv.string,
    }
)

SET_RING_PATTERN_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("pattern"): cv.string,
    }
)

SET_DND_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("force"): cv.boolean,
        vol.Optional("scheduled"): cv.boolean,
        vol.Optional("start_hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Optional("start_minute"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=59)
        ),
        vol.Optional("end_hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Optional("end_minute"): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
    }
)

SET_AUDIO_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("earpiece_volume"): vol.All(
            vol.Coerce(int), vol.Range(min=AUDIO_MIN_LEVEL, max=AUDIO_MAX_LEVEL)
        ),
        vol.Optional("earpiece_gain"): vol.All(
            vol.Coerce(int), vol.Range(min=AUDIO_MIN_LEVEL, max=AUDIO_MAX_LEVEL)
        ),
        vol.Optional("speaker_volume"): vol.All(
            vol.Coerce(int), vol.Range(min=AUDIO_MIN_LEVEL, max=AUDIO_MAX_LEVEL)
        ),
        vol.Optional("speaker_gain"): vol.All(
            vol.Coerce(int), vol.Range(min=AUDIO_MIN_LEVEL, max=AUDIO_MAX_LEVEL)
        ),
    }
)

CALL_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("limit"): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
    }
)

CALL_HISTORY_CLEAR_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Optional("older_than_days"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("keep_last"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)

QUICK_DIAL_ADD_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("code"): cv.string,
        vol.Required("number"): cv.string,
        vol.Optional("name"): cv.string,
    }
)

QUICK_DIAL_REMOVE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("code"): cv.string,
    }
)

BLOCKED_ADD_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("number"): cv.string,
        vol.Optional(
            "reason"
        ): cv.string,  # Changed from 'name' to 'reason' to match API
    }
)

BLOCKED_REMOVE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("number"): cv.string,
    }
)

# Priority caller schemas
PRIORITY_ADD_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("number"): cv.string,
    }
)

PRIORITY_REMOVE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("number"): cv.string,
    }
)

WEBHOOK_ADD_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("url"): cv.string,
        vol.Required("events"): [cv.string],
        vol.Optional("name"): cv.string,
    }
)

WEBHOOK_REMOVE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("url"): cv.string,
    }
)

WEBHOOK_TEST_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("url"): cv.string,
    }
)

MAINTENANCE_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("enabled"): cv.boolean,
    }
)

DEVICE_ONLY_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
    }
)

DIAL_QUICK_DIAL_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("code"): cv.string,
    }
)

SET_HA_URL_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("url"): cv.string,
    }
)

# Phase P4: Bulk import/export schemas
QUICK_DIAL_IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("entries"): [
            vol.Schema(
                {
                    vol.Required("code"): cv.string,
                    vol.Required("number"): cv.string,
                    vol.Optional("name"): cv.string,
                }
            )
        ],
        vol.Optional("clear_existing", default=False): cv.boolean,
    }
)

BLOCKED_IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("entries"): [
            vol.Schema(
                {
                    vol.Required("number"): cv.string,
                    vol.Optional("name"): cv.string,
                }
            )
        ],
        vol.Optional("clear_existing", default=False): cv.boolean,
    }
)

# Phase P8: Resilience service schemas
RESILIENCE_TEST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional("test_type", default="connection"): vol.In(
            ["connection", "sequence", "stress"]
        ),
    }
)


# Phase P8: Resilience and monitoring services


async def async_resilience_status(call: ServiceCall) -> ServiceResponse:
    """Get resilience status for the device."""
    device_id = call.data.get(ATTR_DEVICE_ID)

    coordinator = await _get_coordinator_by_device_id(call.hass, device_id)
    if not coordinator:
        raise ServiceValidationError(f"Device {device_id} not found")

    resilience_status = coordinator.get_resilience_status()

    return {
        "device_id": device_id,
        "status": resilience_status,
        "timestamp": time.time(),
    }


async def async_resilience_test(call: ServiceCall) -> ServiceResponse:
    """Run resilience stress test."""
    device_id = call.data.get(ATTR_DEVICE_ID)
    test_type = call.data.get("test_type", "connection")

    coordinator = await _get_coordinator_by_device_id(call.hass, device_id)
    if not coordinator:
        raise ServiceValidationError(f"Device {device_id} not found")

    if not coordinator._resilience:
        raise ServiceValidationError("Resilience manager not available")

    test_results = {"test_type": test_type, "results": []}

    if test_type == "connection":
        # Test API connectivity
        try:
            await coordinator.api_client.get_tsuryphone_config()
            test_results["results"].append(
                {"test": "api_connectivity", "status": "pass"}
            )
        except Exception as err:
            test_results["results"].append(
                {"test": "api_connectivity", "status": "fail", "error": str(err)}
            )

        # Test WebSocket connectivity
        if coordinator._websocket_client:
            ws_healthy, ws_issues = coordinator._websocket_client.is_healthy()
            test_results["results"].append(
                {
                    "test": "websocket_health",
                    "status": "pass" if ws_healthy else "fail",
                    "issues": ws_issues,
                }
            )

    elif test_type == "sequence":
        # Test sequence handling by simulating events
        test_event_data = {
            "schemaVersion": 2,
            "seq": coordinator.data.last_seq + 1,
            "ts": int(time.time() * 1000),
            "integration": "ha",
            "deviceId": device_id,
            "category": "system",
            "event": "test_event",
        }

        from .models import TsuryPhoneEvent

        test_event = TsuryPhoneEvent.from_json(test_event_data)

        should_process = await coordinator._resilience.handle_event_sequence(test_event)
        test_results["results"].append(
            {
                "test": "sequence_validation",
                "status": "pass" if should_process else "fail",
                "processed": should_process,
            }
        )

    return {
        "device_id": device_id,
        "test_results": test_results,
        "timestamp": time.time(),
    }


async def async_websocket_reconnect(call: ServiceCall) -> ServiceResponse:
    """Force WebSocket reconnection."""
    device_id = call.data.get(ATTR_DEVICE_ID)

    coordinator = await _get_coordinator_by_device_id(call.hass, device_id)
    if not coordinator:
        raise ServiceValidationError(f"Device {device_id} not found")

    if not coordinator._websocket_client:
        raise ServiceValidationError("WebSocket client not available")

    _LOGGER.info("Forcing WebSocket reconnection for device %s", device_id)

    try:
        await coordinator._websocket_client.reconnect()
        status = "success"
        message = "WebSocket reconnection initiated"
    except Exception as err:
        status = "error"
        message = f"Failed to reconnect: {err}"
        _LOGGER.error("WebSocket reconnection failed: %s", err)

    return {
        "device_id": device_id,
        "status": status,
        "message": message,
        "timestamp": time.time(),
    }


async def async_run_health_check(call: ServiceCall) -> ServiceResponse:
    """Run comprehensive health check."""
    device_id = call.data.get(ATTR_DEVICE_ID)

    coordinator = await _get_coordinator_by_device_id(call.hass, device_id)
    if not coordinator:
        raise ServiceValidationError(f"Device {device_id} not found")

    if not coordinator._resilience:
        raise ServiceValidationError("Resilience manager not available")

    health_results = await coordinator._resilience.run_health_check()

    return {
        "device_id": device_id,
        "health_check": health_results,
        "timestamp": time.time(),
    }


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for TsuryPhone integration."""

    async def async_get_coordinator(device_id: str) -> TsuryPhoneDataUpdateCoordinator:
        """Get coordinator for a device ID."""
        # Find the config entry with matching device ID
        for config_entry in hass.config_entries.async_entries(DOMAIN):
            coordinator = config_entry.runtime_data
            if coordinator.device_info.device_id == device_id:
                return coordinator

        raise ServiceValidationError(
            f"TsuryPhone device with ID '{device_id}' not found"
        )

    async def async_dial(call: ServiceCall) -> None:
        """Service to dial a number."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        number = call.data["number"]

        try:
            await coordinator.api_client.dial(number)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to dial {number}: {err}") from err

    async def async_answer(call: ServiceCall) -> None:
        """Service to answer a call."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        if not coordinator.data.is_incoming_call:
            raise ServiceValidationError("No incoming call to answer")

        try:
            await coordinator.api_client.answer_call()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to answer call: {err}") from err

    async def async_hangup(call: ServiceCall) -> None:
        """Service to hang up a call."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        if not coordinator.data.is_call_active:
            raise ServiceValidationError("No active call to hang up")

        try:
            await coordinator.api_client.hangup_call()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to hang up call: {err}") from err

    async def async_ring_device(call: ServiceCall) -> None:
        """Service to ring the device."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        pattern = call.data.get("pattern", "")

        try:
            await coordinator.api_client.ring_device(pattern)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to ring device: {err}") from err

    async def async_set_ring_pattern(call: ServiceCall) -> None:
        """Service to set ring pattern."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        pattern = call.data["pattern"]

        try:
            await coordinator.api_client.set_ring_pattern(pattern)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to set ring pattern: {err}") from err

    async def async_reset_device(call: ServiceCall) -> None:
        """Service to reset the device."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        try:
            await coordinator.api_client.reset_device()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to reset device: {err}") from err

    async def async_set_dnd(call: ServiceCall) -> None:
        """Service to set Do Not Disturb configuration."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        # Build DND config from service parameters
        dnd_config = {}
        # Map service parameter names to API field names (firmware expects camelCase)
        field_mapping = {
            "force": "force",
            "scheduled": "scheduled",
            "start_hour": "startHour",
            "start_minute": "startMinute",
            "end_hour": "endHour",
            "end_minute": "endMinute",
        }
        for service_key, api_key in field_mapping.items():
            if service_key in call.data:
                dnd_config[api_key] = call.data[service_key]

        try:
            await coordinator.api_client.set_dnd(dnd_config)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to set DND: {err}") from err

    async def async_set_audio(call: ServiceCall) -> None:
        """Service to set audio configuration."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        # Build audio config from service parameters
        audio_config = {}
        for key in [
            "earpiece_volume",
            "earpiece_gain",
            "speaker_volume",
            "speaker_gain",
        ]:
            if key in call.data:
                # Convert to camelCase for API
                api_key = (
                    key.replace("_", "")
                    .replace("earpiece", "earpiece")
                    .replace("speaker", "speaker")
                )
                if api_key.startswith("earpiece"):
                    api_key = "earpieceVolume" if "volume" in key else "earpieceGain"
                elif api_key.startswith("speaker"):
                    api_key = "speakerVolume" if "volume" in key else "speakerGain"
                audio_config[api_key] = call.data[key]

        try:
            await coordinator.api_client.set_audio_config(audio_config)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to set audio config: {err}") from err

    async def async_get_call_history(call: ServiceCall) -> dict[str, Any]:
        """Service to get call history."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        limit = call.data.get("limit")

        # Get history from local coordinator data (not API - history is managed locally)
        history = coordinator.data.call_history or []

        # Apply limit if specified (newest first)
        if limit and len(history) > limit:
            history = history[-limit:]  # Get most recent entries

        # Convert to serializable format
        return {
            "call_history": [
                {
                    "number": entry.number,
                    "call_type": entry.call_type,
                    "timestamp": (
                        entry.timestamp.isoformat() if entry.timestamp else None
                    ),
                    "is_incoming": entry.is_incoming,
                    "duration_s": entry.duration_s,
                    "ts_device": entry.ts_device,
                    "received_ts": entry.received_ts,
                    "seq": entry.seq,
                    "synthetic": entry.synthetic,
                    "reason": entry.reason,
                }
                for entry in history
            ]
        }

    async def async_clear_call_history(call: ServiceCall) -> None:
        """Service to clear call history."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        older_than_days = call.data.get("older_than_days")
        keep_last = call.data.get("keep_last")

        # Clear history from local coordinator data (history is managed locally, not on device)
        if older_than_days or keep_last:
            # Selective clearing
            import time

            now = time.time()

            if older_than_days:
                cutoff = now - (older_than_days * 24 * 60 * 60)
                coordinator.data.call_history = [
                    entry
                    for entry in coordinator.data.call_history
                    if entry.received_ts > cutoff
                ]

            if keep_last and len(coordinator.data.call_history) > keep_last:
                coordinator.data.call_history = coordinator.data.call_history[
                    -keep_last:
                ]
        else:
            # Clear all
            coordinator.data.call_history = []

        # Update storage cache
        if coordinator._storage_cache:
            await coordinator._storage_cache.async_save_call_history(
                coordinator.data.call_history
            )

        # Trigger coordinator update
        coordinator.async_set_updated_data(coordinator.data)

    async def async_quick_dial_add(call: ServiceCall) -> None:
        """Service to add quick dial entry."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        code = call.data["code"]
        number = call.data["number"]
        name = call.data.get("name")

        try:
            await coordinator.api_client.add_quick_dial(code, number, name)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to add quick dial: {err}") from err

    async def async_quick_dial_remove(call: ServiceCall) -> None:
        """Service to remove quick dial entry."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        code = call.data["code"]

        try:
            await coordinator.api_client.remove_quick_dial(code)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to remove quick dial: {err}") from err

    async def async_quick_dial_clear(call: ServiceCall) -> None:
        """Service to clear all quick dial entries."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        # Clear all quick dial entries by removing each one individually
        # (API doesn't have bulk clear method)
        errors = []
        for entry in list(coordinator.data.quick_dials):
            try:
                await coordinator.api_client.remove_quick_dial(entry.code)
            except TsuryPhoneAPIError as err:
                errors.append(f"Failed to remove {entry.code}: {err}")

        await coordinator.async_request_refresh()

        if errors:
            raise HomeAssistantError(
                f"Some entries failed to clear: {'; '.join(errors)}"
            )

    async def async_blocked_add(call: ServiceCall) -> None:
        """Service to add blocked number."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        number = call.data["number"]
        reason = call.data.get("reason", "")  # API expects 'reason' not 'name'

        try:
            await coordinator.api_client.add_blocked_number(number, reason)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to add blocked number: {err}") from err

    async def async_blocked_remove(call: ServiceCall) -> None:
        """Service to remove blocked number."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        number = call.data["number"]

        try:
            await coordinator.api_client.remove_blocked_number(number)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to remove blocked number: {err}") from err

    async def async_blocked_clear(call: ServiceCall) -> None:
        """Service to clear all blocked numbers."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        # Clear all blocked numbers by removing each one individually
        # (API doesn't have bulk clear method)
        errors = []
        for entry in list(coordinator.data.blocked_numbers):
            try:
                await coordinator.api_client.remove_blocked_number(entry.number)
            except TsuryPhoneAPIError as err:
                errors.append(f"Failed to remove {entry.number}: {err}")

        await coordinator.async_request_refresh()

        if errors:
            raise HomeAssistantError(
                f"Some entries failed to clear: {'; '.join(errors)}"
            )

    async def async_refetch_all(call: ServiceCall) -> None:
        """Service to refetch all device data."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        try:
            await coordinator.api_client.refetch_all()
            # After refetch_all, the API returns aggregated data; request refresh to sync entities
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to refetch all data: {err}") from err

    async def async_get_tsuryphone_config(call: ServiceCall) -> dict[str, Any]:
        """Service to retrieve full TsuryPhone configuration snapshot."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        try:
            data = await coordinator.api_client.get_tsuryphone_config()
            # Fire an event on the HA bus for consumers (developer tooling)
            coordinator.hass.bus.async_fire(
                f"{DOMAIN}_tsuryphone_config",
                {"device_id": call.data["device_id"], "config": data},
            )
            return data
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to fetch TsuryPhone config: {err}"
            ) from err

    async def async_priority_add(call: ServiceCall) -> None:
        """Service to add priority caller number."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        number = call.data["number"]

        try:
            await coordinator.api_client.add_priority_caller(number)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to add priority caller: {err}") from err

    async def async_priority_remove(call: ServiceCall) -> None:
        """Service to remove priority caller number."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        number = call.data["number"]

        try:
            await coordinator.api_client.remove_priority_caller(number)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to remove priority caller: {err}"
            ) from err

        try:
            await coordinator.api_client.refetch_all()
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to refetch data: {err}") from err

    async def async_get_diagnostics(call: ServiceCall) -> dict[str, Any]:
        """Service to get device diagnostics."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        try:
            diagnostics = await coordinator.api_client.get_diagnostics()
            return {"diagnostics": diagnostics}
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to get diagnostics: {err}") from err

    async def async_webhook_add(call: ServiceCall) -> None:
        """Service to add webhook."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        url = call.data["url"]
        events = call.data["events"]
        name = call.data.get("name")

        try:
            await coordinator.api_client.add_webhook(url, events, name)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to add webhook: {err}") from err

    async def async_webhook_remove(call: ServiceCall) -> None:
        """Service to remove webhook."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        url = call.data["url"]

        try:
            await coordinator.api_client.remove_webhook(url)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to remove webhook: {err}") from err

    async def async_webhook_clear(call: ServiceCall) -> None:
        """Service to clear all webhooks."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        try:
            await coordinator.api_client.clear_webhooks()
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to clear webhooks: {err}") from err

    async def async_webhook_test(call: ServiceCall) -> None:
        """Service to test webhook."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        url = call.data["url"]

        try:
            await coordinator.api_client.test_webhook(url)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to test webhook: {err}") from err

    async def async_switch_call_waiting(call: ServiceCall) -> None:
        """Service to switch call waiting."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        if not coordinator.data.call_waiting_available:
            raise ServiceValidationError("Call waiting not available on this device")

        try:
            await coordinator.api_client.switch_call_waiting()
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to switch call waiting: {err}") from err

    async def async_set_maintenance_mode(call: ServiceCall) -> None:
        """Service to set maintenance mode."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        enabled = call.data["enabled"]

        try:
            await coordinator.api_client.set_maintenance_mode(enabled)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to set maintenance mode: {err}") from err

    async def async_get_missed_calls(call: ServiceCall) -> dict[str, Any]:
        """Service to get missed calls."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        # Get missed calls from local call history (not API - history is managed locally)
        history = coordinator.data.call_history or []
        missed_calls = [entry for entry in history if entry.call_type == "missed"]

        return {
            "missed_calls": [
                {
                    "number": entry.number,
                    "timestamp": (
                        entry.timestamp.isoformat() if entry.timestamp else None
                    ),
                    "call_type": entry.call_type,
                    "ts_device": entry.ts_device,
                    "received_ts": entry.received_ts,
                }
                for entry in missed_calls
            ]
        }

    async def async_dial_quick_dial(call: ServiceCall) -> None:
        """Service to dial a quick dial code."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        code = call.data["code"]

        try:
            await coordinator.api_client.dial_quick_dial(code)
            # No need to refresh - dial operations are reflected in events
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to dial quick dial code {code}: {err}"
            ) from err

    async def async_set_ha_url(call: ServiceCall) -> None:
        """Service to set Home Assistant URL for webhooks."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        url = call.data["url"]

        try:
            await coordinator.api_client.set_ha_url(url)
            # URL setting doesn't require refresh
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to set HA URL: {err}") from err

    async def async_quick_dial_import(call: ServiceCall) -> dict[str, Any]:
        """Service to import quick dial entries in bulk."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        entries = call.data["entries"]
        clear_existing = call.data.get("clear_existing", False)

        results = {"added": [], "failed": [], "cleared": False}

        try:
            # Clear existing entries if requested
            if clear_existing:
                await coordinator.api_client.clear_quick_dial()
                results["cleared"] = True
                _LOGGER.info("Cleared existing quick dial entries")

            # Add new entries
            for entry in entries:
                try:
                    await coordinator.api_client.add_quick_dial(
                        entry["code"], entry["number"], entry.get("name")
                    )
                    results["added"].append(entry["code"])
                    _LOGGER.debug("Added quick dial entry: %s", entry["code"])
                except TsuryPhoneAPIError as err:
                    results["failed"].append({"code": entry["code"], "error": str(err)})
                    _LOGGER.warning(
                        "Failed to add quick dial entry %s: %s", entry["code"], err
                    )

            # Refresh coordinator to update entities
            await coordinator.async_request_refresh()

            return results

        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to import quick dial entries: {err}"
            ) from err

    async def async_quick_dial_export(call: ServiceCall) -> dict[str, Any]:
        """Service to export quick dial entries."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        try:
            # Get current quick dial entries from state
            state = coordinator.data
            if not state.quick_dials:
                return {"entries": []}

            # Convert to exportable format
            entries = [
                {
                    "code": entry.code,
                    "number": entry.number,
                    "name": entry.name,
                }
                for entry in state.quick_dials
            ]

            return {"entries": entries}

        except Exception as err:
            raise HomeAssistantError(
                f"Failed to export quick dial entries: {err}"
            ) from err

    async def async_blocked_import(call: ServiceCall) -> dict[str, Any]:
        """Service to import blocked numbers in bulk."""
        coordinator = await async_get_coordinator(call.data["device_id"])
        entries = call.data["entries"]
        clear_existing = call.data.get("clear_existing", False)

        results = {"added": [], "failed": [], "cleared": False}

        try:
            # Clear existing entries if requested
            if clear_existing:
                await coordinator.api_client.clear_blocked_numbers()
                results["cleared"] = True
                _LOGGER.info("Cleared existing blocked numbers")

            # Add new entries
            for entry in entries:
                try:
                    await coordinator.api_client.add_blocked_number(
                        entry["number"], entry.get("name")
                    )
                    results["added"].append(entry["number"])
                    _LOGGER.debug("Added blocked number: %s", entry["number"])
                except TsuryPhoneAPIError as err:
                    results["failed"].append(
                        {"number": entry["number"], "error": str(err)}
                    )
                    _LOGGER.warning(
                        "Failed to add blocked number %s: %s", entry["number"], err
                    )

            # Refresh coordinator to update entities
            await coordinator.async_request_refresh()

            return results

        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to import blocked numbers: {err}"
            ) from err

    async def async_blocked_export(call: ServiceCall) -> dict[str, Any]:
        """Service to export blocked numbers."""
        coordinator = await async_get_coordinator(call.data["device_id"])

        try:
            # Get current blocked numbers from state
            state = coordinator.data
            if not state.blocked_numbers:
                return {"entries": []}

            # Convert to exportable format
            entries = [
                {
                    "number": entry.number,
                    "name": entry.name,
                }
                for entry in state.blocked_numbers
            ]

            return {"entries": entries}

        except Exception as err:
            raise HomeAssistantError(
                f"Failed to export blocked numbers: {err}"
            ) from err

    # Register all services
    services_config = [
        (SERVICE_DIAL, async_dial, DIAL_SCHEMA),
        (SERVICE_ANSWER, async_answer, DEVICE_ONLY_SCHEMA),
        (SERVICE_HANGUP, async_hangup, DEVICE_ONLY_SCHEMA),
        (SERVICE_RING_DEVICE, async_ring_device, RING_DEVICE_SCHEMA),
        (SERVICE_SET_RING_PATTERN, async_set_ring_pattern, SET_RING_PATTERN_SCHEMA),
        (SERVICE_RESET_DEVICE, async_reset_device, DEVICE_ONLY_SCHEMA),
        (SERVICE_SET_DND, async_set_dnd, SET_DND_SCHEMA),
        (SERVICE_SET_AUDIO, async_set_audio, SET_AUDIO_SCHEMA),
        (SERVICE_GET_CALL_HISTORY, async_get_call_history, CALL_HISTORY_SCHEMA),
        (
            SERVICE_CLEAR_CALL_HISTORY,
            async_clear_call_history,
            CALL_HISTORY_CLEAR_SCHEMA,
        ),
        (SERVICE_QUICK_DIAL_ADD, async_quick_dial_add, QUICK_DIAL_ADD_SCHEMA),
        (SERVICE_QUICK_DIAL_REMOVE, async_quick_dial_remove, QUICK_DIAL_REMOVE_SCHEMA),
        (SERVICE_QUICK_DIAL_CLEAR, async_quick_dial_clear, DEVICE_ONLY_SCHEMA),
        (SERVICE_BLOCKED_ADD, async_blocked_add, BLOCKED_ADD_SCHEMA),
        (SERVICE_BLOCKED_REMOVE, async_blocked_remove, BLOCKED_REMOVE_SCHEMA),
        (SERVICE_BLOCKED_CLEAR, async_blocked_clear, DEVICE_ONLY_SCHEMA),
        (SERVICE_PRIORITY_ADD, async_priority_add, PRIORITY_ADD_SCHEMA),
        (SERVICE_PRIORITY_REMOVE, async_priority_remove, PRIORITY_REMOVE_SCHEMA),
        (SERVICE_REFETCH_ALL, async_refetch_all, DEVICE_ONLY_SCHEMA),
        (SERVICE_GET_DIAGNOSTICS, async_get_diagnostics, DEVICE_ONLY_SCHEMA),
        (SERVICE_WEBHOOK_ADD, async_webhook_add, WEBHOOK_ADD_SCHEMA),
        (SERVICE_WEBHOOK_REMOVE, async_webhook_remove, WEBHOOK_REMOVE_SCHEMA),
        (SERVICE_WEBHOOK_CLEAR, async_webhook_clear, DEVICE_ONLY_SCHEMA),
        (SERVICE_WEBHOOK_TEST, async_webhook_test, WEBHOOK_TEST_SCHEMA),
        (SERVICE_SWITCH_CALL_WAITING, async_switch_call_waiting, DEVICE_ONLY_SCHEMA),
        (
            SERVICE_SET_MAINTENANCE_MODE,
            async_set_maintenance_mode,
            MAINTENANCE_MODE_SCHEMA,
        ),
        (SERVICE_GET_MISSED_CALLS, async_get_missed_calls, DEVICE_ONLY_SCHEMA),
        (SERVICE_DIAL_QUICK_DIAL, async_dial_quick_dial, DIAL_QUICK_DIAL_SCHEMA),
        (SERVICE_SET_HA_URL, async_set_ha_url, SET_HA_URL_SCHEMA),
        (SERVICE_QUICK_DIAL_IMPORT, async_quick_dial_import, QUICK_DIAL_IMPORT_SCHEMA),
        (SERVICE_QUICK_DIAL_EXPORT, async_quick_dial_export, DEVICE_ONLY_SCHEMA),
        (SERVICE_BLOCKED_IMPORT, async_blocked_import, BLOCKED_IMPORT_SCHEMA),
        (SERVICE_BLOCKED_EXPORT, async_blocked_export, DEVICE_ONLY_SCHEMA),
        # Phase P8: Resilience services
        (SERVICE_RESILIENCE_STATUS, async_resilience_status, DEVICE_ONLY_SCHEMA),
        (SERVICE_RESILIENCE_TEST, async_resilience_test, RESILIENCE_TEST_SCHEMA),
        (SERVICE_WEBSOCKET_RECONNECT, async_websocket_reconnect, DEVICE_ONLY_SCHEMA),
        (SERVICE_RUN_HEALTH_CHECK, async_run_health_check, DEVICE_ONLY_SCHEMA),
    ]

    for service_name, service_func, schema in services_config:
        # Services that return data need SupportsResponse.OPTIONAL
        supports_response = (
            SupportsResponse.OPTIONAL
            if service_name
            in [
                SERVICE_GET_CALL_HISTORY,
                SERVICE_GET_DIAGNOSTICS,
                SERVICE_GET_MISSED_CALLS,
                SERVICE_QUICK_DIAL_IMPORT,
                SERVICE_QUICK_DIAL_EXPORT,
                SERVICE_BLOCKED_IMPORT,
                SERVICE_BLOCKED_EXPORT,
                # Phase P8: Resilience services with responses
                SERVICE_RESILIENCE_STATUS,
                SERVICE_RESILIENCE_TEST,
                SERVICE_WEBSOCKET_RECONNECT,
                SERVICE_RUN_HEALTH_CHECK,
            ]
            else SupportsResponse.NONE
        )

        hass.services.async_register(
            DOMAIN,
            service_name,
            service_func,
            schema=schema,
            supports_response=supports_response,
        )

    _LOGGER.info("TsuryPhone services registered successfully")


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload services for TsuryPhone integration."""
    services_to_remove = [
        SERVICE_DIAL,
        SERVICE_ANSWER,
        SERVICE_HANGUP,
        SERVICE_RING_DEVICE,
        SERVICE_SET_RING_PATTERN,
        SERVICE_RESET_DEVICE,
        SERVICE_SET_DND,
        SERVICE_SET_AUDIO,
        SERVICE_GET_CALL_HISTORY,
        SERVICE_CLEAR_CALL_HISTORY,
        SERVICE_QUICK_DIAL_ADD,
        SERVICE_QUICK_DIAL_REMOVE,
        SERVICE_QUICK_DIAL_CLEAR,
        SERVICE_BLOCKED_ADD,
        SERVICE_BLOCKED_REMOVE,
        SERVICE_BLOCKED_CLEAR,
        SERVICE_REFETCH_ALL,
        SERVICE_GET_DIAGNOSTICS,
        SERVICE_WEBHOOK_ADD,
        SERVICE_WEBHOOK_REMOVE,
        SERVICE_WEBHOOK_CLEAR,
        SERVICE_WEBHOOK_TEST,
        SERVICE_SWITCH_CALL_WAITING,
        SERVICE_SET_MAINTENANCE_MODE,
        SERVICE_GET_MISSED_CALLS,
        SERVICE_QUICK_DIAL_IMPORT,
        SERVICE_QUICK_DIAL_EXPORT,
        SERVICE_BLOCKED_IMPORT,
        SERVICE_BLOCKED_EXPORT,
        # Phase P8: Resilience services
        SERVICE_RESILIENCE_STATUS,
        SERVICE_RESILIENCE_TEST,
        SERVICE_WEBSOCKET_RECONNECT,
        SERVICE_RUN_HEALTH_CHECK,
    ]

    for service_name in services_to_remove:
        hass.services.async_remove(DOMAIN, service_name)

    _LOGGER.info("TsuryPhone services unloaded successfully")
