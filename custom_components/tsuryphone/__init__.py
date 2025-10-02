"""The TsuryPhone integration."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceInfo

from .api_client import TsuryPhoneAPIClient, TsuryPhoneAPIError
from .coordinator import TsuryPhoneDataUpdateCoordinator
from .services import async_setup_services, async_unload_services
from .notifications import async_setup_notifications, async_unload_notifications
from .storage_cache import TsuryPhoneStorageCache
from .const import (
    DOMAIN,
    MANUFACTURER,
    MODEL,
    DEFAULT_PORT,
)
from .models import DeviceInfo as TsuryDeviceInfo

_LOGGER = logging.getLogger(__name__)

# Platforms to set up
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.TEXT,
]

if TYPE_CHECKING:
    TsuryPhoneConfigEntry = ConfigEntry[TsuryPhoneDataUpdateCoordinator]
else:
    TsuryPhoneConfigEntry = ConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TsuryPhone from a config entry."""
    _LOGGER.debug("Setting up TsuryPhone integration for %s", entry.title)

    # Extract configuration
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    # Create API client
    api_client = TsuryPhoneAPIClient(hass, host, port)

    # Test connection and get device info
    try:
        config_response = await api_client.get_tsuryphone_config()
        device_data = config_response.get("data", {})
        device_id = device_data.get("deviceId")
        
        if not device_id:
            _LOGGER.error("Device did not return a device ID")
            raise ConfigEntryNotReady("Device missing device ID")

    except TsuryPhoneAPIError as err:
        _LOGGER.error("Failed to connect to TsuryPhone device: %s", err)
        raise ConfigEntryNotReady(f"Cannot connect to device: {err}") from err
    except Exception as err:
        _LOGGER.exception("Unexpected error connecting to TsuryPhone device")
        raise ConfigEntryNotReady(f"Unexpected error: {err}") from err

    # Create device info model
    device_info = TsuryDeviceInfo(
        device_id=device_id,
        host=host,
        port=port,
        name=entry.title,
        sw_version=device_data.get("softwareVersion"),
        hw_version=device_data.get("hardwareVersion"),
    )

    # Create and initialize coordinator
    coordinator = TsuryPhoneDataUpdateCoordinator(hass, api_client, device_info)
    
    # Perform first refresh to populate initial state
    await coordinator.async_config_entry_first_refresh()
    
    # Store coordinator in runtime data for platform access
    entry.runtime_data = coordinator
    
    # Store in hass.data for backwards compatibility
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    # Phase P5: Set up notifications
    coordinator._notification_manager = await async_setup_notifications(hass, coordinator)
    
    # Phase P7: Set up storage cache
    storage_cache = TsuryPhoneStorageCache(hass, device_info.device_id)
    await storage_cache.async_initialize()
    
    # Apply retention settings from options
    options_advanced = entry.options.get("advanced", {})
    if "call_history_retention_days" in options_advanced:
        storage_cache.update_retention_settings(
            call_history_retention_days=options_advanced["call_history_retention_days"]
        )
    
    coordinator._storage_cache = storage_cache
    
    # Load cached call history if available
    try:
        cached_call_history = await storage_cache.async_load_call_history()
        if cached_call_history:
            coordinator.data.call_history = cached_call_history
            _LOGGER.debug("Loaded %d cached call history entries", len(cached_call_history))
    except Exception as err:
        _LOGGER.error("Failed to load cached call history: %s", err)

    _LOGGER.info(
        "Successfully connected to TsuryPhone device %s at %s:%s",
        device_id,
        host,
        port,
    )

    # Set up services (only once for the integration)
    if not hass.services.has_service(DOMAIN, "dial"):
        await async_setup_services(hass)

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("TsuryPhone integration setup complete for %s", entry.title)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading TsuryPhone integration for %s", entry.title)

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Stop coordinator
    if unload_ok:
        coordinator: TsuryPhoneDataUpdateCoordinator = entry.runtime_data
        
        # Phase P5: Clean up notifications
        if hasattr(coordinator, '_notification_manager'):
            await async_unload_notifications(hass, coordinator)
        
        await coordinator.async_shutdown()

        # Unload services if this is the last config entry
        remaining_entries = [
            e for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id
        ]
        if not remaining_entries:
            await async_unload_services(hass)

    # Clean up stored data
    if entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id)

    _LOGGER.debug("TsuryPhone integration unloaded for %s", entry.title)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


def get_device_info(device_info: TsuryDeviceInfo) -> DeviceInfo:
    """Get Home Assistant device info from TsuryPhone device info."""
    return DeviceInfo(
        identifiers={(DOMAIN, device_info.device_id)},
        name=device_info.name,
        manufacturer=MANUFACTURER,
        model=MODEL,
        sw_version=device_info.sw_version,
        hw_version=device_info.hw_version,
        configuration_url=f"http://{device_info.host}:{device_info.port}",
    )