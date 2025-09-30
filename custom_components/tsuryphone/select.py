"""Select platform for TsuryPhone integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError

from . import get_device_info, TsuryPhoneConfigEntry
from .api_client import TsuryPhoneAPIError
from .const import DOMAIN, RING_PATTERN_PRESETS
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .models import TsuryPhoneState

_LOGGER = logging.getLogger(__name__)

SELECT_DESCRIPTIONS = (
    SelectEntityDescription(
        key="ring_pattern",
        name="Ring Pattern",
        icon="mdi:phone-ring",
        entity_category=EntityCategory.CONFIG,
    ),
    SelectEntityDescription(
        key="quick_dial",
        name="Quick Dial",
        icon="mdi:speed-dial",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: TsuryPhoneConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TsuryPhone select entities from a config entry."""
    coordinator = config_entry.runtime_data
    device_info = coordinator.device_info

    entities = [
        TsuryPhoneSelect(coordinator, description, device_info)
        for description in SELECT_DESCRIPTIONS
    ]

    async_add_entities(entities)


class TsuryPhoneSelect(
    CoordinatorEntity[TsuryPhoneDataUpdateCoordinator], SelectEntity
):
    """Representation of a TsuryPhone select entity."""

    def __init__(
        self,
        coordinator: TsuryPhoneDataUpdateCoordinator,
        description: SelectEntityDescription,
        device_info,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_info = device_info

        # Generate unique ID
        self._attr_unique_id = f"{device_info.device_id}_{description.key}"

        # Set device info
        self._attr_device_info = get_device_info(device_info)

    @property
    def options(self) -> list[str]:
        """Return the list of available options."""
        if self.entity_description.key == "ring_pattern":
            return self._get_ring_pattern_options()
        elif self.entity_description.key == "quick_dial":
            return self._get_quick_dial_options()
        return []

    @property
    def current_option(self) -> str | None:
        """Return the selected option."""
        state: TsuryPhoneState = self.coordinator.data

        if self.entity_description.key == "ring_pattern":
            return self._get_current_ring_pattern_option(state)
        elif self.entity_description.key == "quick_dial":
            return self._get_current_quick_dial_option(state)

        return None

    async def async_select_option(self, option: str) -> None:
        """Select an option."""
        try:
            if self.entity_description.key == "ring_pattern":
                await self._select_ring_pattern(option)
            elif self.entity_description.key == "quick_dial":
                await self._select_quick_dial(option)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to select option {option}: {err}"
            ) from err

    def _get_ring_pattern_options(self) -> list[str]:
        """Get ring pattern options."""
        options = list(RING_PATTERN_PRESETS.keys())

        # Add custom option if current pattern doesn't match any preset
        current_pattern = self.coordinator.data.ring_pattern
        if current_pattern and not any(
            RING_PATTERN_PRESETS[preset] == current_pattern
            for preset in RING_PATTERN_PRESETS
            if RING_PATTERN_PRESETS[preset]  # Skip empty default
        ):
            options.append("custom")
        elif not current_pattern:
            # No current pattern set, but custom might be available
            if "custom" not in options:
                options.append("custom")

        return options

    def _get_current_ring_pattern_option(self, state: TsuryPhoneState) -> str | None:
        """Get current ring pattern option."""
        current_pattern = state.ring_pattern

        # Match against presets
        for preset_name, preset_pattern in RING_PATTERN_PRESETS.items():
            if preset_pattern == current_pattern:
                return preset_name

        # If no match and we have a pattern, it's custom
        if current_pattern:
            return "custom"

        # Default to default preset
        return "default"

    async def _select_ring_pattern(self, option: str) -> None:
        """Select ring pattern option."""
        if option == "custom":
            # Don't change pattern, just signal custom mode
            # User will need to set pattern via service or options
            _LOGGER.debug("Selected custom ring pattern mode")
            return

        # Get pattern for preset
        if option not in RING_PATTERN_PRESETS:
            raise HomeAssistantError(f"Unknown ring pattern preset: {option}")

        pattern = RING_PATTERN_PRESETS[option]

        await self.coordinator.api_client.set_ring_pattern(pattern)

        # Update local state optimistically
        self.coordinator.data.ring_pattern = pattern

        # Trigger coordinator update
        await self.coordinator.async_request_refresh()

    def _get_quick_dial_options(self) -> list[str]:
        """Get quick dial options."""
        state: TsuryPhoneState = self.coordinator.data

        if not state.quick_dials:
            return ["None"]

        # Format: "Name (CODE)" if name exists, otherwise just "CODE"
        options = []
        for entry in state.quick_dials:
            if entry.name:
                options.append(f"{entry.name} ({entry.code})")
            else:
                options.append(entry.code)

        # Add "None" option to deselect
        options.insert(0, "None")

        return options

    def _get_current_quick_dial_option(self, state: TsuryPhoneState) -> str | None:
        """Get current quick dial selection."""
        # Phase P4: Check if we have a selected quick dial stored in coordinator
        if (
            hasattr(self.coordinator, "selected_quick_dial_code")
            and self.coordinator.selected_quick_dial_code
        ):
            selected_code = self.coordinator.selected_quick_dial_code
            # Find the entry with this code
            if state.quick_dials:
                for entry in state.quick_dials:
                    if entry.code == selected_code:
                        if entry.name:
                            return f"{entry.name} ({entry.code})"
                        else:
                            return entry.code

        # Default to "None" if nothing selected or selection not found
        return "None"

    async def _select_quick_dial(self, option: str) -> None:
        """Select quick dial option."""
        # Phase P4: Store selection in coordinator for hybrid model
        if option == "None":
            # Clear selection
            self.coordinator.selected_quick_dial_code = None
            _LOGGER.debug("Quick dial selection cleared")
            return

        # Parse the option to extract code
        # Format is either "Name (CODE)" or just "CODE"
        if "(" in option and option.endswith(")"):
            # Extract code from "Name (CODE)" format
            code = option.split("(")[-1].rstrip(")")
        else:
            # Option is just the code
            code = option

        # Validate the code exists in current quick dials
        state: TsuryPhoneState = self.coordinator.data
        if state.quick_dials:
            valid_codes = [entry.code for entry in state.quick_dials]
            if code not in valid_codes:
                raise HomeAssistantError(
                    f"Quick dial code '{code}' not found in current list"
                )

        # Store selection in coordinator
        self.coordinator.selected_quick_dial_code = code
        _LOGGER.debug("Selected quick dial: %s (code: %s)", option, code)

    @property
    def extra_state_attributes(self) -> dict[str, any] | None:
        """Return additional state attributes."""
        state: TsuryPhoneState = self.coordinator.data
        attributes = {}

        # Add restoration indicator if available
        if hasattr(state, "restored") and state.restored:
            attributes["restored"] = True

        # Add specific attributes per select type
        if self.entity_description.key == "ring_pattern":
            # Show current pattern string
            if state.ring_pattern:
                attributes["pattern"] = state.ring_pattern
            else:
                attributes["pattern"] = "default"

            # Show all available presets
            attributes["available_presets"] = list(RING_PATTERN_PRESETS.keys())

            # Validation info
            current_option = self.current_option
            if current_option == "custom":
                attributes["custom_pattern"] = state.ring_pattern
                attributes["pattern_valid"] = self._validate_pattern(state.ring_pattern)

        elif self.entity_description.key == "quick_dial":
            # Show quick dial statistics
            attributes["total_quick_dials"] = state.quick_dial_count

            if state.quick_dials:
                # Show all codes for reference
                attributes["available_codes"] = [
                    entry.code for entry in state.quick_dials
                ]

            # Phase P4: Show selected code for hybrid model
            if (
                hasattr(self.coordinator, "selected_quick_dial_code")
                and self.coordinator.selected_quick_dial_code
            ):
                attributes["selected_code"] = self.coordinator.selected_quick_dial_code
                # Show the number that would be dialed
                if state.quick_dials:
                    for entry in state.quick_dials:
                        if entry.code == self.coordinator.selected_quick_dial_code:
                            attributes["selected_number"] = entry.number
                            attributes["selected_name"] = entry.name
                            break

        # Add connection status for troubleshooting
        if not state.connected:
            attributes["last_seen"] = state.last_seen
            attributes["connection_status"] = "disconnected"

        return attributes if attributes else None

    def _validate_pattern(self, pattern: str) -> bool:
        """Validate ring pattern format (basic check)."""
        if not pattern:
            return True  # Empty is valid (default)

        # Basic validation - firmware will do full validation
        if len(pattern) > 32:
            return False

        # Check for valid characters (digits, commas, single 'x')
        valid_chars = set("0123456789,x")
        return all(c in valid_chars for c in pattern)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Ring pattern select needs device connection for changes
        # Quick dial select can show options even when offline
        if self.entity_description.key == "ring_pattern":
            return (
                self.coordinator.last_update_success and self.coordinator.data.connected
            )
        else:
            return self.coordinator.last_update_success
