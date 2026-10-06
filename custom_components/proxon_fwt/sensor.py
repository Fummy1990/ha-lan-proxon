"""Sensor entities (only verified read points are exposed)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfPressure,
    UnitOfRatio,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    SCHEDULE_DAY_NAMES,
    SCHEDULE_LEVEL_OPTIONS,
    SCHEDULE_PHASES,
    schedule_device_identifier,
    DP_CO2,
    DP_COMPRESSOR_RPM,
    DP_COMPRESSOR_STATUS,
    DP_DAMPER_POSITION,
    DP_FAN_STATUS,
    DP_HP_RELAY_WORD,
    DP_P14_LOW_PRESSURE,
    DP_P19_DEFROST_DIFF,
    DP_T1_SUPPLY,
    DP_T3_FRESH,
    DP_T4_EXHAUST_OUT,
    DP_T7_EXTRACT,
    DP_T12_PRE_CONDENSER,
    DP_T13_COMPRESSOR_MAIN,
    DP_T14_SUCTION_GAS,
    DP_VALVE_COOLING,
    DP_VALVE_HEATING,
    DP_VALVE_PREHEAT,
    DP_ZONE2_CURRENT,
    SP_FILTER_INTERVAL,
    SP_FILTER_RUNTIME,
    SP_OPERATING_HOURS,
    DP_FAN_LEVEL_ACTUAL,
    DP_FIRMWARE_MAIN,
    DP_FIRMWARE_T300,
    DP_FOUR_WAY_VALVE,
    DP_HUMIDITY,
    DP_PV_LEVEL,
    DP_RPM_EXHAUST,
    DP_RPM_SUPPLY,
    DP_T300_COMPRESSOR,
    DP_T300_EVAP,
    DP_T300_FAN_RPM,
    DP_T300_PRE_EVAP,
    DP_T300_SUCTION,
    DP_WATER_BOTTOM,
    DP_WATER_MIDDLE,
    FAULT_POINTS,
    SP_BOOST_E1E2,
    SP_BOOST_REMAINING,
    SP_CLOCK,
    SP_GLOBAL_PTC,
    SP_PV_MODE,
)
from .coordinator import ProxonCoordinator, ProxonData
from .entity import ProxonEntity, ProxonRoomEntity
from .schedule import Phase, read_phase


def _t300(raw: int | None) -> float | None:
    return None if raw in (None, 0) else (raw - 1000) / 10


def _dp(point: tuple[int, int], scale: float = 1) -> Callable[[ProxonData], Any]:
    def get(data: ProxonData) -> Any:
        v = data.dp.get(point)
        return None if v is None else (v / scale if scale != 1 else v)
    return get


def _sp(point: tuple[int, int], scale: float = 1) -> Callable[[ProxonData], Any]:
    def get(data: ProxonData) -> Any:
        v = data.sp.get(point)
        return None if v is None else (v / scale if scale != 1 else v)
    return get


def _clock(data: ProxonData) -> str | None:
    vals = [data.sp.get(p) for p in SP_CLOCK]
    if any(v is None for v in vals):
        return None
    dst, minute, hour, _wd, day, month, year = vals
    return f"20{year:02d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}{' DST' if dst else ''}"


def _clock_offset(data: ProxonData) -> int | None:
    """Device clock minus Home Assistant local time, in minutes."""
    vals = [data.sp.get(p) for p in SP_CLOCK]
    if any(v is None for v in vals):
        return None
    _dst, minute, hour, _wd, day, month, year = vals
    try:
        from datetime import datetime
        dev = datetime(2000 + year, month, day, hour, minute)
    except ValueError:
        return None
    now = dt_util.now().replace(tzinfo=None, second=0, microsecond=0)
    return round((dev - now).total_seconds() / 60)


def _faults(data: ProxonData) -> int:
    return sum(1 for p in FAULT_POINTS if data.dp.get(p))


def _fault_attrs(data: ProxonData) -> dict[str, Any]:
    return {f"DP{o}:{i}": data.dp.get((o, i)) for o, i in FAULT_POINTS}


@dataclass(frozen=True, kw_only=True)
class ProxonSensorDescription(SensorEntityDescription):
    value: Callable[[ProxonData], Any]
    attrs: Callable[[ProxonData], dict[str, Any]] | None = None


TEMP = dict(device_class=SensorDeviceClass.TEMPERATURE, native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1)
RPM = dict(native_unit_of_measurement=REVOLUTIONS_PER_MINUTE, state_class=SensorStateClass.MEASUREMENT, icon="mdi:fan")
DIAG = dict(entity_category=EntityCategory.DIAGNOSTIC)

SENSORS: tuple[ProxonSensorDescription, ...] = (
    # Main unit temperatures (app model, /100)
    ProxonSensorDescription(key="t1_supply", name="T1 Zuluft", value=_dp(DP_T1_SUPPLY, 100), **TEMP),
    ProxonSensorDescription(key="t3_fresh", name="T3 Frischluft", value=_dp(DP_T3_FRESH, 100), **TEMP),
    ProxonSensorDescription(key="t4_exhaust_out", name="T4 Fortluft", value=_dp(DP_T4_EXHAUST_OUT, 100), **TEMP),
    ProxonSensorDescription(key="t7_extract", name="T7 Abluft", value=_dp(DP_T7_EXTRACT, 100), **TEMP),
    ProxonSensorDescription(key="zone2_current", name="Zone 2 Temperatur", value=_dp(DP_ZONE2_CURRENT, 100), **TEMP),
    ProxonSensorDescription(key="t12_pre_condenser", name="T12 vor Kondensator", value=_dp(DP_T12_PRE_CONDENSER, 100), **TEMP, **DIAG),
    ProxonSensorDescription(key="t13_compressor_main", name="T13 Kompressor (Haupt)", value=_dp(DP_T13_COMPRESSOR_MAIN, 100), **TEMP, **DIAG),
    ProxonSensorDescription(key="t14_suction_gas", name="T14 Sauggas nach Kondensator", value=_dp(DP_T14_SUCTION_GAS, 100), **TEMP, **DIAG),
    ProxonSensorDescription(key="p14_low_pressure", name="P14 ND Verdampfer", value=_dp(DP_P14_LOW_PRESSURE, 1000), icon="mdi:gauge",
                            device_class=SensorDeviceClass.PRESSURE, native_unit_of_measurement=UnitOfPressure.BAR,
                            state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2, **DIAG),
    ProxonSensorDescription(key="p19_defrost_diff", name="P19 Druckdifferenz Abtau", value=_dp(DP_P19_DEFROST_DIFF, 100), icon="mdi:gauge",
                            state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2, entity_registry_enabled_default=False, **DIAG),
    # Counters / filter (app model)
    ProxonSensorDescription(key="operating_hours", name="Betriebsstunden", value=lambda d: None if d.sp.get(SP_OPERATING_HOURS) is None else d.sp.get(SP_OPERATING_HOURS) & 0xFFFF,
                            native_unit_of_measurement=UnitOfTime.HOURS, state_class=SensorStateClass.TOTAL_INCREASING, icon="mdi:counter", **DIAG),
    ProxonSensorDescription(key="filter_runtime", name="Filter Laufzeit", value=_sp(SP_FILTER_RUNTIME), icon="mdi:air-filter", **DIAG),
    ProxonSensorDescription(key="filter_interval", name="Filterwechselintervall", value=_sp(SP_FILTER_INTERVAL), icon="mdi:air-filter", **DIAG),
    # States (app model)
    ProxonSensorDescription(key="damper_position", name="Schieberposition", value=_dp(DP_DAMPER_POSITION), icon="mdi:valve", **DIAG),
    ProxonSensorDescription(key="valve_heating", name="E-Ventil Heizung Position", value=_dp(DP_VALVE_HEATING), icon="mdi:valve", **DIAG),
    ProxonSensorDescription(key="valve_preheat", name="E-Ventil Vorwärme Position", value=_dp(DP_VALVE_PREHEAT), icon="mdi:valve", **DIAG),
    ProxonSensorDescription(key="valve_cooling", name="E-Ventil Kühlung Position", value=_dp(DP_VALVE_COOLING), icon="mdi:valve", **DIAG),
    ProxonSensorDescription(key="compressor_status", name="Status Kompressor", value=_dp(DP_COMPRESSOR_STATUS), icon="mdi:heat-pump", **DIAG),
    ProxonSensorDescription(key="fan_status", name="Status Ventilatoren", value=_dp(DP_FAN_STATUS), icon="mdi:fan", **DIAG),
    ProxonSensorDescription(key="hp_relay_word", name="WP-Relaiswort", value=_dp(DP_HP_RELAY_WORD), icon="mdi:electric-switch", entity_registry_enabled_default=False, **DIAG),
    ProxonSensorDescription(key="rpm_supply", name="Drehzahl Zuluft", value=_dp(DP_RPM_SUPPLY), **RPM),
    ProxonSensorDescription(key="rpm_exhaust", name="Drehzahl Abluft", value=_dp(DP_RPM_EXHAUST), **RPM),
    ProxonSensorDescription(key="fan_level_actual", name="Lüfterstufe Ist", value=_dp(DP_FAN_LEVEL_ACTUAL), icon="mdi:fan"),
    ProxonSensorDescription(key="humidity", name="Luftfeuchte Abluft", value=_dp(DP_HUMIDITY),
                            device_class=SensorDeviceClass.HUMIDITY, native_unit_of_measurement=PERCENTAGE,
                            state_class=SensorStateClass.MEASUREMENT),
    ProxonSensorDescription(key="co2", name="CO₂ (roh)", value=_dp(DP_CO2), native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
                            icon="mdi:molecule-co2", entity_registry_enabled_default=False),
    ProxonSensorDescription(key="compressor_rpm", name="Kompressor Drehzahl", value=_dp(DP_COMPRESSOR_RPM), **RPM),
    ProxonSensorDescription(key="four_way_valve", name="Vierwegeventil",
                            value=lambda d: {0: "Heizen", 1: "Kühlen"}.get(d.dp.get(DP_FOUR_WAY_VALVE)), icon="mdi:valve"),
    ProxonSensorDescription(key="boost_remaining", name="Intensivlüftung Restzeit", value=_sp(SP_BOOST_REMAINING),
                            native_unit_of_measurement=UnitOfTime.MINUTES, icon="mdi:timer-sand"),
    ProxonSensorDescription(key="water_middle", name="Warmwasser Mitte", value=lambda d: _t300(d.dp.get(DP_WATER_MIDDLE)), **TEMP),
    ProxonSensorDescription(key="water_bottom", name="Warmwasser Unten", value=lambda d: _t300(d.dp.get(DP_WATER_BOTTOM)), **TEMP),
    ProxonSensorDescription(key="t300_pre_evap", name="T300 vor Verdampfer", value=lambda d: _t300(d.dp.get(DP_T300_PRE_EVAP)), **TEMP, **DIAG),
    ProxonSensorDescription(key="t300_evap", name="T300 Verdampfer", value=lambda d: _t300(d.dp.get(DP_T300_EVAP)), **TEMP, **DIAG),
    ProxonSensorDescription(key="t300_compressor", name="T300 Kompressor", value=lambda d: _t300(d.dp.get(DP_T300_COMPRESSOR)), **TEMP, **DIAG),
    ProxonSensorDescription(key="t300_suction", name="T300 Saugleitung", value=lambda d: _t300(d.dp.get(DP_T300_SUCTION)), **TEMP, **DIAG),
    ProxonSensorDescription(key="t300_fan_rpm", name="T300 Ventilator", value=_dp(DP_T300_FAN_RPM), **RPM, **DIAG),
    ProxonSensorDescription(key="pv_mode", name="PV-Modus", value=_sp(SP_PV_MODE), icon="mdi:solar-power", **DIAG),
    ProxonSensorDescription(key="pv_level", name="PV-Eingangspegel", value=_dp(DP_PV_LEVEL, 10), icon="mdi:solar-power",
                            entity_registry_enabled_default=False, **DIAG),
    ProxonSensorDescription(key="boost_e1e2", name="Intensivlüftung Dauer E1/E2", value=_sp(SP_BOOST_E1E2),
                            native_unit_of_measurement=UnitOfTime.MINUTES, icon="mdi:timer", **DIAG),
    ProxonSensorDescription(key="global_ptc", name="PTC-Freigabe global", value=_sp(SP_GLOBAL_PTC), icon="mdi:radiator", **DIAG),
    ProxonSensorDescription(key="firmware_main", name="Firmware Hauptplatine", value=_dp(DP_FIRMWARE_MAIN, 10), icon="mdi:chip", **DIAG),
    ProxonSensorDescription(key="firmware_t300", name="Firmware T300", value=_dp(DP_FIRMWARE_T300, 10), icon="mdi:chip", **DIAG),
    ProxonSensorDescription(key="clock", name="Geräteuhr", value=_clock, icon="mdi:clock-outline", **DIAG),
    ProxonSensorDescription(key="clock_offset", name="Uhrabweichung", value=_clock_offset, native_unit_of_measurement=UnitOfTime.MINUTES,
                            icon="mdi:clock-alert-outline", state_class=SensorStateClass.MEASUREMENT, **DIAG),
    ProxonSensorDescription(key="faults", name="Fehlerwörter aktiv", value=_faults, attrs=_fault_attrs, icon="mdi:alert-circle", **DIAG),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ProxonCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [ProxonSensor(coordinator, d) for d in SENSORS]
    entities.append(ProxonLastWriteError(coordinator))
    entities += [ProxonScheduleDaySensor(coordinator, day) for day in range(len(SCHEDULE_DAY_NAMES))]
    for room in coordinator.data.rooms.values():
        if room.connected:
            entities.append(ProxonRoomTemperature(coordinator, room.index))
    async_add_entities(entities)


class ProxonSensor(ProxonEntity, SensorEntity):
    entity_description: ProxonSensorDescription

    def __init__(self, coordinator: ProxonCoordinator, description: ProxonSensorDescription) -> None:
        super().__init__(coordinator, description.key, description.name)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs:
            return self.entity_description.attrs(self.coordinator.data)
        return None


class ProxonLastWriteError(ProxonEntity, SensorEntity):
    """Diagnostic: last failed write (after all automatic retries)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:alert-octagon-outline"

    def __init__(self, coordinator: ProxonCoordinator) -> None:
        super().__init__(coordinator, "last_write_error", "Letzter Schreibfehler")

    @property
    def native_value(self) -> str:
        return (self.coordinator.last_write_error or "keiner")[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "timestamp": self.coordinator.last_write_error_at.isoformat() if self.coordinator.last_write_error_at else None,
            "error_count": self.coordinator.write_error_count,
        }


