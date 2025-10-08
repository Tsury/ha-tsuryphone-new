"""Resilience and reliability enhancements for TsuryPhone integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable
from datetime import datetime, timedelta
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .models import TsuryPhoneEvent, TsuryPhoneState

_LOGGER = logging.getLogger(__name__)

# Sequence overflow handling
SEQUENCE_MAX_VALUE = 2**32 - 1  # 32-bit unsigned integer max
SEQUENCE_RESET_THRESHOLD = SEQUENCE_MAX_VALUE - 1000
SEQUENCE_REBOOT_DETECTION_THRESHOLD = 100  # If sequence drops by more than this, likely a reboot

# Reboot detection settings
REBOOT_DETECTION_TIMEOUT = 300  # 5 minutes to confirm reboot
REBOOT_RECOVERY_DELAY = 10     # Wait 10 seconds before recovery actions


@dataclass
class ResilienceStats:
    """Statistics for resilience monitoring."""
    reboot_detections: int = 0
    sequence_overflows: int = 0
    websocket_reconnections: int = 0
    api_fallbacks: int = 0
    error_recoveries: int = 0
    last_reboot_time: datetime | None = None
    last_sequence_reset: datetime | None = None
    total_events_processed: int = 0
    events_dropped: int = 0


class TsuryPhoneResilience:
    """Manage resilience features for TsuryPhone integration."""

    def __init__(self, hass: HomeAssistant, coordinator):
        """Initialize resilience manager."""
        self.hass = hass
        self.coordinator = coordinator
        self.stats = ResilienceStats()
        
        # Sequence tracking
        self._last_sequence = 0
        self._sequence_history: list[int] = []
        self._sequence_overflow_detected = False
        
        # Reboot detection
        self._reboot_detection_task: asyncio.Task | None = None
        self._reboot_confirmed = False
        self._pre_reboot_state: dict[str, Any] | None = None
        
        # Error tracking
        self._error_counts: dict[str, int] = {}
        self._error_timestamps: dict[str, datetime] = {}
        
        # Recovery callbacks
        self._recovery_callbacks: list[Callable] = []

    def register_recovery_callback(self, callback: Callable) -> None:
        """Register a callback to be called during recovery."""
        self._recovery_callbacks.append(callback)

    def reset_sequence_tracking(
        self,
        *,
        reason: str | None = None,
        increment_reconnections: bool = False,
    ) -> None:
        """Reset sequence trackers after a deliberate WebSocket reconnect."""

        if reason:
            _LOGGER.debug("Resetting sequence tracking (%s)", reason)
        else:
            _LOGGER.debug("Resetting sequence tracking")

        self._last_sequence = 0
        self._sequence_history.clear()
        self._sequence_overflow_detected = False
        self._reboot_confirmed = False
        self._pre_reboot_state = None

        if self._reboot_detection_task and not self._reboot_detection_task.done():
            self._reboot_detection_task.cancel()
            self._reboot_detection_task = None

        if increment_reconnections:
            self.stats.websocket_reconnections += 1

    async def handle_event_sequence(self, event: TsuryPhoneEvent) -> bool:
        """Handle event sequence validation and overflow detection.
        
        Returns True if event should be processed, False if it should be dropped.
        """
        current_seq = event.seq
        self.stats.total_events_processed += 1
        
        # First event
        if self._last_sequence == 0:
            self._last_sequence = current_seq
            self._sequence_history = [current_seq]
            return True
        
        # Check for sequence regression (potential reboot)
        if current_seq < self._last_sequence:
            sequence_drop = self._last_sequence - current_seq
            
            if sequence_drop > SEQUENCE_REBOOT_DETECTION_THRESHOLD:
                _LOGGER.warning("Large sequence regression detected: %d -> %d (drop: %d)", 
                               self._last_sequence, current_seq, sequence_drop)
                await self._handle_potential_reboot(event, sequence_drop)
                return True  # Process the event after reboot handling
            else:
                _LOGGER.debug("Minor sequence regression: %d -> %d", self._last_sequence, current_seq)
        
        # Check for sequence overflow
        elif current_seq > SEQUENCE_RESET_THRESHOLD and not self._sequence_overflow_detected:
            _LOGGER.info("Sequence approaching overflow: %d", current_seq)
            self._sequence_overflow_detected = True
            await self._handle_sequence_overflow()
        
        # Check for duplicate events
        if current_seq in self._sequence_history[-10:]:  # Check last 10 events
            _LOGGER.warning("Duplicate event sequence detected: %d", current_seq)
            self.stats.events_dropped += 1
            return False  # Drop duplicate
        
        # Update tracking
        self._last_sequence = current_seq
        self._sequence_history.append(current_seq)
        
        # Keep only recent sequence history
        if len(self._sequence_history) > 100:
            self._sequence_history = self._sequence_history[-100:]
        
        return True

    async def _handle_potential_reboot(self, event: TsuryPhoneEvent, sequence_drop: int) -> None:
        """Handle potential device reboot detection."""
        _LOGGER.info("Potential device reboot detected (sequence drop: %d)", sequence_drop)
        
        # Store pre-reboot state for recovery
        if self.coordinator.data:
            self._pre_reboot_state = {
                "last_seq": self._last_sequence,
                "connected": self.coordinator.data.connected,
                "app_state": self.coordinator.data.app_state.value,
                "call_active": self.coordinator.data.is_call_active,
                "reboot_time": dt_util.utcnow().isoformat(),
            }
        
        # Mark reboot in event
        event._reboot_detected = True
        
        # Start reboot confirmation task
        if self._reboot_detection_task:
            self._reboot_detection_task.cancel()
        
        self._reboot_detection_task = self.hass.async_create_task(
            self._confirm_reboot_recovery()
        )
        
        self.stats.reboot_detections += 1
        self.stats.last_reboot_time = dt_util.utcnow()

    async def _confirm_reboot_recovery(self) -> None:
        """Confirm device reboot and trigger recovery actions."""
        try:
            # Wait for device to stabilize
            await asyncio.sleep(REBOOT_RECOVERY_DELAY)
            
            _LOGGER.info("Starting reboot recovery sequence")
            
            # Mark coordinator data as potentially stale
            if self.coordinator.data:
                self.coordinator.data.reboot_detected = True
            
            # Trigger data refetch
            try:
                await self.coordinator.api_client.refetch_all()
                await self.coordinator.async_request_refresh()
                _LOGGER.info("Successfully refetched data after reboot")
            except Exception as err:
                _LOGGER.error("Failed to refetch data after reboot: %s", err)
                self.stats.error_recoveries += 1
            
            # Call recovery callbacks
            for callback in self._recovery_callbacks:
                try:
                    await callback()
                except Exception as err:
                    _LOGGER.error("Recovery callback failed: %s", err)
            
            # Save reboot event to cache if available
            if hasattr(self.coordinator, '_storage_cache') and self.coordinator._storage_cache:
                try:
                    reboot_data = {
                        "reboot_time": dt_util.utcnow().isoformat(),
                        "sequence_drop": self._last_sequence - (self.coordinator.data.last_seq or 0),
                        "pre_reboot_state": self._pre_reboot_state,
                    }
                    await self.coordinator._storage_cache.async_save_config_backup({
                        "event_type": "reboot_recovery",
                        "data": reboot_data
                    })
                except Exception as err:
                    _LOGGER.error("Failed to save reboot data to cache: %s", err)
            
            self._reboot_confirmed = True
            _LOGGER.info("Reboot recovery sequence completed")
            
        except asyncio.CancelledError:
            _LOGGER.debug("Reboot recovery cancelled")
        except Exception as err:
            _LOGGER.error("Error during reboot recovery: %s", err)
            self.stats.error_recoveries += 1

    async def _handle_sequence_overflow(self) -> None:
        """Handle sequence counter overflow."""
        _LOGGER.info("Handling sequence counter overflow")
        
        try:
            # Reset sequence tracking
            self._sequence_history.clear()
            self._sequence_overflow_detected = False
            
            # Trigger a full state refresh to get new sequence numbers
            await self.coordinator.api_client.refetch_all()
            await self.coordinator.async_request_refresh()
            
            self.stats.sequence_overflows += 1
            self.stats.last_sequence_reset = dt_util.utcnow()
            
            _LOGGER.info("Successfully handled sequence overflow")
            
        except Exception as err:
            _LOGGER.error("Failed to handle sequence overflow: %s", err)
            self.stats.error_recoveries += 1

    async def handle_api_error(self, error_type: str, error: Exception) -> bool:
        """Handle API errors with recovery strategies.
        
        Returns True if recovery was successful, False otherwise.
        """
        current_time = dt_util.utcnow()
        
        # Track error frequency
        self._error_counts[error_type] = self._error_counts.get(error_type, 0) + 1
        self._error_timestamps[error_type] = current_time
        
        # Determine recovery strategy based on error type and frequency
        if error_type == "connection_timeout":
            return await self._recover_from_timeout_error()
        elif error_type == "connection_refused":
            return await self._recover_from_connection_error()
        elif error_type == "http_error":
            return await self._recover_from_http_error(error)
        elif error_type == "websocket_error":
            return await self._recover_from_websocket_error()
        else:
            _LOGGER.warning("Unknown error type for recovery: %s", error_type)
            return False

    async def _recover_from_timeout_error(self) -> bool:
        """Recover from connection timeout errors."""
        _LOGGER.info("Attempting recovery from timeout error")
        
        try:
            # Increase timeout temporarily
            if hasattr(self.coordinator.api_client, '_timeout'):
                original_timeout = self.coordinator.api_client._timeout
                self.coordinator.api_client._timeout = min(original_timeout * 2, 30)
                
                # Try a simple API call
                await self.coordinator.api_client.get_tsuryphone_config()
                
                # Reset timeout after success
                self.coordinator.api_client._timeout = original_timeout
                
                _LOGGER.info("Successfully recovered from timeout error")
                return True
                
        except Exception as err:
            _LOGGER.error("Failed to recover from timeout error: %s", err)
        
        return False

    async def _recover_from_connection_error(self) -> bool:
        """Recover from connection refused errors."""
        _LOGGER.info("Attempting recovery from connection error")
        
        # Wait before retry
        await asyncio.sleep(5)
        
        try:
            # Try to reconnect
            await self.coordinator.api_client.get_tsuryphone_config()
            _LOGGER.info("Successfully recovered from connection error")
            return True
            
        except Exception as err:
            _LOGGER.error("Failed to recover from connection error: %s", err)
        
        return False

    async def _recover_from_http_error(self, error: Exception) -> bool:
        """Recover from HTTP errors."""
        _LOGGER.info("Attempting recovery from HTTP error: %s", error)
        
        # For 5xx errors, wait and retry
        if "5" in str(error):
            await asyncio.sleep(3)
            try:
                await self.coordinator.api_client.get_tsuryphone_config()
                _LOGGER.info("Successfully recovered from HTTP 5xx error")
                return True
            except Exception as err:
                _LOGGER.error("Failed to recover from HTTP error: %s", err)
        
        return False

    async def _recover_from_websocket_error(self) -> bool:
        """Recover from WebSocket errors."""
        _LOGGER.info("Attempting recovery from WebSocket error")
        
        try:
            # Restart WebSocket connection
            if self.coordinator._websocket_client:
                await self.coordinator._websocket_client.disconnect()
                await asyncio.sleep(2)
                await self.coordinator._websocket_client.connect()
                
                _LOGGER.info("Successfully recovered from WebSocket error")
                self.stats.websocket_reconnections += 1
                return True
                
        except Exception as err:
            _LOGGER.error("Failed to recover from WebSocket error: %s", err)
        
        return False

    def get_error_rate(self, error_type: str, window_minutes: int = 15) -> float:
        """Get error rate for a specific error type."""
        if error_type not in self._error_timestamps:
            return 0.0
        
        cutoff_time = dt_util.utcnow() - timedelta(minutes=window_minutes)
        recent_errors = sum(
            1 for timestamp in self._error_timestamps.values()
            if timestamp > cutoff_time
        )
        
        return recent_errors / window_minutes  # errors per minute

    def is_device_healthy(self) -> tuple[bool, list[str]]:
        """Check overall device health and return issues."""
        issues = []
        
        # Check error rates
        high_error_types = []
        for error_type in self._error_counts:
            if self.get_error_rate(error_type) > 2:  # More than 2 errors per minute
                high_error_types.append(error_type)
        
        if high_error_types:
            issues.append(f"High error rate for: {', '.join(high_error_types)}")
        
        # Check recent reboot
        if (self.stats.last_reboot_time and 
            dt_util.utcnow() - self.stats.last_reboot_time < timedelta(minutes=5)):
            issues.append("Recent device reboot detected")
        
        # Check sequence issues
        if self.stats.events_dropped > 10:
            issues.append(f"High number of dropped events: {self.stats.events_dropped}")
        
        # Check coordinator state
        if not self.coordinator.last_update_success:
            issues.append("Coordinator updates failing")
        
        is_healthy = len(issues) == 0
        return is_healthy, issues

    def get_resilience_stats(self) -> dict[str, Any]:
        """Get resilience statistics."""
        is_healthy, issues = self.is_device_healthy()
        
        return {
            "healthy": is_healthy,
            "issues": issues,
            "stats": {
                "reboot_detections": self.stats.reboot_detections,
                "sequence_overflows": self.stats.sequence_overflows,
                "websocket_reconnections": self.stats.websocket_reconnections,
                "api_fallbacks": self.stats.api_fallbacks,
                "error_recoveries": self.stats.error_recoveries,
                "total_events_processed": self.stats.total_events_processed,
                "events_dropped": self.stats.events_dropped,
                "last_reboot_time": self.stats.last_reboot_time.isoformat() if self.stats.last_reboot_time else None,
                "last_sequence_reset": self.stats.last_sequence_reset.isoformat() if self.stats.last_sequence_reset else None,
            },
            "error_counts": self._error_counts.copy(),
            "error_rates": {
                error_type: self.get_error_rate(error_type)
                for error_type in self._error_counts
            },
            "sequence_info": {
                "last_sequence": self._last_sequence,
                "history_size": len(self._sequence_history),
                "overflow_detected": self._sequence_overflow_detected,
            },
        }

    async def run_health_check(self) -> dict[str, Any]:
        """Run comprehensive health check."""
        _LOGGER.debug("Running resilience health check")
        
        health_results = {
            "timestamp": dt_util.utcnow().isoformat(),
            "overall_healthy": True,
            "checks": {},
        }
        
        # API connectivity check
        try:
            await self.coordinator.api_client.get_tsuryphone_config()
            health_results["checks"]["api_connectivity"] = {"status": "pass", "message": "API accessible"}
        except Exception as err:
            health_results["checks"]["api_connectivity"] = {"status": "fail", "message": f"API error: {err}"}
            health_results["overall_healthy"] = False
        
        # WebSocket connectivity check
        websocket_healthy = (
            self.coordinator._websocket_client and 
            self.coordinator._websocket_client.connected
        )
        health_results["checks"]["websocket_connectivity"] = {
            "status": "pass" if websocket_healthy else "fail",
            "message": "WebSocket connected" if websocket_healthy else "WebSocket disconnected"
        }
        if not websocket_healthy:
            health_results["overall_healthy"] = False
        
        # Sequence validation check
        sequence_healthy = not self._sequence_overflow_detected and self.stats.events_dropped < 5
        health_results["checks"]["sequence_integrity"] = {
            "status": "pass" if sequence_healthy else "warning",
            "message": f"Events dropped: {self.stats.events_dropped}, Overflow: {self._sequence_overflow_detected}"
        }
        
        # Error rate check
        high_error_rate = any(self.get_error_rate(error_type) > 1 for error_type in self._error_counts)
        health_results["checks"]["error_rate"] = {
            "status": "pass" if not high_error_rate else "warning",
            "message": "Normal error rate" if not high_error_rate else "Elevated error rate detected"
        }
        
        # Recent reboot check
        recent_reboot = (
            self.stats.last_reboot_time and 
            dt_util.utcnow() - self.stats.last_reboot_time < timedelta(minutes=2)
        )
        health_results["checks"]["reboot_status"] = {
            "status": "warning" if recent_reboot else "pass",
            "message": "Recent reboot detected" if recent_reboot else "No recent reboots"
        }
        
        return health_results

    async def cleanup(self) -> None:
        """Clean up resilience manager resources."""
        if self._reboot_detection_task:
            self._reboot_detection_task.cancel()
            self._reboot_detection_task = None
        
        _LOGGER.debug("Resilience manager cleaned up")