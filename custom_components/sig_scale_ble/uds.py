"""User Data Service (0x181C) consent flow for SIG Weight Scale BLE.

WHY THIS EXISTS
───────────────
Scales that support multiple user profiles implement the SIG User Data Service
(UDS, 0x181C).  Before the scale will stream weight or body composition records
for a specific user it requires a *consent* handshake:

  1. Read 0x2A9A (User Index) to discover the currently active user slot.
     Value 0xFF means no user is currently consented / active.

  2. Write a User Control Point (UCP, 0x2A9F) Consent command:
       [0x02, user_index, consent_lo, consent_hi]
     The consent code is a 16-bit PIN set on the device (default is usually 0).

  3. Wait for a UCP indication (0x2A9F) back:
       [0x20, 0x02, response_code]
     response_code 0x01 = Success → scale will now stream records for that user.
     response_code 0x05 = User Not Authorized → wrong PIN or user does not exist.

  4. Optionally, if the user slot does not exist yet, call Register New User:
       [0x01, consent_lo, consent_hi]
     Indication back: [0x20, 0x01, 0x01, new_user_index]
     Then proceed with Consent using the returned index.

HANDLE RESOLUTION
─────────────────
Same service-scoped pattern as glucose/scale: UCP and User Index are looked up
inside the User Data Service (0x181C) to avoid UUID collisions.

INTEGRATION WITH COORDINATOR
──────────────────────────────
The coordinator calls:
    uds = UserDataService(client, handles, address)
    await uds.consent(user_index, consent_code)

If the device has no UDS service, the coordinator skips this step entirely.
The consent_code and user_index are stored in the config entry options and
exposed via the options flow so users can configure them from the HA UI.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass
from typing import Optional

from bleak import BleakClient, BleakError

from .const import (
    USER_DATA_SERVICE_UUID,
    USER_INDEX_UUID,
    USER_CONTROL_POINT_UUID,
    UCP_OP_REGISTER_NEW_USER,
    UCP_OP_CONSENT,
    UCP_OP_RESPONSE,
    UCP_RESPONSE_SUCCESS,
    UCP_RESPONSE_USER_NOT_AUTHORIZED,
    UCP_USER_INDEX_UNKNOWN,
    UCP_WRITE_TIMEOUT,
    UCP_RESPONSE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class UDSHandles:
    """Resolved GATT handles for User Data Service characteristics."""
    user_index: Optional[int] = None       # 0x2A9A — read
    user_control_point: Optional[int] = None  # 0x2A9F — write + indicate

    @property
    def is_present(self) -> bool:
        return self.user_control_point is not None


class ConsentError(Exception):
    """Raised when the scale refuses consent (wrong PIN / unknown user)."""


class UCPError(Exception):
    """Raised when the UCP response indicates a non-consent failure."""


def resolve_uds_handles(client: BleakClient, address: str) -> UDSHandles:
    """Walk the GATT service tree and return UDS handles.

    First pass: inside User Data Service (0x181C).
    Fallback: global scan for devices with non-standard structure.
    """
    def _n(u: str) -> str:
        return str(u).lower()

    uds_svc_uuid  = _n(USER_DATA_SERVICE_UUID)
    ui_uuid       = _n(USER_INDEX_UUID)
    ucp_uuid      = _n(USER_CONTROL_POINT_UUID)

    handles = UDSHandles()

    # First pass — inside UDS service only
    for svc in client.services:
        if _n(svc.uuid) != uds_svc_uuid:
            continue
        _LOGGER.debug("[%s] Found User Data Service (handle 0x%04X)", address, svc.handle)
        for char in svc.characteristics:
            u = _n(char.uuid)
            if u == ui_uuid:
                handles.user_index = char.handle
                _LOGGER.debug("[%s]  user_index handle=0x%04X", address, char.handle)
            elif u == ucp_uuid:
                handles.user_control_point = char.handle
                _LOGGER.debug("[%s]  ucp        handle=0x%04X", address, char.handle)

    # Fallback — any service
    if not handles.is_present:
        for svc in client.services:
            for char in svc.characteristics:
                u = _n(char.uuid)
                if u == ui_uuid and handles.user_index is None:
                    handles.user_index = char.handle
                elif u == ucp_uuid and handles.user_control_point is None:
                    handles.user_control_point = char.handle
                    _LOGGER.debug(
                        "[%s] fallback ucp handle=0x%04X in svc=%s",
                        address, char.handle, svc.uuid,
                    )

    return handles


async def read_current_user_index(client: BleakClient, handles: UDSHandles) -> int:
    """Read the currently active user index from 0x2A9A.

    Returns UCP_USER_INDEX_UNKNOWN (0xFF) if no user is consented or the
    characteristic is not present.
    """
    if handles.user_index is None:
        return UCP_USER_INDEX_UNKNOWN
    try:
        data = await client.read_gatt_char(handles.user_index)
        return data[0] if data else UCP_USER_INDEX_UNKNOWN
    except BleakError as exc:
        _LOGGER.warning("Could not read User Index: %s", exc)
        return UCP_USER_INDEX_UNKNOWN


async def perform_consent(
    client: BleakClient,
    handles: UDSHandles,
    user_index: int,
    consent_code: int,
    address: str,
) -> int:
    """Send Consent command and wait for UCP indication.

    Returns the consented user index on success.
    Raises ConsentError if the device refuses (wrong PIN / user not found).
    Raises UCPError for other UCP failures.

    UCP Consent command layout:
      [0x02, user_index (uint8), consent_code_lo (uint8), consent_code_hi (uint8)]

    UCP Response indication layout:
      [0x20, 0x02, response_code (uint8)]
    """
    ucp_handle = handles.user_control_point
    if ucp_handle is None:
        raise UCPError("UCP characteristic handle not resolved")

    response_event: asyncio.Event = asyncio.Event()
    response_data: list[bytes] = []

    def _ucp_handler(sender, data: bytearray) -> None:
        _LOGGER.debug(
            "[%s] UCP indication handle=%s data=%s", address, sender, data.hex()
        )
        raw = bytes(data)
        if len(raw) >= 1 and raw[0] == UCP_OP_RESPONSE:
            response_data.append(raw)
            response_event.set()

    # Subscribe to UCP indications before writing the command
    await client.start_notify(ucp_handle, _ucp_handler)
    try:
        # Build the Consent command: [0x02, user_index, code_lo, code_hi]
        command = struct.pack("<BBH", UCP_OP_CONSENT, user_index, consent_code)
        _LOGGER.info(
            "[%s] Sending UCP Consent: user_index=%d consent_code=0x%04X",
            address, user_index, consent_code,
        )
        await asyncio.wait_for(
            client.write_gatt_char(ucp_handle, command, response=True),
            timeout=UCP_WRITE_TIMEOUT,
        )

        # Wait for the indication
        try:
            await asyncio.wait_for(response_event.wait(), timeout=UCP_RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            raise UCPError(
                f"[{address}] UCP Consent response timed out after {UCP_RESPONSE_TIMEOUT}s"
            )

        raw = response_data[0]
        # raw[0] = 0x20 (response op), raw[1] = req_op, raw[2] = response_code
        if len(raw) < 3:
            raise UCPError(f"[{address}] UCP response too short: {raw.hex()}")

        response_code = raw[2]
        if response_code == UCP_RESPONSE_SUCCESS:
            _LOGGER.info(
                "[%s] UCP Consent granted for user index %d", address, user_index
            )
            return user_index
        elif response_code == UCP_RESPONSE_USER_NOT_AUTHORIZED:
            raise ConsentError(
                f"[{address}] Consent refused for user index {user_index} — "
                f"wrong consent code (0x{consent_code:04X}) or user does not exist. "
                f"Check the consent code in the integration options."
            )
        else:
            raise UCPError(
                f"[{address}] UCP Consent failed with response code 0x{response_code:02X}"
            )
    finally:
        try:
            await client.stop_notify(ucp_handle)
        except BleakError:
            pass


async def register_new_user(
    client: BleakClient,
    handles: UDSHandles,
    consent_code: int,
    address: str,
) -> int:
    """Register a new user slot and return the assigned user index.

    UCP Register New User command: [0x01, consent_code_lo, consent_code_hi]
    UCP Response: [0x20, 0x01, 0x01, new_user_index]

    Only call this when the user slot does not yet exist on the device.
    """
    ucp_handle = handles.user_control_point
    if ucp_handle is None:
        raise UCPError("UCP characteristic handle not resolved")

    response_event: asyncio.Event = asyncio.Event()
    response_data: list[bytes] = []

    def _ucp_handler(sender, data: bytearray) -> None:
        raw = bytes(data)
        if len(raw) >= 1 and raw[0] == UCP_OP_RESPONSE:
            response_data.append(raw)
            response_event.set()

    await client.start_notify(ucp_handle, _ucp_handler)
    try:
        command = struct.pack("<BH", UCP_OP_REGISTER_NEW_USER, consent_code)
        _LOGGER.info(
            "[%s] Registering new user with consent_code=0x%04X", address, consent_code
        )
        await asyncio.wait_for(
            client.write_gatt_char(ucp_handle, command, response=True),
            timeout=UCP_WRITE_TIMEOUT,
        )
        try:
            await asyncio.wait_for(response_event.wait(), timeout=UCP_RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            raise UCPError(f"[{address}] Register New User timed out")

        raw = response_data[0]
        if len(raw) < 4:
            raise UCPError(f"[{address}] Register New User response too short: {raw.hex()}")

        response_code = raw[2]
        if response_code != UCP_RESPONSE_SUCCESS:
            raise UCPError(
                f"[{address}] Register New User failed: code 0x{response_code:02X}"
            )

        new_user_index = raw[3]
        _LOGGER.info(
            "[%s] New user registered: index=%d", address, new_user_index
        )
        return new_user_index
    finally:
        try:
            await client.stop_notify(ucp_handle)
        except BleakError:
            pass


async def ensure_consent(
    client: BleakClient,
    handles: UDSHandles,
    user_index: int,
    consent_code: int,
    address: str,
    auto_register: bool = False,
) -> int:
    """High-level consent orchestrator called by the coordinator.

    1. If user_index is UCP_USER_INDEX_UNKNOWN (0xFF) and auto_register=True,
       register a new user slot first.
    2. Run the Consent command/response exchange.
    3. On ConsentError with auto_register=True, try registering a new user.

    Returns the consented user_index.
    Raises ConsentError if consent is ultimately refused.
    """
    if not handles.is_present:
        raise UCPError("Device has no User Data Service — consent not required")

    # If no explicit user index, try to read the current one from the device
    if user_index == UCP_USER_INDEX_UNKNOWN:
        current = await read_current_user_index(client, handles)
        if current != UCP_USER_INDEX_UNKNOWN:
            _LOGGER.debug(
                "[%s] Device reports active user index=%d; using it", address, current
            )
            user_index = current

    # If still unknown and auto_register is enabled, create a new slot
    if user_index == UCP_USER_INDEX_UNKNOWN:
        if auto_register:
            _LOGGER.info(
                "[%s] No user index set — registering new user slot", address
            )
            user_index = await register_new_user(client, handles, consent_code, address)
        else:
            raise ConsentError(
                f"[{address}] User index is 0xFF (unknown) and auto_register is disabled. "
                "Set a user_index in the integration options, or enable auto_register."
            )

    try:
        return await perform_consent(client, handles, user_index, consent_code, address)
    except ConsentError:
        if auto_register:
            _LOGGER.info(
                "[%s] Consent refused for user index %d — registering new slot",
                address, user_index,
            )
            user_index = await register_new_user(client, handles, consent_code, address)
            return await perform_consent(client, handles, user_index, consent_code, address)
        raise
