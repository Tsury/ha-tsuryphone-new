"""Switch platform for TsuryPhone integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError

from . import get_device_info, TsuryPhoneConfigEntry
from .api_client import TsuryPhoneAPIError
from .const import DOMAIN
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .models import TsuryPhoneState

_LOGGER = logging.getLogger(__name__)

SWITCH_DESCRIPTIONS = (
    SwitchEntityDescription(
        key="force_dnd",
        name="Force Do Not Disturb",
        icon="mdi:phone-off",
    ),
    SwitchEntityDescription(
        key="maintenance_mode",
        name="Maintenance Mode",
        icon="mdi:wrench",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: TsuryPhoneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TsuryPhone switch entities from a config entry."""
    coordinator = config_entry.runtime_data
    device_info = coordinator.device_info

    entities = [
        TsuryPhoneSwitch(coordinator, description, device_info)
        for description in SWITCH_DESCRIPTIONS
    ]

    async_add_entities(entities)


class TsuryPhoneSwitch(
    CoordinatorEntity[TsuryPhoneDataUpdateCoordinator], SwitchEntity
):
    """Representation of a TsuryPhone switch."""

    def __init__(
        self,
        coordinator: TsuryPhoneDataUpdateCoordinator,
        description: SwitchEntityDescription,
        device_info,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_info = device_info

        # Generate unique ID
        self._attr_unique_id = f"{device_info.device_id}_{description.key}"

        # Set device info
        self._attr_device_info = get_device_info(device_info)

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        state: TsuryPhoneState = self.coordinator.data

        if self.entity_description.key == "force_dnd":
            return state.dnd_config.force
        elif self.entity_description.key == "maintenance_mode":
            return state.maintenance_mode

        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            if self.entity_description.key == "force_dnd":
                await self._set_dnd_force(True)
            elif self.entity_description.key == "maintenance_mode":
                await self._set_maintenance_mode(True)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to turn on {self.name}: {err}") from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            if self.entity_description.key == "force_dnd":
                await self._set_dnd_force(False)
            elif self.entity_description.key == "maintenance_mode":
                await self._set_maintenance_mode(False)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to turn off {self.name}: {err}") from err

    async def _set_dnd_force(self, enabled: bool) -> None:
        """Set DND force mode."""
        # Use partial update - only send the force field
        dnd_config = {"force": enabled}

        await self.coordinator.api_client.set_dnd(dnd_config)

        # Update local state optimistically
        self.coordinator.data.dnd_config.force = enabled

        # Trigger coordinator update
        await self.coordinator.async_request_refresh()

    async def _set_maintenance_mode(self, enabled: bool) -> None:
        """Set maintenance mode."""
        await self.coordinator.api_client.set_maintenance_mode(enabled)

        # Update local state optimistically
        self.coordinator.data.maintenance_mode = enabled

        # Trigger coordinator update
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, any] | None:
        """Return additional state attributes."""
        state: TsuryPhoneState = self.coordinator.data
        attributes = {}

        # Add restoration indicator if available
        if hasattr(state, "restored") and state.restored:
            attributes["restored"] = True

        # Add specific attributes per switch type
        if self.entity_description.key == "force_dnd":
            # Add DND schedule info for context
            if state.dnd_config.scheduled:
                attributes["schedule_enabled"] = True
                attributes["schedule_start"] = (
                    f"{state.dnd_config.start_hour:02d}:{state.dnd_config.start_minute:02d}"
                )
                attributes["schedule_end"] = (
                    f"{state.dnd_config.end_hour:02d}:{state.dnd_config.end_minute:02d}"
                )
            else:
                attributes["schedule_enabled"] = False

            # Show current DND active state
            attributes["dnd_currently_active"] = state.dnd_active

        elif self.entity_description.key == "maintenance_mode":
            # Add maintenance mode context
            if state.maintenance_mode:
                attributes["status"] = (
                    "Device in maintenance mode - may affect normal operation"
                )

        # Add connection status for troubleshooting
        if not state.connected:
            attributes["last_seen"] = state.last_seen
            attributes["connection_status"] = "disconnected"

        return attributes if attributes else None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Switch should be available if we have data and device is connected for control
        return self.coordinator.last_update_success and self.coordinator.data.connected
