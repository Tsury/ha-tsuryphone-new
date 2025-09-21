"""Storage cache for TsuryPhone integration persistent data."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .models import CallHistoryEntry, TsuryPhoneState, CallDirection

_LOGGER = logging.getLogger(__name__)

# Storage versions
STORAGE_VERSION_CALL_HISTORY = 1
STORAGE_VERSION_DEVICE_STATE = 1
STORAGE_VERSION_CONFIG_BACKUP = 1

# Storage keys
STORAGE_KEY_CALL_HISTORY = "call_history"
STORAGE_KEY_DEVICE_STATE = "device_state"
STORAGE_KEY_CONFIG_BACKUP = "config_backup"

# Default retention settings
DEFAULT_CALL_HISTORY_RETENTION_DAYS = 30
DEFAULT_STATE_BACKUP_RETENTION_DAYS = 7
DEFAULT_MAX_CALL_HISTORY_ENTRIES = 1000


class TsuryPhoneStorageCache:
    """Manage persistent storage cache for TsuryPhone data."""

    def __init__(self, hass: HomeAssistant, device_id: str):
        """Initialize storage cache."""
        self.hass = hass
        self.device_id = device_id
        
        # Create store instances for different data types
        self._call_history_store = Store(
            hass,
            STORAGE_VERSION_CALL_HISTORY,
            f"{DOMAIN}_{device_id}_{STORAGE_KEY_CALL_HISTORY}",
        )
        self._device_state_store = Store(
            hass,
            STORAGE_VERSION_DEVICE_STATE,
            f"{DOMAIN}_{device_id}_{STORAGE_KEY_DEVICE_STATE}",
        )
        self._config_backup_store = Store(
            hass,
            STORAGE_VERSION_CONFIG_BACKUP,
            f"{DOMAIN}_{device_id}_{STORAGE_KEY_CONFIG_BACKUP}",
        )
        
        # Cache settings
        self.call_history_retention_days = DEFAULT_CALL_HISTORY_RETENTION_DAYS
        self.state_backup_retention_days = DEFAULT_STATE_BACKUP_RETENTION_DAYS
        self.max_call_history_entries = DEFAULT_MAX_CALL_HISTORY_ENTRIES
        
        # In-memory cache
        self._call_history_cache: list[CallHistoryEntry] = []
        self._device_state_cache: dict[str, Any] = {}
        self._cache_loaded = False

    async def async_initialize(self) -> None:
        """Initialize the storage cache."""
        await self._load_cache()
        _LOGGER.debug("Storage cache initialized for device %s", self.device_id)

    async def _load_cache(self) -> None:
        """Load data from storage into memory cache."""
        try:
            # Load call history
            call_history_data = await self._call_history_store.async_load()
            if call_history_data:
                self._call_history_cache = [
                    CallHistoryEntry.from_dict(entry)
                    for entry in call_history_data.get("entries", [])
                ]
                _LOGGER.debug("Loaded %d call history entries from cache", len(self._call_history_cache))

            # Load device state backup
            device_state_data = await self._device_state_store.async_load()
            if device_state_data:
                self._device_state_cache = device_state_data.get("state", {})
                _LOGGER.debug("Loaded device state backup from cache")

            self._cache_loaded = True

        except Exception as err:
            _LOGGER.error("Failed to load storage cache: %s", err)
            self._cache_loaded = True  # Continue without cache

    async def async_save_call_history(self, call_history: list[CallHistoryEntry]) -> None:
        """Save call history to persistent storage."""
        if not self._cache_loaded:
            await self._load_cache()

        try:
            # Update in-memory cache
            self._call_history_cache = call_history.copy()
            
            # Clean up old entries before saving
            cleaned_entries = await self._cleanup_call_history(call_history)
            
            # Convert to serializable format
            data = {
                "entries": [entry.to_dict() for entry in cleaned_entries],
                "last_updated": dt_util.utcnow().isoformat(),
                "device_id": self.device_id,
            }
            
            await self._call_history_store.async_save(data)
            _LOGGER.debug("Saved %d call history entries to cache", len(cleaned_entries))

        except Exception as err:
            _LOGGER.error("Failed to save call history to cache: %s", err)

    async def async_load_call_history(self) -> list[CallHistoryEntry]:
        """Load call history from persistent storage."""
        if not self._cache_loaded:
            await self._load_cache()

        return self._call_history_cache.copy()

    async def async_save_device_state(self, state: TsuryPhoneState) -> None:
        """Save device state backup to persistent storage."""
        try:
            # Create state backup (excluding sensitive data)
            state_backup = {
                "app_state": state.app_state.value,
                "connected": state.connected,
                "last_seen": state.last_seen,
                "dnd_config": {
                    "force": state.dnd_config.force,
                    "scheduled": state.dnd_config.scheduled,
                    "start_hour": state.dnd_config.start_hour,
                    "start_minute": state.dnd_config.start_minute,
                    "end_hour": state.dnd_config.end_hour,
                    "end_minute": state.dnd_config.end_minute,
                },
                "audio_config": {
                    "earpiece_volume": state.audio_config.earpiece_volume,
                    "earpiece_gain": state.audio_config.earpiece_gain,
                    "speaker_volume": state.audio_config.speaker_volume,
                    "speaker_gain": state.audio_config.speaker_gain,
                },
                "ring_pattern": state.ring_pattern,
                "maintenance_mode": state.maintenance_mode,
                "stats": {
                    "calls_total": state.stats.calls_total,
                    "calls_incoming": state.stats.calls_incoming,
                    "calls_outgoing": state.stats.calls_outgoing,
                    "calls_blocked": state.stats.calls_blocked,
                    "talk_time_seconds": state.stats.talk_time_seconds,
                    "uptime_seconds": state.stats.uptime_seconds,
                    "free_heap_bytes": state.stats.free_heap_bytes,
                    "rssi_dbm": state.stats.rssi_dbm,
                },
                "quick_dial_count": state.quick_dial_count,
                "blocked_count": state.blocked_count,
                "call_history_size": state.call_history_size,
                "last_seq": state.last_seq,
            }

            data = {
                "state": state_backup,
                "last_updated": dt_util.utcnow().isoformat(),
                "device_id": self.device_id,
            }

            await self._device_state_store.async_save(data)
            self._device_state_cache = state_backup
            _LOGGER.debug("Saved device state backup to cache")

        except Exception as err:
            _LOGGER.error("Failed to save device state to cache: %s", err)

    async def async_load_device_state(self) -> dict[str, Any]:
        """Load device state backup from persistent storage."""
        if not self._cache_loaded:
            await self._load_cache()

        return self._device_state_cache.copy()

    async def async_save_config_backup(self, config_data: dict[str, Any]) -> None:
        """Save configuration backup to persistent storage."""
        try:
            # Create timestamped backup entry
            backup_entry = {
                "timestamp": dt_util.utcnow().isoformat(),
                "config": config_data,
                "device_id": self.device_id,
            }

            # Load existing backups
            existing_data = await self._config_backup_store.async_load()
            if not existing_data:
                existing_data = {"backups": []}

            # Add new backup
            existing_data["backups"].append(backup_entry)

            # Clean up old backups
            cutoff_date = dt_util.utcnow() - timedelta(days=self.state_backup_retention_days)
            existing_data["backups"] = [
                backup for backup in existing_data["backups"]
                if datetime.fromisoformat(backup["timestamp"]) > cutoff_date
            ]

            # Keep only the latest 10 backups
            existing_data["backups"] = existing_data["backups"][-10:]

            await self._config_backup_store.async_save(existing_data)
            _LOGGER.debug("Saved configuration backup to cache")

        except Exception as err:
            _LOGGER.error("Failed to save config backup to cache: %s", err)

    async def async_load_config_backups(self) -> list[dict[str, Any]]:
        """Load configuration backups from persistent storage."""
        try:
            data = await self._config_backup_store.async_load()
            if data:
                return data.get("backups", [])
            return []

        except Exception as err:
            _LOGGER.error("Failed to load config backups from cache: %s", err)
            return []

    async def async_get_latest_config_backup(self) -> dict[str, Any] | None:
        """Get the most recent configuration backup."""
        backups = await self.async_load_config_backups()
        if backups:
            return max(backups, key=lambda b: b["timestamp"])
        return None

    async def _cleanup_call_history(self, entries: list[CallHistoryEntry]) -> list[CallHistoryEntry]:
        """Clean up call history entries based on retention policies."""
        if not entries:
            return entries

        # Sort entries by timestamp (newest first)
        sorted_entries = sorted(
            entries,
            key=lambda e: e.timestamp or datetime.min,
            reverse=True
        )

        # Apply retention policies
        cleaned_entries = []
        cutoff_date = dt_util.utcnow() - timedelta(days=self.call_history_retention_days)

        for entry in sorted_entries:
            # Skip entries that are too old
            if entry.timestamp and entry.timestamp < cutoff_date:
                continue
            
            # Skip if we've reached max entries
            if len(cleaned_entries) >= self.max_call_history_entries:
                break
            
            cleaned_entries.append(entry)

        _LOGGER.debug("Cleaned call history: %d -> %d entries", len(entries), len(cleaned_entries))
        return cleaned_entries

    async def async_cleanup_storage(self) -> dict[str, int]:
        """Clean up old storage data and return cleanup statistics."""
        stats = {
            "call_history_removed": 0,
            "config_backups_removed": 0,
            "storage_errors": 0,
        }

        try:
            # Clean up call history
            if self._call_history_cache:
                original_count = len(self._call_history_cache)
                cleaned_entries = await self._cleanup_call_history(self._call_history_cache)
                stats["call_history_removed"] = original_count - len(cleaned_entries)
                
                if stats["call_history_removed"] > 0:
                    await self.async_save_call_history(cleaned_entries)

            # Clean up config backups
            try:
                existing_data = await self._config_backup_store.async_load()
                if existing_data and "backups" in existing_data:
                    original_count = len(existing_data["backups"])
                    cutoff_date = dt_util.utcnow() - timedelta(days=self.state_backup_retention_days)
                    
                    existing_data["backups"] = [
                        backup for backup in existing_data["backups"]
                        if datetime.fromisoformat(backup["timestamp"]) > cutoff_date
                    ]
                    
                    stats["config_backups_removed"] = original_count - len(existing_data["backups"])
                    
                    if stats["config_backups_removed"] > 0:
                        await self._config_backup_store.async_save(existing_data)
                        
            except Exception as err:
                _LOGGER.error("Failed to clean up config backups: %s", err)
                stats["storage_errors"] += 1

        except Exception as err:
            _LOGGER.error("Failed to clean up storage: %s", err)
            stats["storage_errors"] += 1

        _LOGGER.info("Storage cleanup completed: %s", stats)
        return stats

    async def async_get_storage_stats(self) -> dict[str, Any]:
        """Get storage cache statistics."""
        try:
            stats = {
                "device_id": self.device_id,
                "cache_loaded": self._cache_loaded,
                "call_history_entries": len(self._call_history_cache),
                "device_state_cached": bool(self._device_state_cache),
                "retention_settings": {
                    "call_history_retention_days": self.call_history_retention_days,
                    "state_backup_retention_days": self.state_backup_retention_days,
                    "max_call_history_entries": self.max_call_history_entries,
                },
            }

            # Get storage file information
            try:
                config_backups = await self.async_load_config_backups()
                stats["config_backups_count"] = len(config_backups)
                
                if config_backups:
                    stats["latest_config_backup"] = max(
                        config_backups,
                        key=lambda b: b["timestamp"]
                    )["timestamp"]
                else:
                    stats["latest_config_backup"] = None
                    
            except Exception as err:
                stats["config_backup_error"] = str(err)

            # Calculate storage usage (approximate)
            if self._call_history_cache:
                avg_entry_size = 200  # Approximate bytes per call history entry
                stats["estimated_call_history_size_bytes"] = len(self._call_history_cache) * avg_entry_size
            else:
                stats["estimated_call_history_size_bytes"] = 0

            return stats

        except Exception as err:
            _LOGGER.error("Failed to get storage stats: %s", err)
            return {"error": str(err), "device_id": self.device_id}

    def update_retention_settings(self, **settings) -> None:
        """Update retention settings."""
        if "call_history_retention_days" in settings:
            self.call_history_retention_days = settings["call_history_retention_days"]
        
        if "state_backup_retention_days" in settings:
            self.state_backup_retention_days = settings["state_backup_retention_days"]
        
        if "max_call_history_entries" in settings:
            self.max_call_history_entries = settings["max_call_history_entries"]

        _LOGGER.debug("Updated retention settings: %s", settings)

    async def async_clear_all_storage(self) -> None:
        """Clear all storage data (use with caution)."""
        try:
            await self._call_history_store.async_remove()
            await self._device_state_store.async_remove()
            await self._config_backup_store.async_remove()
            
            # Clear in-memory cache
            self._call_history_cache.clear()
            self._device_state_cache.clear()
            
            _LOGGER.warning("Cleared all storage data for device %s", self.device_id)

        except Exception as err:
            _LOGGER.error("Failed to clear storage data: %s", err)

    async def async_export_data(self) -> dict[str, Any]:
        """Export all cached data for backup purposes."""
        try:
            return {
                "device_id": self.device_id,
                "export_timestamp": dt_util.utcnow().isoformat(),
                "call_history": [entry.to_dict() for entry in self._call_history_cache],
                "device_state": self._device_state_cache.copy(),
                "config_backups": await self.async_load_config_backups(),
                "storage_stats": await self.async_get_storage_stats(),
            }

        except Exception as err:
            _LOGGER.error("Failed to export data: %s", err)
            return {"error": str(err), "device_id": self.device_id}

    async def async_import_data(self, data: dict[str, Any]) -> bool:
        """Import data from backup (use with caution)."""
        try:
            if data.get("device_id") != self.device_id:
                _LOGGER.error("Device ID mismatch in import data")
                return False

            # Import call history
            if "call_history" in data:
                call_history = [
                    CallHistoryEntry.from_dict(entry)
                    for entry in data["call_history"]
                ]
                await self.async_save_call_history(call_history)

            # Import device state
            if "device_state" in data:
                self._device_state_cache = data["device_state"]
                await self._device_state_store.async_save({
                    "state": data["device_state"],
                    "last_updated": dt_util.utcnow().isoformat(),
                    "device_id": self.device_id,
                })

            _LOGGER.info("Successfully imported data for device %s", self.device_id)
            return True

        except Exception as err:
            _LOGGER.error("Failed to import data: %s", err)
            return False