class ProxonScheduleDaySensor(ProxonEntity, SensorEntity):
    """One line per weekday on the 'Proxon Zeitprogramm' device: all three phases at a glance.

    State: "06:00–08:00 Stufe 2 · 12:00–13:00 Stufe 1 · 17:00–22:00 Stufe 3" (inactive phase = "Aus").
    """

    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: ProxonCoordinator, day: int) -> None:
        super().__init__(
            coordinator, f"schedule_day_{day}", translation_key="schedule_day",
            placeholders={"day": SCHEDULE_DAY_NAMES[day]},
        )
        self.day = day

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={schedule_device_identifier(self._device_id)})

    def _phases(self) -> list[Phase | None]:
        return [read_phase(self.coordinator.data, self.day, p) for p in range(SCHEDULE_PHASES)]

    @staticmethod
    def _fmt(p: Phase | None) -> str:
        if p is None:
            return "?"
        if not p.enabled:
            return "Aus"
        return f"{p.start_hour:02d}:{p.start_minute:02d}–{p.end_hour:02d}:{p.end_minute:02d} {SCHEDULE_LEVEL_OPTIONS[p.level]}"

    @property
    def native_value(self) -> str:
        return " · ".join(self._fmt(p) for p in self._phases())[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"weekday": SCHEDULE_DAY_NAMES[self.day]}
        for i, p in enumerate(self._phases(), start=1):
            if p is None:
                continue
            attrs[f"phase_{i}"] = {
                "start": f"{p.start_hour:02d}:{p.start_minute:02d}",
                "end": f"{p.end_hour:02d}:{p.end_minute:02d}",
                "level": p.level,
                "active": p.enabled,
            }
        return attrs


class ProxonRoomTemperature(ProxonRoomEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: ProxonCoordinator, room_index: int) -> None:
        super().__init__(coordinator, room_index, "temperature", "Temperatur")

    @property
    def native_value(self) -> float | None:
        return self.room.current
