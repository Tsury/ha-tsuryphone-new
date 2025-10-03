"""Webhook management UI helpers for TsuryPhone integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import TsuryPhoneDataUpdateCoordinator


class WebhookHelper:
    """Helper class for webhook management tasks."""

    def __init__(self, coordinator: TsuryPhoneDataUpdateCoordinator):
        """Initialize webhook helper."""
        self.coordinator = coordinator

    def validate_webhook_url(self, url: str) -> dict[str, Any]:
        """Validate a webhook URL for common issues."""
        validation_result = {"valid": True, "warnings": [], "errors": []}

        # Basic URL validation
        if not url.startswith(("http://", "https://")):
            validation_result["valid"] = False
            validation_result["errors"].append(
                "URL must start with http:// or https://"
            )
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
            local in url
            for local in ["localhost", "127.0.0.1", "192.168.", "10.", "172."]
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
            "sequence": 12345,
        }

        if event_type == "incoming_call":
            base_payload.update(
                {
                    "data": {
                        "number": "+15551234567",
                        "name": "Test Caller",
                        "call_id": "test_call_123",
                    }
                }
            )
        elif event_type == "call_ended":
            base_payload.update(
                {
                    "data": {
                        "number": "+15551234567",
                        "name": "Test Caller",
                        "duration": 120,
                        "direction": "incoming",
                    }
                }
            )
        elif event_type == "device_state_change":
            base_payload.update(
                {
                    "data": {
                        "old_state": "idle",
                        "new_state": "ringing",
                        "connected": True,
                    }
                }
            )
        elif event_type == "config_change":
            base_payload.update(
                {"data": {"section": "audio", "changes": {"earpiece_volume": 5}}}
            )

        return base_payload


def get_webhook_helper(hass: HomeAssistant, device_id: str) -> WebhookHelper | None:
    """Get webhook helper for a device."""
    # Find coordinator for device
    for config_entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = config_entry.runtime_data
        if coordinator.device_info.device_id == device_id:
            return WebhookHelper(coordinator)
    return None

