"""Button platform for TsuryPhone integration."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError

from . import get_device_info
from .api_client import TsuryPhoneAPIError
from .const import (
    DOMAIN,
    AppState,
    ERROR_CODE_CODE_CONFLICT,
    ERROR_CODE_NO_INCOMING_CALL,
    ERROR_CODE_NO_ACTIVE_CALL,
    ERROR_CODE_PHONE_NOT_READY,
    ERROR_CODE_MISSING_DIGIT,
    ERROR_CODE_INVALID_DIGIT,
    ERROR_CODE_DIAL_BUFFER_FULL,
)
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .models import TsuryPhoneState
from .dialing import DialingContext

_LOGGER = logging.getLogger(__name__)

BUTTON_DESCRIPTIONS = (
    ButtonEntityDescription(
        key="dial_digit_send",
        name="Call - Dial Digit - Send",
        icon="mdi:dialpad",
    ),
    ButtonEntityDescription(
        key="answer",
        name="Call - Answer",
        icon="mdi:phone",
    ),
    ButtonEntityDescription(
        key="hangup",
        name="Call - Hang Up",
        icon="mdi:phone-hangup",
    ),
    ButtonEntityDescription(
        key="dial_selected",
        name="Quick Dial - Dial Selected",
        icon="mdi:speed-dial",
    ),
    ButtonEntityDescription(
        key="blocked_add",
        name="Blocked - Add Number",
        icon="mdi:phone-plus",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key="blocked_remove",
        name="Blocked - Remove Selected",
        icon="mdi:phone-remove",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key="priority_add",
        name="Priority - Add Number",
        icon="mdi:star-plus",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key="priority_remove",
        name="Priority - Remove Selected",
        icon="mdi:star-minus",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key="quick_dial_add",
        name="Quick Dial - Add Entry",
        icon="mdi:playlist-plus",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key="quick_dial_remove",
        name="Quick Dial - Remove Selected",
        icon="mdi:playlist-remove",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key="webhook_add",
        name="Webhook - Add Action",
        icon="mdi:webhook",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key="webhook_remove",
        name="Webhook - Remove Selected",
        icon="mdi:webhook-off",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key="ring",
        name="Ring Pattern - Test (Bypass DND)",
        icon="mdi:phone-ring",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key="reset",
        name="Device - Reset",
        icon="mdi:restart",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key="factory_reset",
        name="Device - Factory Reset",
        icon="mdi:factory",
        entity_category=EntityCategory.CONFIG,
    ),
    ButtonEntityDescription(
        key="refetch",
        name="Device - Refresh Data",
        icon="mdi:refresh",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ButtonEntityDescription(
        key="refresh_snapshot",
        name="Device - Snapshot",
        icon="mdi:camera-flip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ButtonEntityDescription(
        key="toggle_call_waiting",
        name="Call - Toggle Waiting",
        icon="mdi:phone-plus",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TsuryPhone button entities from a config entry."""
    coordinator = config_entry.runtime_data
    device_info = coordinator.device_info

    entities = [
        TsuryPhoneButton(coordinator, description, device_info)
        for description in BUTTON_DESCRIPTIONS
    ]

    async_add_entities(entities)


