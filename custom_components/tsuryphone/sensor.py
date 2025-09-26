"""Sensor platform for TsuryPhone integration."""
from __future__ import annotations

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
from .models import TsuryPhoneState
from .webhook_helpers import get_webhook_entity_attributes

SENSOR_DESCRIPTIONS = (
    SensorEntityDescription(
        key="app_state",
        name="Phone State",
        icon="mdi:phone-check",
    ),
    SensorEntityDescription(
        key="current_call_number",
        name="Current Call Number",
        icon="mdi:phone-in-talk",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="current_dialing_number",
        name="Current Dialing Number",
        icon="mdi:phone-dial",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="call_duration",
        name="Call Duration",
        icon="mdi:timer",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="last_call_number",
        name="Last Call Number",
        icon="mdi:phone-log",
        entity_registry_enabled_default=False,
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
        state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="free_heap",
        name="Free Memory",
        icon="mdi:memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.MEASUREMENT,
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
        state_class=SensorStateClass.TOTAL_INCREASING,
    entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="quick_dial_count",
        name="Quick Dial Count",
        icon="mdi:speed-dial",
        state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="blocked_count",
        name="Blocked Numbers Count",
        icon="mdi:phone-off",
        state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="last_blocked_number",
        name="Last Blocked Number",
        icon="mdi:phone-remove",
    entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="call_history_size",
        name="Call History Size",
        icon="mdi:history",
        state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Phase P4: Webhook management sensor
    SensorEntityDescription(
        key="webhook_status",
        name="Webhook Status",
        icon="mdi:webhook",
    entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
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


class TsuryPhoneSensor(CoordinatorEntity[TsuryPhoneDataUpdateCoordinator], SensorEntity):
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
        elif self.entity_description.key == "current_call_number":
            return state.current_call.number if state.current_call.number else None
        elif self.entity_description.key == "current_dialing_number":
            return state.current_dialing_number if state.current_dialing_number else None
        elif self.entity_description.key == "call_duration":
            if state.is_call_active:
                return self.coordinator.current_call_duration_seconds
            return 0
        elif self.entity_description.key == "last_call_number":
            return state.last_call.number if state.last_call.number else None
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
        elif self.entity_description.key == "quick_dial_count":
            return state.quick_dial_count
        elif self.entity_description.key == "blocked_count":
            return state.blocked_count
        elif self.entity_description.key == "last_blocked_number":
            return state.last_blocked_number if state.last_blocked_number else None
        elif self.entity_description.key == "call_history_size":
            return state.call_history_size
        elif self.entity_description.key == "webhook_status":
            # Phase P4: Webhook status sensor
            if not state.webhooks:
                return "no_webhooks"
            active_count = len([w for w in state.webhooks if w.active])
            if active_count == 0:
                return "inactive"
            elif active_count == len(state.webhooks):
                return "all_active"
            else:
                return "partially_active"
        
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
            attributes["previous_state"] = self._format_app_state(state.previous_app_state)
            attributes["previous_state_code"] = state.previous_app_state.value

        elif self.entity_description.key == "current_call_number":
            if state.current_call.number:
                attributes["is_incoming"] = state.current_call.is_incoming
                attributes["call_start_ts"] = state.current_call.start_time
                attributes["call_type"] = "incoming" if state.current_call.is_incoming else "outgoing"

        elif self.entity_description.key == "last_call_number":
            if state.last_call.number:
                attributes["is_incoming"] = state.last_call.is_incoming
                attributes["call_start_ts"] = state.last_call.start_time
                attributes["call_type"] = "incoming" if state.last_call.is_incoming else "outgoing"
                if state.last_call.duration_ms is not None:
                    attributes["duration_seconds"] = state.last_call.duration_ms // 1000

        elif self.entity_description.key == "call_duration":
            if state.is_call_active:
                attributes["call_number"] = state.current_call.number
                attributes["is_incoming"] = state.current_call.is_incoming
                attributes["call_start_ts"] = state.current_call.start_time

        elif self.entity_description.key == "call_history_size":
            attributes["capacity"] = state.call_history_capacity
            
            if state.call_history:
                # Add info about newest and oldest entries
                newest = state.call_history[-1]  # Newest is last
                oldest = state.call_history[0]   # Oldest is first
                
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

        elif self.entity_description.key == "webhook_status":
            # Phase P4: Add webhook management attributes
            webhook_attrs = get_webhook_entity_attributes(self.coordinator)
            attributes.update(webhook_attrs)

        # Add connection status for troubleshooting
        if not state.connected:
            attributes["last_seen"] = state.last_seen
            attributes["connection_status"] = "disconnected"

        # Add reboot detection flag
        if state.reboot_detected:
            attributes["reboot_detected"] = True

        return attributes if attributes else None

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
    def available(self) -> bool:
        """Return if entity is available."""
        # Entity is available if we have data (even if device is offline)
        return self.coordinator.last_update_success