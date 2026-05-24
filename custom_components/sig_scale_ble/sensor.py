"""Sensor platform for SIG Weight Scale BLE integration.

Entities created per device:

  From Weight Measurement (0x2A9D) — always present:
    • Weight (kg)
    • Weight (lb)
    • BMI                       — if device reports it
    • Height (m / cm)           — if device reports it
    • Last Measurement Time     — if device reports timestamp
    • User ID                   — if device reports it

  From Body Composition Measurement (0x2A9C) — present on body-comp scales:
    • Body Fat (%)
    • Muscle Percentage (%)
    • Muscle Mass (kg)
    • Fat-Free Mass (kg)
    • Soft Lean Mass (kg)
    • Body Water Mass (kg)
    • Impedance (Ω)
    • BMI (computed from BCM weight + height if not already from 0x2A9D)

  All body-composition sensors are optional — they only become available once
  the device has sent at least one body composition record.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, UNIT_KG, UNIT_LB, UNIT_OHM, UNIT_KJ
from .coordinator import ScaleCoordinator
from .parser import ScaleMeasurement

_ATTRIBUTION = "Bluetooth SIG Weight Scale / Body Composition Service (0x181D / 0x181B)"

UNIT_PERCENT = "%"
UNIT_CM      = "cm"


@dataclass(frozen=True, kw_only=True)
class ScaleSensorDescription(SensorEntityDescription):
    value_fn: Callable[[ScaleMeasurement], Any] = lambda _: None
    optional: bool = False   # if True, entity unavailable until value is seen


# ── Weight sensors (from 0x2A9D) ──────────────────────────────────────────────
_WEIGHT_DESCRIPTIONS: tuple[ScaleSensorDescription, ...] = (
    ScaleSensorDescription(
        key="weight_kg",
        name="Weight",
        icon="mdi:scale-bathroom",
        native_unit_of_measurement=UNIT_KG,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: m.weight_kg,
    ),
    ScaleSensorDescription(
        key="weight_lb",
        name="Weight (lb)",
        icon="mdi:scale-bathroom",
        native_unit_of_measurement=UNIT_LB,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda m: (
            m.weight.weight_lb if m.weight else
            (round(m.body_composition.weight_kg / 0.45359237, 1)
             if m.body_composition and m.body_composition.weight_kg else None)
        ),
    ),
    ScaleSensorDescription(
        key="bmi",
        name="BMI",
        icon="mdi:human",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        optional=True,
        value_fn=lambda m: m.bmi,
    ),
    ScaleSensorDescription(
        key="height_cm",
        name="Height",
        icon="mdi:human-male-height",
        native_unit_of_measurement=UNIT_CM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        optional=True,
        value_fn=lambda m: (
            round(m.weight.height_m * 100, 1)
            if m.weight and m.weight.height_m is not None
            else (
                round(m.body_composition.height_m * 100, 1)
                if m.body_composition and m.body_composition.height_m is not None
                else None
            )
        ),
    ),
    ScaleSensorDescription(
        key="measurement_time",
        name="Last Measurement Time",
        icon="mdi:clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        optional=True,
        value_fn=lambda m: m.timestamp,
    ),
    ScaleSensorDescription(
        key="user_id",
        name="User ID",
        icon="mdi:account",
        optional=True,
        value_fn=lambda m: (
            m.weight.user_id if m.weight and m.weight.user_id is not None
            else (m.body_composition.user_id
                  if m.body_composition else None)
        ),
    ),
)

# ── Body Composition sensors (from 0x2A9C) ─────────────────────────────────────
_BCM_DESCRIPTIONS: tuple[ScaleSensorDescription, ...] = (
    ScaleSensorDescription(
        key="body_fat_percent",
        name="Body Fat",
        icon="mdi:water-percent",
        native_unit_of_measurement=UNIT_PERCENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        optional=True,
        value_fn=lambda m: (
            m.body_composition.body_fat_percent if m.body_composition else None
        ),
    ),
    ScaleSensorDescription(
        key="muscle_percent",
        name="Muscle Percentage",
        icon="mdi:arm-flex",
        native_unit_of_measurement=UNIT_PERCENT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        optional=True,
        value_fn=lambda m: (
            m.body_composition.muscle_percent if m.body_composition else None
        ),
    ),
    ScaleSensorDescription(
        key="muscle_mass_kg",
        name="Muscle Mass",
        icon="mdi:arm-flex-outline",
        native_unit_of_measurement=UNIT_KG,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        optional=True,
        value_fn=lambda m: (
            m.body_composition.muscle_mass_kg if m.body_composition else None
        ),
    ),
    ScaleSensorDescription(
        key="fat_free_mass_kg",
        name="Fat-Free Mass",
        icon="mdi:human",
        native_unit_of_measurement=UNIT_KG,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        optional=True,
        value_fn=lambda m: (
            m.body_composition.fat_free_mass_kg if m.body_composition else None
        ),
    ),
    ScaleSensorDescription(
        key="soft_lean_mass_kg",
        name="Soft Lean Mass",
        icon="mdi:human-handsup",
        native_unit_of_measurement=UNIT_KG,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        optional=True,
        value_fn=lambda m: (
            m.body_composition.soft_lean_mass_kg if m.body_composition else None
        ),
    ),
    ScaleSensorDescription(
        key="body_water_mass_kg",
        name="Body Water Mass",
        icon="mdi:water",
        native_unit_of_measurement=UNIT_KG,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        optional=True,
        value_fn=lambda m: (
            m.body_composition.body_water_mass_kg if m.body_composition else None
        ),
    ),
    ScaleSensorDescription(
        key="impedance_ohm",
        name="Impedance",
        icon="mdi:lightning-bolt",
        native_unit_of_measurement=UNIT_OHM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        optional=True,
        value_fn=lambda m: (
            m.body_composition.impedance_ohm if m.body_composition else None
        ),
    ),
    ScaleSensorDescription(
        key="basal_metabolism_kj",
        name="Basal Metabolic Rate",
        icon="mdi:fire",
        native_unit_of_measurement=UNIT_KJ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        optional=True,
        value_fn=lambda m: (
            m.body_composition.basal_metabolism_kj if m.body_composition else None
        ),
    ),
)


# ── Platform setup ─────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ScaleCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ScaleSensor] = [
        ScaleSensor(coordinator, entry, desc)
        for desc in (*_WEIGHT_DESCRIPTIONS, *_BCM_DESCRIPTIONS)
    ]
    async_add_entities(entities)


# ── Entity class ───────────────────────────────────────────────────────────────

class ScaleSensor(CoordinatorEntity[ScaleCoordinator], SensorEntity):
    """A single sensor entity backed by the Scale coordinator."""

    entity_description: ScaleSensorDescription
    _attr_has_entity_name = True
    _attr_attribution = _ATTRIBUTION

    def __init__(
        self,
        coordinator: ScaleCoordinator,
        entry: ConfigEntry,
        description: ScaleSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.device_name,
            manufacturer="Bluetooth SIG",
            model="Weight Scale (0x181D / 0x181B)",
        )

    @property
    def native_value(self) -> Any:
        m = self.coordinator.data
        if m is None:
            return None
        return self.entity_description.value_fn(m)

    @property
    def available(self) -> bool:
        if not super().available or self.coordinator.data is None:
            return False
        if self.entity_description.optional:
            return self.entity_description.value_fn(self.coordinator.data) is not None
        return True

    @property
    def extra_state_attributes(self) -> dict:
        m = self.coordinator.data
        if m is None:
            return {}
        attrs: dict = {}
        if m.weight:
            attrs["weight_unit_from_device"] = m.weight.unit
            if m.weight.raw:
                attrs["weight_raw_hex"] = m.weight.raw.hex()
        if m.body_composition:
            attrs["bcm_raw_hex"] = m.body_composition.raw.hex()
            if m.body_composition.timestamp:
                attrs["bcm_timestamp"] = m.body_composition.timestamp.isoformat()
        if m.timestamp:
            attrs["device_timestamp"] = m.timestamp.isoformat()
        return attrs
