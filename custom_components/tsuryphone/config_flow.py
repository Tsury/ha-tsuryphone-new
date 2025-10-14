"""Config flow for TsuryPhone integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import zeroconf
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv
import aiohttp

from .const import (
    DOMAIN,
    DEFAULT_PORT,
    MDNS_SERVICE_TYPE,
    MDNS_DEVICE_TYPE,
    API_CONFIG_TSURYPHONE,
    INTEGRATION_EVENT_SCHEMA_VERSION,
    CONF_HOST_OVERRIDE,
    CONF_POLLING_FALLBACK_SECONDS,
    CONF_REFETCH_INTERVAL_MINUTES,
    AUDIO_MIN_LEVEL,
    AUDIO_MAX_LEVEL,
    RING_PATTERN_PRESET_LABELS,
    RING_PATTERN_PRESETS,
)
from .validation import is_valid_ring_pattern

# Import for type hints - will be imported at runtime to avoid circular imports
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import TsuryPhoneDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
    }
)


class TsuryPhoneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TsuryPhone."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self.discovery_info: dict[str, Any] = {}
        self._pattern_label_map: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
                await self.async_set_unique_id(info["device_id"])
                self._abort_if_unique_id_configured()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except InvalidHost:
                errors["base"] = "invalid_host"
            except SchemaVersionMismatch as err:
                errors["base"] = "schema_version_mismatch"
                _LOGGER.warning("Schema version mismatch: %s", err)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_zeroconf(
        self, discovery_info: zeroconf.ZeroconfServiceInfo
    ) -> FlowResult:
        """Handle zeroconf discovery."""
        _LOGGER.debug("Discovered TsuryPhone via zeroconf: %s", discovery_info)

        # Extract device type from TXT records
        properties = discovery_info.properties
        raw_device = properties.get("device")

        if isinstance(raw_device, bytes):
            device_type = raw_device.decode("utf-8", "ignore")
        elif isinstance(raw_device, str):
            device_type = raw_device
        else:
            device_type = ""

        if device_type != MDNS_DEVICE_TYPE:
            _LOGGER.debug("Ignoring discovery - not a TsuryPhone device")
            return self.async_abort(reason="not_tsuryphone_device")

        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT

        # Check if already configured
        await self.async_set_unique_id(f"{host}:{port}")
        self._abort_if_unique_id_configured()

        # Store discovery info
        self.discovery_info = {
            CONF_HOST: host,
            CONF_PORT: port,
        }

        # Try to validate the discovered device
        try:
            info = await validate_input(self.hass, self.discovery_info)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Could not validate discovered device: %s", err)
            return self.async_abort(reason="cannot_connect")

        # Set unique ID based on device ID from device response
        device_id = info.get("device_id")
        if device_id:
            await self.async_set_unique_id(device_id)
            self._abort_if_unique_id_configured()

        # Update discovery info with device info
        self.discovery_info.update(info)

        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user-confirmation of discovered node."""
        if user_input is not None:
            return self.async_create_entry(
                title=self.discovery_info["title"], data=self.discovery_info
            )

        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={
                "name": self.discovery_info.get("title", "TsuryPhone"),
                "host": self.discovery_info[CONF_HOST],
                "port": self.discovery_info[CONF_PORT],
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TsuryPhoneOptionsFlow:
        """Create the options flow."""
        return TsuryPhoneOptionsFlow(config_entry)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    host = data[CONF_HOST]
    port = data[CONF_PORT]

    # Validate host format
    if not host:
        raise InvalidHost("Host cannot be empty")

    # Basic URL validation for manual entry
    if "://" in host:
        try:
            parsed = urlparse(host)
            if parsed.hostname:
                host = parsed.hostname
            if parsed.port:
                port = parsed.port
        except Exception as err:
            raise InvalidHost(f"Invalid host format: {err}") from err

    # Test connection by fetching device config
    session = async_get_clientsession(hass)
    url = f"http://{host}:{port}{API_CONFIG_TSURYPHONE}"

    try:
        async with asyncio.timeout(10):
            async with session.get(url) as response:
                if response.status != 200:
                    raise CannotConnect(f"HTTP {response.status}")

                data_resp = await response.json()

                # Validate response structure
                if not data_resp.get("success"):
                    # Firmware sends "message" field, not "errorMessage"
                    error_msg = data_resp.get("message", "Unknown error")
                    raise CannotConnect(f"Device error: {error_msg}")

                device_data = data_resp.get("data", {})

                # Check schema version
                schema_version = data_resp.get("schemaVersion")
                if schema_version != INTEGRATION_EVENT_SCHEMA_VERSION:
                    raise SchemaVersionMismatch(
                        f"Expected schema version {INTEGRATION_EVENT_SCHEMA_VERSION}, "
                        f"got {schema_version}"
                    )

                # Extract device info
                device_id = device_data.get("deviceId", "")
                if not device_id:
                    raise InvalidAuth("No device ID found in response")

                device_name = (
                    device_data.get("deviceName") or device_id or f"TsuryPhone ({host})"
                )

                return {
                    "title": device_name,
                    "device_id": device_id,
                    "host": host,
                    "port": port,
                    "sw_version": device_data.get("softwareVersion"),
                    "hw_version": device_data.get("hardwareVersion"),
                }

    except asyncio.TimeoutError as err:
        raise CannotConnect("Connection timeout") from err
    except aiohttp.ClientError as err:
        raise CannotConnect(f"Connection error: {err}") from err
    except Exception as err:
        _LOGGER.exception("Unexpected error during validation")
        raise CannotConnect(f"Unexpected error: {err}") from err


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class InvalidHost(HomeAssistantError):
    """Error to indicate the host is invalid."""


class SchemaVersionMismatch(HomeAssistantError):
    """Error to indicate schema version mismatch."""


# Phase P6: Options Flow Implementation
class TsuryPhoneOptionsFlow(config_entries.OptionsFlow):
    """Handle TsuryPhone options flow."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self.coordinator: TsuryPhoneDataUpdateCoordinator | None = None
        self._current_step_data: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle options flow initialization."""
        # Get coordinator
        if self.config_entry.state != ConfigEntryState.LOADED:
            return self.async_abort(reason="integration_not_loaded")

        coordinator_data: TsuryPhoneDataUpdateCoordinator | None = (
            self.config_entry.runtime_data
        )
        if coordinator_data is None:
            return self.async_abort(reason="coordinator_not_found")

        self.coordinator = coordinator_data

        # Show main options menu
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "audio_settings",
                "dnd_settings",
                "ring_pattern_settings",
                "quick_dial_manager",
                "blocked_numbers_manager",
                "webhook_manager",
                "notification_settings",
                "advanced_settings",
            ],
        )

    async def async_step_audio_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle audio settings configuration."""
        if user_input is not None:
            # Validate and save audio settings
            try:
                audio_config = {
                    "earpieceVolume": user_input["earpiece_volume"],
                    "earpieceGain": user_input["earpiece_gain"],
                    "speakerVolume": user_input["speaker_volume"],
                    "speakerGain": user_input["speaker_gain"],
                }

                await self.coordinator.api_client.set_audio_config(audio_config)
                await self.coordinator.async_request_refresh()

                return self.async_create_entry(title="", data={})

            except Exception as err:
                return self.async_show_form(
                    step_id="audio_settings",
                    data_schema=self._get_audio_settings_schema(),
                    errors={"base": "audio_config_failed"},
                    description_placeholders={"error": str(err)},
                )

        return self.async_show_form(
            step_id="audio_settings",
            data_schema=self._get_audio_settings_schema(),
            description_placeholders={"title": "Audio Settings"},
        )

    async def async_step_dnd_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle DND settings configuration."""
        if user_input is not None:
            try:
                dnd_config = {
                    "force": user_input["force_dnd"],
                    "scheduled": user_input["scheduled_dnd"],
                }

                if user_input["scheduled_dnd"]:
                    dnd_config.update(
                        {
                            "start_hour": user_input["start_hour"],
                            "start_minute": user_input["start_minute"],
                            "end_hour": user_input["end_hour"],
                            "end_minute": user_input["end_minute"],
                        }
                    )

                await self.coordinator.api_client.set_dnd(dnd_config)
                await self.coordinator.async_request_refresh()

                return self.async_create_entry(title="", data={})

            except Exception as err:
                return self.async_show_form(
                    step_id="dnd_settings",
                    data_schema=self._get_dnd_settings_schema(),
                    errors={"base": "dnd_config_failed"},
                    description_placeholders={"error": str(err)},
                )

        return self.async_show_form(
            step_id="dnd_settings",
            data_schema=self._get_dnd_settings_schema(),
        )

    async def async_step_ring_pattern_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle ring pattern settings."""
        if user_input is not None:
            try:
                if not self._pattern_label_map:
                    self._pattern_label_map, _ = self._build_ring_pattern_options(
                        self.coordinator.data.ring_pattern
                    )

                selected_label = user_input["pattern_mode"]
                pattern_mode = self._pattern_label_map.get(
                    selected_label,
                    "Custom" if selected_label.startswith("Custom") else selected_label,
                )

                if pattern_mode == "Custom":
                    pattern = (user_input.get("custom_pattern") or "").strip()

                    if not pattern:
                        return self.async_show_form(
                            step_id="ring_pattern_settings",
                            data_schema=self._get_ring_pattern_schema(),
                            errors={"custom_pattern": "pattern_required"},
                        )
                else:
                    pattern = RING_PATTERN_PRESETS.get(pattern_mode, "")

                # Validate pattern
                if pattern and not self._validate_ring_pattern(pattern):
                    return self.async_show_form(
                        step_id="ring_pattern_settings",
                        data_schema=self._get_ring_pattern_schema(),
                        errors={"custom_pattern": "invalid_pattern"},
                    )

                await self.coordinator.api_client.set_ring_pattern(pattern)
                await self.coordinator.async_request_refresh()

                return self.async_create_entry(title="", data={})

            except Exception as err:
                return self.async_show_form(
                    step_id="ring_pattern_settings",
                    data_schema=self._get_ring_pattern_schema(),
                    errors={"base": "ring_pattern_failed"},
                    description_placeholders={"error": str(err)},
                )

        return self.async_show_form(
            step_id="ring_pattern_settings",
            data_schema=self._get_ring_pattern_schema(),
        )

    def _get_audio_settings_schema(self) -> vol.Schema:
        """Get audio settings schema with current values."""
        current_audio = self.coordinator.data.audio_config

        return vol.Schema(
            {
                vol.Required(
                    "earpiece_volume", default=current_audio.earpiece_volume
                ): vol.All(
                    vol.Coerce(int), vol.Range(min=AUDIO_MIN_LEVEL, max=AUDIO_MAX_LEVEL)
                ),
                vol.Required(
                    "earpiece_gain", default=current_audio.earpiece_gain
                ): vol.All(
                    vol.Coerce(int), vol.Range(min=AUDIO_MIN_LEVEL, max=AUDIO_MAX_LEVEL)
                ),
                vol.Required(
                    "speaker_volume", default=current_audio.speaker_volume
                ): vol.All(
                    vol.Coerce(int), vol.Range(min=AUDIO_MIN_LEVEL, max=AUDIO_MAX_LEVEL)
                ),
                vol.Required(
                    "speaker_gain", default=current_audio.speaker_gain
                ): vol.All(
                    vol.Coerce(int), vol.Range(min=AUDIO_MIN_LEVEL, max=AUDIO_MAX_LEVEL)
                ),
            }
        )

    def _get_dnd_settings_schema(self) -> vol.Schema:
        """Get DND settings schema with current values."""
        current_dnd = self.coordinator.data.dnd_config

        return vol.Schema(
            {
                vol.Required("force_dnd", default=current_dnd.force): cv.boolean,
                vol.Required(
                    "scheduled_dnd", default=current_dnd.scheduled
                ): cv.boolean,
                vol.Required("start_hour", default=current_dnd.start_hour): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=23)
                ),
                vol.Required("start_minute", default=current_dnd.start_minute): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=59)
                ),
                vol.Required("end_hour", default=current_dnd.end_hour): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=23)
                ),
                vol.Required("end_minute", default=current_dnd.end_minute): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=59)
                ),
            }
        )

    def _get_ring_pattern_schema(self) -> vol.Schema:
        """Get ring pattern settings schema."""
        current_pattern = self.coordinator.data.ring_pattern

        # Determine current mode
        pattern_options, default_label = self._build_ring_pattern_options(
            current_pattern
        )
        self._pattern_label_map = pattern_options

        return vol.Schema(
            {
                vol.Required("pattern_mode", default=default_label): vol.In(
                    list(pattern_options.keys())
                ),
                vol.Optional(
                    "custom_pattern",
                    default=current_pattern if default_label.startswith("Custom") else "",
                ): cv.string,
            }
        )

    def _validate_ring_pattern(self, pattern: str) -> bool:
        """Validate ring pattern format."""
        return is_valid_ring_pattern(pattern)

    def _canonicalize_number(self, value: str, field: str) -> str:
        """Convert user input into the device's canonical phone number format."""

        if not self.coordinator:
            raise ValueError("Coordinator not available")

        context = self.coordinator.data.dialing_context
        candidate = (value or "").strip()
        if not candidate:
            raise ValueError(f"{field} cannot be empty")

        normalized = context.normalize(candidate)
        if not normalized:
            raise ValueError(f"{field} must contain at least one digit")

        device_value = context.canonicalize(candidate)
        if not device_value:
            raise ValueError(f"{field} could not be canonicalized")

        self.coordinator.remember_number_display_hint(candidate)
        return device_value

    def _build_ring_pattern_options(
        self, current_pattern: str | None
    ) -> tuple[dict[str, str], str]:
        """Build labeled ring pattern options and return default label."""

        option_map: dict[str, str] = {
            RING_PATTERN_PRESET_LABELS[name]: name for name in RING_PATTERN_PRESETS
        }

        preset_match = next(
            (name for name, pattern in RING_PATTERN_PRESETS.items() if pattern == current_pattern),
            None,
        )

        custom_label = "Custom"
        if current_pattern and not preset_match:
            custom_label = f"Custom ({current_pattern})"

        option_map[custom_label] = "Custom"

        default_key = preset_match or ("Default" if not current_pattern else "Custom")
        default_label = next(
            (label for label, name in option_map.items() if name == default_key),
            custom_label,
        )

        return option_map, default_label

    async def async_step_quick_dial_manager(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle quick dial management."""
        if user_input is not None:
            action = user_input.get("action")

            if action == "add":
                return await self.async_step_quick_dial_add()
            elif action == "remove":
                return await self.async_step_quick_dial_remove()
            elif action == "import":
                return await self.async_step_quick_dial_import()
            elif action == "export":
                return await self.async_step_quick_dial_export()
            elif action == "clear_all":
                return await self.async_step_quick_dial_clear()

        return self.async_show_form(
            step_id="quick_dial_manager",
            data_schema=vol.Schema(
                {
                    vol.Required("action"): vol.In(
                        ["add", "remove", "import", "export", "clear_all"]
                    )
                }
            ),
        )

    async def async_step_quick_dial_add(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a quick dial entry."""
        if user_input is not None:
            try:
                code = user_input["code"]
                number = self._canonicalize_number(user_input["number"], "number")
                name = user_input["name"]

                await self.coordinator.api_client.add_quick_dial(code, number, name)
                await self.coordinator.async_request_refresh()

                return self.async_create_entry(title="", data={})

            except Exception as err:
                return self.async_show_form(
                    step_id="quick_dial_add",
                    data_schema=self._get_quick_dial_add_schema(),
                    errors={"base": "add_quick_dial_failed"},
                    description_placeholders={"error": str(err)},
                )

        return self.async_show_form(
            step_id="quick_dial_add",
            data_schema=self._get_quick_dial_add_schema(),
        )

    async def async_step_quick_dial_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Remove a quick dial entry."""
        current_entries = self.coordinator.data.quick_dials or []

        if not current_entries:
            return self.async_abort(reason="no_quick_dial_entries")

        if user_input is not None:
            try:
                code = user_input["code"]
                await self.coordinator.api_client.remove_quick_dial(code)
                await self.coordinator.async_request_refresh()

                return self.async_create_entry(title="", data={})

            except Exception as err:
                return self.async_show_form(
                    step_id="quick_dial_remove",
                    data_schema=self._get_quick_dial_remove_schema(),
                    errors={"base": "remove_quick_dial_failed"},
                    description_placeholders={"error": str(err)},
                )

        return self.async_show_form(
            step_id="quick_dial_remove",
            data_schema=self._get_quick_dial_remove_schema(),
        )

    async def async_step_blocked_numbers_manager(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle blocked numbers management."""
        if user_input is not None:
            action = user_input.get("action")

            if action == "add":
                return await self.async_step_blocked_add()
            elif action == "remove":
                return await self.async_step_blocked_remove()
            elif action == "import":
                return await self.async_step_blocked_import()
            elif action == "export":
                return await self.async_step_blocked_export()
            elif action == "clear_all":
                return await self.async_step_blocked_clear()

        return self.async_show_form(
            step_id="blocked_numbers_manager",
            data_schema=vol.Schema(
                {
                    vol.Required("action"): vol.In(
                        ["add", "remove", "import", "export", "clear_all"]
                    )
                }
            ),
        )

    async def async_step_blocked_add(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a blocked number."""
        if user_input is not None:
            try:
                number = self._canonicalize_number(user_input["number"], "number")
                name = user_input["name"]

                await self.coordinator.api_client.add_blocked_number(number, name)
                await self.coordinator.async_request_refresh()

                return self.async_create_entry(title="", data={})

            except Exception as err:
                return self.async_show_form(
                    step_id="blocked_add",
                    data_schema=self._get_blocked_add_schema(),
                    errors={"base": "add_blocked_failed"},
                    description_placeholders={"error": str(err)},
                )

        return self.async_show_form(
            step_id="blocked_add",
            data_schema=self._get_blocked_add_schema(),
        )

    def _get_quick_dial_add_schema(self) -> vol.Schema:
        """Get quick dial add schema."""
        return vol.Schema(
            {
                vol.Required("code"): cv.string,
                vol.Required("number"): cv.string,
                vol.Required("name"): vol.All(cv.string, vol.Length(min=1)),
            }
        )

    def _get_quick_dial_remove_schema(self) -> vol.Schema:
        """Get quick dial remove schema."""
        current_entries = self.coordinator.data.quick_dials or []
        codes = [entry.code for entry in current_entries]

        return vol.Schema(
            {
                vol.Required("code"): vol.In(codes),
            }
        )

    def _get_blocked_add_schema(self) -> vol.Schema:
        """Get blocked number add schema."""
        return vol.Schema(
            {
                vol.Required("number"): cv.string,
                vol.Required("name"): vol.All(cv.string, vol.Length(min=1)),
            }
        )

    async def async_step_blocked_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Remove a blocked number."""
        current_entries = self.coordinator.data.blocked_numbers or []

        if not current_entries:
            return self.async_abort(reason="no_blocked_numbers")

        if user_input is not None:
            try:
                number = user_input["number"]
                await self.coordinator.api_client.remove_blocked_number(number)
                await self.coordinator.async_request_refresh()

                return self.async_create_entry(title="", data={})

            except Exception as err:
                return self.async_show_form(
                    step_id="blocked_remove",
                    data_schema=self._get_blocked_remove_schema(),
                    errors={"base": "remove_blocked_failed"},
                    description_placeholders={"error": str(err)},
                )

        return self.async_show_form(
            step_id="blocked_remove",
            data_schema=self._get_blocked_remove_schema(),
        )

    def _get_blocked_remove_schema(self) -> vol.Schema:
        """Get blocked number remove schema."""
        current_entries = self.coordinator.data.blocked_numbers or []
        numbers = [
            f"{entry.number} ({entry.name})" if entry.name else entry.number
            for entry in current_entries
        ]

        return vol.Schema(
            {
                vol.Required("number"): vol.In(numbers),
            }
        )

    async def async_step_webhook_manager(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle webhook management."""
        if user_input is not None:
            action = user_input.get("action")

            if action == "add":
                return await self.async_step_webhook_add()
            elif action == "remove":
                return await self.async_step_webhook_remove()
            elif action == "test":
                return await self.async_step_webhook_test()
            elif action == "clear_all":
                return await self.async_step_webhook_clear()

        return self.async_show_form(
            step_id="webhook_manager",
            data_schema=vol.Schema(
                {vol.Required("action"): vol.In(["add", "remove", "test", "clear_all"])}
            ),
        )

    async def async_step_webhook_add(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a webhook."""
        if user_input is not None:
            try:
                url = user_input["url"]
                events = user_input["events"]
                name = user_input.get("name")

                await self.coordinator.api_client.add_webhook(url, events, name)
                await self.coordinator.async_request_refresh()

                return self.async_create_entry(title="", data={})

            except Exception as err:
                return self.async_show_form(
                    step_id="webhook_add",
                    data_schema=self._get_webhook_add_schema(),
                    errors={"base": "add_webhook_failed"},
                    description_placeholders={"error": str(err)},
                )

        return self.async_show_form(
            step_id="webhook_add",
            data_schema=self._get_webhook_add_schema(),
        )

    def _get_webhook_add_schema(self) -> vol.Schema:
        """Get webhook add schema."""
        return vol.Schema(
            {
                vol.Required("url"): cv.url,
                vol.Required("events"): cv.multi_select(
                    [
                        "incoming_call",
                        "call_answered",
                        "call_ended",
                        "missed_call",
                        "device_state_change",
                        "config_change",
                        "diagnostic",
                        "error",
                        "system_event",
                    ]
                ),
                vol.Optional("name"): cv.string,
            }
        )

    async def async_step_notification_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle notification settings."""
        if user_input is not None:
            # Store notification preferences in options
            return self.async_create_entry(
                title="",
                data={
                    "notifications": {
                        "missed_calls_enabled": user_input["missed_calls_enabled"],
                        "maintenance_mode_enabled": user_input[
                            "maintenance_mode_enabled"
                        ],
                        "device_offline_enabled": user_input["device_offline_enabled"],
                        "device_reboot_enabled": user_input["device_reboot_enabled"],
                        "offline_threshold_minutes": user_input[
                            "offline_threshold_minutes"
                        ],
                    }
                },
            )

        return self.async_show_form(
            step_id="notification_settings",
            data_schema=self._get_notification_settings_schema(),
        )

    def _get_notification_settings_schema(self) -> vol.Schema:
        """Get notification settings schema."""
        current_options = self.config_entry.options.get("notifications", {})

        return vol.Schema(
            {
                vol.Required(
                    "missed_calls_enabled",
                    default=current_options.get("missed_calls_enabled", True),
                ): cv.boolean,
                vol.Required(
                    "maintenance_mode_enabled",
                    default=current_options.get("maintenance_mode_enabled", True),
                ): cv.boolean,
                vol.Required(
                    "device_offline_enabled",
                    default=current_options.get("device_offline_enabled", True),
                ): cv.boolean,
                vol.Required(
                    "device_reboot_enabled",
                    default=current_options.get("device_reboot_enabled", True),
                ): cv.boolean,
                vol.Required(
                    "offline_threshold_minutes",
                    default=current_options.get("offline_threshold_minutes", 10),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
            }
        )

    async def async_step_advanced_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle advanced settings."""
        if user_input is not None:
            # Store advanced settings in options
            return self.async_create_entry(
                title="",
                data={
                    "advanced": {
                        "polling_fallback_seconds": user_input[
                            "polling_fallback_seconds"
                        ],
                        "refetch_interval_minutes": user_input[
                            "refetch_interval_minutes"
                        ],
                        "websocket_reconnect_delay": user_input[
                            "websocket_reconnect_delay"
                        ],
                        "call_history_retention_days": user_input[
                            "call_history_retention_days"
                        ],
                        "debug_logging": user_input["debug_logging"],
                    }
                },
            )

        return self.async_show_form(
            step_id="advanced_settings",
            data_schema=self._get_advanced_settings_schema(),
        )

    def _get_advanced_settings_schema(self) -> vol.Schema:
        """Get advanced settings schema."""
        current_options = self.config_entry.options.get("advanced", {})

        return vol.Schema(
            {
                vol.Required(
                    "polling_fallback_seconds",
                    default=current_options.get("polling_fallback_seconds", 60),
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=300)),
                vol.Required(
                    "refetch_interval_minutes",
                    default=current_options.get("refetch_interval_minutes", 30),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
                vol.Required(
                    "websocket_reconnect_delay",
                    default=current_options.get("websocket_reconnect_delay", 5),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
                vol.Required(
                    "call_history_retention_days",
                    default=current_options.get("call_history_retention_days", 30),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
                vol.Required(
                    "debug_logging", default=current_options.get("debug_logging", False)
                ): cv.boolean,
            }
        )
