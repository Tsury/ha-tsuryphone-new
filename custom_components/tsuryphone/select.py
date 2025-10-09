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
from .const import DOMAIN, RING_PATTERN_PRESETS, RING_PATTERN_PRESET_LABELS
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .models import QuickDialEntry, TsuryPhoneState
from .validation import is_valid_ring_pattern

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
    SelectEntityDescription(
        key="blocked_number",
        name="Blocked Number",
        icon="mdi:phone-remove",
        entity_category=EntityCategory.CONFIG,
    ),
    SelectEntityDescription(
        key="priority_number",
        name="Priority Number",
        icon="mdi:star",
        entity_category=EntityCategory.CONFIG,
    ),
    SelectEntityDescription(
        key="webhook_action",
        name="Webhook Action",
        icon="mdi:webhook",
        entity_category=EntityCategory.CONFIG,
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
        self._quick_dial_option_map: dict[str, str | None] = {}
        self._ring_pattern_option_map: dict[str, str] = {}

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
        elif self.entity_description.key == "blocked_number":
            return self._get_blocked_number_options()
        elif self.entity_description.key == "priority_number":
            return self._get_priority_number_options()
        elif self.entity_description.key == "webhook_action":
            return self._get_webhook_action_options()
        return []

    @property
    def current_option(self) -> str | None:
        """Return the selected option."""
        state: TsuryPhoneState = self.coordinator.data

        if self.entity_description.key == "ring_pattern":
            return self._get_current_ring_pattern_option(state)
        elif self.entity_description.key == "quick_dial":
            return self._get_current_quick_dial_option(state)
        elif self.entity_description.key == "blocked_number":
            return self._get_current_blocked_option(state)
        elif self.entity_description.key == "priority_number":
            return self._get_current_priority_option(state)
        elif self.entity_description.key == "webhook_action":
            return self._get_current_webhook_option(state)

        return None

    async def async_select_option(self, option: str) -> None:
        """Select an option."""
        try:
            if self.entity_description.key == "ring_pattern":
                await self._select_ring_pattern(option)
            elif self.entity_description.key == "quick_dial":
                await self._select_quick_dial(option)
            elif self.entity_description.key == "blocked_number":
                await self._select_blocked_number(option)
            elif self.entity_description.key == "priority_number":
                await self._select_priority_number(option)
            elif self.entity_description.key == "webhook_action":
                await self._select_webhook_action(option)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to select option {option}: {err}"
            ) from err

    def _get_ring_pattern_options(self) -> list[str]:
        """Get ring pattern options."""
        option_map = self._build_ring_pattern_option_map()
        return list(option_map.keys())

    def _get_current_ring_pattern_option(self, state: TsuryPhoneState) -> str | None:
        """Get current ring pattern option."""
        current_pattern = state.ring_pattern

        option_map = self._ring_pattern_option_map or self._build_ring_pattern_option_map()
        reverse_map = {value: label for label, value in option_map.items()}

        preset_match = next(
            (name for name, pattern in RING_PATTERN_PRESETS.items() if pattern == current_pattern),
            None,
        )

        if preset_match and preset_match in reverse_map:
            return reverse_map[preset_match]

        if current_pattern:
            return next(
                (label for label, value in option_map.items() if value == "Custom"),
                "Custom",
            )

        return reverse_map.get("Default", next(iter(option_map)))

    async def _select_ring_pattern(self, option: str) -> None:
        """Select ring pattern option."""
        option_map = self._ring_pattern_option_map or self._build_ring_pattern_option_map()
        preset_name = option_map.get(option)

        if not preset_name:
            raise HomeAssistantError(f"Unknown ring pattern selection: {option}")

        if preset_name == "Custom":
            # Don't change pattern, just signal custom mode
            # User will need to set pattern via service or options
            _LOGGER.debug("Selected custom ring pattern mode")
            return

        pattern = RING_PATTERN_PRESETS[preset_name]

        await self.coordinator.api_client.set_ring_pattern(pattern)

        # Update local state optimistically
        self.coordinator.data.ring_pattern = pattern

        # Trigger coordinator update
        await self.coordinator.async_request_refresh()

    def _format_quick_dial_option(self, entry: QuickDialEntry) -> str:
        """Return a user-facing label for a quick dial entry."""
        if entry.name:
            base = f"{entry.name} ({entry.code})"
        else:
            base = entry.code

        return f"{base} – {entry.number}" if entry.number else base

    def _build_quick_dial_option_map(
        self, state: TsuryPhoneState
    ) -> dict[str, str | None]:
        """Build and cache option->code mapping for quick dial select."""
        option_map: dict[str, str | None] = {"None": None}

        quick_dials = sorted(
            state.quick_dials or [],
            key=lambda entry: ((entry.name or "").casefold(), (entry.code or "").casefold()),
        )

        for entry in quick_dials:
            label = self._format_quick_dial_option(entry)
            option_map[label] = entry.code

        self._quick_dial_option_map = option_map
        return option_map

    def _get_quick_dial_options(self) -> list[str]:
        """Get quick dial options."""
        option_map = self._build_quick_dial_option_map(self.coordinator.data)
        return list(option_map.keys())

    def _get_current_quick_dial_option(self, state: TsuryPhoneState) -> str | None:
        """Get current quick dial selection."""
        # Phase P4: Check if we have a selected quick dial stored in coordinator
        if (
            hasattr(self.coordinator, "selected_quick_dial_code")
            and self.coordinator.selected_quick_dial_code
        ):
            selected_code = self.coordinator.selected_quick_dial_code
            option_map = self._build_quick_dial_option_map(state)
            for option_label, code in option_map.items():
                if code == selected_code:
                    return option_label

        # Default to "None" if nothing selected or selection not found
        return "None"

    async def _select_quick_dial(self, option: str) -> None:
        """Select quick dial option."""
        # Phase P4: Store selection in coordinator for hybrid model
        option_map = self._quick_dial_option_map
        state: TsuryPhoneState = self.coordinator.data
        if option_map is None or option not in option_map:
            option_map = self._build_quick_dial_option_map(state)

        if option == "None":
            previous = self.coordinator.selected_quick_dial_code
            self.coordinator.selected_quick_dial_code = None
            if previous is not None:
                self.coordinator.async_update_listeners()
            _LOGGER.debug("Quick dial selection cleared")
            return

        code = option_map.get(option)
        if not code:
            raise HomeAssistantError(f"Unknown quick dial option: {option}")

        if state.quick_dials and code not in [
            entry.code for entry in state.quick_dials
        ]:
            raise HomeAssistantError(
                f"Quick dial code '{code}' not found in current list"
            )

        previous = self.coordinator.selected_quick_dial_code
        self.coordinator.selected_quick_dial_code = code
        if previous != code:
            self.coordinator.async_update_listeners()
        _LOGGER.debug("Selected quick dial: %s (code: %s)", option, code)

    def _format_blocked_option(self, entry) -> str:
        """Format blocked number option label."""
        return f"{entry.number} ({entry.reason})" if entry.reason else entry.number

    def _get_blocked_number_options(self) -> list[str]:
        """Get blocked number options."""
        state: TsuryPhoneState = self.coordinator.data

        if not state.blocked_numbers:
            return ["None"]

        options = ["None"]
        options.extend(
            label
            for label in sorted(
                (self._format_blocked_option(entry) for entry in state.blocked_numbers),
                key=str.casefold,
            )
        )
        return options

    def _get_current_blocked_option(self, state: TsuryPhoneState) -> str:
        """Get currently selected blocked number."""
        if self.coordinator.selected_blocked_number and state.blocked_numbers:
            for entry in state.blocked_numbers:
                if entry.number == self.coordinator.selected_blocked_number:
                    return self._format_blocked_option(entry)
        return "None"

    async def _select_blocked_number(self, option: str) -> None:
        """Select a blocked number entry."""
        if option == "None":
            previous = self.coordinator.selected_blocked_number
            self.coordinator.selected_blocked_number = None
            if previous is not None:
                self.coordinator.async_update_listeners()
            _LOGGER.debug("Blocked number selection cleared")
            return

        state: TsuryPhoneState = self.coordinator.data
        for entry in state.blocked_numbers:
            if self._format_blocked_option(entry) == option:
                previous = self.coordinator.selected_blocked_number
                self.coordinator.selected_blocked_number = entry.number
                if previous != entry.number:
                    self.coordinator.async_update_listeners()
                _LOGGER.debug("Selected blocked number: %s", entry.number)
                return

        raise HomeAssistantError(f"Blocked number selection '{option}' not found")

    def _get_priority_number_options(self) -> list[str]:
        """Get priority number options."""
        state: TsuryPhoneState = self.coordinator.data
        if not state.priority_callers:
            return ["None"]

        options = ["None"]
        options.extend(
            number
            for number in sorted(
                (entry.number for entry in state.priority_callers),
                key=str.casefold,
            )
        )
        return options

    def _get_current_priority_option(self, state: TsuryPhoneState) -> str:
        """Get the currently selected priority number."""
        if self.coordinator.selected_priority_number and state.priority_callers:
            for entry in state.priority_callers:
                if entry.number == self.coordinator.selected_priority_number:
                    return entry.number
        return "None"

    async def _select_priority_number(self, option: str) -> None:
        """Select priority number."""
        if option == "None":
            previous = self.coordinator.selected_priority_number
            self.coordinator.selected_priority_number = None
            if previous is not None:
                self.coordinator.async_update_listeners()
            _LOGGER.debug("Priority selection cleared")
            return

        state: TsuryPhoneState = self.coordinator.data
        if any(entry.number == option for entry in state.priority_callers):
            previous = self.coordinator.selected_priority_number
            self.coordinator.selected_priority_number = option
            if previous != option:
                self.coordinator.async_update_listeners()
            _LOGGER.debug("Selected priority number: %s", option)
            return

        raise HomeAssistantError(f"Priority number '{option}' not found")

    def _format_webhook_option(self, entry) -> str:
        """Format webhook select label."""
        base = entry.code or entry.webhook_id
        if entry.action_name:
            return f"{entry.action_name} ({base})"
        return str(base)

    def _get_webhook_action_options(self) -> list[str]:
        """Get webhook action options."""
        state: TsuryPhoneState = self.coordinator.data
        if not state.webhooks:
            return ["None"]

        options = ["None"]
        options.extend(
            label
            for label in sorted(
                (self._format_webhook_option(entry) for entry in state.webhooks),
                key=str.casefold,
            )
        )
        return options

    def _build_ring_pattern_option_map(self) -> dict[str, str]:
        """Build labeled ring pattern options for the select entity."""

        option_map: dict[str, str] = {
            RING_PATTERN_PRESET_LABELS[name]: name
            for name in RING_PATTERN_PRESETS
        }

        current_pattern = self.coordinator.data.ring_pattern
        preset_match = next(
            (name for name, pattern in RING_PATTERN_PRESETS.items() if pattern == current_pattern),
            None,
        )

        if current_pattern and not preset_match:
            custom_label = f"Custom ({current_pattern})"
        else:
            custom_label = "Custom"

        option_map[custom_label] = "Custom"

        self._ring_pattern_option_map = option_map
        return option_map

    def _get_current_webhook_option(self, state: TsuryPhoneState) -> str:
        """Get the currently selected webhook option."""
        if self.coordinator.selected_webhook_code and state.webhooks:
            for entry in state.webhooks:
                if entry.code == self.coordinator.selected_webhook_code:
                    return self._format_webhook_option(entry)
        return "None"

    async def _select_webhook_action(self, option: str) -> None:
        """Select webhook action."""
        if option == "None":
            previous = self.coordinator.selected_webhook_code
            self.coordinator.selected_webhook_code = None
            if previous is not None:
                self.coordinator.async_update_listeners()
            _LOGGER.debug("Webhook selection cleared")
            return

        state: TsuryPhoneState = self.coordinator.data
        for entry in state.webhooks:
            if self._format_webhook_option(entry) == option:
                previous = self.coordinator.selected_webhook_code
                self.coordinator.selected_webhook_code = entry.code
                if previous != entry.code:
                    self.coordinator.async_update_listeners()
                _LOGGER.debug("Selected webhook action: %s", entry.code)
                return

        raise HomeAssistantError(f"Webhook action '{option}' not found")

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
        elif self.entity_description.key == "blocked_number":
            attributes["total_blocked"] = state.blocked_count
            if state.blocked_numbers:
                attributes["blocked_numbers"] = [
                    {"number": entry.number, "reason": entry.reason}
                    for entry in state.blocked_numbers
                ]
            if self.coordinator.selected_blocked_number:
                attributes["selected_number"] = self.coordinator.selected_blocked_number
        elif self.entity_description.key == "priority_number":
            attributes["total_priority"] = len(state.priority_callers)
            if state.priority_callers:
                attributes["priority_numbers"] = [
                    entry.number for entry in state.priority_callers
                ]
            if self.coordinator.selected_priority_number:
                attributes["selected_number"] = (
                    self.coordinator.selected_priority_number
                )
        elif self.entity_description.key == "webhook_action":
            attributes["total_webhooks"] = len(state.webhooks)
            if state.webhooks:
                attributes["webhooks"] = [
                    {
                        "code": entry.code,
                        "webhook_id": entry.webhook_id,
                        "action_name": entry.action_name,
                        "active": entry.active,
                    }
                    for entry in state.webhooks
                ]
            if self.coordinator.selected_webhook_code:
                attributes["selected_code"] = self.coordinator.selected_webhook_code

        # Add connection status for troubleshooting
        if not state.connected:
            attributes["last_seen"] = state.last_seen
            attributes["connection_status"] = "disconnected"

        return attributes if attributes else None

    def _validate_pattern(self, pattern: str) -> bool:
        """Validate ring pattern format (basic check)."""
        return is_valid_ring_pattern(pattern)

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
