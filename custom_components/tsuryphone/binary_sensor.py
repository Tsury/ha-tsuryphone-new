"""Binary sensor platform for TsuryPhone integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import get_device_info, TsuryPhoneConfigEntry
from .const import DOMAIN
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .models import TsuryPhoneState

BINARY_SENSOR_DESCRIPTIONS = (
    BinarySensorEntityDescription(
        key="ringing",
        name="Ringing",
        icon="mdi:phone-ring",
        device_class=BinarySensorDeviceClass.SOUND,
    ),
    BinarySensorEntityDescription(
        key="dnd",
        name="Do Not Disturb",
        icon="mdi:phone-off",
    ),
    BinarySensorEntityDescription(
        key="maintenance_mode",
        name="Maintenance Mode",
        icon="mdi:wrench",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    BinarySensorEntityDescription(
        key="hook_off",
        name="Handset Off Hook",
        icon="mdi:phone-hangup",
    ),
    BinarySensorEntityDescription(
        key="in_call",
        name="In Call",
        icon="mdi:phone-in-talk",
    ),
    BinarySensorEntityDescription(
        key="call_waiting_available",
        name="Call Waiting Available",
        icon="mdi:phone-plus",
    ),
    BinarySensorEntityDescription(
        key="current_call_priority",
        name="Priority Call Active",
        icon="mdi:star",
        entity_registry_enabled_default=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: TsuryPhoneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TsuryPhone binary sensor entities from a config entry."""
    coordinator = config_entry.runtime_data
    device_info = coordinator.device_info

    entities = [
        TsuryPhoneBinarySensor(coordinator, description, device_info)
        for description in BINARY_SENSOR_DESCRIPTIONS
    ]

    async_add_entities(entities)


class TsuryPhoneBinarySensor(
    CoordinatorEntity[TsuryPhoneDataUpdateCoordinator], BinarySensorEntity
):
    """Representation of a TsuryPhone binary sensor."""

    def __init__(
        self,
        coordinator: TsuryPhoneDataUpdateCoordinator,
        description: BinarySensorEntityDescription,
        device_info,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_info = device_info

        # Generate unique ID
        self._attr_unique_id = f"{device_info.device_id}_{description.key}"

        # Set device info
        self._attr_device_info = get_device_info(device_info)

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        state: TsuryPhoneState = self.coordinator.data

        if self.entity_description.key == "ringing":
            return state.ringing
        elif self.entity_description.key == "dnd":
            return state.dnd_active
        elif self.entity_description.key == "maintenance_mode":
            return state.maintenance_mode
        elif self.entity_description.key == "hook_off":
            return state.hook_off
        elif self.entity_description.key == "in_call":
            return state.is_call_active
        elif self.entity_description.key == "call_waiting_available":
            return state.call_waiting_available
        elif self.entity_description.key == "current_call_priority":
            return state.current_call_is_priority

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
        if self.entity_description.key == "call_waiting_available":
            # Add heuristic mode indicator (R61 detail)
            attributes["mode"] = (
                "heuristic"  # Will be "firmware" when device exposes field
            )

            # Add toggle statistics if available
            if hasattr(state, "call_waiting_toggles"):
                attributes["toggles_this_boot"] = state.call_waiting_toggles

        elif self.entity_description.key == "in_call":
            # Add call details when in call
            if state.is_call_active and state.current_call.number:
                attributes["call_number"] = state.current_call.number
                attributes["is_incoming"] = state.current_call.is_incoming
                attributes["call_start_ts"] = state.current_call.start_time

                # Add current duration
                if hasattr(self.coordinator, "current_call_duration_seconds"):
                    duration = self.coordinator.current_call_duration_seconds
                    if duration > 0:
                        attributes["duration_seconds"] = duration

        elif self.entity_description.key == "ringing":
            # Add incoming call details when ringing
            if state.ringing and state.current_call.number:
                attributes["incoming_number"] = state.current_call.number
        elif self.entity_description.key == "hook_off":
            attributes["handset"] = "off_hook" if state.hook_off else "on_hook"
        elif self.entity_description.key == "current_call_priority":
            # Add current priority call number if flag active
            if state.current_call_is_priority and state.current_call.number:
                attributes["priority_number"] = state.current_call.number
                attributes["is_incoming"] = state.current_call.is_incoming

        # Add connection status for troubleshooting
        if not state.connected:
            attributes["last_seen"] = state.last_seen
            attributes["connection_status"] = "disconnected"

        return attributes if attributes else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Entity is available if we have data (even if device is offline)
        return self.coordinator.last_update_success
