"""Sensor platform for TsuryPhone integration."""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfInformation,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.typing import StateType

from . import get_device_info, TsuryPhoneConfigEntry
from .const import DOMAIN, AppState
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .models import TsuryPhoneState, CallInfo

SENSOR_DESCRIPTIONS = (
    SensorEntityDescription(
        key="app_state",
        name="Phone State",
        icon="mdi:phone-check",
    ),
    SensorEntityDescription(
        key="current_call_summary",
        name="Current Active Call",
        icon="mdi:phone",
    ),
    SensorEntityDescription(
        key="current_call_number",
        name="Current Active Call Number",
        icon="mdi:phone-in-talk",
    ),
    SensorEntityDescription(
        key="current_call_name",
        name="Current Active Call Name",
        icon="mdi:account-voice",
    ),
    SensorEntityDescription(
        key="current_dialing_number",
        name="Current Dialing Number",
        icon="mdi:phone-dial",
    ),
    SensorEntityDescription(
        key="current_call_direction",
        name="Current Active Call Direction",
        icon="mdi:phone-incoming",
    ),
    SensorEntityDescription(
        key="volume_mode",
        name="Call Audio Output",
        icon="mdi:volume-source",
    ),
    SensorEntityDescription(
        key="send_mode",
        name="Send Mode",
        icon="mdi:send",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="call_duration",
        name="Current Active Call Duration",
        icon="mdi:timer",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="waiting_call_summary",
        name="Current Waiting Call",
        icon="mdi:phone-clock",
    ),
    SensorEntityDescription(
        key="waiting_call_number",
        name="Current Waiting Call Number",
        icon="mdi:phone-clock",
    ),
    SensorEntityDescription(
        key="waiting_call_name",
        name="Current Waiting Call Name",
        icon="mdi:account-clock",
    ),
    SensorEntityDescription(
        key="waiting_call_direction",
        name="Current Waiting Call Direction",
        icon="mdi:swap-horizontal",
    ),
    SensorEntityDescription(
        key="waiting_call_duration",
        name="Current Waiting Call Duration",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="last_call_number",
        name="Last Call Number",
        icon="mdi:phone-log",
    ),
    SensorEntityDescription(
        key="last_call_name",
        name="Last Caller Name",
        icon="mdi:account-voice",
    ),
    SensorEntityDescription(
        key="last_call_direction",
        name="Last Call Direction",
        icon="mdi:compass",
    ),
    SensorEntityDescription(
        key="last_call_result",
        name="Last Call Result",
        icon="mdi:phone-log",
    ),
    SensorEntityDescription(
        key="last_call_duration",
        name="Last Call Duration",
        icon="mdi:timer",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="last_call_priority",
        name="Last Call Priority",
        icon="mdi:star",
    ),
    SensorEntityDescription(
        key="last_call_summary",
        name="Last Call",
        icon="mdi:phone",
    ),
    SensorEntityDescription(
        key="last_call_date",
        name="Last Call Date",
        icon="mdi:calendar-clock",
    ),
    SensorEntityDescription(
        key="uptime",
        name="Uptime",
        icon="mdi:clock-outline",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="rssi",
        name="WiFi Signal Strength",
        icon="mdi:wifi-strength-2",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="free_heap",
        name="Free Memory",
        icon="mdi:memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="calls_total",
        name="Total Calls",
        icon="mdi:phone",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="calls_incoming",
        name="Incoming Calls",
        icon="mdi:phone-incoming",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="calls_outgoing",
        name="Outgoing Calls",
        icon="mdi:phone-outgoing",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="calls_blocked",
        name="Blocked Calls",
        icon="mdi:phone-off",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="talk_time_total",
        name="Total Talk Time",
        icon="mdi:phone-in-talk",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="last_blocked_number",
        name="Last Blocked Number",
        icon="mdi:phone-remove",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="call_history_size",
        name="Call History Size",
        icon="mdi:history",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: TsuryPhoneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TsuryPhone sensor entities from a config entry."""
    coordinator = config_entry.runtime_data
    device_info = coordinator.device_info

    entities = [
        TsuryPhoneSensor(coordinator, description, device_info)
        for description in SENSOR_DESCRIPTIONS
    ]

    async_add_entities(entities)


class TsuryPhoneSensor(
    CoordinatorEntity[TsuryPhoneDataUpdateCoordinator], SensorEntity
):
    """Representation of a TsuryPhone sensor."""

    def __init__(
        self,
        coordinator: TsuryPhoneDataUpdateCoordinator,
        description: SensorEntityDescription,
        device_info,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_info = device_info

        # Generate unique ID
        self._attr_unique_id = f"{device_info.device_id}_{description.key}"

        # Set device info
        self._attr_device_info = get_device_info(device_info)

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        state: TsuryPhoneState = self.coordinator.data

        if self.entity_description.key == "app_state":
            return self._format_app_state(state.app_state)
        elif self.entity_description.key == "current_call_summary":
            return self._build_current_call_summary(state)
        elif self.entity_description.key == "current_call_number":
            return state.current_call.number if state.current_call.number else None
        elif self.entity_description.key == "current_call_name":
            return state.current_call.name if state.current_call.name else None
        elif self.entity_description.key == "current_dialing_number":
            return (
                state.current_dialing_number if state.current_dialing_number else None
            )
        elif self.entity_description.key == "current_call_direction":
            direction = state.current_call.direction or state.current_call_direction
            return direction if direction else "Idle"
        elif self.entity_description.key == "volume_mode":
            return state.volume_mode_label
        elif self.entity_description.key == "send_mode":
            return "On" if self.coordinator.send_mode_enabled else "Off"
        elif self.entity_description.key == "call_duration":
            if state.is_call_active:
                return self.coordinator.current_call_duration_seconds
            if state.current_call.duration_seconds is not None:
                return state.current_call.duration_seconds
            if state.current_call.duration_ms is not None:
                return state.current_call.duration_ms // 1000
            return 0
        elif self.entity_description.key == "waiting_call_summary":
            return self._build_waiting_call_summary(state)
        elif self.entity_description.key == "waiting_call_number":
            return state.waiting_call.number or None
        elif self.entity_description.key == "waiting_call_name":
            return state.waiting_call.name or None
        elif self.entity_description.key == "waiting_call_direction":
            return self._get_waiting_call_direction(state)
        elif self.entity_description.key == "waiting_call_duration":
            if state.waiting_call.duration_seconds is not None:
                return state.waiting_call.duration_seconds
            if state.waiting_call.duration_ms is not None:
                return state.waiting_call.duration_ms // 1000
            return None
        elif self.entity_description.key == "last_call_number":
            return state.last_call.number if state.last_call.number else None
        elif self.entity_description.key == "last_call_name":
            return state.last_call.name if state.last_call.name else None
        elif self.entity_description.key == "last_call_direction":
            direction = state.last_call.direction
            if not direction and state.last_call.call_type:
                if state.last_call.call_type.startswith("incoming"):
                    direction = "incoming"
                elif state.last_call.call_type.startswith("outgoing"):
                    direction = "outgoing"
            return direction if direction else "Unknown"
        elif self.entity_description.key == "last_call_result":
            return self._humanize_call_result(state.last_call)
        elif self.entity_description.key == "last_call_duration":
            if state.last_call.duration_seconds is not None:
                return state.last_call.duration_seconds
            if state.last_call.duration_ms is not None:
                return state.last_call.duration_ms // 1000
            return None
        elif self.entity_description.key == "last_call_priority":
            if state.last_call.number:
                return "Yes" if state.last_call.is_priority else "No"
            return "Unknown"
        elif self.entity_description.key == "last_call_summary":
            return self._build_last_call_summary(state)
        elif self.entity_description.key == "last_call_date":
            return self._get_last_call_iso_timestamp(state)
        elif self.entity_description.key == "uptime":
            return state.stats.uptime_seconds
        elif self.entity_description.key == "rssi":
            return state.stats.rssi_dbm if state.stats.rssi_dbm != 0 else None
        elif self.entity_description.key == "free_heap":
            return state.stats.free_heap_bytes
        elif self.entity_description.key == "calls_total":
            return state.stats.calls_total
        elif self.entity_description.key == "calls_incoming":
            return state.stats.calls_incoming
        elif self.entity_description.key == "calls_outgoing":
            return state.stats.calls_outgoing
        elif self.entity_description.key == "calls_blocked":
            return state.stats.calls_blocked
        elif self.entity_description.key == "talk_time_total":
            return state.stats.talk_time_seconds
        elif self.entity_description.key == "last_blocked_number":
            return state.last_blocked_number if state.last_blocked_number else None
        elif self.entity_description.key == "call_history_size":
            return state.call_history_size

        return None

    @property
    def extra_state_attributes(self) -> dict[str, any] | None:
        """Return additional state attributes."""
        state: TsuryPhoneState = self.coordinator.data
        attributes = {}

        # Add restoration indicator if available
        if hasattr(state, "restored") and state.restored:
            attributes["restored"] = True

        # Add specific attributes per sensor type
        if self.entity_description.key == "app_state":
            attributes["state_code"] = state.app_state.value
            attributes["previous_state"] = self._format_app_state(
                state.previous_app_state
            )
            attributes["previous_state_code"] = state.previous_app_state.value
            
            # Add quick_dials list for contacts view
            attributes["quick_dials"] = [
                {
                    "id": entry.id,
                    "code": entry.code,
                    "number": entry.number,  # Already normalized E.164 format
                    "name": entry.name,
                    "display_number": entry.display_number,
                }
                for entry in state.quick_dials
            ]
            
            # Add blocked_numbers list for blocked view
            attributes["blocked_numbers"] = [
                {
                    "id": entry.id,
                    "number": entry.number,  # Already normalized E.164 format
                    "name": entry.name,
                    "display_number": entry.display_number,
                }
                for entry in state.blocked_numbers
            ]
            
            # Add priority_callers list for priority indicators
            attributes["priority_callers"] = [
                {
                    "id": entry.id,
                    "number": entry.number,  # Already normalized E.164 format
                    "display_number": entry.display_number,
                }
                for entry in state.priority_callers
            ]

        elif self.entity_description.key == "current_call_summary":
            attributes.update(
                self._build_current_call_attributes(state, include_summary=True)
            )

        elif self.entity_description.key == "current_call_number":
            if state.current_call.number:
                attributes.update(self._build_current_call_attributes(state))

        elif self.entity_description.key == "current_call_name":
            if state.current_call.name:
                attributes["number"] = state.current_call.number
                attributes.update(self._build_current_call_attributes(state))

        elif self.entity_description.key == "current_call_direction":
            attributes.update(self._build_current_call_attributes(state))

        elif self.entity_description.key == "volume_mode":
            attributes["mode"] = state.volume_mode
            attributes["mode_code"] = state.volume_mode_code
            attributes["is_speaker_mode"] = state.is_speaker_mode
            attributes["call_active"] = state.is_call_active

        elif self.entity_description.key == "last_call_summary":
            attributes.update(
                self._build_last_call_attributes(state, include_summary=True)
            )

        elif self.entity_description.key == "waiting_call_summary":
            attributes.update(
                self._build_waiting_call_attributes(state, include_summary=True)
            )

        elif self.entity_description.key == "waiting_call_number":
            if state.waiting_call.number:
                attributes.update(self._build_waiting_call_attributes(state))

        elif self.entity_description.key == "waiting_call_name":
            if state.waiting_call.name:
                attributes.update(self._build_waiting_call_attributes(state))

        elif self.entity_description.key == "waiting_call_direction":
            attributes.update(self._build_waiting_call_attributes(state))

        elif self.entity_description.key == "waiting_call_duration":
            attributes.update(self._build_waiting_call_attributes(state))

        elif self.entity_description.key == "last_call_date":
            attributes.update(
                self._build_last_call_attributes(state, include_summary=True)
            )
            if state.last_call.start_received_ts is not None:
                attributes["start_received_ts"] = state.last_call.start_received_ts
            if state.last_call.end_received_ts is not None:
                attributes["end_received_ts"] = state.last_call.end_received_ts

        elif self.entity_description.key == "last_call_number":
            if state.last_call.number:
                attributes["is_incoming"] = state.last_call.is_incoming
                attributes["call_start_ts"] = state.last_call.start_time
                attributes["direction"] = state.last_call.direction or (
                    "incoming" if state.last_call.is_incoming else "outgoing"
                )
                if state.last_call.result:
                    attributes["result"] = state.last_call.result
                if state.last_call.duration_seconds is not None:
                    attributes["duration_seconds"] = state.last_call.duration_seconds
                if state.last_call.duration_ms is not None:
                    attributes["duration_ms"] = state.last_call.duration_ms
                if state.last_call.normalized_number:
                    attributes["normalized_number"] = state.last_call.normalized_number
                if state.last_call.is_priority:
                    attributes["is_priority"] = True

        elif self.entity_description.key == "last_call_name":
            if state.last_call.name:
                attributes.update(self._build_last_call_attributes(state))

        elif self.entity_description.key == "last_call_direction":
            if state.last_call.number:
                attributes.update(self._build_last_call_attributes(state))

        elif self.entity_description.key == "last_call_result":
            attributes.update(self._build_last_call_attributes(state))

        elif self.entity_description.key == "last_call_duration":
            if state.last_call.number:
                attributes.update(self._build_last_call_attributes(state))

        elif self.entity_description.key == "last_call_priority":
            if state.last_call.number:
                attributes.update(self._build_last_call_attributes(state))

        elif self.entity_description.key == "call_duration":
            if state.is_call_active:
                attributes["call_number"] = state.current_call.number
                attributes["is_incoming"] = state.current_call.is_incoming
                attributes["call_start_ts"] = state.current_call.start_time
                attributes["direction"] = state.current_call.direction or (
                    "incoming" if state.current_call.is_incoming else "outgoing"
                )
                if state.current_call.is_priority:
                    attributes["is_priority"] = True
            else:
                attributes.update(self._build_current_call_attributes(state))

        elif self.entity_description.key == "call_history_size":
            attributes["capacity"] = state.call_history_capacity

            if state.call_history:
                # Add the full call history as a list of dicts
                attributes["entries"] = [
                    {
                        "number": entry.number,
                        "name": entry.name,
                        "call_type": entry.call_type,
                        "is_incoming": entry.is_incoming,
                        "duration_s": entry.duration_s,
                        "received_ts": entry.received_ts,
                        "reason": entry.reason,
                        "seq": entry.seq,
                    }
                    for entry in state.call_history
                ]
                
                # Add info about newest and oldest entries
                newest = state.call_history[-1]  # Newest is last
                oldest = state.call_history[0]  # Oldest is first

                attributes["newest_entry_number"] = newest.number
                attributes["newest_entry_type"] = newest.call_type

                # Calculate age of oldest entry
                import time

                oldest_age = time.time() - oldest.received_ts
                attributes["oldest_entry_age_s"] = int(oldest_age)

        elif self.entity_description.key == "rssi":
            # Add signal quality interpretation
            rssi = state.stats.rssi_dbm
            if rssi != 0:
                if rssi >= -50:
                    quality = "excellent"
                elif rssi >= -60:
                    quality = "good"
                elif rssi >= -70:
                    quality = "fair"
                else:
                    quality = "poor"
                attributes["signal_quality"] = quality

        # Add connection status for troubleshooting
        if not state.connected:
            attributes["last_seen"] = state.last_seen
            attributes["connection_status"] = "disconnected"

        # Add reboot detection flag
        if state.reboot_detected:
            attributes["reboot_detected"] = True

        return attributes if attributes else None

    def _build_current_call_attributes(
        self, state: TsuryPhoneState, *, include_summary: bool = False
    ) -> dict[str, Any]:
        """Collect attribute data for the current call sensors."""
        attributes: dict[str, Any] = {}
        call = state.current_call

        if include_summary:
            attributes["summary"] = self._build_current_call_summary(state)

        status = self._determine_current_call_status(state)
        attributes["status"] = status
        attributes["app_state"] = self._format_app_state(state.app_state)

        direction = call.direction or state.current_call_direction
        if direction:
            attributes["direction"] = direction

        if call.number:
            attributes["number"] = call.number
        if call.name:
            attributes["name"] = call.name
        if call.normalized_number:
            attributes["normalized_number"] = call.normalized_number

        if state.current_dialing_number:
            attributes["dialing_number"] = state.current_dialing_number

        if call.call_start_ts:
            attributes["call_start_ts"] = call.call_start_ts

        if call.call_id != -1:
            attributes["call_id"] = call.call_id

        if call.duration_seconds is not None:
            attributes["duration_seconds"] = call.duration_seconds
        if call.duration_ms is not None:
            attributes["duration_ms"] = call.duration_ms

        if call.result:
            attributes["result"] = call.result

        if call.is_priority:
            attributes["is_priority"] = True

        if call.is_on_hold:
            attributes["is_on_hold"] = True

        if call.is_blocked:
            attributes["is_blocked"] = True

        if call.start_received_ts is not None:
            attributes["start_received_ts"] = call.start_received_ts

        if call.end_received_ts is not None:
            attributes["end_received_ts"] = call.end_received_ts

        return attributes

    def _build_last_call_attributes(
        self, state: TsuryPhoneState, *, include_summary: bool = False
    ) -> dict[str, Any]:
        """Collect attribute data for the last call sensors."""
        attributes: dict[str, Any] = {}
        call = state.last_call

        if include_summary:
            attributes["summary"] = self._build_last_call_summary(state)

        direction = call.direction
        if not direction and call.call_type:
            if call.call_type.startswith("incoming"):
                direction = "incoming"
            elif call.call_type.startswith("outgoing"):
                direction = "outgoing"

        if direction:
            attributes["direction"] = direction

        if call.number:
            attributes["number"] = call.number
        if call.name:
            attributes["name"] = call.name
        if call.normalized_number:
            attributes["normalized_number"] = call.normalized_number

        attributes["is_incoming"] = call.is_incoming

        if call.call_start_ts:
            attributes["call_start_ts"] = call.call_start_ts

        if call.call_id != -1:
            attributes["call_id"] = call.call_id

        if call.duration_seconds is not None:
            attributes["duration_seconds"] = call.duration_seconds
        if call.duration_ms is not None:
            attributes["duration_ms"] = call.duration_ms

        human_result = self._humanize_call_result(call)
        if human_result:
            attributes["result"] = human_result

        if call.is_priority:
            attributes["is_priority"] = True

        if call.is_blocked:
            attributes["is_blocked"] = True

        if call.start_received_ts is not None:
            attributes["start_received_ts"] = call.start_received_ts

        if call.end_received_ts is not None:
            attributes["end_received_ts"] = call.end_received_ts

        return attributes

    def _build_current_call_summary(self, state: TsuryPhoneState) -> str:
        """Generate a friendly summary for the active call context."""
        status = self._determine_current_call_status(state)
        call = state.current_call

        if status == "idle":
            return "Idle"

        parts: list[str] = []
        status_map = {
            "in_call": "In call",
            "ringing": "Ringing",
            "incoming": "Incoming",
            "dialing": "Dialing",
            "context": "Call",
        }
        parts.append(status_map.get(status, status.title()))

        direction = call.direction or state.current_call_direction
        if direction:
            parts.append(direction.capitalize())

        if call.is_priority:
            parts.append("(Priority)")

        contact = self._format_call_contact(call)
        if contact:
            parts.append(contact)
        elif state.current_dialing_number:
            parts.append(state.current_dialing_number)

        if call.result:
            parts.append(self._humanize_call_result(call))

        duration = call.duration_seconds
        if duration is None and call.duration_ms is not None:
            duration = call.duration_ms // 1000
        if duration:
            parts.append(f"{duration}s")

        return " ".join(parts).strip()

    def _build_waiting_call_summary(self, state: TsuryPhoneState) -> str:
        """Generate a friendly summary for the waiting call (if any)."""
        call = state.waiting_call
        if not call.number and not call.name:
            return "No waiting call"

        parts: list[str] = []

        direction = self._get_waiting_call_direction(state)
        if direction:
            parts.append(direction.capitalize())

        if call.is_priority:
            parts.append("(Priority)")

        if call.is_on_hold:
            parts.append("(On Hold)")

        if call.is_blocked:
            parts.append("(Blocked)")

        contact = self._format_call_contact(call)
        if contact:
            parts.append(contact)

        duration = call.duration_seconds
        if duration is None and call.duration_ms is not None:
            duration = call.duration_ms // 1000
        if duration:
            parts.append(f"{duration}s")

        return " ".join(parts).strip()

    def _build_last_call_summary(self, state: TsuryPhoneState) -> str:
        """Generate a friendly summary for the most recent call."""
        call = state.last_call
        if not call.number and not call.name:
            return "No recent call"

        parts: list[str] = []

        direction = call.direction
        if not direction and call.call_type:
            if call.call_type.startswith("incoming"):
                direction = "incoming"
            elif call.call_type.startswith("outgoing"):
                direction = "outgoing"
        if direction:
            parts.append(direction.capitalize())

        human_result = self._humanize_call_result(call)
        if human_result:
            parts.append(human_result)

        if call.is_priority:
            parts.append("(Priority)")

        contact = self._format_call_contact(call)
        if contact:
            parts.append(contact)

        duration = call.duration_seconds
        if duration is None and call.duration_ms is not None:
            duration = call.duration_ms // 1000
        if duration:
            parts.append(f"{duration}s")

        return " ".join(parts).strip()

    def _determine_current_call_status(self, state: TsuryPhoneState) -> str:
        """Return a machine-friendly label for the current call status."""
        if state.is_call_active:
            return "in_call"
        if state.ringing or state.is_incoming_call:
            return "ringing"
        if state.current_call.number:
            return "context"
        if state.current_dialing_number:
            return "dialing"
        return "idle"

    def _format_call_contact(self, call: CallInfo) -> str | None:
        """Format the best available representation of a call contact."""
        if call.name and call.number:
            return f"{call.name} ({call.number})"
        if call.name:
            return call.name
        if call.number:
            return call.number
        return None

    def _build_waiting_call_attributes(
        self, state: TsuryPhoneState, *, include_summary: bool = False
    ) -> dict[str, Any]:
        """Collect attribute data for waiting call sensors."""
        attributes: dict[str, Any] = {}
        call = state.waiting_call

        if include_summary:
            attributes["summary"] = self._build_waiting_call_summary(state)

        direction = self._get_waiting_call_direction(state)
        if direction:
            attributes["direction"] = direction

        if call.number:
            attributes["number"] = call.number
        if call.name:
            attributes["name"] = call.name
        if call.normalized_number:
            attributes["normalized_number"] = call.normalized_number

        if call.call_id != -1:
            attributes["call_id"] = call.call_id

        if call.call_start_ts:
            attributes["call_start_ts"] = call.call_start_ts

        if call.duration_seconds is not None:
            attributes["duration_seconds"] = call.duration_seconds
        if call.duration_ms is not None:
            attributes["duration_ms"] = call.duration_ms

        if call.is_priority:
            attributes["is_priority"] = True

        if call.is_on_hold:
            attributes["is_on_hold"] = True

        if call.is_blocked:
            attributes["is_blocked"] = True

        if call.start_received_ts is not None:
            attributes["start_received_ts"] = call.start_received_ts

        if call.end_received_ts is not None:
            attributes["end_received_ts"] = call.end_received_ts

        attributes["available"] = bool(call.number)

        return attributes

    def _get_waiting_call_direction(self, state: TsuryPhoneState) -> str | None:
        """Return the direction label for the waiting call, if present."""
        call = state.waiting_call
        direction = call.direction
        if not direction:
            if call.is_incoming:
                direction = "incoming"
            elif call.is_incoming is False:
                direction = "outgoing"
        return direction or None

    def _timestamp_to_iso(self, value: float | int | None) -> str | None:
        """Convert a timestamp (seconds or milliseconds) to ISO-8601."""
        if value in (None, 0):
            return None

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None

        # Handle millisecond precision inputs
        if numeric > 1_000_000_000_000:
            numeric /= 1000.0

        try:
            return dt_util.utc_from_timestamp(numeric).isoformat()
        except (ValueError, OSError):
            return None

    def _get_last_call_iso_timestamp(self, state: TsuryPhoneState) -> str | None:
        """Compute the last-call start timestamp formatted as ISO-8601."""
        ts: float | int | None = state.last_call.start_received_ts

        if ts is None and state.last_call.call_start_ts:
            ts = state.last_call.call_start_ts

        if ts is None and state.last_call.end_received_ts is not None:
            duration = state.last_call.duration_seconds
            if duration is None and state.last_call.duration_ms is not None:
                duration = state.last_call.duration_ms / 1000
            if duration is not None:
                ts = state.last_call.end_received_ts - duration

        return self._timestamp_to_iso(ts)

    def _humanize_call_result(self, call: CallInfo) -> str:
        """Convert result/call type fields into a user-friendly label."""
        # Prioritize explicit result field from firmware
        if call.result:
            return call.result.replace("_", " ").title()

        if not call.call_type:
            return "Unknown"

        mapping = {
            "incoming_answered": "Answered",
            "incoming_missed": "Missed",
            "incoming_blocked": "Blocked",
            "outgoing_answered": "Completed",
            "outgoing_unanswered": "No Answer",
            "blocked": "Blocked",
        }
        return mapping.get(call.call_type, call.call_type.replace("_", " ").title())

    def _format_app_state(self, app_state: AppState) -> str:
        """Format app state for display."""
        state_names = {
            AppState.STARTUP: "Startup",
            AppState.CHECK_HARDWARE: "Checking Hardware",
            AppState.CHECK_LINE: "Checking Line",
            AppState.IDLE: "Idle",
            AppState.INVALID_NUMBER: "Invalid Number",
            AppState.INCOMING_CALL: "Incoming Call",
            AppState.INCOMING_CALL_RING: "Ringing",
            AppState.IN_CALL: "In Call",
            AppState.DIALING: "Dialing",
        }
        return state_names.get(app_state, f"Unknown ({app_state.value})")

    @property
    def icon(self) -> str | None:
        """Return icon, adapting to dynamic volume mode state."""

        if self.entity_description.key == "volume_mode":
            state: TsuryPhoneState = self.coordinator.data
            return "mdi:volume-high" if state.is_speaker_mode else "mdi:headset"

        return super().icon

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Entity is available if we have data (even if device is offline)
        return self.coordinator.last_update_success
