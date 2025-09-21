"""Device trigger support for TsuryPhone integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Trigger types
TRIGGER_INCOMING_CALL = "incoming_call"
TRIGGER_CALL_ANSWERED = "call_answered"
TRIGGER_CALL_ENDED = "call_ended"
TRIGGER_MISSED_CALL = "missed_call"
TRIGGER_DEVICE_CONNECTED = "device_connected"
TRIGGER_DEVICE_DISCONNECTED = "device_disconnected"
TRIGGER_DND_ENABLED = "dnd_enabled"
TRIGGER_DND_DISABLED = "dnd_disabled"
TRIGGER_MAINTENANCE_MODE_ENABLED = "maintenance_mode_enabled"
TRIGGER_MAINTENANCE_MODE_DISABLED = "maintenance_mode_disabled"
TRIGGER_CONFIG_CHANGED = "config_changed"
TRIGGER_DEVICE_REBOOTED = "device_rebooted"

TRIGGER_TYPES = [
    TRIGGER_INCOMING_CALL,
    TRIGGER_CALL_ANSWERED,
    TRIGGER_CALL_ENDED,
    TRIGGER_MISSED_CALL,
    TRIGGER_DEVICE_CONNECTED,
    TRIGGER_DEVICE_DISCONNECTED,
    TRIGGER_DND_ENABLED,
    TRIGGER_DND_DISABLED,
    TRIGGER_MAINTENANCE_MODE_ENABLED,
    TRIGGER_MAINTENANCE_MODE_DISABLED,
    TRIGGER_CONFIG_CHANGED,
    TRIGGER_DEVICE_REBOOTED,
]

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Optional("number"): cv.string,  # For call-related triggers
        vol.Optional("config_section"): cv.string,  # For config change triggers
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device triggers for TsuryPhone devices."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    
    if not device or device.via_device_id:
        return []
    
    # Check if this is a TsuryPhone device
    if not any(identifier[0] == DOMAIN for identifier in device.identifiers):
        return []

    triggers = []

    # Call event triggers
    call_triggers = [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_INCOMING_CALL,
            "metadata": {"name": "Incoming Call", "description": "When an incoming call is received"}
        },
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_CALL_ANSWERED,
            "metadata": {"name": "Call Answered", "description": "When a call is answered"}
        },
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_CALL_ENDED,
            "metadata": {"name": "Call Ended", "description": "When a call ends"}
        },
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_MISSED_CALL,
            "metadata": {"name": "Missed Call", "description": "When a call is missed"}
        },
    ]

    # Device state triggers
    device_triggers = [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_DEVICE_CONNECTED,
            "metadata": {"name": "Device Connected", "description": "When device comes online"}
        },
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_DEVICE_DISCONNECTED,
            "metadata": {"name": "Device Disconnected", "description": "When device goes offline"}
        },
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_DEVICE_REBOOTED,
            "metadata": {"name": "Device Rebooted", "description": "When device reboots"}
        },
    ]

    # Feature state triggers
    feature_triggers = [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_DND_ENABLED,
            "metadata": {"name": "Do Not Disturb Enabled", "description": "When DND is enabled"}
        },
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_DND_DISABLED,
            "metadata": {"name": "Do Not Disturb Disabled", "description": "When DND is disabled"}
        },
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_MAINTENANCE_MODE_ENABLED,
            "metadata": {"name": "Maintenance Mode Enabled", "description": "When maintenance mode is enabled"}
        },
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_MAINTENANCE_MODE_DISABLED,
            "metadata": {"name": "Maintenance Mode Disabled", "description": "When maintenance mode is disabled"}
        },
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: TRIGGER_CONFIG_CHANGED,
            "metadata": {"name": "Configuration Changed", "description": "When device configuration changes"}
        },
    ]

    triggers.extend(call_triggers)
    triggers.extend(device_triggers)
    triggers.extend(feature_triggers)

    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger."""
    device_id = config[CONF_DEVICE_ID]
    trigger_type = config[CONF_TYPE]
    
    # Map trigger types to event types
    event_type_mapping = {
        TRIGGER_INCOMING_CALL: "tsuryphone_incoming_call",
        TRIGGER_CALL_ANSWERED: "tsuryphone_call_answered",
        TRIGGER_CALL_ENDED: "tsuryphone_call_ended",
        TRIGGER_MISSED_CALL: "tsuryphone_missed_call",
        TRIGGER_DEVICE_CONNECTED: "tsuryphone_device_connected",
        TRIGGER_DEVICE_DISCONNECTED: "tsuryphone_device_disconnected",
        TRIGGER_DND_ENABLED: "tsuryphone_dnd_enabled",
        TRIGGER_DND_DISABLED: "tsuryphone_dnd_disabled",
        TRIGGER_MAINTENANCE_MODE_ENABLED: "tsuryphone_maintenance_enabled",
        TRIGGER_MAINTENANCE_MODE_DISABLED: "tsuryphone_maintenance_disabled",
        TRIGGER_CONFIG_CHANGED: "tsuryphone_config_changed",
        TRIGGER_DEVICE_REBOOTED: "tsuryphone_device_rebooted",
    }
    
    event_type = event_type_mapping.get(trigger_type)
    if not event_type:
        _LOGGER.error("Unknown trigger type: %s", trigger_type)
        return lambda: None

    event_config = {
        "platform": "event",
        "event_type": event_type,
        "event_data": {"device_id": device_id},
    }

    # Add optional filters
    if "number" in config and config["number"]:
        event_config["event_data"]["number"] = config["number"]
    
    if "config_section" in config and config["config_section"]:
        event_config["event_data"]["config_section"] = config["config_section"]

    event_config = event_trigger.TRIGGER_SCHEMA(event_config)
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """List trigger capabilities."""
    trigger_type = config[CONF_TYPE]
    
    # Base capabilities
    fields = {}
    
    # Add number filter for call-related triggers
    if trigger_type in [
        TRIGGER_INCOMING_CALL,
        TRIGGER_CALL_ANSWERED,
        TRIGGER_CALL_ENDED,
        TRIGGER_MISSED_CALL,
    ]:
        fields["number"] = {
            "selector": {"text": {"type": "tel"}},
            "name": "Phone Number",
            "description": "Filter by specific phone number (optional)",
        }
    
    # Add config section filter for config changes
    if trigger_type == TRIGGER_CONFIG_CHANGED:
        fields["config_section"] = {
            "selector": {
                "select": {
                    "options": [
                        "audio",
                        "dnd", 
                        "ring_pattern",
                        "quick_dial",
                        "blocked_numbers",
                        "webhooks",
                        "maintenance",
                    ]
                }
            },
            "name": "Configuration Section",
            "description": "Filter by specific configuration section (optional)",
        }
    
    return {"extra_fields": vol.Schema(fields)} if fields else {}


def get_trigger_event_data_schema(trigger_type: str) -> dict[str, Any]:
    """Get the event data schema for a trigger type."""
    base_schema = {
        "device_id": str,
        "timestamp": str,
    }
    
    if trigger_type in [
        TRIGGER_INCOMING_CALL,
        TRIGGER_CALL_ANSWERED,
        TRIGGER_CALL_ENDED,
        TRIGGER_MISSED_CALL,
    ]:
        base_schema.update({
            "number": str,
            "name": str,
            "call_id": str,
        })
        
        if trigger_type == TRIGGER_CALL_ENDED:
            base_schema.update({
                "duration": int,
                "direction": str,
            })
    
    elif trigger_type == TRIGGER_CONFIG_CHANGED:
        base_schema.update({
            "config_section": str,
            "changes": dict,
        })
    
    elif trigger_type in [TRIGGER_DEVICE_CONNECTED, TRIGGER_DEVICE_DISCONNECTED]:
        base_schema.update({
            "previous_state": str,
            "new_state": str,
        })
    
    return base_schema