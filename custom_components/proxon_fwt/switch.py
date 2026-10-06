"""Switch entities (verified writes with read-back)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    FUNC_BOOST,
    FUNC_COOLING,
    FUNC_HEATER_ENABLE,
    FUNC_LEGIONELLA,
    FUNC_LIVING_PTC,
    FUNC_T300_MODE,
    WriteFunc,
    room_ptc_func,
)
from .coordinator import ProxonCoordinator
from .entity import ProxonEntity, ProxonRoomEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ProxonCoordinator = entry.runtime_data
    entities: list[SwitchEntity] = [
        ProxonSwitch(coordinator, FUNC_COOLING, "Kühlfreigabe", "mdi:snowflake"),
        ProxonSwitch(coordinator, FUNC_BOOST, "Intensivlüftung", "mdi:fan-plus"),
        ProxonSwitch(coordinator, FUNC_HEATER_ENABLE, "E-Heizstab Freigabe", "mdi:water-boiler", EntityCategory.CONFIG),
        ProxonSwitch(coordinator, FUNC_T300_MODE, "Warmwasserbereitung (T300)", "mdi:water-boiler-auto", EntityCategory.CONFIG),
        ProxonSwitch(coordinator, FUNC_LEGIONELLA, "Legionellenfunktion", "mdi:bacteria", EntityCategory.CONFIG, enabled=False),
    ]
    for room in coordinator.data.rooms.values():
        if room.connected:
            entities.append(ProxonRoomPtcSwitch(coordinator, room.index))
    entities.append(ProxonHolidaySwitch(coordinator))
    async_add_entities(entities)


class ProxonSwitch(ProxonEntity, SwitchEntity):
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self, coordinator: ProxonCoordinator, func: WriteFunc, name: str, icon: str,
        category: EntityCategory | None = None, enabled: bool = True,
    ) -> None:
        super().__init__(coordinator, func.key, name)
        self._func = func
        self._attr_icon = icon
        self._attr_entity_category = category
        self._attr_entity_registry_enabled_default = enabled

    @property
    def is_on(self) -> bool | None:
        v = self.coordinator.data.sp.get(self._func.read)
        return None if v is None else bool(v)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_write_switch(self._func, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_write_switch(self._func, False)


class ProxonRoomPtcSwitch(ProxonRoomEntity, SwitchEntity):
    """Electric heater (PTC) enable per room."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator: ProxonCoordinator, room_index: int) -> None:
        super().__init__(coordinator, room_index, "ptc", "Elektroheizung (PTC)")
        self._func = FUNC_LIVING_PTC if room_index == 0 else room_ptc_func(room_index)

    @property
    def is_on(self) -> bool:
        return self.room.ptc

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_write_switch(self._func, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_write_switch(self._func, False)


class ProxonHolidaySwitch(ProxonEntity, SwitchEntity):
    """Holiday profile (app side-writes without raw mode 5); restores saved values on off."""

    _attr_icon = "mdi:beach"

    def __init__(self, coordinator: ProxonCoordinator) -> None:
        super().__init__(coordinator, "holiday", "Urlaubsmodus")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.holiday and self.coordinator.holiday.active)

    @property
    def extra_state_attributes(self) -> dict:
        h = self.coordinator.holiday
        return {"saved_values": (h._state.get("saved") if h else None) or {}}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.holiday.async_enter()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.holiday.async_leave()
        self.async_write_ha_state()
