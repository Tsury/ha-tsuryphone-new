"""Number platform for TsuryPhone integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError

from . import get_device_info, TsuryPhoneConfigEntry
from .api_client import TsuryPhoneAPIError
from .const import DOMAIN, AUDIO_MIN_LEVEL, AUDIO_MAX_LEVEL
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .models import TsuryPhoneState

_LOGGER = logging.getLogger(__name__)

NUMBER_DESCRIPTIONS = (
    NumberEntityDescription(
        key="earpiece_volume",
        name="Earpiece Volume",
        icon="mdi:volume-high",
        native_min_value=AUDIO_MIN_LEVEL,
        native_max_value=AUDIO_MAX_LEVEL,
        native_step=1,
    entity_category=EntityCategory.CONFIG,
    ),
    NumberEntityDescription(
        key="earpiece_gain",
        name="Earpiece Gain",
        icon="mdi:amplifier",
        native_min_value=AUDIO_MIN_LEVEL,
        native_max_value=AUDIO_MAX_LEVEL,
        native_step=1,
    entity_category=EntityCategory.CONFIG,
    ),
    NumberEntityDescription(
        key="speaker_volume",
        name="Speaker Volume",
        icon="mdi:volume-high",
        native_min_value=AUDIO_MIN_LEVEL,
        native_max_value=AUDIO_MAX_LEVEL,
        native_step=1,
    entity_category=EntityCategory.CONFIG,
    ),
    NumberEntityDescription(
        key="speaker_gain",
        name="Speaker Gain",
        icon="mdi:amplifier",
        native_min_value=AUDIO_MIN_LEVEL,
        native_max_value=AUDIO_MAX_LEVEL,
        native_step=1,
    entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: TsuryPhoneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TsuryPhone number entities from a config entry."""
    coordinator = config_entry.runtime_data
    device_info = coordinator.device_info

    entities = [
        TsuryPhoneNumber(coordinator, description, device_info)
        for description in NUMBER_DESCRIPTIONS
    ]

    async_add_entities(entities)


class TsuryPhoneNumber(CoordinatorEntity[TsuryPhoneDataUpdateCoordinator], NumberEntity):
    """Representation of a TsuryPhone number entity."""

    def __init__(
        self,
        coordinator: TsuryPhoneDataUpdateCoordinator,
        description: NumberEntityDescription,
        device_info,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_info = device_info

        # Generate unique ID
        self._attr_unique_id = f"{device_info.device_id}_{description.key}"
        
        # Set device info
        self._attr_device_info = get_device_info(device_info)

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        state: TsuryPhoneState = self.coordinator.data
        
        if self.entity_description.key == "earpiece_volume":
            return float(state.audio_config.earpiece_volume)
        elif self.entity_description.key == "earpiece_gain":
            return float(state.audio_config.earpiece_gain)
        elif self.entity_description.key == "speaker_volume":
            return float(state.audio_config.speaker_volume)
        elif self.entity_description.key == "speaker_gain":
            return float(state.audio_config.speaker_gain)
        
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        int_value = int(value)
        
        # Validate range (extra safety)
        if not (AUDIO_MIN_LEVEL <= int_value <= AUDIO_MAX_LEVEL):
            raise HomeAssistantError(
                f"Value {int_value} is out of range {AUDIO_MIN_LEVEL}-{AUDIO_MAX_LEVEL}"
            )
        
        try:
            # Use partial update - only send the changed field
            audio_config = {self._get_api_field_name(): int_value}
            
            await self.coordinator.api_client.set_audio_config(audio_config)
            
            # Update local state optimistically
            self._update_local_state(int_value)
            
            # Trigger coordinator update to get server confirmation
            await self.coordinator.async_request_refresh()
            
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to set {self.name}: {err}") from err

    def _get_api_field_name(self) -> str:
        """Get the API field name for this number entity."""
        # Map entity key to API field name (they match in this case)
        field_mapping = {
            "earpiece_volume": "earpieceVolume",
            "earpiece_gain": "earpieceGain", 
            "speaker_volume": "speakerVolume",
            "speaker_gain": "speakerGain",
        }
        return field_mapping.get(self.entity_description.key, self.entity_description.key)

    def _update_local_state(self, value: int) -> None:
        """Update local state optimistically."""
        if self.entity_description.key == "earpiece_volume":
            self.coordinator.data.audio_config.earpiece_volume = value
        elif self.entity_description.key == "earpiece_gain":
            self.coordinator.data.audio_config.earpiece_gain = value
        elif self.entity_description.key == "speaker_volume":
            self.coordinator.data.audio_config.speaker_volume = value
        elif self.entity_description.key == "speaker_gain":
            self.coordinator.data.audio_config.speaker_gain = value

    @property
    def extra_state_attributes(self) -> dict[str, any] | None:
        """Return additional state attributes."""
        state: TsuryPhoneState = self.coordinator.data
        attributes = {}

        # Add restoration indicator if available
        if hasattr(state, "restored") and state.restored:
            attributes["restored"] = True

        # Add audio config context
        if self.entity_description.key in ["earpiece_volume", "earpiece_gain"]:
            attributes["audio_type"] = "earpiece"
            attributes["other_earpiece_volume"] = state.audio_config.earpiece_volume
            attributes["other_earpiece_gain"] = state.audio_config.earpiece_gain
        elif self.entity_description.key in ["speaker_volume", "speaker_gain"]:
            attributes["audio_type"] = "speaker"
            attributes["other_speaker_volume"] = state.audio_config.speaker_volume
            attributes["other_speaker_gain"] = state.audio_config.speaker_gain

        # Add range info for UI
        attributes["min_value"] = AUDIO_MIN_LEVEL
        attributes["max_value"] = AUDIO_MAX_LEVEL

        # Add connection status for troubleshooting
        if not state.connected:
            attributes["last_seen"] = state.last_seen
            attributes["connection_status"] = "disconnected"

        return attributes if attributes else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Number entities should be available if we have data and device is connected for control
        return self.coordinator.last_update_success and self.coordinator.data.connected