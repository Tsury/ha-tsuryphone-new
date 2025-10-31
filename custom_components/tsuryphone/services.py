"""Services for TsuryPhone integration."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import (
    DOMAIN,
    SERVICE_DIAL,
    SERVICE_DIAL_DIGIT,
    SERVICE_ANSWER,
    SERVICE_HANGUP,
    SERVICE_RING_DEVICE,
    SERVICE_SET_RING_PATTERN,
    SERVICE_RESET_DEVICE,
    SERVICE_FACTORY_RESET_DEVICE,
    SERVICE_SET_DND,
    SERVICE_SET_AUDIO,
    SERVICE_SET_DIALING_CONFIG,
    SERVICE_GET_CALL_HISTORY,
    SERVICE_CLEAR_CALL_HISTORY,
    SERVICE_GET_TSURYPHONE_CONFIG,
    SERVICE_QUICK_DIAL_ADD,
    SERVICE_QUICK_DIAL_REMOVE,
    SERVICE_QUICK_DIAL_CLEAR,
    SERVICE_DIAL_QUICK_DIAL,
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
    SERVICE_TOGGLE_VOLUME_MODE,
    SERVICE_SET_MAINTENANCE_MODE,
    SERVICE_GET_MISSED_CALLS,
    SERVICE_SET_HA_URL,
    SERVICE_QUICK_DIAL_IMPORT,
    SERVICE_QUICK_DIAL_EXPORT,
    SERVICE_BLOCKED_IMPORT,
    SERVICE_BLOCKED_EXPORT,
    SERVICE_RESILIENCE_STATUS,
    SERVICE_RESILIENCE_TEST,
    SERVICE_WEBSOCKET_RECONNECT,
    SERVICE_RUN_HEALTH_CHECK,
    INTEGRATION_EVENT_SCHEMA_VERSION,
    AUDIO_MIN_LEVEL,
    AUDIO_MAX_LEVEL,
    RING_PATTERN_PRESETS,
)
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .api_client import TsuryPhoneAPIError
from .dialing import DialingContext, sanitize_default_dialing_code

_LOGGER = logging.getLogger(__name__)

# Service schemas


def _service_schema(schema: Any) -> vol.Schema:
    """Allow standard service fields while tolerating target selectors."""

    return vol.Schema(schema, extra=vol.ALLOW_EXTRA)


DIAL_SCHEMA = _service_schema(
    {
        vol.Required("number"): cv.string,
    }
)


def _validate_digit(value: Any) -> int:
    """Validate a single dial digit."""

    digit = vol.Coerce(int)(value)
    if digit < 0 or digit > 9:
        raise vol.Invalid("Digit must be between 0 and 9")
    return digit


DIAL_DIGIT_SCHEMA = _service_schema({vol.Required("digit"): _validate_digit})

RING_DEVICE_SCHEMA = _service_schema(
    {
        vol.Optional("pattern", default=""): cv.string,
        vol.Optional("force"): cv.boolean,
    }
)

SET_RING_PATTERN_SCHEMA = _service_schema(
    {
        vol.Required("pattern"): cv.string,
    }
)

SET_DND_SCHEMA = _service_schema(
    {
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

SET_AUDIO_SCHEMA = _service_schema(
    {
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

SET_DIALING_CONFIG_SCHEMA = _service_schema(
    {
        vol.Required("default_code"): cv.string,
    }
)

CALL_HISTORY_SCHEMA = _service_schema(
    {
        vol.Optional("limit"): vol.All(vol.Coerce(int), vol.Range(min=1, max=1000)),
    }
)

CALL_HISTORY_CLEAR_SCHEMA = _service_schema(
    {
        vol.Optional("older_than_days"): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional("keep_last"): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)

QUICK_DIAL_ADD_SCHEMA = _service_schema(
    {
        vol.Required("code"): cv.string,
        vol.Required("number"): cv.string,
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1)),
    }
)

QUICK_DIAL_REMOVE_SCHEMA = _service_schema(
    {
        vol.Required("code"): cv.string,
    }
)

BLOCKED_ADD_SCHEMA = _service_schema(
    {
        vol.Required("number"): cv.string,
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1)),
    }
)

BLOCKED_REMOVE_SCHEMA = _service_schema(
    {
        vol.Required("number"): cv.string,
    }
)

# Priority caller schemas
PRIORITY_ADD_SCHEMA = _service_schema(
    {
        vol.Required("number"): cv.string,
    }
)

PRIORITY_REMOVE_SCHEMA = _service_schema(
    {
        vol.Required("number"): cv.string,
    }
)

WEBHOOK_ADD_SCHEMA = _service_schema(
    {
        vol.Required("url"): cv.string,
        vol.Required("events"): [cv.string],
        vol.Optional("name"): cv.string,
    }
)

WEBHOOK_REMOVE_SCHEMA = _service_schema(
    {
        vol.Required("url"): cv.string,
    }
)

WEBHOOK_TEST_SCHEMA = _service_schema(
    {
        vol.Required("url"): cv.string,
    }
)

MAINTENANCE_MODE_SCHEMA = _service_schema(
    {
        vol.Required("enabled"): cv.boolean,
    }
)

DEVICE_ONLY_SCHEMA = _service_schema({})

DIAL_QUICK_DIAL_SCHEMA = _service_schema(
    {
        vol.Required("code"): cv.string,
    }
)

SET_HA_URL_SCHEMA = _service_schema(
    {
        vol.Required("url"): cv.string,
    }
)

# Phase P4: Bulk import/export schemas
QUICK_DIAL_IMPORT_SCHEMA = _service_schema(
    {
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

BLOCKED_IMPORT_SCHEMA = _service_schema(
    {
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
RESILIENCE_TEST_SCHEMA = _service_schema(
    {
        vol.Optional("test_type", default="connection"): vol.In(
            ["connection", "sequence", "stress"]
        ),
    }
)


# Target resolution helpers


def _get_dialing_context(
    coordinator: TsuryPhoneDataUpdateCoordinator,
) -> DialingContext:
    """Return the dialing context associated with a coordinator."""

    if coordinator.data:
        return coordinator.data.dialing_context
    return DialingContext(default_code="", default_prefix="")


def _normalize_number_for_service(
    coordinator: TsuryPhoneDataUpdateCoordinator,
    raw_value: Any,
    *,
    field_name: str = "number",
    remember: bool = False,
) -> str:
    """Normalize outbound phone numbers for service calls."""

    candidate = str(raw_value).strip()
    if not candidate:
        raise ServiceValidationError(f"{field_name} cannot be empty")

    context = _get_dialing_context(coordinator)
    normalized = context.normalize(candidate)
    if not normalized:
        raise ServiceValidationError(f"{field_name} must contain at least one digit")

    device_value = context.canonicalize(candidate)
    if not device_value:
        raise ServiceValidationError(
            f"{field_name} could not be converted to a canonical phone number"
        )

    if remember:
        coordinator.remember_number_display_hint(candidate)

    return device_value


def _extract_ids(value: Any) -> set[str]:
    """Normalize a target value into a set of string IDs."""
    if not value:
        return set()
    if isinstance(value, str):
        return {value}
    return {item for item in value if isinstance(item, str)}


@dataclass(slots=True)
class ServiceDeviceContext:
    """Resolved context for a TsuryPhone device targeted by a service."""

    hass_device_id: str
    coordinator: TsuryPhoneDataUpdateCoordinator

    @property
    def tsury_device_id(self) -> str:
        return self.coordinator.device_info.device_id


def _resolve_target_device_contexts(call: ServiceCall) -> list[ServiceDeviceContext]:
    """Resolve targeted devices for a service call."""

    hass = call.hass
    raw_target: dict[str, Any] = {}

    def _merge_target(source: Any) -> None:
        if not source:
            return

        mapping: dict[str, Any]
        if isinstance(source, Mapping):
            mapping = dict(source)
        elif hasattr(source, "items"):
            mapping = dict(source.items())  # type: ignore[arg-type]
        else:
            try:
                mapping = dict(source)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return

        for key in ("device_id", "entity_id", "area_id"):
            if key in mapping and mapping[key] is not None:
                raw_target[key] = mapping[key]

    _merge_target(getattr(call, "target", None))
    _merge_target(call.data.get("target"))

    for key in ("device_id", "entity_id", "area_id"):
        if key in call.data and call.data[key] is not None:
            raw_target[key] = call.data[key]

    normalized_target: dict[str, list[str]] = {}
    for key, validator in (
        ("device_id", cv.string),
        ("entity_id", cv.entity_id),
        ("area_id", cv.string),
    ):
        if key not in raw_target:
            continue

        try:
            values = cv.ensure_list(raw_target[key])
            normalized_target[key] = [validator(value) for value in values]
        except vol.Invalid as err:
            raise ServiceValidationError(
                f"Invalid {key} target for service '{call.service}': {err}"
            ) from err

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    device_ids = _extract_ids(normalized_target.get("device_id"))

    for entity_id in _extract_ids(normalized_target.get("entity_id")):
        if entry := entity_registry.async_get(entity_id):
            if entry.device_id:
                device_ids.add(entry.device_id)

    area_ids = _extract_ids(normalized_target.get("area_id"))
    if area_ids:
        for device_entry in device_registry.devices.values():
            if device_entry.area_id in area_ids:
                device_ids.add(device_entry.id)

    if not device_ids:
        raise ServiceValidationError(
            f"Service '{call.service}' requires targeting at least one TsuryPhone device."
        )

    contexts: list[ServiceDeviceContext] = []
    for hass_device_id in device_ids:
        device_entry = device_registry.async_get(hass_device_id)
        if not device_entry:
            raise ServiceValidationError(f"Device {hass_device_id} not found")

        coordinator: TsuryPhoneDataUpdateCoordinator | None = None
        for entry_id in device_entry.config_entries:
            config_entry = hass.config_entries.async_get_entry(entry_id)
            if config_entry and config_entry.domain == DOMAIN:
                runtime = getattr(config_entry, "runtime_data", None)
                if isinstance(runtime, TsuryPhoneDataUpdateCoordinator):
                    coordinator = runtime
                    break

        if coordinator is None:
            raise ServiceValidationError(
                f"Device {hass_device_id} is not associated with an active TsuryPhone integration"
            )

        contexts.append(
            ServiceDeviceContext(
                hass_device_id=hass_device_id,
                coordinator=coordinator,
            )
        )

    contexts.sort(key=lambda ctx: ctx.tsury_device_id)
    return contexts


def _require_single_device_context(call: ServiceCall) -> ServiceDeviceContext:
    """Return exactly one targeted device context or raise."""

    contexts = _resolve_target_device_contexts(call)
    if len(contexts) != 1:
        raise ServiceValidationError(  # noqa: TRY003 - user-facing validation message
            f"Service '{call.service}' supports exactly one TsuryPhone device at a time."
        )
    return contexts[0]


# Phase P8: Resilience and monitoring services


async def async_resilience_status(call: ServiceCall) -> ServiceResponse:
    """Get resilience status for the device."""
    context = _require_single_device_context(call)
    coordinator = context.coordinator
    device_id = context.tsury_device_id

    resilience_status = coordinator.get_resilience_status()

    return {
        "device_id": device_id,
        "status": resilience_status,
        "timestamp": time.time(),
    }


async def async_resilience_test(call: ServiceCall) -> ServiceResponse:
    """Run resilience stress test."""
    context = _require_single_device_context(call)
    test_type = call.data.get("test_type", "connection")

    coordinator = context.coordinator
    device_id = context.tsury_device_id

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
            "schemaVersion": INTEGRATION_EVENT_SCHEMA_VERSION,
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
    context = _require_single_device_context(call)
    coordinator = context.coordinator
    device_id = context.tsury_device_id

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
    context = _require_single_device_context(call)
    coordinator = context.coordinator
    device_id = context.tsury_device_id

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

    async def async_dial(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        number = _normalize_number_for_service(
            coordinator, call.data["number"], field_name="number"
        )

        try:
            await coordinator.api_client.dial(number)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to dial {number}: {err}") from err

    async def async_dial_digit(call: ServiceCall) -> None:
        # Debug: Always log the send mode state FIRST
        _LOGGER.debug("========== DIAL_DIGIT SERVICE CALLED ==========")
        
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        digit = call.data["digit"]

        # Check if send mode is enabled - if so, defer validation
        defer_validation = coordinator.send_mode_enabled
        
        # Debug: Always log the send mode state
        _LOGGER.debug(
            "dial_digit: digit=%s | coordinator.send_mode_enabled=%s | defer_validation=%s",
            digit,
            coordinator.send_mode_enabled,
            defer_validation,
        )

        try:
            await coordinator.api_client.dial_digit(digit, defer_validation=defer_validation)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to send digit {digit}: {err}") from err

    async def async_answer(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        if not coordinator.data.is_incoming_call:
            raise ServiceValidationError("No incoming call to answer")

        try:
            await coordinator.api_client.answer_call()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to answer call: {err}") from err

    async def async_hangup(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        if not coordinator.data.is_call_active:
            raise ServiceValidationError("No active call to hang up")

        try:
            await coordinator.api_client.hangup_call()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to hang up call: {err}") from err

    async def async_ring_device(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        pattern = call.data.get("pattern", "")
        force_bypass = call.data.get("force")

        try:
            if force_bypass is None:
                await coordinator.api_client.ring_device(pattern)
            else:
                await coordinator.api_client.ring_device(pattern, force=force_bypass)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to ring device: {err}") from err

    async def async_set_ring_pattern(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        pattern = call.data["pattern"]

        try:
            await coordinator.api_client.set_ring_pattern(pattern)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to set ring pattern: {err}") from err

    async def async_reset_device(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        try:
            await coordinator.api_client.reset_device()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to reset device: {err}") from err

    async def async_factory_reset_device(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        try:
            await coordinator.api_client.factory_reset_device()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to factory reset device: {err}") from err

    async def async_set_dnd(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        dnd_config: dict[str, Any] = {}
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
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        audio_config: dict[str, Any] = {}
        for key in [
            "earpiece_volume",
            "earpiece_gain",
            "speaker_volume",
            "speaker_gain",
        ]:
            if key in call.data:
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

    async def async_set_dialing_config(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        raw_code = call.data["default_code"]
        sanitized_code = sanitize_default_dialing_code(raw_code)

        if not sanitized_code:
            raise ServiceValidationError("default_code must contain at least one digit")

        try:
            await coordinator.api_client.set_dialing_config(sanitized_code)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to set default dialing code: {err}"
            ) from err

    async def async_get_call_history(call: ServiceCall) -> dict[str, Any]:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        limit = call.data.get("limit")

        history = coordinator.data.call_history or []
        if limit and len(history) > limit:
            history = history[-limit:]

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
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        older_than_days = call.data.get("older_than_days")
        keep_last = call.data.get("keep_last")

        if older_than_days or keep_last:
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
            coordinator.data.call_history = []

        if coordinator._storage_cache:
            await coordinator._storage_cache.async_save_call_history(
                coordinator.data.call_history
            )

        coordinator.async_set_updated_data(coordinator.data)

    async def async_quick_dial_add(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        code = call.data["code"]
        number = _normalize_number_for_service(
            coordinator,
            call.data["number"],
            field_name="number",
            remember=True,
        )
        name = call.data["name"].strip()
        if not name:
            raise ServiceValidationError("name cannot be empty")

        try:
            await coordinator.api_client.add_quick_dial(code, number, name)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to add quick dial: {err}") from err

    async def async_quick_dial_remove(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        code = call.data["code"]

        try:
            await coordinator.api_client.remove_quick_dial(code)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to remove quick dial: {err}") from err

    async def async_quick_dial_clear(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        errors: list[str] = []
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
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        number = _normalize_number_for_service(
            coordinator,
            call.data["number"],
            field_name="number",
            remember=True,
        )
        name = call.data["name"].strip()
        if not name:
            raise ServiceValidationError("name cannot be empty")

        try:
            await coordinator.api_client.add_blocked_number(number, name)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to add blocked number: {err}") from err

    async def async_blocked_remove(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        number = _normalize_number_for_service(
            coordinator, call.data["number"], field_name="number"
        )

        try:
            await coordinator.api_client.remove_blocked_number(number)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to remove blocked number: {err}") from err

    async def async_blocked_clear(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        errors: list[str] = []
        for entry in list(coordinator.data.blocked_numbers):
            try:
                target = entry.normalized_number or entry.number
                if not target:
                    _LOGGER.debug(
                        "Skipping blocked entry without number during clear: %s",
                        entry,
                    )
                    continue

                await coordinator.api_client.remove_blocked_number(target)
            except TsuryPhoneAPIError as err:
                errors.append(f"Failed to remove {entry.number}: {err}")

        await coordinator.async_request_refresh()

        if errors:
            raise HomeAssistantError(
                f"Some entries failed to clear: {'; '.join(errors)}"
            )

    async def async_refetch_all(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        try:
            await coordinator.api_client.refetch_all()
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to refetch all data: {err}") from err

    async def async_get_tsuryphone_config(call: ServiceCall) -> dict[str, Any]:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        device_id = context.tsury_device_id

        try:
            data = await coordinator.api_client.get_tsuryphone_config()
            coordinator.hass.bus.async_fire(
                f"{DOMAIN}_tsuryphone_config",
                {"device_id": device_id, "config": data},
            )
            return data
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to fetch TsuryPhone config: {err}"
            ) from err

    async def async_priority_add(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        number = _normalize_number_for_service(
            coordinator,
            call.data["number"],
            field_name="number",
            remember=True,
        )

        try:
            await coordinator.api_client.add_priority_caller(number)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to add priority caller: {err}") from err

    async def async_priority_remove(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        number = _normalize_number_for_service(
            coordinator, call.data["number"], field_name="number"
        )

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
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        try:
            diagnostics = await coordinator.api_client.get_diagnostics()
            return {"diagnostics": diagnostics}
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to get diagnostics: {err}") from err

    async def async_webhook_add(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        url = call.data["url"]
        events = call.data["events"]
        name = call.data.get("name")

        try:
            await coordinator.api_client.add_webhook(url, events, name)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to add webhook: {err}") from err

    async def async_webhook_remove(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        url = call.data["url"]

        try:
            await coordinator.api_client.remove_webhook(url)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to remove webhook: {err}") from err

    async def async_webhook_clear(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        try:
            await coordinator.api_client.clear_webhooks()
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to clear webhooks: {err}") from err

    async def async_webhook_test(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        url = call.data["url"]

        try:
            await coordinator.api_client.test_webhook(url)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to test webhook: {err}") from err

    async def async_switch_call_waiting(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        if not coordinator.data.call_waiting_available:
            raise ServiceValidationError("Call waiting not available on this device")

        try:
            await coordinator.api_client.switch_call_waiting()
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to switch call waiting: {err}") from err

    async def async_toggle_volume_mode(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        if not coordinator.data.is_call_active:
            raise ServiceValidationError("No active call to toggle volume mode")

        try:
            await coordinator.api_client.toggle_volume_mode()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to toggle volume mode: {err}") from err

    async def async_set_maintenance_mode(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        enabled = call.data["enabled"]

        try:
            await coordinator.api_client.set_maintenance_mode(enabled)
            await coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to set maintenance mode: {err}") from err

    async def async_get_missed_calls(call: ServiceCall) -> dict[str, Any]:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

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
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        code = call.data["code"]

        try:
            await coordinator.api_client.dial_quick_dial(code)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to dial quick dial code {code}: {err}"
            ) from err

    async def async_set_ha_url(call: ServiceCall) -> None:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        url = call.data["url"]

        try:
            await coordinator.api_client.set_ha_url(url)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to set HA URL: {err}") from err

    async def async_quick_dial_import(call: ServiceCall) -> dict[str, Any]:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        entries = call.data["entries"]
        clear_existing = call.data.get("clear_existing", False)

        results = {"added": [], "failed": [], "cleared": False}

        try:
            if clear_existing:
                await coordinator.api_client.clear_quick_dial()
                results["cleared"] = True
                _LOGGER.info("Cleared existing quick dial entries")

            for entry in entries:
                code = entry.get("code")
                raw_number = entry.get("number")
                name = entry.get("name")

                if not code:
                    results["failed"].append(
                        {"code": code or "", "error": "Missing code"}
                    )
                    _LOGGER.warning(
                        "Skipping quick dial entry with missing code: %s", entry
                    )
                    continue

                if not name or not str(name).strip():
                    results["failed"].append(
                        {"code": code, "error": "Missing quick dial name"}
                    )
                    _LOGGER.warning("Skipping quick dial entry without name: %s", entry)
                    continue

                try:
                    number = _normalize_number_for_service(
                        coordinator,
                        raw_number,
                        field_name="number",
                        remember=True,
                    )
                except ServiceValidationError as err:
                    results["failed"].append({"code": code, "error": str(err)})
                    _LOGGER.warning(
                        "Failed to normalize quick dial entry %s: %s", code, err
                    )
                    continue

                try:
                    await coordinator.api_client.add_quick_dial(code, number, name)
                    results["added"].append(code)
                    _LOGGER.debug("Added quick dial entry: %s", code)
                except TsuryPhoneAPIError as err:
                    results["failed"].append({"code": code, "error": str(err)})
                    _LOGGER.warning("Failed to add quick dial entry %s: %s", code, err)

            await coordinator.async_request_refresh()
            return results

        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to import quick dial entries: {err}"
            ) from err

    async def async_quick_dial_export(call: ServiceCall) -> dict[str, Any]:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        state = coordinator.data
        if not state.quick_dials:
            return {"entries": []}

        entries = [
            {
                "code": entry.code,
                "number": entry.number,
                "name": entry.name,
                "normalized_number": entry.normalized_number,
            }
            for entry in state.quick_dials
        ]
        return {"entries": entries}

    async def async_blocked_import(call: ServiceCall) -> dict[str, Any]:
        context = _require_single_device_context(call)
        coordinator = context.coordinator
        entries = call.data["entries"]
        clear_existing = call.data.get("clear_existing", False)

        results = {"added": [], "failed": [], "cleared": False}

        try:
            if clear_existing:
                await coordinator.api_client.clear_blocked_numbers()
                results["cleared"] = True
                _LOGGER.info("Cleared existing blocked numbers")

            for entry in entries:
                raw_number = entry.get("number")
                name = entry.get("name")

                if not name or not str(name).strip():
                    results["failed"].append(
                        {"number": raw_number or "", "error": "Missing name"}
                    )
                    _LOGGER.warning("Skipping blocked number without name: %s", entry)
                    continue

                try:
                    number = _normalize_number_for_service(
                        coordinator,
                        raw_number,
                        field_name="number",
                        remember=True,
                    )
                except ServiceValidationError as err:
                    results["failed"].append(
                        {"number": raw_number or "", "error": str(err)}
                    )
                    _LOGGER.warning(
                        "Failed to normalize blocked number %s: %s", raw_number, err
                    )
                    continue

                name = str(name).strip()

                try:
                    await coordinator.api_client.add_blocked_number(number, name)
                    results["added"].append(number)
                    _LOGGER.debug("Added blocked number: %s", number)
                except TsuryPhoneAPIError as err:
                    results["failed"].append({"number": number, "error": str(err)})
                    _LOGGER.warning("Failed to add blocked number %s: %s", number, err)

            await coordinator.async_request_refresh()
            return results

        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to import blocked numbers: {err}"
            ) from err

    async def async_blocked_export(call: ServiceCall) -> dict[str, Any]:
        context = _require_single_device_context(call)
        coordinator = context.coordinator

        state = coordinator.data
        if not state.blocked_numbers:
            return {"entries": []}

        entries = [
            {
                "number": entry.number,
                "name": entry.name,
                "normalized_number": entry.normalized_number,
            }
            for entry in state.blocked_numbers
        ]
        return {"entries": entries}

    # Register all services
    services_config = [
        (SERVICE_DIAL, async_dial, DIAL_SCHEMA),
        (SERVICE_DIAL_DIGIT, async_dial_digit, DIAL_DIGIT_SCHEMA),
        (SERVICE_ANSWER, async_answer, DEVICE_ONLY_SCHEMA),
        (SERVICE_HANGUP, async_hangup, DEVICE_ONLY_SCHEMA),
        (SERVICE_RING_DEVICE, async_ring_device, RING_DEVICE_SCHEMA),
        (SERVICE_SET_RING_PATTERN, async_set_ring_pattern, SET_RING_PATTERN_SCHEMA),
        (SERVICE_RESET_DEVICE, async_reset_device, DEVICE_ONLY_SCHEMA),
        (
            SERVICE_FACTORY_RESET_DEVICE,
            async_factory_reset_device,
            DEVICE_ONLY_SCHEMA,
        ),
        (SERVICE_SET_DND, async_set_dnd, SET_DND_SCHEMA),
        (SERVICE_SET_AUDIO, async_set_audio, SET_AUDIO_SCHEMA),
        (
            SERVICE_SET_DIALING_CONFIG,
            async_set_dialing_config,
            SET_DIALING_CONFIG_SCHEMA,
        ),
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
        (
            SERVICE_GET_TSURYPHONE_CONFIG,
            async_get_tsuryphone_config,
            DEVICE_ONLY_SCHEMA,
        ),
        (SERVICE_GET_DIAGNOSTICS, async_get_diagnostics, DEVICE_ONLY_SCHEMA),
        (SERVICE_WEBHOOK_ADD, async_webhook_add, WEBHOOK_ADD_SCHEMA),
        (SERVICE_WEBHOOK_REMOVE, async_webhook_remove, WEBHOOK_REMOVE_SCHEMA),
        (SERVICE_WEBHOOK_CLEAR, async_webhook_clear, DEVICE_ONLY_SCHEMA),
        (SERVICE_WEBHOOK_TEST, async_webhook_test, WEBHOOK_TEST_SCHEMA),
        (SERVICE_SWITCH_CALL_WAITING, async_switch_call_waiting, DEVICE_ONLY_SCHEMA),
        (SERVICE_TOGGLE_VOLUME_MODE, async_toggle_volume_mode, DEVICE_ONLY_SCHEMA),
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
                SERVICE_GET_TSURYPHONE_CONFIG,
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
        SERVICE_DIAL_DIGIT,
        SERVICE_ANSWER,
        SERVICE_HANGUP,
        SERVICE_RING_DEVICE,
        SERVICE_SET_RING_PATTERN,
        SERVICE_RESET_DEVICE,
        SERVICE_FACTORY_RESET_DEVICE,
        SERVICE_SET_DND,
        SERVICE_SET_AUDIO,
        SERVICE_SET_DIALING_CONFIG,
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
        SERVICE_GET_TSURYPHONE_CONFIG,
        SERVICE_GET_DIAGNOSTICS,
        SERVICE_WEBHOOK_ADD,
        SERVICE_WEBHOOK_REMOVE,
        SERVICE_WEBHOOK_CLEAR,
        SERVICE_WEBHOOK_TEST,
        SERVICE_SWITCH_CALL_WAITING,
        SERVICE_TOGGLE_VOLUME_MODE,
        SERVICE_SET_MAINTENANCE_MODE,
        SERVICE_GET_MISSED_CALLS,
        SERVICE_DIAL_QUICK_DIAL,
        SERVICE_SET_HA_URL,
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
