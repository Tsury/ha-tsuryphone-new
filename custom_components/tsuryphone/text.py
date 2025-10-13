"""Text platform for TsuryPhone integration.

Provides editable text fields that buffer user input for device management
operations (quick dials, blocked numbers, priority callers, and webhooks).
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.text import TextEntity, TextEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.exceptions import HomeAssistantError

from . import get_device_info
from .const import (
    DOMAIN,
    MAX_CODE_LENGTH,
    MAX_DIALING_CODE_LENGTH,
    MAX_NAME_LENGTH,
    MAX_NUMBER_LENGTH,
    MAX_PATTERN_LENGTH,
    MAX_REASON_LENGTH,
)
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .api_client import TsuryPhoneAPIError
from .dialing import sanitize_default_dialing_code
from .validation import is_valid_ring_pattern


@dataclass(frozen=True, kw_only=True)
class TsuryPhoneTextDescription(TextEntityDescription):
    """Description for TsuryPhone text entities."""

    buffer_name: str
    field_name: str
    placeholder: str | None = None
    max_length: int | None = None
    apply_dialing_code: bool = False
    apply_ring_pattern: bool = False


TEXT_DESCRIPTIONS: tuple[TsuryPhoneTextDescription, ...] = (
    TsuryPhoneTextDescription(
        key="ring_pattern_custom",
        name="Ring Pattern - Custom",
        icon="mdi:pencil",
        entity_category=EntityCategory.CONFIG,
        max_length=MAX_PATTERN_LENGTH,
        buffer_name="ring_pattern",
        field_name="pattern",
        placeholder="500,500,0,500",
        apply_ring_pattern=True,
    ),
    TsuryPhoneTextDescription(
        key="dialing_default_code",
        name="Dialing - Default Code",
        icon="mdi:phone-in-talk",
        entity_category=EntityCategory.CONFIG,
        max_length=MAX_DIALING_CODE_LENGTH,
        buffer_name="dialing",
        field_name="default_code",
        placeholder="972",
        apply_dialing_code=True,
    ),
    TsuryPhoneTextDescription(
        key="dial_digit",
        name="Call - Dial Digit",
        icon="mdi:dialpad",
        max_length=1,
        buffer_name="dial_digit",
        field_name="digit",
        placeholder="5",
    ),
    # Blocked numbers
    TsuryPhoneTextDescription(
        key="blocked_number",
        name="Blocked - Number",
        icon="mdi:phone-remove",
        entity_category=EntityCategory.CONFIG,
        max_length=MAX_NUMBER_LENGTH,
        buffer_name="blocked",
        field_name="number",
        placeholder="+15559876543",
    ),
    TsuryPhoneTextDescription(
        key="blocked_reason",
        name="Blocked - Reason",
        icon="mdi:text",
        entity_category=EntityCategory.CONFIG,
        max_length=MAX_REASON_LENGTH,
        buffer_name="blocked",
        field_name="reason",
        placeholder="Telemarketer",
    ),
    # Priority callers
    TsuryPhoneTextDescription(
        key="priority_number",
        name="Priority - Number",
        icon="mdi:star",
        entity_category=EntityCategory.CONFIG,
        max_length=MAX_NUMBER_LENGTH,
        buffer_name="priority",
        field_name="number",
        placeholder="+15557654321",
    ),
    # Do Not Disturb schedule
    TsuryPhoneTextDescription(
        key="dnd_start_hour",
        name="DND - Start Hour",
        icon="mdi:clock-outline",
        entity_category=EntityCategory.CONFIG,
        max_length=2,
        buffer_name="dnd_schedule",
        field_name="start_hour",
        placeholder="22",
    ),
    TsuryPhoneTextDescription(
        key="dnd_start_minute",
        name="DND - Start Minute",
        icon="mdi:clock-outline",
        entity_category=EntityCategory.CONFIG,
        max_length=2,
        buffer_name="dnd_schedule",
        field_name="start_minute",
        placeholder="00",
    ),
    TsuryPhoneTextDescription(
        key="dnd_end_hour",
        name="DND - End Hour",
        icon="mdi:clock-outline",
        entity_category=EntityCategory.CONFIG,
        max_length=2,
        buffer_name="dnd_schedule",
        field_name="end_hour",
        placeholder="07",
    ),
    TsuryPhoneTextDescription(
        key="dnd_end_minute",
        name="DND - End Minute",
        icon="mdi:clock-outline",
        entity_category=EntityCategory.CONFIG,
        max_length=2,
        buffer_name="dnd_schedule",
        field_name="end_minute",
        placeholder="00",
    ),
    # Quick dial entries
    TsuryPhoneTextDescription(
        key="quick_dial_code",
        name="Quick Dial - Code",
        icon="mdi:numeric",
        entity_category=EntityCategory.CONFIG,
        max_length=MAX_CODE_LENGTH,
        buffer_name="quick_dial",
        field_name="code",
        placeholder="123",
    ),
    TsuryPhoneTextDescription(
        key="quick_dial_number",
        name="Quick Dial - Number",
        icon="mdi:phone",
        entity_category=EntityCategory.CONFIG,
        max_length=MAX_NUMBER_LENGTH,
        buffer_name="quick_dial",
        field_name="number",
        placeholder="+15551234567",
    ),
    TsuryPhoneTextDescription(
        key="quick_dial_name",
        name="Quick Dial - Name",
        icon="mdi:account",
        entity_category=EntityCategory.CONFIG,
        max_length=MAX_NAME_LENGTH,
        buffer_name="quick_dial",
        field_name="name",
        placeholder="Family",
    ),
    # Webhook actions
    TsuryPhoneTextDescription(
        key="webhook_code",
        name="Webhook - Code",
        icon="mdi:webhook",
        entity_category=EntityCategory.CONFIG,
        max_length=MAX_CODE_LENGTH,
        buffer_name="webhook",
        field_name="code",
        placeholder="W1",
    ),
    TsuryPhoneTextDescription(
        key="webhook_id",
        name="Webhook - ID",
        icon="mdi:identifier",
        entity_category=EntityCategory.CONFIG,
        max_length=MAX_NAME_LENGTH,
        buffer_name="webhook",
        field_name="webhook_id",
        placeholder="homeassistant_webhook",
    ),
    TsuryPhoneTextDescription(
        key="webhook_action_name",
        name="Webhook - Action Name",
        icon="mdi:label",
        entity_category=EntityCategory.CONFIG,
        max_length=MAX_NAME_LENGTH,
        buffer_name="webhook",
        field_name="action_name",
        placeholder="Door Unlock",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TsuryPhone text entities from a config entry."""
    coordinator = config_entry.runtime_data
    device_info = coordinator.device_info

    entities = [
        TsuryPhoneText(coordinator, description, device_info)
        for description in TEXT_DESCRIPTIONS
    ]

    async_add_entities(entities)