class TsuryPhoneButton(
    CoordinatorEntity[TsuryPhoneDataUpdateCoordinator], ButtonEntity
):
    """Representation of a TsuryPhone button."""

    def __init__(
        self,
        coordinator: TsuryPhoneDataUpdateCoordinator,
        description: ButtonEntityDescription,
        device_info,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_info = device_info

        # Generate unique ID
        self._attr_unique_id = f"{device_info.device_id}_{description.key}"

        # Set device info
        self._attr_device_info = get_device_info(device_info)

    def _buffer_has_values(
        self, buffer_name: str, required_fields: Iterable[str]
    ) -> bool:
        """Check if coordinator buffer has required non-empty fields."""

        buffer = getattr(self.coordinator, f"{buffer_name}_input", None)
        if not buffer:
            return False

        for field in required_fields:
            if not (buffer.get(field) or "").strip():
                return False

        return True

    def _get_buffer_snapshot(self, buffer_name: str) -> dict[str, str]:
        """Return a trimmed snapshot of a coordinator buffer."""

        buffer = getattr(self.coordinator, f"{buffer_name}_input", None) or {}
        return {key: (value or "").strip() for key, value in buffer.items()}

    def _prepare_number_input(self, value: str, *, field: str) -> str:
        """Convert a user-supplied number into the canonical device format."""

        candidate = (value or "").strip()
        if not candidate:
            raise HomeAssistantError(f"{field} cannot be empty")

        state: TsuryPhoneState = self.coordinator.data
        context = state.dialing_context if state else DialingContext("", "")

        normalized = context.normalize(candidate)
        if not normalized:
            raise HomeAssistantError(f"{field} must contain at least one digit")

        device_value = context.canonicalize(candidate)
        if not device_value:
            raise HomeAssistantError(
                f"{field} could not be converted to a canonical phone number"
            )

        self.coordinator.remember_number_display_hint(candidate)
        return device_value

    def _clear_buffer(
        self, buffer_name: str, fields: Iterable[str] | None = None
    ) -> None:
        """Clear coordinator buffer fields and notify listeners."""

        buffer = getattr(self.coordinator, f"{buffer_name}_input", None)
        if not buffer:
            return

        target_fields = fields or list(buffer.keys())
        for field in target_fields:
            if field in buffer:
                buffer[field] = ""

        # Notify dependent entities (text inputs) so UI clears immediately
        self.coordinator.async_update_listeners()

    async def async_press(self) -> None:
        """Handle the button press."""
        try:
            if self.entity_description.key == "answer":
                await self._answer_call()
            elif self.entity_description.key == "hangup":
                await self._hangup_call()
            elif self.entity_description.key == "ring":
                await self._ring_device()
            elif self.entity_description.key == "reset":
                await self._reset_device()
            elif self.entity_description.key == "factory_reset":
                await self._factory_reset_device()
            elif self.entity_description.key == "refetch":
                await self._refetch_data()
            elif self.entity_description.key == "refresh_snapshot":
                await self._refresh_snapshot()
            elif self.entity_description.key == "dial_selected":
                await self._dial_selected_quick_dial()
            elif self.entity_description.key == "dial_digit_send":
                await self._dial_digit_from_buffer()
            elif self.entity_description.key == "quick_dial_add":
                await self._add_quick_dial_entry()
            elif self.entity_description.key == "quick_dial_remove":
                await self._remove_selected_quick_dial()
            elif self.entity_description.key == "blocked_add":
                await self._add_blocked_number()
            elif self.entity_description.key == "blocked_remove":
                await self._remove_selected_blocked_number()
            elif self.entity_description.key == "priority_add":
                await self._add_priority_number()
            elif self.entity_description.key == "priority_remove":
                await self._remove_selected_priority_number()
            elif self.entity_description.key == "webhook_add":
                await self._add_webhook_action()
            elif self.entity_description.key == "webhook_remove":
                await self._remove_selected_webhook()
            elif self.entity_description.key == "toggle_call_waiting":
                await self._toggle_call_waiting()
        except TsuryPhoneAPIError as err:
            # Provide user-friendly error messages
            error_msg = self._get_user_friendly_error(err)
            raise HomeAssistantError(
                f"Failed to execute {self.name}: {error_msg}"
            ) from err

    async def _answer_call(self) -> None:
        """Answer the incoming call."""
        state: TsuryPhoneState = self.coordinator.data

        if not (
            state.is_incoming_call
            or state.is_call_active
            or state.is_dialing
            or state.ringing
        ):
            raise HomeAssistantError("No incoming call to answer")

        await self.coordinator.api_client.answer_call()

    async def _hangup_call(self) -> None:
        """Hang up the active call."""
        state: TsuryPhoneState = self.coordinator.data

        if not (
            state.is_call_active
            or state.is_incoming_call
            or state.is_dialing
            or state.current_call.number
            or state.current_dialing_number
        ):
            raise HomeAssistantError("No active call to hang up")

        await self.coordinator.api_client.hangup_call()

    async def _ring_device(self) -> None:
        """Ring the device using the configured pattern while bypassing DND."""

        state: TsuryPhoneState = self.coordinator.data
        pattern = state.ring_pattern or ""

        await self.coordinator.api_client.ring_device(pattern, force=True)

    async def _reset_device(self) -> None:
        """Reset the device."""
        await self.coordinator.api_client.reset_device()

    async def _factory_reset_device(self) -> None:
        """Factory reset the device."""
        await self.coordinator.api_client.factory_reset_device()

    async def _refetch_data(self) -> None:
        """Refetch all device data."""
        await self.coordinator.api_client.refetch_all()

        # Trigger coordinator refresh to update entities
        await self.coordinator.async_request_refresh()

    async def _refresh_snapshot(self) -> None:
        """Refresh diagnostic snapshot."""
        # Get diagnostics from device and update coordinator
        diag_data = await self.coordinator.api_client.get_diagnostics()

        # Trigger coordinator refresh
        await self.coordinator.async_request_refresh()

    async def _dial_selected_quick_dial(self) -> None:
        """Dial the selected quick dial entry."""
        # Phase P4: Use the coordinator's selected quick dial
        if (
            not hasattr(self.coordinator, "selected_quick_dial_code")
            or not self.coordinator.selected_quick_dial_code
        ):
            raise HomeAssistantError("No quick dial entry selected")

        selected_code = self.coordinator.selected_quick_dial_code
        state: TsuryPhoneState = self.coordinator.data

        # Find the entry with the selected code
        if not state.quick_dials:
            raise HomeAssistantError("No quick dial entries available")

        selected_entry = None
        for entry in state.quick_dials:
            if entry.code == selected_code:
                selected_entry = entry
                break

        if not selected_entry:
            raise HomeAssistantError(
                f"Selected quick dial code '{selected_code}' not found"
            )

        # Dial the number
        await self.coordinator.api_client.dial(selected_entry.number)

        # Clear selection so UI inputs reset after action completes
        self.coordinator.selected_quick_dial_code = None
        self.coordinator.async_update_listeners()

    async def _dial_digit_from_buffer(self) -> None:
        """Send the buffered digit to the device dial buffer and clear it."""

        buffer = self._get_buffer_snapshot("dial_digit")
        digit = buffer.get("digit", "").strip()

        if not digit:
            raise HomeAssistantError("Enter a digit to send")

        if len(digit) != 1 or digit not in "0123456789":
            raise HomeAssistantError("Digit must be between 0 and 9")

        state: TsuryPhoneState = self.coordinator.data
        if state.app_state != AppState.IDLE:
            raise HomeAssistantError("Phone must be idle to dial digits")

        await self.coordinator.api_client.dial_digit(digit)

        # Clear the buffer so the UI input resets after sending
        self._clear_buffer("dial_digit", ("digit",))
        self.coordinator.async_update_listeners()

    async def _add_quick_dial_entry(self) -> None:
        """Add a quick dial entry from buffered text inputs."""
        buffer = self._get_buffer_snapshot("quick_dial")
        code = buffer.get("code", "")
        number = buffer.get("number", "")
        name = buffer.get("name", "")

        missing: list[str] = []
        if not code:
            missing.append("code")
        if not number:
            missing.append("number")

        if missing:
            raise HomeAssistantError(
                "Quick dial input missing required field(s): " + ", ".join(missing)
            )

        if not name:
            raise HomeAssistantError("Enter a name for the quick dial entry")

        device_number = self._prepare_number_input(
            number, field="Quick dial number"
        )

        try:
            await self.coordinator.api_client.add_quick_dial(code, device_number, name)
            self.coordinator.selected_quick_dial_code = code
            self.coordinator.remember_number_display_hint(number)
            self._clear_buffer("quick_dial")
            await self.coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to add quick dial entry: {err}") from err

    async def _remove_selected_quick_dial(self) -> None:
        """Remove the currently selected quick dial entry."""
        selected_code = getattr(self.coordinator, "selected_quick_dial_code", None)

        if not selected_code:
            raise HomeAssistantError("Select a quick dial entry to remove")

        try:
            await self.coordinator.api_client.remove_quick_dial(selected_code)
            self.coordinator.selected_quick_dial_code = None
            self._clear_buffer("quick_dial")
            await self.coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to remove quick dial entry '{selected_code}': {err}"
            ) from err

    async def _add_blocked_number(self) -> None:
        """Add a blocked number from buffered text inputs."""
        buffer = self._get_buffer_snapshot("blocked")
        number = buffer.get("number", "")
        reason = buffer.get("reason", "")

        if not number:
            raise HomeAssistantError("Enter a number to block")

        if not reason:
            raise HomeAssistantError("Enter a reason for blocking this number")

        device_number = self._prepare_number_input(
            number, field="Blocked number"
        )

        try:
            await self.coordinator.api_client.add_blocked_number(
                device_number, reason
            )
            self.coordinator.selected_blocked_number = device_number
            self.coordinator.remember_number_display_hint(number)
            self._clear_buffer("blocked")
            await self.coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to add blocked number: {err}") from err

    async def _remove_selected_blocked_number(self) -> None:
        """Remove the selected blocked number."""
        selected_number = getattr(self.coordinator, "selected_blocked_number", None)

        if not selected_number:
            raise HomeAssistantError("Select a blocked number to remove")

        try:
            await self.coordinator.api_client.remove_blocked_number(selected_number)
            self.coordinator.selected_blocked_number = None
            self._clear_buffer("blocked")
            await self.coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to remove blocked number '{selected_number}': {err}"
            ) from err

    async def _add_priority_number(self) -> None:
        """Add a priority number from buffered text inputs."""
        buffer = self._get_buffer_snapshot("priority")
        number = buffer.get("number", "")

        if not number:
            raise HomeAssistantError("Enter a priority number to add")

        device_number = self._prepare_number_input(
            number, field="Priority number"
        )

        try:
            await self.coordinator.api_client.add_priority_caller(device_number)
            self.coordinator.selected_priority_number = device_number
            self.coordinator.remember_number_display_hint(number)
            self._clear_buffer("priority")
            await self.coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to add priority number '{number}': {err}"
            ) from err

    async def _remove_selected_priority_number(self) -> None:
        """Remove the selected priority number."""
        selected_number = getattr(self.coordinator, "selected_priority_number", None)

        if not selected_number:
            raise HomeAssistantError("Select a priority number to remove")

        try:
            await self.coordinator.api_client.remove_priority_caller(selected_number)
            self.coordinator.selected_priority_number = None
            self._clear_buffer("priority")
            await self.coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to remove priority number '{selected_number}': {err}"
            ) from err

    async def _add_webhook_action(self) -> None:
        """Add a webhook action from buffered text inputs."""
        buffer = self._get_buffer_snapshot("webhook")
        code = buffer.get("code", "")
        webhook_id = buffer.get("webhook_id", "")
        action_name = buffer.get("action_name", "")

        missing: list[str] = []
        if not code:
            missing.append("code")
        if not webhook_id:
            missing.append("webhook_id")

        if missing:
            raise HomeAssistantError(
                "Webhook input missing required field(s): " + ", ".join(missing)
            )

        try:
            await self.coordinator.api_client.add_webhook_action(
                code, webhook_id, action_name
            )
            self.coordinator.selected_webhook_code = code
            self._clear_buffer("webhook")
            await self.coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to add webhook action: {err}") from err

    async def _remove_selected_webhook(self) -> None:
        """Remove the selected webhook action."""
        selected_code = getattr(self.coordinator, "selected_webhook_code", None)

        if not selected_code:
            raise HomeAssistantError("Select a webhook action to remove")

        try:
            await self.coordinator.api_client.remove_webhook_action(selected_code)
            self.coordinator.selected_webhook_code = None
            self._clear_buffer("webhook")
            await self.coordinator.async_request_refresh()
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to remove webhook '{selected_code}': {err}"
            ) from err

    async def _toggle_call_waiting(self) -> None:
        """Toggle call waiting state."""
        state: TsuryPhoneState = self.coordinator.data

        if not state.call_waiting_available:
            raise HomeAssistantError("Call waiting not available on this device")

        await self.coordinator.api_client.switch_call_waiting()

    def _get_user_friendly_error(self, error: TsuryPhoneAPIError) -> str:
        """Convert API error to user-friendly message."""
        if self.coordinator.api_client.is_api_error_code(
            error, ERROR_CODE_NO_INCOMING_CALL
        ):
            return "No incoming call"
        elif self.coordinator.api_client.is_api_error_code(
            error, ERROR_CODE_NO_ACTIVE_CALL
        ):
            return "No active call"
        elif self.coordinator.api_client.is_api_error_code(
            error, ERROR_CODE_CODE_CONFLICT
        ):
            return "Code conflicts with an existing quick dial or webhook action"
        elif self.coordinator.api_client.is_api_error_code(
            error, ERROR_CODE_PHONE_NOT_READY
        ):
            return "Phone is busy"
        elif self.coordinator.api_client.is_api_error_code(
            error, ERROR_CODE_MISSING_DIGIT
        ):
            return "Digit is required"
        elif self.coordinator.api_client.is_api_error_code(
            error, ERROR_CODE_INVALID_DIGIT
        ):
            return "Digit must be between 0 and 9"
        elif self.coordinator.api_client.is_api_error_code(
            error, ERROR_CODE_DIAL_BUFFER_FULL
        ):
            return "Dial buffer is full"
        else:
            return str(error)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes."""
        state: TsuryPhoneState = self.coordinator.data
        attributes = {}

        # Add restoration indicator if available
        if hasattr(state, "restored") and state.restored:
            attributes["restored"] = True

        # Add specific attributes per button type
        if self.entity_description.key == "answer":
            attributes["can_execute"] = state.is_incoming_call
            if state.is_incoming_call and state.current_call.number:
                attributes["incoming_number"] = state.current_call.number

        elif self.entity_description.key == "hangup":
            can_execute = bool(
                state.is_call_active
                or state.is_incoming_call
                or state.is_dialing
                or state.current_call.number
                or state.current_dialing_number
            )
            attributes["can_execute"] = can_execute
            if state.current_call.number:
                attributes["active_call_number"] = state.current_call.number
                # Add call duration
                duration = self.coordinator.current_call_duration_seconds
                if duration > 0:
                    attributes["call_duration_seconds"] = duration
            if state.current_dialing_number:
                attributes["current_dialing_number"] = state.current_dialing_number

        elif self.entity_description.key == "ring":
            attributes["current_ring_pattern"] = state.ring_pattern or "default"

        elif self.entity_description.key == "toggle_call_waiting":
            attributes["can_execute"] = state.call_waiting_available
            attributes["call_waiting_available"] = state.call_waiting_available

        elif self.entity_description.key == "dial_selected":
            # Phase P4: Show actual selection state
            has_selection = (
                hasattr(self.coordinator, "selected_quick_dial_code")
                and self.coordinator.selected_quick_dial_code is not None
            )
            attributes["can_execute"] = has_selection and state.quick_dial_count > 0

            if has_selection:
                attributes["selected_code"] = self.coordinator.selected_quick_dial_code
                # Find the selected entry details
                if state.quick_dials:
                    for entry in state.quick_dials:
                        if entry.code == self.coordinator.selected_quick_dial_code:
                            attributes["selected_number"] = entry.number
                            attributes["selected_name"] = entry.name
                            break
            else:
                attributes["selected_quick_dial"] = None

        elif self.entity_description.key == "dial_digit_send":
            buffer = self._get_buffer_snapshot("dial_digit")
            digit = buffer.get("digit", "")
            attributes["buffered_digit"] = digit
            attributes["can_execute"] = bool(digit and state.app_state == AppState.IDLE)
            if state.current_dialing_number:
                attributes["current_dialing_number"] = state.current_dialing_number

        elif self.entity_description.key == "quick_dial_add":
            buffer = self._get_buffer_snapshot("quick_dial")
            has_required = self._buffer_has_values("quick_dial", ("code", "number"))
            attributes["can_execute"] = bool(has_required and state.connected)
            attributes["required_fields"] = ["code", "number"]
            if not has_required:
                missing = [
                    field for field in ("code", "number") if not buffer.get(field)
                ]
                if missing:
                    attributes["missing_fields"] = missing
            attributes["buffer"] = {
                "code": buffer.get("code", ""),
                "number": buffer.get("number", ""),
                "name": buffer.get("name", ""),
            }

        elif self.entity_description.key == "quick_dial_remove":
            selected_code = getattr(self.coordinator, "selected_quick_dial_code", None)
            attributes["can_execute"] = bool(selected_code and state.connected)
            attributes["selected_code"] = selected_code

        elif self.entity_description.key == "blocked_add":
            buffer = self._get_buffer_snapshot("blocked")
            has_required = self._buffer_has_values("blocked", ("number",))
            attributes["can_execute"] = bool(has_required and state.connected)
            if not has_required:
                attributes["missing_fields"] = ["number"]
            attributes["buffer"] = {
                "number": buffer.get("number", ""),
                "reason": buffer.get("reason", ""),
            }

        elif self.entity_description.key == "blocked_remove":
            selected_number = getattr(self.coordinator, "selected_blocked_number", None)
            attributes["can_execute"] = bool(selected_number and state.connected)
            attributes["selected_number"] = selected_number

        elif self.entity_description.key == "priority_add":
            buffer = self._get_buffer_snapshot("priority")
            has_required = self._buffer_has_values("priority", ("number",))
            attributes["can_execute"] = bool(has_required and state.connected)
            if not has_required:
                attributes["missing_fields"] = ["number"]
            attributes["buffer"] = {"number": buffer.get("number", "")}

        elif self.entity_description.key == "priority_remove":
            selected_number = getattr(
                self.coordinator, "selected_priority_number", None
            )
            attributes["can_execute"] = bool(selected_number and state.connected)
            attributes["selected_number"] = selected_number

        elif self.entity_description.key == "webhook_add":
            buffer = self._get_buffer_snapshot("webhook")
            has_required = self._buffer_has_values("webhook", ("code", "webhook_id"))
            attributes["required_fields"] = ["code", "webhook_id"]
            if not has_required:
                missing = [
                    field for field in ("code", "webhook_id") if not buffer.get(field)
                ]
                if missing:
                    attributes["missing_fields"] = missing
            attributes["buffer"] = {
                "code": buffer.get("code", ""),
                "webhook_id": buffer.get("webhook_id", ""),
                "action_name": buffer.get("action_name", ""),
            }
            attributes["can_execute"] = bool(has_required and state.connected)

        elif self.entity_description.key == "webhook_remove":
            selected_code = getattr(self.coordinator, "selected_webhook_code", None)
            attributes["can_execute"] = bool(selected_code and state.connected)
            attributes["selected_code"] = selected_code

        elif self.entity_description.key in ["refetch", "refresh_snapshot"]:
            # Show last update time
            if hasattr(self.coordinator, "last_update_time"):
                attributes["last_refresh"] = self.coordinator.last_update_time

        # Add connection status for troubleshooting
        if not state.connected:
            attributes["last_seen"] = state.last_seen
            attributes["connection_status"] = "disconnected"

        # Add execution readiness for UI
        attributes["device_connected"] = state.connected

        return attributes if attributes else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        state: TsuryPhoneState = self.coordinator.data

        # Most buttons require device connection
        if self.entity_description.key in [
            "answer",
            "hangup",
            "ring",
            "reset",
            "toggle_call_waiting",
        ]:
            if not (self.coordinator.last_update_success and state.connected):
                return False

            if self.entity_description.key == "answer":
                if state.is_call_active:
                    return False

                return bool(state.is_incoming_call or state.ringing)
            if self.entity_description.key == "hangup":
                return bool(
                    state.is_call_active
                    or state.is_incoming_call
                    or state.is_dialing
                    or state.current_call.number
                    or state.current_dialing_number
                )
            if self.entity_description.key == "toggle_call_waiting":
                return state.call_waiting_available

            return True

        # Data refresh buttons can work if we have coordinator data
        elif self.entity_description.key in ["refetch", "refresh_snapshot"]:
            return self.coordinator.last_update_success and state.connected

        elif self.entity_description.key == "dial_digit_send":
            if not (self.coordinator.last_update_success and state.connected):
                return False

            buffer = self._get_buffer_snapshot("dial_digit")
            digit = buffer.get("digit", "").strip()
            if not digit:
                return False

            return state.app_state == AppState.IDLE

        # Phase P4 features
        elif self.entity_description.key == "dial_selected":
            # Available if we have a selection and quick dial entries exist
            has_selection = (
                hasattr(self.coordinator, "selected_quick_dial_code")
                and self.coordinator.selected_quick_dial_code is not None
            )
            return (
                self.coordinator.last_update_success
                and state.connected
                and has_selection
                and state.quick_dial_count > 0
            )

        elif self.entity_description.key in [
            "quick_dial_add",
            "quick_dial_remove",
            "blocked_add",
            "blocked_remove",
            "priority_add",
            "priority_remove",
            "webhook_add",
            "webhook_remove",
        ]:
            if not (self.coordinator.last_update_success and state.connected):
                return False

            key = self.entity_description.key
            if key == "quick_dial_add":
                return self._buffer_has_values("quick_dial", ("code", "number"))
            if key == "quick_dial_remove":
                return bool(getattr(self.coordinator, "selected_quick_dial_code", None))
            if key == "blocked_add":
                return self._buffer_has_values("blocked", ("number",))
            if key == "blocked_remove":
                return bool(getattr(self.coordinator, "selected_blocked_number", None))
            if key == "priority_add":
                return self._buffer_has_values("priority", ("number",))
            if key == "priority_remove":
                return bool(getattr(self.coordinator, "selected_priority_number", None))
            if key == "webhook_add":
                return self._buffer_has_values("webhook", ("code", "webhook_id"))
            if key == "webhook_remove":
                return bool(getattr(self.coordinator, "selected_webhook_code", None))

            return False

        return self.coordinator.last_update_success
