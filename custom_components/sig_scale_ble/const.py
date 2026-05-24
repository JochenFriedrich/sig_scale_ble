"""Constants for the SIG Weight Scale BLE integration."""

DOMAIN = "sig_scale_ble"

# ── Bluetooth SIG service UUIDs ───────────────────────────────────────────────
WEIGHT_SCALE_SERVICE_UUID      = "0000181d-0000-1000-8000-00805f9b34fb"
BODY_COMPOSITION_SERVICE_UUID  = "0000181b-0000-1000-8000-00805f9b34fb"

# ── Characteristic UUIDs ──────────────────────────────────────────────────────
# Weight Scale Service
WEIGHT_MEASUREMENT_UUID        = "00002a9d-0000-1000-8000-00805f9b34fb"
WEIGHT_SCALE_FEATURE_UUID      = "00002a9e-0000-1000-8000-00805f9b34fb"
# Body Composition Service
BODY_COMPOSITION_MEASUREMENT_UUID = "00002a9c-0000-1000-8000-00805f9b34fb"
BODY_COMPOSITION_FEATURE_UUID  = "00002a9b-0000-1000-8000-00805f9b34fb"

# ── Weight Measurement (0x2A9D) flag bits ─────────────────────────────────────
# Bit 0: Measurement Units  0 = SI (kg / m)   1 = Imperial (lb / inch)
WM_FLAG_IMPERIAL               = 0x0001
WM_FLAG_TIMESTAMP              = 0x0002
WM_FLAG_USER_ID                = 0x0004
WM_FLAG_BMI_HEIGHT             = 0x0008

# ── Body Composition Measurement (0x2A9C) flag bits (uint16) ─────────────────
# Bit 0: Measurement Units  0 = SI   1 = Imperial
BCM_FLAG_IMPERIAL              = 0x0001
BCM_FLAG_TIMESTAMP             = 0x0002
BCM_FLAG_USER_ID               = 0x0004
BCM_FLAG_BASAL_METABOLISM      = 0x0008   # Basal Metabolic Rate (kJ)
BCM_FLAG_MUSCLE_PERCENTAGE     = 0x0010
BCM_FLAG_MUSCLE_MASS           = 0x0020
BCM_FLAG_FAT_FREE_MASS         = 0x0040
BCM_FLAG_SOFT_LEAN_MASS        = 0x0080
BCM_FLAG_BODY_WATER_MASS       = 0x0100
BCM_FLAG_IMPEDANCE             = 0x0200
BCM_FLAG_WEIGHT                = 0x0400
BCM_FLAG_HEIGHT                = 0x0800
BCM_FLAG_MULTIPLE_PACKET_MEASUREMENT = 0x1000

# ── Unit strings ───────────────────────────────────────────────────────────────
UNIT_KG    = "kg"
UNIT_LB    = "lb"
UNIT_M     = "m"
UNIT_INCH  = "in"
UNIT_KJ    = "kJ"
UNIT_OHM   = "Ω"

# ── Resolution constants (from SIG spec) ──────────────────────────────────────
# Weight Measurement 0x2A9D:
#   SI weight:      uint16, resolution 0.005 kg  → divide raw by 200
#   Imperial weight: uint16, resolution 0.01 lb   → divide raw by 100
#   BMI:            uint16, resolution 0.1         → divide raw by 10
#   Height (SI):    uint16, resolution 0.001 m     → divide raw by 1000
#   Height (imperial): uint16, resolution 0.1 inch → divide raw by 10
WM_WEIGHT_SI_RES     = 200.0   # raw / 200 = kg
WM_WEIGHT_IMP_RES    = 100.0   # raw / 100 = lb
WM_BMI_RES           = 10.0    # raw / 10  = BMI
WM_HEIGHT_SI_RES     = 1000.0  # raw / 1000 = m
WM_HEIGHT_IMP_RES    = 10.0    # raw / 10   = inches