class TsuryPhoneText(CoordinatorEntity[TsuryPhoneDataUpdateCoordinator], TextEntity):
    """Text entity that buffers user input for TsuryPhone management actions."""

    entity_description: TsuryPhoneTextDescription

    def __init__(
        self,
        coordinator: TsuryPhoneDataUpdateCoordinator,
        description: TsuryPhoneTextDescription,
        device_info,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._device_info = device_info
        self._is_dnd_field = description.buffer_name == "dnd_schedule"
        self._is_dialing_code = description.apply_dialing_code
        self._is_ring_pattern = description.apply_ring_pattern

        self._attr_unique_id = f"{device_info.device_id}_{description.key}"
        self._attr_device_info = get_device_info(device_info)
        self._attr_mode = "text"
        self._attr_native_max_length = description.max_length
        self._attr_native_min_length = 0

    @property
    def native_value(self) -> str:
        """Return the current buffered value."""
        if self._is_dialing_code:
            return self.coordinator.data.default_dialing_code or ""

        if self._is_ring_pattern:
            return self.coordinator.data.ring_pattern or ""

        if self._is_dnd_field:
            dnd_config = self.coordinator.data.dnd_config
            value = getattr(dnd_config, self.entity_description.field_name, None)
            if value is None:
                return ""
            try:
                return f"{int(value):02d}"
            except (TypeError, ValueError):
                return str(value)

        buffer = getattr(
            self.coordinator,
            f"{self.entity_description.buffer_name}_input",
            None,
        )
        if not buffer:
            return ""
        return buffer.get(self.entity_description.field_name, "")

    async def async_set_value(self, value: str) -> None:
        """Update the buffered value and expose it to dependent buttons."""
        if self._is_dnd_field:
            await self._async_set_dnd_field(value)
            return

        if self._is_dialing_code:
            await self._async_set_dialing_code(value)
            return

        if self._is_ring_pattern:
            await self._async_set_ring_pattern(value)
            return

        buffer = getattr(
            self.coordinator,
            f"{self.entity_description.buffer_name}_input",
            None,
        )
        if buffer is None:
            return

        # Normalize whitespace and enforce max length if provided
        normalized = value.strip()
        max_length = self.entity_description.max_length
        if max_length is not None and len(normalized) > max_length:
            normalized = normalized[:max_length]

        field_name = self.entity_description.field_name
        current = buffer.get(field_name, "")

        buffer[field_name] = normalized
        if current != normalized:
            self.coordinator.async_update_listeners()
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Provide helper metadata for the UI."""
        attrs: dict[str, str] = {}
        if self.entity_description.placeholder:
            attrs["placeholder"] = self.entity_description.placeholder
        if self._is_dialing_code:
            attrs["default_prefix"] = self.coordinator.data.default_dialing_prefix or ""
            return attrs
        if self._is_ring_pattern:
            attrs["hint"] = "Use commas for segments, add xN repeats, 0 for muted gaps"
            return attrs
        attrs["buffer"] = self.entity_description.buffer_name
        attrs["field"] = self.entity_description.field_name
        return attrs

    @property
    def available(self) -> bool:
        """Text entities are available whenever the coordinator has data."""
        return self.coordinator.last_update_success

    async def _async_set_dnd_field(self, value: str) -> None:
        """Apply DND schedule changes immediately when edited."""
        normalized = value.strip()
        field_name = self.entity_description.field_name

        if not normalized:
            raise HomeAssistantError("Enter a value before updating the schedule")

        if not normalized.isdigit():
            raise HomeAssistantError("Use numeric values for the DND schedule")

        number = int(normalized)

        ranges = {
            "start_hour": (0, 23),
            "end_hour": (0, 23),
            "start_minute": (0, 59),
            "end_minute": (0, 59),
        }

        if field_name not in ranges:
            raise HomeAssistantError("Unsupported DND field")

        min_value, max_value = ranges[field_name]
        if not (min_value <= number <= max_value):
            raise HomeAssistantError(
                f"Value must be between {min_value} and {max_value}"
            )

        payload_field_map = {
            "start_hour": "startHour",
            "start_minute": "startMinute",
            "end_hour": "endHour",
            "end_minute": "endMinute",
        }

        dnd_config = self.coordinator.data.dnd_config
        payload = {payload_field_map[field_name]: number}

        try:
            await self.coordinator.api_client.set_dnd(payload)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to update DND schedule: {err}") from err

        # Update coordinator state optimistically
        setattr(dnd_config, field_name, number)
        self.coordinator.async_update_listeners()
        self.async_write_ha_state()

    async def _async_set_dialing_code(self, value: str) -> None:
        """Apply default dialing code changes immediately when edited."""

        sanitized = sanitize_default_dialing_code(value)
        if not sanitized:
            raise HomeAssistantError("Enter digits for the default dialing code")

        if len(sanitized) > MAX_DIALING_CODE_LENGTH:
            raise HomeAssistantError(
                f"Default dialing code must be {MAX_DIALING_CODE_LENGTH} digits or fewer"
            )

        state = self.coordinator.data
        if state.default_dialing_code == sanitized:
            self.async_write_ha_state()
            return

        try:
            await self.coordinator.api_client.set_dialing_config(sanitized)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(
                f"Failed to update default dialing code: {err}"
            ) from err

        # Update coordinator cache and notify listeners
        self.coordinator._update_default_dialing_metadata(code=sanitized)
        self.coordinator.async_update_listeners()

    async def _async_set_ring_pattern(self, value: str) -> None:
        """Apply a ring pattern directly from the text entity."""

        normalized = value.strip()

        if normalized and not is_valid_ring_pattern(normalized):
            raise HomeAssistantError(
                "Enter durations in milliseconds separated by commas. Use xN for repeats and 0 for muted segments."
            )

        pattern = normalized  # Empty string keeps device default

        try:
            await self.coordinator.api_client.set_ring_pattern(pattern)
        except TsuryPhoneAPIError as err:
            raise HomeAssistantError(f"Failed to update ring pattern: {err}") from err

        self.coordinator.data.ring_pattern = pattern
        self.coordinator.async_update_listeners()
        self.async_write_ha_state()
        self.async_write_ha_state()
