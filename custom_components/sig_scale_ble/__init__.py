"""SIG Weight Scale BLE integration for Home Assistant."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    async_register_callback,
    BluetoothCallbackMatcher,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback

from .const import (
    DOMAIN,
    UCP_USER_INDEX_UNKNOWN,
    UCP_DEFAULT_CONSENT_CODE,
)
from .config_flow import (
    _apply_options_to_coordinator,
)
from .coordinator import ScaleCoordinator

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    address: str = entry.data["address"]
    coordinator = ScaleCoordinator(hass, address, entry.title)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Apply saved options (UDS + gating) immediately at startup
    if entry.options:
        _apply_options_to_coordinator(coordinator, entry.options)

    await coordinator.async_config_entry_first_refresh()

    @callback
    def _ble_callback(service_info: BluetoothServiceInfoBleak, change) -> None:
        coordinator.handle_advertisement(service_info)

    entry.async_on_unload(
        async_register_callback(
            hass,
            _ble_callback,
            BluetoothCallbackMatcher(address=address),
            BluetoothScanningMode.ACTIVE,
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