# Body Composition Measurement 0x2A9C (all SFLOAT):
#   Body fat %:        SFLOAT, %     resolution 0.1
#   Muscle %:          SFLOAT, %     resolution 0.1
#   Muscle mass:       SFLOAT, kg    resolution 0.005
#   Fat-free mass:     SFLOAT, kg    resolution 0.005
#   Soft lean mass:    SFLOAT, kg    resolution 0.005
#   Body water mass:   SFLOAT, kg    resolution 0.005
#   Impedance:         SFLOAT, Ω     resolution 0.1
#   Weight (BCM):      SFLOAT, kg    resolution 0.005
#   Height (BCM):      SFLOAT, m     resolution 0.001
#   Basal metabolism:  SFLOAT, kJ    resolution 1

# ── Timing ────────────────────────────────────────────────────────────────────
PAIR_TIMEOUT                   = 30.0
FIRST_INDICATION_TIMEOUT       = 60.0
IDLE_AFTER_LAST_RECORD_TIMEOUT = 30.0

# ── User Data Service (0x181C) UUIDs ──────────────────────────────────────────
USER_DATA_SERVICE_UUID         = "0000181c-0000-1000-8000-00805f9b34fb"
# Mandatory for consent flow
USER_INDEX_UUID                = "00002a9a-0000-1000-8000-00805f9b34fb"  # read
USER_CONTROL_POINT_UUID        = "00002a9f-0000-1000-8000-00805f9b34fb"  # write + indicate
# Optional user demographic characteristics (read/write)
USER_FIRST_NAME_UUID           = "00002a8a-0000-1000-8000-00805f9b34fb"
USER_LAST_NAME_UUID            = "00002a90-0000-1000-8000-00805f9b34fb"
USER_AGE_UUID                  = "00002a80-0000-1000-8000-00805f9b34fb"
USER_HEIGHT_UUID               = "00002a8e-0000-1000-8000-00805f9b34fb"  # uint16, 0.01 m res
USER_GENDER_UUID               = "00002a8c-0000-1000-8000-00805f9b34fb"  # 0=Male 1=Female 2=Unspecified

# ── User Control Point (0x2A9F) Op Codes ─────────────────────────────────────
UCP_OP_REGISTER_NEW_USER       = 0x01  # param: uint16 consent_code → response: 0x20
UCP_OP_CONSENT                 = 0x02  # param: uint8 user_index + uint16 consent_code → 0x20
UCP_OP_DELETE_USER_DATA        = 0x03  # param: none → response: 0x20
UCP_OP_LIST_ALL_USERS          = 0x04  # response: 0x20 + list (optional, not widely implemented)
UCP_OP_DELETE_USERS            = 0x05  # param: uint8 user_index → response: 0x20
UCP_OP_RESPONSE                = 0x20  # indication: 0x20 + req_op + response_code [+ params]

# ── User Control Point Response Codes (byte 2 of 0x20 indication) ─────────────
UCP_RESPONSE_SUCCESS           = 0x01
UCP_RESPONSE_OP_NOT_SUPPORTED  = 0x02
UCP_RESPONSE_INVALID_PARAMETER = 0x03
UCP_RESPONSE_OP_FAILED         = 0x04
UCP_RESPONSE_USER_NOT_AUTHORIZED = 0x05  # consent code wrong / user not found

# ── Special user index values ─────────────────────────────────────────────────
UCP_USER_INDEX_UNKNOWN         = 0xFF   # read from 0x2A9A when no user is active

# ── Default consent code ──────────────────────────────────────────────────────
# Scales typically ship with a fixed default PIN (commonly 0x0000 = 0).
# HA stores the configured consent code per config entry.
UCP_DEFAULT_CONSENT_CODE       = 0x0000

# ── UDS timing ────────────────────────────────────────────────────────────────
UCP_WRITE_TIMEOUT              = 5.0   # seconds for the UCP write-with-response
UCP_RESPONSE_TIMEOUT           = 10.0  # seconds to wait for the UCP indication
