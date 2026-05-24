"""Config flow for SIG Weight Scale BLE.

Options flow exposes both UDS consent settings and advertisement gating tuning:
  UDS:
    • uds_user_index        — user slot (255 = auto)
    • uds_consent_code      — 16-bit PIN
    • uds_auto_register     — create slot if missing
  Gating:
    • cooldown_minutes      — post-read lockout (default 30 min)
    • require_payload_change — only connect when adv payload changes
    • require_connectable   — only connect when device is connectable
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
    async_scanner_devices_by_address,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    WEIGHT_SCALE_SERVICE_UUID,
    BODY_COMPOSITION_SERVICE_UUID,
    UCP_USER_INDEX_UNKNOWN,
    UCP_DEFAULT_CONSENT_CODE,
)

_LOGGER = logging.getLogger(__name__)

_TRIGGER_UUIDS = {WEIGHT_SCALE_SERVICE_UUID, BODY_COMPOSITION_SERVICE_UUID}

# Options keys
CONF_UDS_USER_INDEX      = "uds_user_index"
CONF_UDS_CONSENT_CODE    = "uds_consent_code"
CONF_UDS_AUTO_REGISTER   = "uds_auto_register"
CONF_COOLDOWN_MINUTES    = "cooldown_minutes"
CONF_REQUIRE_PAYLOAD_CHANGE = "require_payload_change"
CONF_REQUIRE_CONNECTABLE = "require_connectable"

_DEFAULT_COOLDOWN = 30


class SIGScaleConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered_devices: dict[str, str] = {}
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    # ── Bluetooth auto-discovery ───────────────────────────────────────────────

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": discovery_info.name or discovery_info.address
        }
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery_info is not None
        info = self._discovery_info
        if user_input is not None:
            return self.async_create_entry(
                title=info.name or info.address,
                data={
                    CONF_ADDRESS: info.address,
                },
            )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": info.name or "Unknown",
                "address": info.address,
            },
        )

    # ── Manual setup ───────────────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        current_addresses = self._async_current_ids()
        for service_info in async_discovered_service_info(self.hass, connectable=True):
            address = service_info.address
            if address in current_addresses:
                continue
            if _TRIGGER_UUIDS & set(service_info.service_uuids or []):
                self._discovered_devices[address] = service_info.name or address

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address.upper(), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            name = self._discovered_devices.get(address, address)

            return self.async_create_entry(
                title=name, 
                data={
                    CONF_ADDRESS: info.address,
                },
            )

        device_options = {
            addr: f"{name} ({addr})"
            for addr, name in self._discovered_devices.items()
        }
        schema = vol.Schema(
            {vol.Required(CONF_ADDRESS): vol.In(device_options)}
            if device_options
            else {vol.Required(CONF_ADDRESS): str}
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            description_placeholders={"count": str(len(device_options))},
        )

    # ── Options flow entry point ───────────────────────────────────────────────

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SIGScaleOptionsFlow()


class SIGScaleOptionsFlow(OptionsFlow):
    """Options flow — UDS consent + advertisement gating settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            if not (0 <= user_input[CONF_UDS_USER_INDEX] <= 255):
                errors[CONF_UDS_USER_INDEX] = "user_index_out_of_range"
            if not (0 <= user_input[CONF_UDS_CONSENT_CODE] <= 65535):
                errors[CONF_UDS_CONSENT_CODE] = "consent_code_out_of_range"
            if not (1 <= user_input[CONF_COOLDOWN_MINUTES] <= 1440):
                errors[CONF_COOLDOWN_MINUTES] = "cooldown_out_of_range"

            if not errors:
                # Apply immediately to live coordinator
                coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
                if coordinator is not None:
                    _apply_options_to_coordinator(coordinator, user_input)
                return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema({
            # ── UDS section ───────────────────────────────────────────────────
            vol.Required(
                CONF_UDS_USER_INDEX,
            ): vol.All(int, vol.Range(min=0, max=255)),
            vol.Required(
                CONF_UDS_CONSENT_CODE,
            ): vol.All(int, vol.Range(min=0, max=65535)),
            vol.Required(
                CONF_UDS_AUTO_REGISTER,
            ): bool,
            # ── Gating section ────────────────────────────────────────────────
            vol.Required(
                CONF_COOLDOWN_MINUTES,
            ): vol.All(int, vol.Range(min=1, max=1440)),
            vol.Required(
                CONF_REQUIRE_PAYLOAD_CHANGE,
            ): bool,
            vol.Required(
                CONF_REQUIRE_CONNECTABLE,
            ): bool,
        })

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, self.config_entry.options),
            errors=errors,
            description_placeholders={
                "user_index_auto": str(UCP_USER_INDEX_UNKNOWN),
                "default_cooldown": str(_DEFAULT_COOLDOWN),
            },
        )


def _apply_options_to_coordinator(coordinator: Any, options: dict) -> None:
    """Push options dict into a live coordinator instance."""
    from datetime import timedelta
    coordinator.uds_user_index        = options.get(CONF_UDS_USER_INDEX, UCP_USER_INDEX_UNKNOWN)
    coordinator.uds_consent_code      = options.get(CONF_UDS_CONSENT_CODE, UCP_DEFAULT_CONSENT_CODE)
    coordinator.uds_auto_register     = options.get(CONF_UDS_AUTO_REGISTER, False)
    coordinator.require_payload_change = options.get(CONF_REQUIRE_PAYLOAD_CHANGE, True)
    coordinator.require_connectable   = options.get(CONF_REQUIRE_CONNECTABLE, True)
    coordinator.COOLDOWN_AFTER_MEASUREMENT = timedelta(
        minutes=options.get(CONF_COOLDOWN_MINUTES, _DEFAULT_COOLDOWN)
    )
