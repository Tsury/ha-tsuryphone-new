"""The TsuryPhone integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.network import get_url
from urllib.parse import urlparse

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


def _normalize_url(url: str | None) -> str | None:
    """Normalize a Home Assistant URL candidate."""

    if not url:
        return None

    candidate = url.strip()
    if not candidate:
        return None

    # Remove trailing slash for consistency
    if candidate.endswith("/"):
        candidate = candidate[:-1]

    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        return None

    host = parsed.hostname or ""
    if host in {"localhost", "127.0.0.1", "::1"}:
        return None

    return candidate


async def _ensure_device_ha_url(
    hass: HomeAssistant,
    api_client: TsuryPhoneAPIClient,
    device_data: dict,
) -> None:
    """Ensure the TsuryPhone device knows how to reach this Home Assistant instance."""

    desired_url: str | None = None

    # Prefer explicitly configured URLs
    for candidate in (hass.config.external_url, hass.config.internal_url):
        desired_url = _normalize_url(candidate)
        if desired_url:
            break

    if not desired_url:
        try:
            # Fall back to Home Assistant's best guess if explicit URLs are missing
            guessed_url = get_url(
                hass,
                allow_external=True,
                allow_internal=True,
                allow_ip=True,
                allow_cloud=False,
            )
        except HomeAssistantError:  # URL discovery not available yet
            guessed_url = None

        desired_url = _normalize_url(guessed_url)

    if not desired_url:
        _LOGGER.debug("Skipping HA URL sync: no suitable Home Assistant URL available")
        return

    # Extract current URL from the device response, if present, to avoid unnecessary writes
    current_url: str | None = None
    if isinstance(device_data, dict):
        phone_section = device_data.get("phone")
        if isinstance(phone_section, dict):
            current_url = phone_section.get("homeAssistantUrl")
        if not current_url:
            current_url = device_data.get("homeAssistantUrl")

    current_url = _normalize_url(current_url)
    if current_url == desired_url:
        _LOGGER.debug(
            "Device already configured with Home Assistant URL %s", desired_url
        )
        return

    try:
        await api_client.set_ha_url(desired_url)
    except TsuryPhoneAPIError as err:
        _LOGGER.warning(
            "Failed to update TsuryPhone Home Assistant URL to %s: %s",
            desired_url,
            err,
        )
    else:
        _LOGGER.info("Updated TsuryPhone Home Assistant URL to %s", desired_url)


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

    # Ensure device can reach this Home Assistant instance for webhook callbacks
    await _ensure_device_ha_url(hass, api_client, device_data)

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
    _LOGGER.info("========== CREATING COORDINATOR ==========")
    coordinator = TsuryPhoneDataUpdateCoordinator(hass, api_client, device_info)
    _LOGGER.info("Coordinator created, data state: %s", coordinator.data)

    # Phase P7: Set up storage cache BEFORE first refresh
    _LOGGER.info("========== SETTING UP STORAGE CACHE ==========")
    storage_cache = TsuryPhoneStorageCache(hass, device_info.device_id)
    _LOGGER.info("Storage cache instance created for device: %s", device_info.device_id)
    
    await storage_cache.async_initialize()
    _LOGGER.info("Storage cache initialized")

    # Apply retention settings from options
    options_advanced = entry.options.get("advanced", {})
    if "call_history_retention_days" in options_advanced:
        storage_cache.update_retention_settings(
            call_history_retention_days=options_advanced["call_history_retention_days"]
        )
        _LOGGER.info("Applied retention settings: %d days", options_advanced["call_history_retention_days"])

    coordinator._storage_cache = storage_cache
    _LOGGER.info("Storage cache attached to coordinator")

    # Initialize state and load cached data BEFORE first refresh
    coordinator._ensure_state()
    
    # Load cached call history if available
    try:
        cached_call_history = await storage_cache.async_load_call_history()
        if cached_call_history is not None:
            coordinator.data.call_history = cached_call_history
    except Exception as err:
        _LOGGER.error("Failed to load cached call history: %s", err, exc_info=True)

    # Load cached device state (including send_mode)
    try:
        cached_device_state = await storage_cache.async_load_device_state()
        if cached_device_state and "send_mode_enabled" in cached_device_state:
            coordinator._send_mode_enabled = cached_device_state["send_mode_enabled"]
    except Exception as err:
        _LOGGER.error("Failed to load cached device state: %s", err, exc_info=True)

    # Perform first refresh to populate initial state (will preserve call_history now)
    await coordinator.async_config_entry_first_refresh()

    # Refetch device statistics from firmware to ensure they're up-to-date
    # This is important when HA restarts but device stays powered on
    try:
        _LOGGER.info("Refetching device statistics from firmware after reconnect")
        await api_client.refetch_all()
        await coordinator.async_refresh()
        _LOGGER.info("Successfully refetched device statistics")
    except Exception as err:
        _LOGGER.warning("Failed to refetch stats on reconnect: %s", err)
        # Non-fatal - continue setup even if refetch fails

    # Store coordinator in runtime data for platform access
    entry.runtime_data = coordinator

    # Phase P5: Set up notifications
    coordinator._notification_manager = await async_setup_notifications(
        hass, coordinator
    )

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
        coordinator: TsuryPhoneDataUpdateCoordinator | None = entry.runtime_data

        if coordinator is not None:
            # Phase P5: Clean up notifications
            if hasattr(coordinator, "_notification_manager"):
                await async_unload_notifications(hass, coordinator)

            await coordinator.async_shutdown()

        # Unload services if this is the last config entry
        remaining_entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id
        ]
        if not remaining_entries:
            await async_unload_services(hass)

    entry.runtime_data = None

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
