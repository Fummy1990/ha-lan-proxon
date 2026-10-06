"""Binary sensors."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DP_BYPASS,
    DP_GEOTHERMAL_RELAY,
    DP_PTC_RELAY_A,
    DP_SOLENOID_VALVE,
    SP_HAS_CO2,
    SP_SCHEDULE_GLOBAL,
    DP_PTC_RELAY_B,
    DP_PV_FLAG_HEATER,
    DP_PV_FLAG_HP,
    DP_T300_FAULT,
    DP_T300_RELAYS,
    FAULT_POINTS,
    SP_PV_ENABLE,
)
from .coordinator import ProxonCoordinator, ProxonData
from .entity import ProxonEntity


@dataclass(frozen=True, kw_only=True)
class ProxonBinaryDescription(BinarySensorEntityDescription):
    value: Callable[[ProxonData], bool | None]


def _dp_on(point: tuple[int, int]) -> Callable[[ProxonData], bool | None]:
    def get(data: ProxonData) -> bool | None:
        v = data.dp.get(point)
        return None if v is None else bool(v)
    return get


BINARY_SENSORS: tuple[ProxonBinaryDescription, ...] = (
    ProxonBinaryDescription(key="bypass", name="Bypass", device_class=BinarySensorDeviceClass.OPENING, value=_dp_on(DP_BYPASS)),
    ProxonBinaryDescription(key="geothermal_relay", name="Erdwärme", device_class=BinarySensorDeviceClass.RUNNING, value=_dp_on(DP_GEOTHERMAL_RELAY)),
    ProxonBinaryDescription(key="solenoid_valve", name="Magnetventil", device_class=BinarySensorDeviceClass.OPENING, value=_dp_on(DP_SOLENOID_VALVE), entity_category=EntityCategory.DIAGNOSTIC),
    ProxonBinaryDescription(key="schedule_active", name="Zeitprogramm Lüftung aktiv", icon="mdi:calendar-clock",
                            value=lambda d: None if d.sp.get(SP_SCHEDULE_GLOBAL) is None else bool(d.sp.get(SP_SCHEDULE_GLOBAL))),
    ProxonBinaryDescription(key="has_co2", name="CO₂-Sensor vorhanden", icon="mdi:molecule-co2",
                            value=lambda d: None if d.sp.get(SP_HAS_CO2) is None else bool(d.sp.get(SP_HAS_CO2)), entity_category=EntityCategory.DIAGNOSTIC),
    ProxonBinaryDescription(
        key="fault", name="Störung", device_class=BinarySensorDeviceClass.PROBLEM,
        value=lambda d: any(d.dp.get(p) for p in FAULT_POINTS) or bool(d.dp.get(DP_T300_FAULT)),
    ),
    ProxonBinaryDescription(
        key="ptc_relay", name="PTC-Relais aktiv", device_class=BinarySensorDeviceClass.HEAT,
        value=lambda d: bool(d.dp.get(DP_PTC_RELAY_A)) or bool(d.dp.get(DP_PTC_RELAY_B)),
    ),
    ProxonBinaryDescription(key="t300_compressor_relay", name="T300 Kompressor", device_class=BinarySensorDeviceClass.RUNNING,
                            value=_dp_on(DP_T300_RELAYS[0]), entity_category=EntityCategory.DIAGNOSTIC),
    ProxonBinaryDescription(key="t300_solar_relay", name="T300 Solar", device_class=BinarySensorDeviceClass.RUNNING,
                            value=_dp_on(DP_T300_RELAYS[1]), entity_category=EntityCategory.DIAGNOSTIC),
    ProxonBinaryDescription(key="t300_heater_relay", name="E-Heizstab aktiv", device_class=BinarySensorDeviceClass.HEAT,
                            value=_dp_on(DP_T300_RELAYS[2])),
    ProxonBinaryDescription(key="t300_fan_relay", name="T300 Ventilator", device_class=BinarySensorDeviceClass.RUNNING,
                            value=_dp_on(DP_T300_RELAYS[3]), entity_category=EntityCategory.DIAGNOSTIC),
    ProxonBinaryDescription(key="pv_enable", name="PV-Funktion freigegeben", icon="mdi:solar-power",
                            value=lambda d: None if d.sp.get(SP_PV_ENABLE) is None else bool(d.sp.get(SP_PV_ENABLE)),
                            entity_category=EntityCategory.DIAGNOSTIC),
    ProxonBinaryDescription(key="pv_flag_heater", name="PV-Signal Heizstab", icon="mdi:solar-power",
                            value=_dp_on(DP_PV_FLAG_HEATER), entity_category=EntityCategory.DIAGNOSTIC),
    ProxonBinaryDescription(key="pv_flag_hp", name="PV-Signal Wärmepumpe", icon="mdi:solar-power",
                            value=_dp_on(DP_PV_FLAG_HP), entity_category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ProxonCoordinator = entry.runtime_data
    async_add_entities(ProxonBinarySensor(coordinator, d) for d in BINARY_SENSORS)


class ProxonBinarySensor(ProxonEntity, BinarySensorEntity):
    entity_description: ProxonBinaryDescription

    def __init__(self, coordinator: ProxonCoordinator, description: ProxonBinaryDescription) -> None:
        super().__init__(coordinator, description.key, description.name)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.coordinator.data)
