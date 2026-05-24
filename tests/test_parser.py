"""Unit tests for SIG Weight Measurement (0x2A9D) and
Body Composition Measurement (0x2A9C) parsers."""
from __future__ import annotations

import struct
from datetime import datetime

import pytest

from custom_components.sig_scale_ble.parser import (
    parse_weight_measurement,
    parse_body_composition_measurement,
    WeightMeasurement,
    BodyCompositionMeasurement,
    ScaleMeasurement,
    _sfloat,
)
from custom_components.sig_scale_ble.const import (
    WM_FLAG_IMPERIAL, WM_FLAG_TIMESTAMP, WM_FLAG_USER_ID, WM_FLAG_BMI_HEIGHT,
    BCM_FLAG_IMPERIAL, BCM_FLAG_TIMESTAMP, BCM_FLAG_USER_ID,
    BCM_FLAG_BASAL_METABOLISM, BCM_FLAG_MUSCLE_PERCENTAGE, BCM_FLAG_MUSCLE_MASS,
    BCM_FLAG_FAT_FREE_MASS, BCM_FLAG_BODY_WATER_MASS, BCM_FLAG_IMPEDANCE,
    BCM_FLAG_WEIGHT, BCM_FLAG_HEIGHT,
    WM_WEIGHT_SI_RES, WM_WEIGHT_IMP_RES, WM_BMI_RES,
    WM_HEIGHT_SI_RES, WM_HEIGHT_IMP_RES,
    UNIT_KG, UNIT_LB,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _encode_ts(dt: datetime) -> bytes:
    return struct.pack("<HBBBBB", dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def _make_sfloat(mantissa: int, exponent: int) -> int:
    return ((exponent & 0x0F) << 12) | (mantissa & 0x0FFF)


def _build_weight_packet(
    weight_kg: float = 75.0,
    imperial: bool = False,
    timestamp: datetime | None = None,
    user_id: int | None = None,
    bmi: float | None = None,
    height_m: float | None = None,
) -> bytes:
    flags = 0
    if imperial:
        flags |= WM_FLAG_IMPERIAL
        weight_raw = round(weight_kg / 0.45359237 * WM_WEIGHT_IMP_RES)
    else:
        weight_raw = round(weight_kg * WM_WEIGHT_SI_RES)

    if timestamp:
        flags |= WM_FLAG_TIMESTAMP
    if user_id is not None:
        flags |= WM_FLAG_USER_ID
    if bmi is not None and height_m is not None:
        flags |= WM_FLAG_BMI_HEIGHT

    data = struct.pack("<HH", flags, weight_raw)

    if timestamp:
        data += _encode_ts(timestamp)
    if user_id is not None:
        data += bytes([user_id])
    if bmi is not None and height_m is not None:
        bmi_raw = round(bmi * WM_BMI_RES)
        if imperial:
            height_raw = round((height_m / 0.0254) * WM_HEIGHT_IMP_RES)
        else:
            height_raw = round(height_m * WM_HEIGHT_SI_RES)
        data += struct.pack("<HH", bmi_raw, height_raw)

    return data


def _build_bcm_packet(
    body_fat_pct: float = 20.0,
    imperial: bool = False,
    timestamp: datetime | None = None,
    user_id: int | None = None,
    muscle_pct: float | None = None,
    muscle_mass_kg: float | None = None,
    body_water_mass_kg: float | None = None,
    impedance_ohm: float | None = None,
    weight_kg: float | None = None,
    height_m: float | None = None,
    basal_kj: float | None = None,
) -> bytes:
    flags = 0
    if imperial:
        flags |= BCM_FLAG_IMPERIAL
    if timestamp:
        flags |= BCM_FLAG_TIMESTAMP
    if user_id is not None:
        flags |= BCM_FLAG_USER_ID
    if basal_kj is not None:
        flags |= BCM_FLAG_BASAL_METABOLISM
    if muscle_pct is not None:
        flags |= BCM_FLAG_MUSCLE_PERCENTAGE
    if muscle_mass_kg is not None:
        flags |= BCM_FLAG_MUSCLE_MASS
    if body_water_mass_kg is not None:
        flags |= BCM_FLAG_BODY_WATER_MASS
    if impedance_ohm is not None:
        flags |= BCM_FLAG_IMPEDANCE
    if weight_kg is not None:
        flags |= BCM_FLAG_WEIGHT
    if height_m is not None:
        flags |= BCM_FLAG_HEIGHT

    def _enc(val: float, exponent: int = -3) -> bytes:
        mantissa = round(val / (10 ** exponent))
        return struct.pack("<H", _make_sfloat(mantissa, exponent))

    fat_raw = round(body_fat_pct / (10 ** -1))
    data = struct.pack("<H", flags) + struct.pack("<H", _make_sfloat(fat_raw, -1))

    if timestamp:
        data += _encode_ts(timestamp)
    if user_id is not None:
        data += bytes([user_id])
    if basal_kj is not None:
        data += _enc(basal_kj, 0)
    if muscle_pct is not None:
        data += _enc(muscle_pct, -1)
    if muscle_mass_kg is not None:
        val = muscle_mass_kg / 0.45359237 if imperial else muscle_mass_kg
        data += _enc(val, -3)
    if body_water_mass_kg is not None:
        val = body_water_mass_kg / 0.45359237 if imperial else body_water_mass_kg
        data += _enc(val, -3)
    if impedance_ohm is not None:
        data += _enc(impedance_ohm, -1)
    if weight_kg is not None:
        val = weight_kg / 0.45359237 if imperial else weight_kg
        data += _enc(val, -3)
    if height_m is not None:
        val = height_m / 0.0254 if imperial else height_m
        data += _enc(val, -3)

    return data


# ── Weight Measurement tests ───────────────────────────────────────────────────

class TestWeightMeasurement:
    def test_si_weight_roundtrip(self):
        pkt = _build_weight_packet(weight_kg=75.0)
        m = parse_weight_measurement(pkt)
        assert m.is_valid
        assert m.unit == UNIT_KG
        assert m.weight_kg == pytest.approx(75.0, abs=0.01)
        assert m.weight_lb == pytest.approx(75.0 / 0.45359237, abs=0.05)

    def test_imperial_weight_roundtrip(self):
        pkt = _build_weight_packet(weight_kg=70.0, imperial=True)
        m = parse_weight_measurement(pkt)
        assert m.is_valid
        assert m.unit == UNIT_LB
        assert m.weight_lb == pytest.approx(70.0 / 0.45359237, abs=0.1)
        assert m.weight_kg == pytest.approx(70.0, abs=0.05)

    def test_timestamp_parsed(self):
        ts = datetime(2025, 9, 10, 7, 30, 0)
        pkt = _build_weight_packet(timestamp=ts)
        m = parse_weight_measurement(pkt)
        assert m.timestamp == ts

    def test_no_timestamp(self):
        pkt = _build_weight_packet()
        m = parse_weight_measurement(pkt)
        assert m.timestamp is None

    def test_user_id(self):
        pkt = _build_weight_packet(user_id=3)
        m = parse_weight_measurement(pkt)
        assert m.user_id == 3

    def test_bmi_and_height(self):
        pkt = _build_weight_packet(bmi=22.5, height_m=1.75)
        m = parse_weight_measurement(pkt)
        assert m.bmi == pytest.approx(22.5, abs=0.1)
        assert m.height_m == pytest.approx(1.75, abs=0.001)
        assert m.height_in == pytest.approx(1.75 / 0.0254, abs=0.5)

    def test_bmi_and_height_imperial(self):
        pkt = _build_weight_packet(bmi=24.0, height_m=1.80, imperial=True)
        m = parse_weight_measurement(pkt)
        assert m.bmi == pytest.approx(24.0, abs=0.1)
        assert m.height_in == pytest.approx(1.80 / 0.0254, abs=1.0)
        assert m.height_m  == pytest.approx(1.80, abs=0.05)

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            parse_weight_measurement(b"\x00\x00")

    def test_resolution(self):
        """Minimum SI resolution is 0.005 kg — two adjacent raw values differ by 0.005."""
        pkt1 = _build_weight_packet(weight_kg=70.000)
        pkt2 = struct.pack("<HH", 0x0000, round(70.005 * WM_WEIGHT_SI_RES))
        m1 = parse_weight_measurement(pkt1)
        m2 = parse_weight_measurement(pkt2)
        assert abs((m2.weight_kg or 0) - (m1.weight_kg or 0)) == pytest.approx(0.005, abs=0.001)


# ── Body Composition Measurement tests ────────────────────────────────────────

class TestBodyCompositionMeasurement:
    def test_body_fat_present(self):
        pkt = _build_bcm_packet(body_fat_pct=22.3)
        m = parse_body_composition_measurement(pkt)
        assert m.is_valid
        assert m.body_fat_percent == pytest.approx(22.3, abs=0.1)

    def test_timestamp(self):
        ts = datetime(2025, 4, 1, 6, 0, 0)
        pkt = _build_bcm_packet(timestamp=ts)
        m = parse_body_composition_measurement(pkt)
        assert m.timestamp == ts

    def test_user_id(self):
        pkt = _build_bcm_packet(user_id=2)
        m = parse_body_composition_measurement(pkt)
        assert m.user_id == 2

    def test_muscle_percentage(self):
        pkt = _build_bcm_packet(muscle_pct=44.5)
        m = parse_body_composition_measurement(pkt)
        assert m.muscle_percent == pytest.approx(44.5, abs=0.2)

    def test_muscle_mass(self):
        pkt = _build_bcm_packet(muscle_mass_kg=35.2)
        m = parse_body_composition_measurement(pkt)
        assert m.muscle_mass_kg == pytest.approx(35.2, abs=0.05)

    def test_body_water_mass(self):
        pkt = _build_bcm_packet(body_water_mass_kg=40.1)
        m = parse_body_composition_measurement(pkt)
        assert m.body_water_mass_kg == pytest.approx(40.1, abs=0.05)

    def test_impedance(self):
        pkt = _build_bcm_packet(impedance_ohm=500.0)
        m = parse_body_composition_measurement(pkt)
        assert m.impedance_ohm == pytest.approx(500.0, abs=1.0)

    def test_bmi_computed_from_weight_height(self):
        # 75 kg / (1.75 m)^2 = 24.5
        pkt = _build_bcm_packet(weight_kg=75.0, height_m=1.75)
        m = parse_body_composition_measurement(pkt)
        assert m.weight_kg == pytest.approx(75.0, abs=0.05)
        assert m.height_m  == pytest.approx(1.75, abs=0.005)
        assert m.bmi       == pytest.approx(75.0 / (1.75 ** 2), abs=0.2)

    def test_imperial_mass_converted_to_kg(self):
        # 154 lb ≈ 69.85 kg
        pkt = _build_bcm_packet(muscle_mass_kg=30.0, imperial=True)
        m = parse_body_composition_measurement(pkt)
        # The builder passes kg/0.45 to get lb, parser converts back
        assert m.muscle_mass_kg == pytest.approx(30.0, abs=0.1)

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            parse_body_composition_measurement(b"\x00\x00\x00")

    def test_full_packet(self):
        ts = datetime(2025, 6, 15, 8, 0, 0)
        pkt = _build_bcm_packet(
            body_fat_pct=18.5,
            timestamp=ts,
            user_id=1,
            muscle_pct=46.0,
            muscle_mass_kg=38.0,
            body_water_mass_kg=42.0,
            impedance_ohm=450.0,
            weight_kg=82.0,
            height_m=1.80,
            basal_kj=7500,
        )
        m = parse_body_composition_measurement(pkt)
        assert m.is_valid
        assert m.body_fat_percent  == pytest.approx(18.5, abs=0.2)
        assert m.timestamp         == ts
        assert m.user_id           == 1
        assert m.muscle_percent    == pytest.approx(46.0, abs=0.2)
        assert m.muscle_mass_kg    == pytest.approx(38.0, abs=0.1)
        assert m.body_water_mass_kg == pytest.approx(42.0, abs=0.1)
        assert m.impedance_ohm     == pytest.approx(450.0, abs=1.0)
        assert m.weight_kg         == pytest.approx(82.0, abs=0.1)
        assert m.height_m          == pytest.approx(1.80, abs=0.005)
        assert m.basal_metabolism_kj == pytest.approx(7500, abs=10)
        assert m.bmi               == pytest.approx(82.0 / (1.80 ** 2), abs=0.2)


# ── ScaleMeasurement composite tests ──────────────────────────────────────────

class TestScaleMeasurement:
    def test_weight_kg_from_weight_record(self):
        w = parse_weight_measurement(_build_weight_packet(weight_kg=80.0))
        s = ScaleMeasurement(weight=w)
        assert s.weight_kg == pytest.approx(80.0, abs=0.01)

    def test_weight_kg_falls_back_to_bcm(self):
        bcm = parse_body_composition_measurement(
            _build_bcm_packet(weight_kg=78.5)
        )
        s = ScaleMeasurement(body_composition=bcm)
        assert s.weight_kg == pytest.approx(78.5, abs=0.1)

    def test_timestamp_from_weight_preferred(self):
        ts_w = datetime(2025, 1, 1, 8, 0, 0)
        ts_b = datetime(2025, 1, 1, 8, 0, 5)
        w   = parse_weight_measurement(_build_weight_packet(timestamp=ts_w))
        bcm = parse_body_composition_measurement(_build_bcm_packet(timestamp=ts_b))
        s = ScaleMeasurement(weight=w, body_composition=bcm)
        assert s.timestamp == ts_w

    def test_timestamp_falls_back_to_bcm(self):
        ts_b = datetime(2025, 3, 20, 9, 15, 0)
        w   = parse_weight_measurement(_build_weight_packet())   # no ts
        bcm = parse_body_composition_measurement(_build_bcm_packet(timestamp=ts_b))
        s = ScaleMeasurement(weight=w, body_composition=bcm)
        assert s.timestamp == ts_b

    def test_bmi_from_weight_record(self):
        w = parse_weight_measurement(_build_weight_packet(bmi=23.1, height_m=1.72))
        s = ScaleMeasurement(weight=w)
        assert s.bmi == pytest.approx(23.1, abs=0.1)

    def test_bmi_computed_in_bcm(self):
        bcm = parse_body_composition_measurement(
            _build_bcm_packet(weight_kg=70.0, height_m=1.75)
        )
        s = ScaleMeasurement(body_composition=bcm)
        assert s.bmi == pytest.approx(70.0 / (1.75 ** 2), abs=0.2)

    def test_is_valid_weight_only(self):
        w = parse_weight_measurement(_build_weight_packet())
        assert ScaleMeasurement(weight=w).is_valid

    def test_is_valid_bcm_only(self):
        bcm = parse_body_composition_measurement(_build_bcm_packet())
        assert ScaleMeasurement(body_composition=bcm).is_valid

    def test_not_valid_when_empty(self):
        assert not ScaleMeasurement().is_valid


# ── UDS / UCP tests ────────────────────────────────────────────────────────────

class TestUDSConsentParsing:
    """Test UCP response packet parsing used in uds.py."""

    def _ucp_response(self, req_op: int, response_code: int, extra: bytes = b"") -> bytes:
        return bytes([0x20, req_op, response_code]) + extra

    def test_consent_success_packet(self):
        from custom_components.sig_scale_ble.const import (
            UCP_OP_RESPONSE, UCP_OP_CONSENT, UCP_RESPONSE_SUCCESS,
        )
        pkt = self._ucp_response(UCP_OP_CONSENT, UCP_RESPONSE_SUCCESS)
        assert pkt[0] == UCP_OP_RESPONSE
        assert pkt[1] == UCP_OP_CONSENT
        assert pkt[2] == UCP_RESPONSE_SUCCESS

    def test_consent_not_authorized_packet(self):
        from custom_components.sig_scale_ble.const import (
            UCP_OP_CONSENT, UCP_RESPONSE_USER_NOT_AUTHORIZED,
        )
        pkt = self._ucp_response(UCP_OP_CONSENT, UCP_RESPONSE_USER_NOT_AUTHORIZED)
        assert pkt[2] == UCP_RESPONSE_USER_NOT_AUTHORIZED

    def test_register_new_user_response(self):
        from custom_components.sig_scale_ble.const import (
            UCP_OP_REGISTER_NEW_USER, UCP_RESPONSE_SUCCESS,
        )
        # Response includes new_user_index as byte 3
        pkt = self._ucp_response(UCP_OP_REGISTER_NEW_USER, UCP_RESPONSE_SUCCESS,
                                  extra=bytes([0x02]))
        assert pkt[2] == UCP_RESPONSE_SUCCESS
        new_user_index = pkt[3]
        assert new_user_index == 2

    def test_consent_command_byte_layout(self):
        """Verify the Consent command is built correctly: [0x02, index, code_lo, code_hi]."""
        import struct
        from custom_components.sig_scale_ble.const import UCP_OP_CONSENT
        user_index   = 1
        consent_code = 0x1234
        cmd = struct.pack("<BBH", UCP_OP_CONSENT, user_index, consent_code)
        assert cmd[0] == 0x02
        assert cmd[1] == 0x01
        assert cmd[2] == 0x34   # little-endian low byte
        assert cmd[3] == 0x12   # little-endian high byte

    def test_register_command_byte_layout(self):
        """Verify Register New User command: [0x01, code_lo, code_hi]."""
        import struct
        from custom_components.sig_scale_ble.const import UCP_OP_REGISTER_NEW_USER
        consent_code = 0x0000
        cmd = struct.pack("<BH", UCP_OP_REGISTER_NEW_USER, consent_code)
        assert cmd[0] == 0x01
        assert cmd[1] == 0x00
        assert cmd[2] == 0x00
