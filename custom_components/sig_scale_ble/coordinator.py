"""Coordinator for SIG Weight Scale BLE integration.

Connection strategy
───────────────────
establish_connection() from bleak-retry-connector handles proxy selection,
retries, backoff, and ESP32 error codes.  We no longer manage retries manually,
and we no longer attempt to pin a specific proxy — habluetooth always routes by
RSSI score regardless of which BLEDevice we pass, so all proxy-pinning code
was removed as ineffective.

Two services handled in parallel:
  Weight Scale Service (0x181D)     -> 0x2A9D Weight Measurement (indicate)
  Body Composition Service (0x181B) -> 0x2A9C Body Composition Measurement (indicate)

User Data Service (0x181C) consent
------------------------------------
Scales with multi-user support require a UDS consent handshake before streaming.
Detected automatically; devices without UDS skip this step.

Battery drain prevention
------------------------
Scales advertise continuously. Three guards in handle_advertisement() prevent
connecting on every beacon:
  1. Post-read cooldown  (default 30 min after a successful reading)
  2. Post-failure cooldown (2 min after a failed session)
  3. Advertisement payload change detection (MD5 of manufacturer+service data)
  4. Connectable flag gate (optional, enabled by default)

Drain-all / Err-6 avoidance
-----------------------------
Keep session open until both streams go quiet or device disconnects.
Race condition fix: done_event and first_indication_event checked before
any wait, so simultaneous weight+BCM on first step is handled correctly.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any

from bleak import BleakError
from bleak_retry_connector import establish_connection, BleakClientWithServiceCache

from homeassistant.components.bluetooth import async_clear_advertisement_history
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    WEIGHT_SCALE_SERVICE_UUID,
    BODY_COMPOSITION_SERVICE_UUID,
    WEIGHT_MEASUREMENT_UUID,
    BODY_COMPOSITION_MEASUREMENT_UUID,
    UCP_USER_INDEX_UNKNOWN,
    UCP_DEFAULT_CONSENT_CODE,
    FIRST_INDICATION_TIMEOUT,
    IDLE_AFTER_LAST_RECORD_TIMEOUT,
    PAIR_TIMEOUT,
)
from .parser import (
    ScaleMeasurement,
    WeightMeasurement,
    BodyCompositionMeasurement,
    parse_weight_measurement,
    parse_body_composition_measurement,
)
from .uds import resolve_uds_handles, ensure_consent, ConsentError, UCPError

_LOGGER = logging.getLogger(__name__)
_POLL_INTERVAL = timedelta(hours=24)

COOLDOWN_AFTER_MEASUREMENT = timedelta(minutes=30)
COOLDOWN_AFTER_FAILURE     = timedelta(minutes=2)


def _is_auth_error(exc: BleakError) -> bool:
    msg = str(exc).lower()
    return (
        "insufficient authentication" in msg
        or "insufficient encryption" in msg
        or "error=5" in msg or "error=8" in msg or "error=15" in msg
    )


class ScaleCoordinator(DataUpdateCoordinator[ScaleMeasurement | None]):
    """Coordinate BLE connections and data parsing for a single weight scale."""

    def __init__(self, hass: HomeAssistant, address: str, name: str) -> None:
        super().__init__(
            hass, _LOGGER,
            name=f"{DOMAIN}_{address}",
            update_interval=_POLL_INTERVAL,
        )
        self.address = address
        self.device_name = name
        self._connecting = False
        self._last_measurement: ScaleMeasurement | None = None

        # UDS consent settings (populated from config entry options)
        self.uds_user_index: int     = UCP_USER_INDEX_UNKNOWN
        self.uds_consent_code: int   = UCP_DEFAULT_CONSENT_CODE
        self.uds_auto_register: bool = False

        # Advertisement gating state
        self._last_successful_read: datetime | None = None
        self._last_failed_attempt: datetime | None = None

        # Tunable gating flags (configurable via options flow)
        self.require_payload_change: bool = True
        self.require_connectable: bool    = True

    # ── Public API ────────────────────────────────────────────────────────────

    def handle_advertisement(self, service_info: Any) -> None:
        now = datetime.now()

        # Guard 1: post-read cooldown
        if self._last_successful_read is not None:
            elapsed = now - self._last_successful_read
            if elapsed < COOLDOWN_AFTER_MEASUREMENT:
                remaining = int((COOLDOWN_AFTER_MEASUREMENT - elapsed).total_seconds() // 60)
                _LOGGER.debug("[%s] Post-read cooldown (%dm remaining)", self.address, remaining)
                return

        # Guard 2: post-failure cooldown
        if self._last_failed_attempt is not None:
            elapsed = now - self._last_failed_attempt
            if elapsed < COOLDOWN_AFTER_FAILURE:
                _LOGGER.debug("[%s] Post-failure cooldown", self.address)
                return

        # Guard 3: connectable flag
        if self.require_connectable and not service_info.connectable:
            _LOGGER.debug("[%s] Not connectable – skipping", self.address)
            return

        # Guard 4: payload change detection
        if self.require_payload_change:
            _LOGGER.debug("[%s] Payload %s", self.address, service_info.service_data[WEIGHT_SCALE_SERVICE_UUID])
            if service_info.service_data[WEIGHT_SCALE_SERVICE_UUID] == b'':
                _LOGGER.debug("[%s] Payload indicates no data – skipping", self.address)
                return

        if self._connecting:
            _LOGGER.debug("[%s] Already connecting – skipping", self.address)
            return

        _LOGGER.info("[%s] Advertisement passed guards – connecting", self.address)
        self.hass.async_create_task(self._connect_and_read(service_info))

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _connect_and_read(self, service_info: Any) -> None:
        self._connecting = True
        success = False
        try:
            await self._do_connect_and_read(service_info)
            success = True
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("[%s] Session failed: %s", self.address, err)
        finally:
            self._connecting = False
            if success:
                self._last_successful_read = datetime.now()
                _LOGGER.debug("[%s] Cooldown active for %dm", self.address,
                              int(COOLDOWN_AFTER_MEASUREMENT.total_seconds() // 60))
            else:
                self._last_failed_attempt = datetime.now()
                self._last_adv_fingerprint = None  # allow retry on next adv
            try:
                async_clear_advertisement_history(self.hass, self.address)
            except Exception:  # noqa: BLE001
                pass

    async def _do_connect_and_read(self, service_info: Any) -> None:
        """Connect, consent if needed, subscribe, drain both streams, publish."""
        weight_records: list[WeightMeasurement] = []
        bcm_records:    list[BodyCompositionMeasurement] = []
        first_indication_event: asyncio.Event = asyncio.Event()
        done_event:             asyncio.Event = asyncio.Event()
        idle_handle: list[asyncio.TimerHandle | None] = [None]

        def _reschedule_idle() -> None:
            if idle_handle[0] is not None:
                idle_handle[0].cancel()
            idle_handle[0] = asyncio.get_event_loop().call_later(
                IDLE_AFTER_LAST_RECORD_TIMEOUT, done_event.set
            )

        def _weight_handler(sender: Any, data: bytearray) -> None:
            _LOGGER.debug("[%s] Weight indication handle=%s data=%s",
                          self.address, sender, data.hex())
            _reschedule_idle()
            try:
                m = parse_weight_measurement(bytes(data))
                if m.is_valid:
                    weight_records.append(m)
                    first_indication_event.set()
            except ValueError as exc:
                _LOGGER.warning("[%s] Weight parse error: %s", self.address, exc)

        def _bcm_handler(sender: Any, data: bytearray) -> None:
            _LOGGER.debug("[%s] BCM indication handle=%s data=%s",
                          self.address, sender, data.hex())
            _reschedule_idle()
            try:
                m = parse_body_composition_measurement(bytes(data))
                if m.is_valid:
                    bcm_records.append(m)
                    first_indication_event.set()
            except ValueError as exc:
                _LOGGER.warning("[%s] BCM parse error: %s", self.address, exc)

        def _disconnected_callback(_client: Any) -> None:
            _LOGGER.debug("[%s] Device disconnected – signalling done", self.address)
            if idle_handle[0] is not None:
                idle_handle[0].cancel()
            done_event.set()

        _LOGGER.info("[%s] Connecting via establish_connection …", self.address)
        client = await establish_connection(
            BleakClientWithServiceCache,
            service_info.device,
            self.device_name,
            disconnected_callback=_disconnected_callback,
            max_attempts=3,
        )
        try:
            _LOGGER.info("[%s] Connected – pairing …", self.address)
            await self._ensure_paired(client)

            handles = self._resolve_characteristics(client)
            _LOGGER.debug("[%s] Resolved handles: %s", self.address, handles)

            if handles.get("weight") is None:
                raise BleakError(
                    f"[{self.address}] Weight Measurement (0x2A9D) not found"
                )

            # Subscribe to Weight Measurement (mandatory)
            _LOGGER.info("[%s] Enabling Weight indications …", self.address)
            try:
                await client.start_notify(handles["weight"], _weight_handler)
            except BleakError as exc:
                if _is_auth_error(exc):
                    raise BleakError(
                        f"[{self.address}] Auth error – "
                        f"try: bluetoothctl remove {self.address} then re-pair. {exc}"
                    ) from exc
                raise

            # Subscribe to Body Composition (optional).
            # Non-fatal if device disconnects mid-CCCD-write (race on first step).
            bcm_available = False
            if handles.get("bcm") is not None:
                try:
                    await client.start_notify(handles["bcm"], _bcm_handler)
                    bcm_available = True
                    _LOGGER.info("[%s] BCM indications enabled", self.address)
                except BleakError as exc:
                    if done_event.is_set() or weight_records or bcm_records:
                        _LOGGER.debug(
                            "[%s] start_notify(bcm) failed after data already received "
                            "(device disconnected during CCCD write — expected): %s",
                            self.address, exc,
                        )
                    else:
                        _LOGGER.warning("[%s] BCM subscribe failed: %s", self.address, exc)

            # UDS consent (skip if device has no User Data Service)
            uds_handles = resolve_uds_handles(client, self.address)
            if uds_handles.is_present:
                _LOGGER.info("[%s] UDS detected – running consent flow", self.address)
                try:
                    consented_index = await ensure_consent(
                        client, uds_handles,
                        self.uds_user_index, self.uds_consent_code,
                        self.address, auto_register=self.uds_auto_register,
                    )
                    _LOGGER.info("[%s] Consent granted for user %d",
                                 self.address, consented_index)
                    if self.uds_user_index == UCP_USER_INDEX_UNKNOWN:
                        self.uds_user_index = consented_index
                except ConsentError as exc:
                    _LOGGER.error("[%s] Consent refused – aborting: %s", self.address, exc)
                    return
                except UCPError as exc:
                    _LOGGER.warning("[%s] UCP error (continuing): %s", self.address, exc)

            # Phase 1 — wait for first indication.
            # Fast-path: if done_event or first_indication_event already set,
            # the device sent data (and possibly disconnected) while we were
            # subscribing — skip the wait entirely.
            if not done_event.is_set() and not first_indication_event.is_set():
                try:
                    await asyncio.wait_for(
                        first_indication_event.wait(),
                        timeout=FIRST_INDICATION_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    _LOGGER.warning("[%s] No indications within %ds",
                                    self.address, FIRST_INDICATION_TIMEOUT)
                    if idle_handle[0] is not None:
                        idle_handle[0].cancel()
                    raise BleakError(
                        f"[{self.address}] No indications within {FIRST_INDICATION_TIMEOUT}s"
                    )
            elif done_event.is_set() and not weight_records and not bcm_records:
                _LOGGER.info("[%s] Device disconnected with no data – no pending measurement",
                             self.address)
                return
            else:
                _LOGGER.debug("[%s] Data already received – fast-path to drain", self.address)

            # Phase 2 — drain all remaining records
            if not done_event.is_set():
                await done_event.wait()

            if idle_handle[0] is not None:
                idle_handle[0].cancel()

            _LOGGER.info("[%s] Transfer complete – %d weight, %d BCM record(s)",
                         self.address, len(weight_records), len(bcm_records))

            for h in [h for h in [
                handles.get("weight"),
                handles.get("bcm") if bcm_available else None,
            ] if h is not None]:
                try:
                    await client.stop_notify(h)
                except BleakError:
                    pass
        finally:
            try:
                await client.disconnect()
            except BleakError:
                pass

        # Publish most-recent record from each stream
        if not weight_records and not bcm_records:
            return

        def _latest(records):
            if not records:
                return None
            with_ts = [r for r in records if r.timestamp is not None]
            return max(with_ts, key=lambda r: r.timestamp) if with_ts else records[-1]

        result = ScaleMeasurement(
            weight=_latest(weight_records),
            body_composition=_latest(bcm_records),
        )
        _LOGGER.info(
            "[%s] Publishing: %.3f kg bmi=%s fat=%s%% ts=%s",
            self.address, result.weight_kg or 0, result.bmi,
            result.body_composition.body_fat_percent
            if result.body_composition else "N/A",
            result.timestamp,
        )
        self._last_measurement = result
        self.async_set_updated_data(result)

    def _resolve_characteristics(self, client: Any) -> dict[str, int | None]:
        """Resolve weight and BCM handles; service-scoped then global fallback."""
        def _n(u: str) -> str:
            return str(u).lower()

        ws_svc   = _n(WEIGHT_SCALE_SERVICE_UUID)
        bc_svc   = _n(BODY_COMPOSITION_SERVICE_UUID)
        wm_uuid  = _n(WEIGHT_MEASUREMENT_UUID)
        bcm_uuid = _n(BODY_COMPOSITION_MEASUREMENT_UUID)

        handles: dict[str, int | None] = {"weight": None, "bcm": None}

        for svc in client.services:
            su = _n(svc.uuid)
            for char in svc.characteristics:
                cu = _n(char.uuid)
                if cu == wm_uuid and su == ws_svc and handles["weight"] is None:
                    handles["weight"] = char.handle
                elif cu == bcm_uuid and su == bc_svc and handles["bcm"] is None:
                    handles["bcm"] = char.handle

        if handles["weight"] is None or handles["bcm"] is None:
            for svc in client.services:
                for char in svc.characteristics:
                    cu = _n(char.uuid)
                    if cu == wm_uuid and handles["weight"] is None:
                        handles["weight"] = char.handle
                    elif cu == bcm_uuid and handles["bcm"] is None:
                        handles["bcm"] = char.handle

        return handles

    async def _ensure_paired(self, client: Any) -> None:
        """Attempt to pair/bond with the device; handle proxies and failures gracefully.

        On BlueZ (Linux / HAOS):
          - If already bonded, pair() returns almost instantly.
          - If not bonded, BlueZ performs the SMP exchange ("Just Works" for most
            BP monitors) and stores the Long Term Key (LTK) for future connections.

        On ESPHome Bluetooth Proxies:
          - pair() raises NotImplementedError or BleakError.  We log a warning
            and continue; if the device is already bonded at the adapter level
            the GATT ops will succeed anyway.
        """
        try:
            _LOGGER.debug("[%s] Calling client.pair() …", self.address)
            await asyncio.wait_for(client.pair(), timeout=PAIR_TIMEOUT)
            _LOGGER.info("[%s] Paired/bonded successfully (or already bonded)", self.address)
            self._paired_successfully = True
        except NotImplementedError:
            # ESPHome proxy backend does not implement pair()
            _LOGGER.debug(
                "[%s] pair() not supported on this backend (ESPHome proxy?). "
                "Continuing without explicit pairing.",
                self.address,
            )
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "[%s] Pairing timed out after %ds. "
                "The device may require a button press to confirm pairing. "
                "Attempting to continue — GATT ops may fail with error=5.",
                self.address, PAIR_TIMEOUT,
            )
        except BleakError as exc:
            # pair() itself failed (e.g. already paired and BlueZ returned fast,
            # or the ESPHome proxy raised BleakError instead of NotImplementedError).
            if "already" in str(exc).lower() or "paired" in str(exc).lower():
                _LOGGER.debug("[%s] Device reports already paired: %s", self.address, exc)
                self._paired_successfully = True
            else:
                _LOGGER.warning(
                    "[%s] pair() failed: %s. "
                    "If you see GATT error=5 next, run: "
                    "bluetoothctl; agent on; pair %s",
                    self.address, exc, self.address,
                )

    # ── DataUpdateCoordinator override ────────────────────────────────────────

    async def _async_update_data(self) -> ScaleMeasurement | None:
        return self._last_measurement
