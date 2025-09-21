"""Button platform for TsuryPhone integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError

from . import get_device_info, TsuryPhoneConfigEntry
from .api_client import TsuryPhoneAPIError
from .const import DOMAIN, ERROR_CODE_NO_INCOMING_CALL, ERROR_CODE_NO_ACTIVE_CALL
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .models import TsuryPhoneState

_LOGGER = logging.getLogger(__name__)

BUTTON_DESCRIPTIONS = (
    ButtonEntityDescription(
        key="answer",
        name="Answer Call",
        icon="mdi:phone",
    ),
    ButtonEntityDescription(
        key="hangup",
        name="Hang Up",
        icon="mdi:phone-hangup",
    ),
    ButtonEntityDescription(
        key="ring",
        name="Ring Device",
        icon="mdi:phone-ring",
        entity_category="config",
    ),
    ButtonEntityDescription(
        key="reset",
        name="Reset Device",
        icon="mdi:restart",
        entity_category="config",
    ),
    ButtonEntityDescription(
        key="refetch",
        name="Refresh Device Data",
        icon="mdi:refresh",
        entity_category="diagnostic",
    ),
    ButtonEntityDescription(
        key="refresh_snapshot",
        name="Refresh Snapshot",
        icon="mdi:camera-flip",
        entity_category="diagnostic",
    ),
    ButtonEntityDescription(
        key="dial_selected",
        name="Dial Selected Quick Dial",
        icon="mdi:speed-dial",
        entity_registry_enabled_default=False,  # Phase P4 feature
    ),
    ButtonEntityDescription(
        key="toggle_call_waiting",
        name="Toggle Call Waiting",
        icon="mdi:phone-plus",
        entity_registry_enabled_default=False,  # Advanced feature
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: TsuryPhoneConfigEntry,
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


class TsuryPhoneButton(CoordinatorEntity[TsuryPhoneDataUpdateCoordinator], ButtonEntity):
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
            elif self.entity_description.key == "refetch":
                await self._refetch_data()
            elif self.entity_description.key == "refresh_snapshot":
                await self._refresh_snapshot()
            elif self.entity_description.key == "dial_selected":
                await self._dial_selected_quick_dial()
            elif self.entity_description.key == "toggle_call_waiting":
                await self._toggle_call_waiting()
        except TsuryPhoneAPIError as err:
            # Provide user-friendly error messages
            error_msg = self._get_user_friendly_error(err)
            raise HomeAssistantError(f"Failed to execute {self.name}: {error_msg}") from err

    async def _answer_call(self) -> None:
        """Answer the incoming call."""
        state: TsuryPhoneState = self.coordinator.data
        
        if not state.is_incoming_call:
            raise HomeAssistantError("No incoming call to answer")
        
        await self.coordinator.api_client.answer_call()

    async def _hangup_call(self) -> None:
        """Hang up the active call."""
        state: TsuryPhoneState = self.coordinator.data
        
        if not state.is_call_active:
            raise HomeAssistantError("No active call to hang up")
        
        await self.coordinator.api_client.hangup_call()

    async def _ring_device(self) -> None:
        """Ring the device with current pattern."""
        # Use empty pattern to trigger default device ring
        await self.coordinator.api_client.ring_device("")

    async def _reset_device(self) -> None:
        """Reset the device."""
        await self.coordinator.api_client.reset_device()

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
        if not hasattr(self.coordinator, 'selected_quick_dial_code') or not self.coordinator.selected_quick_dial_code:
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
            raise HomeAssistantError(f"Selected quick dial code '{selected_code}' not found")
        
        # Dial the number
        await self.coordinator.api_client.dial(selected_entry.number)

    async def _toggle_call_waiting(self) -> None:
        """Toggle call waiting state."""
        state: TsuryPhoneState = self.coordinator.data
        
        if not state.call_waiting_available:
            raise HomeAssistantError("Call waiting not available on this device")
        
        await self.coordinator.api_client.switch_call_waiting()

    def _get_user_friendly_error(self, error: TsuryPhoneAPIError) -> str:
        """Convert API error to user-friendly message."""
        if self.coordinator.api_client.is_api_error_code(error, ERROR_CODE_NO_INCOMING_CALL):
            return "No incoming call"
        elif self.coordinator.api_client.is_api_error_code(error, ERROR_CODE_NO_ACTIVE_CALL):
            return "No active call"
        else:
            return str(error)

    @property
    def extra_state_attributes(self) -> dict[str, any] | None:
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
            attributes["can_execute"] = state.is_call_active
            if state.is_call_active and state.current_call.number:
                attributes["active_call_number"] = state.current_call.number
                # Add call duration
                duration = self.coordinator.current_call_duration_seconds
                if duration > 0:
                    attributes["call_duration_seconds"] = duration

        elif self.entity_description.key == "ring":
            attributes["current_ring_pattern"] = state.ring_pattern or "default"

        elif self.entity_description.key == "toggle_call_waiting":
            attributes["can_execute"] = state.call_waiting_available
            attributes["call_waiting_available"] = state.call_waiting_available

        elif self.entity_description.key == "dial_selected":
            # Phase P4: Show actual selection state
            has_selection = (hasattr(self.coordinator, 'selected_quick_dial_code') and 
                           self.coordinator.selected_quick_dial_code is not None)
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
        if self.entity_description.key in ["answer", "hangup", "ring", "reset", "toggle_call_waiting"]:
            return self.coordinator.last_update_success and state.connected
        
        # Data refresh buttons can work if we have coordinator data
        elif self.entity_description.key in ["refetch", "refresh_snapshot"]:
            return self.coordinator.last_update_success and state.connected
        
        # Phase P4 features
        elif self.entity_description.key == "dial_selected":
            # Available if we have a selection and quick dial entries exist
            has_selection = (hasattr(self.coordinator, 'selected_quick_dial_code') and 
                           self.coordinator.selected_quick_dial_code is not None)
            return self.coordinator.last_update_success and state.connected and has_selection and state.quick_dial_count > 0
        
        return self.coordinator.last_update_success