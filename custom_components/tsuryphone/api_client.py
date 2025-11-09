"""API client for TsuryPhone device communication."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_CONFIG_TSURYPHONE,
    API_REFETCH_ALL,
    API_DIAGNOSTICS,
    API_CALL_DIAL,
    API_CALL_ANSWER,
    API_CALL_HANGUP,
    API_CALL_SWITCH_CALL_WAITING,
    API_CALL_TOGGLE_VOLUME_MODE,
    API_CALL_TOGGLE_MUTE,
    API_SYSTEM_RESET,
    API_SYSTEM_FACTORY_RESET,
    API_SYSTEM_RING,
    API_CONFIG_DND,
    API_CONFIG_MAINTENANCE,
    API_CONFIG_AUDIO,
    API_CONFIG_RING_PATTERN,
    API_CONFIG_DIALING,
    API_CALL_DIAL_QUICK_DIAL,
    API_CONFIG_QUICK_DIAL_ADD,
    API_CONFIG_QUICK_DIAL_REMOVE,
    API_CONFIG_EDIT_CONTACT,
    API_CONFIG_WEBHOOK_ADD,
    API_CONFIG_WEBHOOK_REMOVE,
    API_CONFIG_BLOCKED_ADD,
    API_CONFIG_BLOCKED_REMOVE,
    API_CONFIG_HA_URL,
    API_CONFIG_PRIORITY_ADD,
    API_CONFIG_PRIORITY_REMOVE,
    ERROR_CODE_INVALID_NUMBER,
    ERROR_CODE_PHONE_NOT_READY,
    ERROR_CODE_NO_INCOMING_CALL,
    ERROR_CODE_NO_ACTIVE_CALL,
    INTEGRATION_EVENT_SCHEMA_VERSION,
    API_CALL_DIAL_DIGIT,
    API_CALL_SEND_DTMF,
    API_CALL_DELETE_LAST_DIGIT,
    API_CALL_SEND_DIALED_NUMBER,
    ERROR_CODE_MISSING_DIGIT,
    ERROR_CODE_INVALID_DIGIT,
    ERROR_CODE_MISSING_DEFAULT_CODE,
    ERROR_CODE_INVALID_DEFAULT_CODE,
)

_LOGGER = logging.getLogger(__name__)


class TsuryPhoneAPIError(Exception):
    """Exception for API errors."""

    def __init__(self, message: str, error_code: str | None = None) -> None:
        """Initialize API error."""
        super().__init__(message)
        self.error_code = error_code


class TsuryPhoneAPIClient:
    """Client for TsuryPhone device API."""

    def __init__(self, hass: HomeAssistant, host: str, port: int = 8080) -> None:
        """Initialize API client."""
        self._hass = hass
        self._host = host
        self._port = port
        self._base_url = f"http://{host}:{port}"
        self._session = async_get_clientsession(hass)
        self._request_timeout = 10.0

    @property
    def base_url(self) -> str:
        """Get base URL for the device."""
        return self._base_url

    @property
    def websocket_url(self) -> str:
        """Get WebSocket URL for the device."""
        return f"ws://{self._host}:{self._port}/ws"

    async def _request(
        self, method: str, endpoint: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make HTTP request to device."""
        url = f"{self._base_url}{endpoint}"

        try:
            async with asyncio.timeout(self._request_timeout):
                if method.upper() == "GET":
                    async with self._session.get(url) as response:
                        return await self._handle_response(response, endpoint)
                elif method.upper() == "POST":
                    headers = {"Content-Type": "application/json"}
                    async with self._session.post(
                        url, json=data or {}, headers=headers
                    ) as response:
                        return await self._handle_response(response, endpoint)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

        except asyncio.TimeoutError as err:
            _LOGGER.error("Timeout connecting to device at %s", url)
            raise TsuryPhoneAPIError("Connection timeout") from err
        except aiohttp.ClientError as err:
            _LOGGER.error("Client error connecting to device: %s", err)
            raise TsuryPhoneAPIError(f"Connection error: {err}") from err

    async def _handle_response(
        self, response: aiohttp.ClientResponse, endpoint: str
    ) -> dict[str, Any]:
        """Handle HTTP response from device."""
        try:
            response_data = await response.json()
        except json.JSONDecodeError as err:
            _LOGGER.error("Invalid JSON response from %s: %s", endpoint, err)
            raise TsuryPhoneAPIError("Invalid JSON response") from err

        # Check HTTP status
        if response.status != 200:
            error_msg = f"HTTP {response.status}"
            # Firmware sends "message" field, not "errorMessage"
            if response_data and "message" in response_data:
                error_msg = response_data["message"]
            raise TsuryPhoneAPIError(error_msg, response_data.get("errorCode"))

        # Check device success response
        if not response_data.get("success", True):
            # Firmware sends "message" field, not "errorMessage"
            error_msg = response_data.get("message", "Unknown device error")
            error_code = response_data.get("errorCode")
            _LOGGER.error(
                "Device API error on %s: %s (code: %s)", endpoint, error_msg, error_code
            )
            raise TsuryPhoneAPIError(error_msg, error_code)

        # Validate schema version if present
        schema_version = response_data.get("schemaVersion")
        if (
            schema_version is not None
            and schema_version != INTEGRATION_EVENT_SCHEMA_VERSION
        ):
            _LOGGER.warning(
                "Schema version mismatch on %s: expected %d, got %d",
                endpoint,
                INTEGRATION_EVENT_SCHEMA_VERSION,
                schema_version,
            )

        return response_data

    # Configuration endpoints
    async def get_tsuryphone_config(self) -> dict[str, Any]:
        """Get full device configuration."""
        return await self._request("GET", API_CONFIG_TSURYPHONE)

    async def refetch_all(self) -> dict[str, Any]:
        """Trigger device to refetch all data."""
        return await self._request("GET", API_REFETCH_ALL)

    async def get_diagnostics(self) -> dict[str, Any]:
        """Get device diagnostics."""
        return await self._request("GET", API_DIAGNOSTICS)

    # Call control endpoints
    async def dial(self, number: str) -> dict[str, Any]:
        """Dial a phone number."""
        if not number:
            raise TsuryPhoneAPIError(
                "Number cannot be empty", ERROR_CODE_INVALID_NUMBER
            )
        return await self._request("POST", API_CALL_DIAL, {"number": number})

    async def dial_digit(
        self, digit: str | int, *, defer_validation: bool = False
    ) -> dict[str, Any]:
        """Send a single dial digit to the device (0-9 or + for international)."""

        # Handle '+' for international dialing
        if digit == "+":
            data = {"digit": "+"}
        else:
            # Convert to int for validation
            if isinstance(digit, bool):
                raise TsuryPhoneAPIError(
                    "Digit must be between 0 and 9, or '+'", ERROR_CODE_INVALID_DIGIT
                )
            
            try:
                digit_int = int(digit)
            except (ValueError, TypeError):
                raise TsuryPhoneAPIError(
                    "Digit must be between 0 and 9, or '+'", ERROR_CODE_INVALID_DIGIT
                )

            if digit_int < 0 or digit_int > 9:
                raise TsuryPhoneAPIError(
                    "Digit must be between 0 and 9, or '+'", ERROR_CODE_INVALID_DIGIT
                )
            
            data = {"digit": digit_int}

        if defer_validation:
            data["deferValidation"] = True

        return await self._request("POST", API_CALL_DIAL_DIGIT, data)

    async def delete_last_digit(self) -> dict[str, Any]:
        """Delete the last digit from the pending dial buffer."""
        return await self._request("POST", API_CALL_DELETE_LAST_DIGIT)

    async def send_dtmf(self, digit: str) -> dict[str, Any]:
        """Send DTMF digit during active call."""
        if not digit or len(digit) != 1 or digit not in "0123456789*#":
            raise TsuryPhoneAPIError(
                "DTMF digit must be one of: 0-9, *, #", ERROR_CODE_INVALID_DIGIT
            )

        return await self._request("POST", API_CALL_SEND_DTMF, {"digit": digit})

    async def send_dialed_number(self) -> dict[str, Any]:
        """Send/validate the currently dialed number."""
        return await self._request("POST", API_CALL_SEND_DIALED_NUMBER)

    async def answer_call(self) -> dict[str, Any]:
        """Answer incoming call."""
        return await self._request("POST", API_CALL_ANSWER)

    async def hangup_call(self) -> dict[str, Any]:
        """Hangup active call."""
        return await self._request("POST", API_CALL_HANGUP)

    async def switch_call_waiting(self) -> dict[str, Any]:
        """Toggle call waiting."""
        return await self._request("POST", API_CALL_SWITCH_CALL_WAITING)

    async def toggle_volume_mode(self) -> dict[str, Any]:
        """Toggle between speaker and earpiece modes during a call."""
        return await self._request("POST", API_CALL_TOGGLE_VOLUME_MODE)

    async def toggle_mute(self) -> dict[str, Any]:
        """Toggle mute status during a call."""
        return await self._request("POST", API_CALL_TOGGLE_MUTE)

    # System endpoints
    async def reset_device(self) -> dict[str, Any]:
        """Reset the device."""
        return await self._request("POST", API_SYSTEM_RESET)

    async def factory_reset_device(self) -> dict[str, Any]:
        """Factory reset the device."""
        return await self._request("POST", API_SYSTEM_FACTORY_RESET)

    async def ring_device(
        self, pattern: str | None = None, *, force: bool | None = None
    ) -> dict[str, Any]:
        """Ring the device with optional pattern and DND override."""

        data: dict[str, Any] = {}
        if pattern:
            data["pattern"] = pattern
        if force is not None:
            data["force"] = force
        return await self._request("POST", API_SYSTEM_RING, data)

    # Configuration endpoints
    async def set_dnd(self, config: dict[str, Any]) -> dict[str, Any]:
        """Set Do Not Disturb configuration."""
        return await self._request("POST", API_CONFIG_DND, config)

    async def set_maintenance_mode(self, enabled: bool) -> dict[str, Any]:
        """Set maintenance mode."""
        return await self._request("POST", API_CONFIG_MAINTENANCE, {"enabled": enabled})

    async def set_audio_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Set audio configuration (partial updates supported)."""
        return await self._request("POST", API_CONFIG_AUDIO, config)

    async def set_ring_pattern(self, pattern: str) -> dict[str, Any]:
        """Set ring pattern."""
        return await self._request(
            "POST", API_CONFIG_RING_PATTERN, {"pattern": pattern}
        )

    async def set_dialing_config(self, default_code: str) -> dict[str, Any]:
        """Set the default dialing code for the device."""
        if not default_code:
            raise TsuryPhoneAPIError(
                "default_code is required", ERROR_CODE_MISSING_DEFAULT_CODE
            )

        if not default_code.isdigit():
            raise TsuryPhoneAPIError(
                "default_code must contain digits only",
                ERROR_CODE_INVALID_DEFAULT_CODE,
            )

        return await self._request(
            "POST", API_CONFIG_DIALING, {"defaultCode": default_code}
        )

    # Quick dial management
    async def add_quick_dial(
        self, number: str, name: str = "", code: str = ""
    ) -> dict[str, Any]:
        """Add quick dial entry."""
        # Validate code is numeric only if provided (rotary phone dial codes)
        if code and not code.isdigit():
            raise TsuryPhoneAPIError("Quick dial code must contain only digits (0-9)")
        
        data = {"number": number}
        # Code is optional
        if code:
            data["code"] = code
        if name:
            data["name"] = name
        _LOGGER.debug(
            "add_quick_dial API call: number='%s' | name='%s' | code='%s' | data=%s",
            number,
            name,
            code,
            data,
        )
        return await self._request("POST", API_CONFIG_QUICK_DIAL_ADD, data)

    async def remove_quick_dial_by_id(self, entry_id: str) -> dict[str, Any]:
        """Remove quick dial entry by ID."""
        return await self._request(
            "POST", API_CONFIG_QUICK_DIAL_REMOVE, {"id": entry_id}
        )

    async def edit_contact(
        self, entry_id: str, name: str, number: str, code: str, is_priority: bool
    ) -> dict[str, Any]:
        """Edit an existing contact."""
        data = {
            "id": entry_id,
            "name": name,
            "number": number,
            "code": code,
            "isPriority": is_priority,
        }
        _LOGGER.debug(
            "edit_contact API call: id='%s' | name='%s' | number='%s' | code='%s' | priority=%s",
            entry_id,
            name,
            number,
            code,
            is_priority,
        )
        return await self._request("POST", API_CONFIG_EDIT_CONTACT, data)

    async def dial_quick_dial(self, code: str) -> dict[str, Any]:
        """Dial quick dial code."""
        return await self._request("POST", API_CALL_DIAL_QUICK_DIAL, {"code": code})

    # Blocked number management
    async def add_blocked_number(self, number: str, name: str = "") -> dict[str, Any]:
        """Add blocked number."""
        if not number:
            raise TsuryPhoneAPIError(
                "Number cannot be empty", ERROR_CODE_INVALID_NUMBER
            )
        data = {"number": number}
        if name:
            data["name"] = name

        return await self._request("POST", API_CONFIG_BLOCKED_ADD, data)

    async def remove_blocked_number_by_id(self, entry_id: str) -> dict[str, Any]:
        """Remove blocked number by ID."""
        if not entry_id:
            raise TsuryPhoneAPIError("ID cannot be empty", ERROR_CODE_INVALID_NUMBER)
        return await self._request("POST", API_CONFIG_BLOCKED_REMOVE, {"id": entry_id})

    # Priority caller management
    async def add_priority_caller(self, number: str) -> dict[str, Any]:
        """Add a priority caller number."""
        if not number:
            raise TsuryPhoneAPIError(
                "Number cannot be empty", ERROR_CODE_INVALID_NUMBER
            )
        return await self._request("POST", API_CONFIG_PRIORITY_ADD, {"number": number})

    async def remove_priority_caller_by_id(self, entry_id: str) -> dict[str, Any]:
        """Remove a priority caller by ID."""
        if not entry_id:
            raise TsuryPhoneAPIError("ID cannot be empty", ERROR_CODE_INVALID_NUMBER)
        return await self._request("POST", API_CONFIG_PRIORITY_REMOVE, {"id": entry_id})

    # Webhook management
    async def add_webhook_action(
        self, code: str, webhook_id: str, action_name: str = ""
    ) -> dict[str, Any]:
        """Add webhook action."""
        # Validate code is numeric only (rotary phone dial codes)
        if not code or not code.isdigit():
            raise TsuryPhoneAPIError("Webhook code must contain only digits (0-9)")
        
        data = {"code": code, "id": webhook_id}
        if action_name:
            data["actionName"] = action_name
        return await self._request("POST", API_CONFIG_WEBHOOK_ADD, data)

    async def remove_webhook_action(self, code: str) -> dict[str, Any]:
        """Remove webhook action."""
        # Validate code is numeric only
        if not code or not code.isdigit():
            raise TsuryPhoneAPIError("Webhook code must contain only digits (0-9)")
        
        return await self._request("POST", API_CONFIG_WEBHOOK_REMOVE, {"code": code})

    async def set_ha_url(self, url: str) -> dict[str, Any]:
        """Set Home Assistant URL."""
        return await self._request("POST", API_CONFIG_HA_URL, {"url": url})

    # Helper methods
    def is_api_error_code(self, error: Exception, expected_code: str) -> bool:
        """Check if exception is API error with specific code."""
        return (
            isinstance(error, TsuryPhoneAPIError) and error.error_code == expected_code
        )

    async def test_connection(self) -> bool:
        """Test if device is reachable."""
        try:
            await self.get_tsuryphone_config()
            return True
        except Exception as err:
            _LOGGER.debug("Connection test failed: %s", err)
            return False
