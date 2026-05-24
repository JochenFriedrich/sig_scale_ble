# ⚖️ SIG Weight Scale BLE — Home Assistant Integration

A **local_push** custom integration for Home Assistant supporting any BLE weight
scale implementing the Bluetooth SIG
[Weight Scale Service](https://www.bluetooth.com/specifications/specs/weight-scale-service-1-0/) (`0x181D`)
and/or [Body Composition Service](https://www.bluetooth.com/specifications/specs/body-composition-service-1-0/) (`0x181B`).

## Compatible Devices

| Brand        | Example models                           |
|--------------|------------------------------------------|
| A&D          | UC-352BLE, UC-450BLE                     |
| Omron        | HBF-702T, HBF-222T                       |
| Withings     | Body+, Body Cardio (SIG mode)            |
| Beurer       | BF 800, BG 22                            |
| Garmin       | Index S2                                 |
| Tanita       | RD-953                                   |

> **Note:** Many cheap scales use proprietary protocols despite having BLE.
> This integration only works with devices that advertise `0x181D` or `0x181B`.

---

## Installation

```bash
cp -r custom_components/sig_scale_ble /config/custom_components/
```

Restart Home Assistant, then step on your scale — it advertises briefly after each measurement.

---

## Sensors Created

### Always present (Weight Scale Service `0x181D`)

| Entity | Unit | Notes |
|--------|------|-------|
| `sensor.<name>_weight` | kg | Primary weight |
| `sensor.<name>_weight_lb` | lb | Always computed |

### Optional (if device reports them via `0x2A9D`)

| Entity | Unit |
|--------|------|
| `sensor.<name>_bmi` | — |
| `sensor.<name>_height` | cm |
| `sensor.<name>_last_measurement_time` | — |
| `sensor.<name>_user_id` | — |

### Body Composition (Body Composition Service `0x181B`, if available)

| Entity | Unit | Notes |
|--------|------|-------|
| `sensor.<name>_body_fat` | % | |
| `sensor.<name>_muscle_percentage` | % | |
| `sensor.<name>_muscle_mass` | kg | |
| `sensor.<name>_fat_free_mass` | kg | |
| `sensor.<name>_soft_lean_mass` | kg | |
| `sensor.<name>_body_water_mass` | kg | |
| `sensor.<name>_impedance` | Ω | Raw BIA value |
| `sensor.<name>_basal_metabolic_rate` | kJ | |

Body composition sensors are only `available` once the device has reported them.

---

## How It Works

```
Step on scale → stable weight detected
        │
        ▼
Scale advertises via BLE  (brief window, ~30 s)
        │
        ▼  HA Bluetooth stack sees advertisement
async_register_callback fires
        │
        ▼
BleakClient connects + pairs (if needed)
        │
        ▼
_resolve_characteristics() walks GATT tree
→ weight handle from Weight Scale Service (0x181D)
→ bcm    handle from Body Composition Service (0x181B)
        │
        ▼
Subscribe to 0x2A9D indications (weight)
Subscribe to 0x2A9C indications (body comp, if present)
        │
        ▼
Device streams all stored records (both services in parallel)
bleak sends ATT Confirmation for each indication automatically
        │
        ▼
Idle timer fires 3 s after last indication
        │
        ▼
Latest weight + latest BCM record merged into ScaleMeasurement
Published to HA sensors
```

### Key differences from Glucose (RACP) integration

Weight scales use the same **auto-stream** pattern as blood pressure monitors —
no RACP write required. Both `0x2A9D` (weight) and `0x2A9C` (body composition)
stream records independently as soon as you subscribe. The coordinator subscribes
to both, drains all indications from both streams using a shared idle timer, then
merges the most-recent record from each into a single `ScaleMeasurement` before publishing.

---

## Automation Example

```yaml
alias: Log weight after morning weigh-in
trigger:
  - platform: state
    entity_id: sensor.my_scale_weight
action:
  - service: notify.mobile_app_my_phone
    data:
      title: "Morning weight"
      message: >
        {{ states('sensor.my_scale_weight') }} kg
        (BMI {{ states('sensor.my_scale_bmi') }},
        fat {{ states('sensor.my_scale_body_fat') }}%)
```

---

## Running Tests

```bash
pip install pytest
cd sig_scale_ble
pytest tests/ -v
```

---

## License

MIT
