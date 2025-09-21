"""Webhook management UI helpers for TsuryPhone integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .models import TsuryPhoneState

_LOGGER = logging.getLogger(__name__)

WEBHOOK_EVENT_TYPES = [
    "incoming_call",
    "call_answered", 
    "call_ended",
    "missed_call",
    "device_state_change",
    "config_change",
    "diagnostic",
    "error",
    "system_event"
]


class WebhookHelper:
    """Helper class for webhook management UI integration."""
    
    def __init__(self, coordinator: TsuryPhoneDataUpdateCoordinator):
        """Initialize webhook helper."""
        self.coordinator = coordinator
    
    def get_webhook_summary(self) -> dict[str, Any]:
        """Get summary of webhook configuration for UI display."""
        state: TsuryPhoneState = self.coordinator.data
        
        if not state.webhooks:
            return {
                "total_webhooks": 0,
                "active_webhooks": 0,
                "webhook_urls": [],
                "event_coverage": {},
                "status": "no_webhooks"
            }
        
        # Count active webhooks
        active_count = len([w for w in state.webhooks if w.active])
        
        # Get all unique URLs
        webhook_urls = list(set(w.url for w in state.webhooks))
        
        # Calculate event coverage
        event_coverage = {}
        for event_type in WEBHOOK_EVENT_TYPES:
            subscribed_webhooks = [
                w for w in state.webhooks 
                if event_type in w.events and w.active
            ]
            event_coverage[event_type] = {
                "subscribed_webhooks": len(subscribed_webhooks),
                "webhook_urls": [w.url for w in subscribed_webhooks]
            }
        
        return {
            "total_webhooks": len(state.webhooks),
            "active_webhooks": active_count,
            "webhook_urls": webhook_urls,
            "event_coverage": event_coverage,
            "status": "active" if active_count > 0 else "inactive"
        }
    
    def get_webhook_recommendations(self) -> list[dict[str, Any]]:
        """Get webhook setup recommendations for common use cases."""
        state: TsuryPhoneState = self.coordinator.data
        recommendations = []
        
        # Check if basic call events are covered
        call_events = ["incoming_call", "call_answered", "call_ended", "missed_call"]
        covered_call_events = []
        
        if state.webhooks:
            for event in call_events:
                if any(event in w.events and w.active for w in state.webhooks):
                    covered_call_events.append(event)
        
        missing_call_events = [e for e in call_events if e not in covered_call_events]
        
        if missing_call_events:
            recommendations.append({
                "title": "Call Event Monitoring",
                "description": "Monitor call events for automation and logging",
                "missing_events": missing_call_events,
                "priority": "high",
                "example_url": "http://homeassistant.local:8123/api/webhook/tsuryphone_calls"
            })
        
        # Check device state monitoring
        if not any("device_state_change" in w.events and w.active for w in (state.webhooks or [])):
            recommendations.append({
                "title": "Device State Monitoring", 
                "description": "Monitor device online/offline status and app state changes",
                "missing_events": ["device_state_change"],
                "priority": "medium",
                "example_url": "http://homeassistant.local:8123/api/webhook/tsuryphone_device"
            })
        
        # Check configuration monitoring
        if not any("config_change" in w.events and w.active for w in (state.webhooks or [])):
            recommendations.append({
                "title": "Configuration Change Tracking",
                "description": "Track changes to device settings and lists",
                "missing_events": ["config_change"],
                "priority": "low",
                "example_url": "http://homeassistant.local:8123/api/webhook/tsuryphone_config"
            })
        
        return recommendations
    
    def validate_webhook_url(self, url: str) -> dict[str, Any]:
        """Validate a webhook URL for common issues."""
        validation_result = {
            "valid": True,
            "warnings": [],
            "errors": []
        }
        
        # Basic URL validation
        if not url.startswith(("http://", "https://")):
            validation_result["valid"] = False
            validation_result["errors"].append("URL must start with http:// or https://")
            return validation_result
        
        # Check for localhost/127.0.0.1 issues
        if "localhost" in url or "127.0.0.1" in url:
            validation_result["warnings"].append(
                "Localhost URLs may not be reachable from the device network"
            )
        
        # Check for common Home Assistant webhook patterns
        if "/api/webhook/" in url:
            validation_result["warnings"].append(
                "Home Assistant webhook detected - ensure webhook automation is configured"
            )
        
        # Check for HTTPS in production
        if not url.startswith("https://") and not any(
            local in url for local in ["localhost", "127.0.0.1", "192.168.", "10.", "172."]
        ):
            validation_result["warnings"].append(
                "Consider using HTTPS for external webhooks"
            )
        
        return validation_result
    
    def get_webhook_test_payload(self, event_type: str) -> dict[str, Any]:
        """Generate a test payload for webhook testing."""
        base_payload = {
            "event": event_type,
            "timestamp": "2025-01-01T12:00:00Z",
            "device_id": self.coordinator.device_info.device_id,
            "sequence": 12345
        }
        
        if event_type == "incoming_call":
            base_payload.update({
                "data": {
                    "number": "+15551234567",
                    "name": "Test Caller",
                    "call_id": "test_call_123"
                }
            })
        elif event_type == "call_ended":
            base_payload.update({
                "data": {
                    "number": "+15551234567", 
                    "name": "Test Caller",
                    "duration": 120,
                    "direction": "incoming"
                }
            })
        elif event_type == "device_state_change":
            base_payload.update({
                "data": {
                    "old_state": "idle",
                    "new_state": "ringing",
                    "connected": True
                }
            })
        elif event_type == "config_change":
            base_payload.update({
                "data": {
                    "section": "audio",
                    "changes": {
                        "earpiece_volume": 5
                    }
                }
            })
        
        return base_payload


def get_webhook_helper(hass: HomeAssistant, device_id: str) -> WebhookHelper | None:
    """Get webhook helper for a device."""
    # Find coordinator for device
    for config_entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = config_entry.runtime_data
        if coordinator.device_info.device_id == device_id:
            return WebhookHelper(coordinator)
    return None


def get_webhook_entity_attributes(coordinator: TsuryPhoneDataUpdateCoordinator) -> dict[str, Any]:
    """Get webhook-related attributes for entity state."""
    helper = WebhookHelper(coordinator)
    summary = helper.get_webhook_summary()
    recommendations = helper.get_webhook_recommendations()
    
    attributes = {
        "webhook_count": summary["total_webhooks"],
        "active_webhooks": summary["active_webhooks"],
        "webhook_status": summary["status"]
    }
    
    # Add recommendation count
    if recommendations:
        attributes["recommendations_count"] = len(recommendations)
        attributes["high_priority_recommendations"] = len([
            r for r in recommendations if r["priority"] == "high"
        ])
    
    # Add event coverage summary
    covered_events = [
        event for event, info in summary["event_coverage"].items()
        if info["subscribed_webhooks"] > 0
    ]
    attributes["covered_events"] = covered_events
    attributes["coverage_percentage"] = int(
        (len(covered_events) / len(WEBHOOK_EVENT_TYPES)) * 100
    )
    
    return attributes