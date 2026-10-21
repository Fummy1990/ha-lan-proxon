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
        # T300 D-04 "Legionellenfunktion" (0/1); write verified on a real unit (1 -> 0 -> 1, read-back ok).
        ProxonSwitch(coordinator, FUNC_LEGIONELLA, "Legionellenfunktion", "mdi:bacteria", EntityCategory.CONFIG),
        # The unit has no global PTC enable reachable over LAN (service parameter SP1:115 is read-only
        # via Nabto); this virtual switch writes the PTC enable of every connected room in one session.
        ProxonAllRoomsPtcSwitch(coordinator),
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
        category: EntityCategory | None = None, enabled: bool = True, key_suffix: str = "",
    ) -> None:
        super().__init__(coordinator, func.key + key_suffix, name)
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


class ProxonAllRoomsPtcSwitch(ProxonEntity, SwitchEntity):
    """Virtual 'all rooms' electric heating enable: on = every connected room's PTC enable is on.

    Turning on/off writes the PTC enable of all connected rooms (living room via SP0:187, secondary
    rooms via SP1:(42+i)) in ONE Nabto session with the usual PTC guard; rooms already in the target
    state are skipped by the coordinator.
    """

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:radiator"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ProxonCoordinator) -> None:
        super().__init__(coordinator, "all_rooms_ptc", "Elektroheizung Freigabe (alle Räume)")

    def _connected_rooms(self) -> list:
        return [r for r in self.coordinator.data.rooms.values() if r.connected]

    @property
    def is_on(self) -> bool | None:
        rooms = self._connected_rooms()
        return all(r.ptc for r in rooms) if rooms else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rooms = self._connected_rooms()
        return {
            "rooms_enabled": [r.name for r in rooms if r.ptc],
            "rooms_disabled": [r.name for r in rooms if not r.ptc],
        }

    async def _write_all(self, on: bool) -> None:
        items = [
            (FUNC_LIVING_PTC if r.is_master else room_ptc_func(r.index), 1 if on else 0)
            for r in self._connected_rooms()
        ]
        await self.coordinator.async_write_batch(items)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write_all(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write_all(False)


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
