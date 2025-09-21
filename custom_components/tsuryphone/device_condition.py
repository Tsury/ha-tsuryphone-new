"""Device condition support for TsuryPhone integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_CONDITION_BASE_SCHEMA
from homeassistant.const import (
    CONF_CONDITION,
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import condition, config_validation as cv, device_registry as dr
from homeassistant.helpers.config_validation import DEVICE_CONDITION_BASE_SCHEMA
from homeassistant.helpers.typing import ConfigType, TemplateVarsType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Condition types
CONDITION_IS_CONNECTED = "is_connected"
CONDITION_IS_RINGING = "is_ringing"
CONDITION_IS_IN_CALL = "is_in_call"
CONDITION_HAS_INCOMING_CALL = "has_incoming_call"
CONDITION_DND_ACTIVE = "dnd_active"
CONDITION_MAINTENANCE_MODE = "maintenance_mode"
CONDITION_CALL_WAITING_AVAILABLE = "call_waiting_available"
CONDITION_APP_STATE = "app_state"
CONDITION_SIGNAL_STRENGTH = "signal_strength"

CONDITION_TYPES = [
    CONDITION_IS_CONNECTED,
    CONDITION_IS_RINGING,
    CONDITION_IS_IN_CALL,
    CONDITION_HAS_INCOMING_CALL,
    CONDITION_DND_ACTIVE,
    CONDITION_MAINTENANCE_MODE,
    CONDITION_CALL_WAITING_AVAILABLE,
    CONDITION_APP_STATE,
    CONDITION_SIGNAL_STRENGTH,
]

CONDITION_SCHEMA = DEVICE_CONDITION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(CONDITION_TYPES),
        vol.Optional("app_state"): cv.string,  # For app_state condition
        vol.Optional("signal_threshold"): vol.All(vol.Coerce(int), vol.Range(min=-100, max=0)),  # For signal_strength condition
        vol.Optional("comparison"): vol.In(["above", "below", "equals"]),  # For numeric comparisons
    }
)


async def async_get_conditions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device conditions for TsuryPhone devices."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)
    
    if not device or device.via_device_id:
        return []
    
    # Check if this is a TsuryPhone device
    if not any(identifier[0] == DOMAIN for identifier in device.identifiers):
        return []

    conditions = []

    # Connection conditions
    connection_conditions = [
        {
            CONF_CONDITION: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: CONDITION_IS_CONNECTED,
            "metadata": {"name": "Device Connected", "description": "Check if device is online"}
        },
    ]

    # Call state conditions
    call_conditions = [
        {
            CONF_CONDITION: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: CONDITION_IS_RINGING,
            "metadata": {"name": "Device Ringing", "description": "Check if device is ringing"}
        },
        {
            CONF_CONDITION: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: CONDITION_IS_IN_CALL,
            "metadata": {"name": "In Call", "description": "Check if device is in an active call"}
        },
        {
            CONF_CONDITION: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: CONDITION_HAS_INCOMING_CALL,
            "metadata": {"name": "Has Incoming Call", "description": "Check if there's an incoming call"}
        },
    ]

    # Feature state conditions
    feature_conditions = [
        {
            CONF_CONDITION: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: CONDITION_DND_ACTIVE,
            "metadata": {"name": "Do Not Disturb Active", "description": "Check if DND is currently active"}
        },
        {
            CONF_CONDITION: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: CONDITION_MAINTENANCE_MODE,
            "metadata": {"name": "Maintenance Mode", "description": "Check if device is in maintenance mode"}
        },
        {
            CONF_CONDITION: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: CONDITION_CALL_WAITING_AVAILABLE,
            "metadata": {"name": "Call Waiting Available", "description": "Check if call waiting is available"}
        },
    ]

    # Advanced conditions
    advanced_conditions = [
        {
            CONF_CONDITION: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: CONDITION_APP_STATE,
            "metadata": {"name": "App State", "description": "Check device app state"}
        },
        {
            CONF_CONDITION: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: CONDITION_SIGNAL_STRENGTH,
            "metadata": {"name": "Signal Strength", "description": "Check signal strength"}
        },
    ]

    conditions.extend(connection_conditions)
    conditions.extend(call_conditions)
    conditions.extend(feature_conditions)
    conditions.extend(advanced_conditions)

    return conditions


@callback
def async_condition_from_config(
    hass: HomeAssistant, config: ConfigType
) -> condition.ConditionCheckerType:
    """Create a function to test a device condition."""
    device_id = config[CONF_DEVICE_ID]
    condition_type = config[CONF_TYPE]
    
    def test_condition(hass: HomeAssistant, variables: TemplateVarsType) -> bool:
        """Test if condition is true."""
        # Find the coordinator for this device
        coordinator = None
        for config_entry in hass.config_entries.async_entries(DOMAIN):
            if config_entry.runtime_data.device_info.device_id == device_id:
                coordinator = config_entry.runtime_data
                break
        
        if not coordinator:
            _LOGGER.warning("No coordinator found for device %s", device_id)
            return False
        
        state = coordinator.data
        
        # Test the specific condition
        if condition_type == CONDITION_IS_CONNECTED:
            return state.connected
        
        elif condition_type == CONDITION_IS_RINGING:
            return state.is_ringing
        
        elif condition_type == CONDITION_IS_IN_CALL:
            return state.is_call_active
        
        elif condition_type == CONDITION_HAS_INCOMING_CALL:
            return state.is_incoming_call
        
        elif condition_type == CONDITION_DND_ACTIVE:
            return state.dnd_active
        
        elif condition_type == CONDITION_MAINTENANCE_MODE:
            return state.maintenance_mode
        
        elif condition_type == CONDITION_CALL_WAITING_AVAILABLE:
            return state.call_waiting_available
        
        elif condition_type == CONDITION_APP_STATE:
            expected_state = config.get("app_state")
            if expected_state:
                return state.app_state.value == expected_state
            return True  # No specific state required
        
        elif condition_type == CONDITION_SIGNAL_STRENGTH:
            signal_threshold = config.get("signal_threshold", -70)
            comparison = config.get("comparison", "above")
            current_signal = state.stats.rssi_dbm
            
            if current_signal == 0:  # No signal data
                return False
                
            if comparison == "above":
                return current_signal > signal_threshold
            elif comparison == "below":
                return current_signal < signal_threshold
            elif comparison == "equals":
                return current_signal == signal_threshold
        
        _LOGGER.warning("Unknown condition type: %s", condition_type)
        return False
    
    return test_condition


async def async_get_condition_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """List condition capabilities."""
    condition_type = config[CONF_TYPE]
    
    # Base capabilities
    fields = {}
    
    # Add app state selection for app_state condition
    if condition_type == CONDITION_APP_STATE:
        fields["app_state"] = {
            "selector": {
                "select": {
                    "options": [
                        "idle",
                        "ringing",
                        "in_call",
                        "dialing",
                        "connecting",
                        "call_waiting",
                        "error",
                        "maintenance",
                        "booting",
                        "updating",
                    ]
                }
            },
            "name": "App State",
            "description": "Expected app state",
        }
    
    # Add signal strength configuration
    elif condition_type == CONDITION_SIGNAL_STRENGTH:
        fields["signal_threshold"] = {
            "selector": {"number": {"min": -100, "max": 0, "step": 1}},
            "name": "Signal Threshold (dBm)",
            "description": "Signal strength threshold in dBm",
        }
        fields["comparison"] = {
            "selector": {
                "select": {
                    "options": ["above", "below", "equals"]
                }
            },
            "name": "Comparison",
            "description": "How to compare signal strength",
        }
    
    return {"extra_fields": vol.Schema(fields)} if fields else {}


def get_condition_description(condition_type: str, config: ConfigType) -> str:
    """Get a human-readable description of the condition."""
    descriptions = {
        CONDITION_IS_CONNECTED: "Device is connected",
        CONDITION_IS_RINGING: "Device is ringing",
        CONDITION_IS_IN_CALL: "Device is in an active call",
        CONDITION_HAS_INCOMING_CALL: "Device has an incoming call",
        CONDITION_DND_ACTIVE: "Do Not Disturb is active",
        CONDITION_MAINTENANCE_MODE: "Device is in maintenance mode",
        CONDITION_CALL_WAITING_AVAILABLE: "Call waiting is available",
    }
    
    if condition_type == CONDITION_APP_STATE:
        app_state = config.get("app_state", "any")
        return f"Device app state is {app_state}"
    
    elif condition_type == CONDITION_SIGNAL_STRENGTH:
        threshold = config.get("signal_threshold", -70)
        comparison = config.get("comparison", "above")
        return f"Signal strength is {comparison} {threshold} dBm"
    
    return descriptions.get(condition_type, f"Unknown condition: {condition_type}")