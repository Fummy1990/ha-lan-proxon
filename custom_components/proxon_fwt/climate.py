"""Climate entities: one per connected room (target / current temperature)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DP_COMPRESSOR_RPM,
    DP_FOUR_WAY_VALVE,
    DP_OPERATION_MODE,
    DP_PTC_RELAY_A,
    DP_PTC_RELAY_B,
    FUNC_LIVING_PTC,
    FUNC_LIVING_TARGET,
    LIVING_TARGET_MAX,
    LIVING_TARGET_MIN,
    LIVING_TARGET_STEP,
    ROOM_OFFSET_MAX,
    ROOM_OFFSET_MIN,
    room_offset_func,
    room_ptc_func,
)
from .coordinator import ProxonCoordinator, ProxonWriteError
from .entity import ProxonRoomEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ProxonCoordinator = entry.runtime_data
    async_add_entities(
        ProxonRoomClimate(coordinator, room.index)
        for room in coordinator.data.rooms.values()
        if room.connected
    )


class ProxonRoomClimate(ProxonRoomEntity, ClimateEntity):
    """Room setpoint. AUTO = the unit decides (heat pump / ventilation); HEAT = electric heater (PTC)
    enabled for this room in addition. The PTC switch entity stays available as well."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_precision = PRECISION_TENTHS
    _attr_name = None  # use the room device name

    def __init__(self, coordinator: ProxonCoordinator, room_index: int) -> None:
        super().__init__(coordinator, room_index, "climate")
        self._attr_name = None  # entity carries the room device name
        if room_index == 0:
            self._attr_min_temp = LIVING_TARGET_MIN
            self._attr_max_temp = LIVING_TARGET_MAX
            self._attr_target_temperature_step = LIVING_TARGET_STEP

    @property
    def min_temp(self) -> float:
        if self.room_index == 0:
            return LIVING_TARGET_MIN
        mean = self.room.mean
        return float(mean + ROOM_OFFSET_MIN) if mean is not None else LIVING_TARGET_MIN

    @property
    def max_temp(self) -> float:
        if self.room_index == 0:
            return LIVING_TARGET_MAX
        mean = self.room.mean
        return float(mean + ROOM_OFFSET_MAX) if mean is not None else LIVING_TARGET_MAX

    @property
    def target_temperature_step(self) -> float:
        return LIVING_TARGET_STEP if self.room_index == 0 else 1.0

    @property
    def current_temperature(self) -> float | None:
        return self.room.current

    @property
    def target_temperature(self) -> float | None:
        return self.room.target

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self.room.ptc else HVACMode.AUTO

    @property
    def hvac_action(self) -> HVACAction | None:
        """PTC enabled and a heating module relay active -> heating; otherwise the unit's current
        operation (DP0:26: 1 heating, 2 cooling); fallback compressor + four-way valve; else idle."""
        data = self.coordinator.data
        ptc_relay = bool(data.dp.get(DP_PTC_RELAY_A)) or bool(data.dp.get(DP_PTC_RELAY_B))
        if self.room.ptc and ptc_relay:
            return HVACAction.HEATING
        op = data.dp.get(DP_OPERATION_MODE)
        if op == 1:
            return HVACAction.HEATING
        if op == 2:
            return HVACAction.COOLING
        if op is None and data.dp.get(DP_COMPRESSOR_RPM):
            return HVACAction.COOLING if data.dp.get(DP_FOUR_WAY_VALVE) == 1 else HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        r = self.room
        return {
            "room_index": r.index,
            "ptc_enabled": r.ptc,
            "offset": r.offset,
            "reference_temperature": r.mean,
        }

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """HEAT enables the room's electric heater (PTC), AUTO disables it (same guard as the switch)."""
        if hvac_mode not in (HVACMode.AUTO, HVACMode.HEAT):
            raise ProxonWriteError("Nur Auto (Anlage entscheidet) oder Heizen (PTC-Freigabe) möglich")
        func = FUNC_LIVING_PTC if self.room_index == 0 else room_ptc_func(self.room_index)
        await self.coordinator.async_write(func, 1 if hvac_mode == HVACMode.HEAT else 0)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        if self.room_index == 0:
            value = int(round(float(temp) * 2) * 50)  # 0.5 K steps, °C × 100
            if not LIVING_TARGET_MIN * 100 <= value <= LIVING_TARGET_MAX * 100:
                raise ProxonWriteError(f"Sollwert {temp} °C außerhalb {LIVING_TARGET_MIN}–{LIVING_TARGET_MAX} °C")
            await self.coordinator.async_write(FUNC_LIVING_TARGET, value)
            return
        mean = self.room.mean
        if mean is None:
            raise ProxonWriteError("Referenztemperatur des Raums unbekannt")
        offset = int(round(float(temp))) - mean
        if not ROOM_OFFSET_MIN <= offset <= ROOM_OFFSET_MAX:
            raise ProxonWriteError(
                f"Sollwert {temp} °C außerhalb {mean + ROOM_OFFSET_MIN}–{mean + ROOM_OFFSET_MAX} °C"
            )
        await self.coordinator.async_write(room_offset_func(self.room_index), offset)
