"""Persistent notification support for TsuryPhone integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.persistent_notification import (
    DOMAIN as PERSISTENT_NOTIFICATION_DOMAIN,
    async_create,
    async_dismiss,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    NOTIFICATION_ID_SYSTEM_ERROR,
    NOTIFICATION_ID_MAINTENANCE,
    NOTIFICATION_ID_REBOOT,
    NOTIFICATION_ID_MISSED_CALLS,
)
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .models import TsuryPhoneState

_LOGGER = logging.getLogger(__name__)


class TsuryPhoneNotificationManager:
    """Manage persistent notifications for TsuryPhone devices."""
    
    def __init__(self, hass: HomeAssistant, coordinator: TsuryPhoneDataUpdateCoordinator):
        """Initialize notification manager."""
        self.hass = hass
        self.coordinator = coordinator
        self.device_id = coordinator.device_info.device_id
        
        # Track notification states to avoid spam
        self._notification_states: dict[str, dict[str, Any]] = {}

    def _coerce_to_datetime_utc(self, value: Any) -> datetime | None:
        """Convert supported timestamp representations to an aware UTC datetime."""
        if value is None:
            return None

        if isinstance(value, datetime):
            if value.tzinfo:
                return dt_util.as_utc(value)
            return value.replace(tzinfo=dt_util.UTC)

        if isinstance(value, (int, float)):
            try:
                return dt_util.utc_from_timestamp(float(value))
            except (ValueError, OSError):
                return None

        if isinstance(value, str):
            parsed = dt_util.parse_datetime(value)
            if parsed:
                return dt_util.as_utc(parsed)
            # Fall back to treating the string as a numeric timestamp
            try:
                return dt_util.utc_from_timestamp(float(value))
            except (ValueError, OSError):
                return None

        return None
        
    def get_notification_id(self, notification_type: str) -> str:
        """Generate notification ID for this device."""
        return f"{notification_type}_{self.device_id}"
    
    async def async_check_and_update_notifications(self) -> None:
        """Check device state and update notifications accordingly."""
        state: TsuryPhoneState = self.coordinator.data
        
        # Check maintenance mode notification
        await self._handle_maintenance_mode_notification(state)
        
        # Check connection/error notifications
        await self._handle_connection_notifications(state)
        
        # Check reboot notification
        await self._handle_reboot_notification(state)
        
        # Check missed calls notification
        await self._handle_missed_calls_notification(state)
    
    async def _handle_maintenance_mode_notification(self, state: TsuryPhoneState) -> None:
        """Handle maintenance mode notifications."""
        notification_id = self.get_notification_id(NOTIFICATION_ID_MAINTENANCE)
        
        if state.maintenance_mode:
            # Create/update maintenance mode notification
            if not self._is_notification_active(NOTIFICATION_ID_MAINTENANCE):
                await async_create(
                    self.hass,
                    message=(
                        f"TsuryPhone device '{self.coordinator.device_info.name}' is in maintenance mode. "
                        "Normal phone operations may be disrupted. "
                        "Disable maintenance mode when finished with device configuration."
                    ),
                    title="TsuryPhone Maintenance Mode Active",
                    notification_id=notification_id,
                )
                self._set_notification_active(NOTIFICATION_ID_MAINTENANCE, {
                    "enabled_at": dt_util.utcnow(),
                    "message_type": "maintenance_active"
                })
                _LOGGER.info("Created maintenance mode notification for device %s", self.device_id)
        else:
            # Dismiss maintenance mode notification if it exists
            if self._is_notification_active(NOTIFICATION_ID_MAINTENANCE):
                await async_dismiss(self.hass, notification_id)
                self._clear_notification_state(NOTIFICATION_ID_MAINTENANCE)
                _LOGGER.info("Dismissed maintenance mode notification for device %s", self.device_id)
    
    async def _handle_connection_notifications(self, state: TsuryPhoneState) -> None:
        """Handle connection and system error notifications."""
        notification_id = self.get_notification_id(NOTIFICATION_ID_SYSTEM_ERROR)
        
        # Check if device has been offline for an extended period
        if not state.connected and state.last_seen:
            try:
                last_seen_dt = self._coerce_to_datetime_utc(state.last_seen)
                if last_seen_dt is None:
                    raise ValueError(f"Unsupported last_seen value: {state.last_seen!r}")

                offline_duration = dt_util.utcnow() - last_seen_dt
                
                # Create notification if offline for more than 10 minutes
                if (offline_duration > timedelta(minutes=10) and 
                    not self._is_notification_active(NOTIFICATION_ID_SYSTEM_ERROR)):
                    
                    await async_create(
                        self.hass,
                        message=(
                            f"TsuryPhone device '{self.coordinator.device_info.name}' has been offline for "
                            f"{self._format_duration(offline_duration)}. "
                            f"Last seen: {dt_util.as_local(last_seen_dt).strftime('%Y-%m-%d %H:%M:%S')}. "
                            "Please check device power and network connectivity."
                        ),
                        title="TsuryPhone Device Offline",
                        notification_id=notification_id,
                    )
                    self._set_notification_active(NOTIFICATION_ID_SYSTEM_ERROR, {
                        "offline_since": last_seen_dt.isoformat(),
                        "message_type": "device_offline"
                    })
                    _LOGGER.warning("Created offline notification for device %s", self.device_id)
            except (ValueError, TypeError) as err:
                _LOGGER.warning("Failed to parse last_seen timestamp: %s", err)
        else:
            # Device is connected, dismiss offline notification if it exists
            if (self._is_notification_active(NOTIFICATION_ID_SYSTEM_ERROR) and
                self._notification_states[NOTIFICATION_ID_SYSTEM_ERROR].get("message_type") == "device_offline"):
                
                await async_dismiss(self.hass, notification_id)
                self._clear_notification_state(NOTIFICATION_ID_SYSTEM_ERROR)
                _LOGGER.info("Dismissed offline notification for device %s", self.device_id)
    
    async def _handle_reboot_notification(self, state: TsuryPhoneState) -> None:
        """Handle device reboot notifications."""
        notification_id = self.get_notification_id(NOTIFICATION_ID_REBOOT)
        
        if state.reboot_detected:
            # Create reboot notification if not already shown recently
            last_reboot_notification = self._notification_states.get(NOTIFICATION_ID_REBOOT, {})
            last_notification_time = last_reboot_notification.get("created_at")
            
            # Only notify if no notification in the last hour to avoid spam
            should_notify = True
            if last_notification_time:
                time_since_last = dt_util.utcnow() - last_notification_time
                should_notify = time_since_last > timedelta(hours=1)
            
            if should_notify:
                await async_create(
                    self.hass,
                    message=(
                        f"TsuryPhone device '{self.coordinator.device_info.name}' has rebooted. "
                        "The device is reconnecting and synchronizing state. "
                        "Some data may be refreshed automatically."
                    ),
                    title="TsuryPhone Device Rebooted",
                    notification_id=notification_id,
                )
                self._set_notification_active(NOTIFICATION_ID_REBOOT, {
                    "reboot_detected_at": dt_util.utcnow(),
                    "message_type": "device_reboot",
                    "created_at": dt_util.utcnow()
                })
                _LOGGER.info("Created reboot notification for device %s", self.device_id)
                
                # Auto-dismiss reboot notification after 30 seconds
                self.hass.async_create_task(
                    self._auto_dismiss_notification(notification_id, NOTIFICATION_ID_REBOOT, 30)
                )
    
    async def _handle_missed_calls_notification(self, state: TsuryPhoneState) -> None:
        """Handle missed calls notifications."""
        notification_id = self.get_notification_id(NOTIFICATION_ID_MISSED_CALLS)
        
        # Count recent missed calls (last 24 hours)
        missed_calls_count = 0
        recent_missed_calls = []
        
        if state.call_history:
            cutoff_time = dt_util.utcnow() - timedelta(hours=24)
            for call in state.call_history:
                if (call.missed and call.timestamp and 
                    call.timestamp > cutoff_time):
                    missed_calls_count += 1
                    recent_missed_calls.append(call)
        
        # Get last notified count to avoid repeat notifications
        last_state = self._notification_states.get(NOTIFICATION_ID_MISSED_CALLS, {})
        last_notified_count = last_state.get("missed_calls_count", 0)
        
        # Create/update notification if there are new missed calls
        if missed_calls_count > last_notified_count and missed_calls_count > 0:
            # Format message based on count
            if missed_calls_count == 1:
                latest_call = recent_missed_calls[0] if recent_missed_calls else None
                caller_info = f"from {latest_call.name or latest_call.number}" if latest_call else ""
                message = (
                    f"You have 1 missed call {caller_info} "
                    f"on TsuryPhone device '{self.coordinator.device_info.name}'."
                )
            else:
                message = (
                    f"You have {missed_calls_count} missed calls "
                    f"on TsuryPhone device '{self.coordinator.device_info.name}' in the last 24 hours."
                )
            
            await async_create(
                self.hass,
                message=message,
                title="TsuryPhone Missed Calls",
                notification_id=notification_id,
            )
            self._set_notification_active(NOTIFICATION_ID_MISSED_CALLS, {
                "missed_calls_count": missed_calls_count,
                "message_type": "missed_calls",
                "created_at": dt_util.utcnow()
            })
            _LOGGER.info("Created/updated missed calls notification for device %s (%d calls)", 
                        self.device_id, missed_calls_count)
        
        # Dismiss notification if no recent missed calls
        elif missed_calls_count == 0 and self._is_notification_active(NOTIFICATION_ID_MISSED_CALLS):
            await async_dismiss(self.hass, notification_id)
            self._clear_notification_state(NOTIFICATION_ID_MISSED_CALLS)
            _LOGGER.info("Dismissed missed calls notification for device %s", self.device_id)
    
    async def _auto_dismiss_notification(self, notification_id: str, notification_type: str, delay_seconds: int) -> None:
        """Auto-dismiss a notification after a delay."""
        await asyncio.sleep(delay_seconds)
        
        if self._is_notification_active(notification_type):
            await async_dismiss(self.hass, notification_id)
            self._clear_notification_state(notification_type)
            _LOGGER.info("Auto-dismissed %s notification for device %s", notification_type, self.device_id)
    
    def _is_notification_active(self, notification_type: str) -> bool:
        """Check if a notification type is currently active."""
        return notification_type in self._notification_states
    
    def _set_notification_active(self, notification_type: str, state_data: dict[str, Any]) -> None:
        """Mark a notification type as active with state data."""
        self._notification_states[notification_type] = state_data
    
    def _clear_notification_state(self, notification_type: str) -> None:
        """Clear state for a notification type."""
        self._notification_states.pop(notification_type, None)
    
    def _format_duration(self, duration: timedelta) -> str:
        """Format a duration for display."""
        total_seconds = int(duration.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        
        if hours > 0:
            return f"{hours} hours, {minutes} minutes"
        elif minutes > 0:
            return f"{minutes} minutes"
        else:
            return "less than a minute"
    
    async def async_dismiss_all_notifications(self) -> None:
        """Dismiss all notifications for this device."""
        notification_types = [
            NOTIFICATION_ID_SYSTEM_ERROR,
            NOTIFICATION_ID_MAINTENANCE,
            NOTIFICATION_ID_REBOOT,
            NOTIFICATION_ID_MISSED_CALLS,
        ]
        
        for notification_type in notification_types:
            if self._is_notification_active(notification_type):
                notification_id = self.get_notification_id(notification_type)
                await async_dismiss(self.hass, notification_id)
                self._clear_notification_state(notification_type)
        
        _LOGGER.info("Dismissed all notifications for device %s", self.device_id)


async def async_setup_notifications(hass: HomeAssistant, coordinator: TsuryPhoneDataUpdateCoordinator) -> TsuryPhoneNotificationManager:
    """Set up notification manager for a coordinator."""
    notification_manager = TsuryPhoneNotificationManager(hass, coordinator)
    
    # Initial notification check
    await notification_manager.async_check_and_update_notifications()
    
    return notification_manager


async def async_unload_notifications(hass: HomeAssistant, coordinator: TsuryPhoneDataUpdateCoordinator) -> None:
    """Clean up notifications when unloading."""
    if hasattr(coordinator, '_notification_manager'):
        await coordinator._notification_manager.async_dismiss_all_notifications()
        delattr(coordinator, '_notification_manager')