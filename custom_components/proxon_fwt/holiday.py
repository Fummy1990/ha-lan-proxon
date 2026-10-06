"""Holiday mode as implemented by the original app's handleModePress (without raw mode 5).

Entering: fan level 1, living-room and zone-2 target 18 °C, every room offset to its
minimum (-3 K), all room PTC enables off, T300 hot-water preparation off.
Leaving: the values saved when the mode was entered are written back
(T300 on, then heater enable, then the rest) – mirroring the app's exit sequence.
The raw operating mode 5 is deliberately NOT written (unknown controller effect;
the manufacturer disabled the holiday mode in app 1.7.4).
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    FUNC_FAN_LEVEL,
    FUNC_HEATER_ENABLE,
    FUNC_LIVING_PTC,
    FUNC_LIVING_TARGET,
    FUNC_T300_MODE,
    FUNC_ZONE2_TARGET,
    ROOM_OFFSET_MIN,
    WriteFunc,
    room_offset_func,
    room_ptc_func,
)
from .coordinator import ProxonCoordinator, ProxonWriteError

_LOGGER = logging.getLogger(__name__)
HOLIDAY_TARGET = 18.0
# On leaving, the heater is re-enabled right after T300 start (as the app does) - relays may be active.
_HEATER_RESTORE = replace(FUNC_HEATER_ENABLE, policy="t300")


class HolidayController:
    """Applies / reverts the holiday profile and persists the saved values."""

    def __init__(self, hass: HomeAssistant, coordinator: ProxonCoordinator, entry_id: str) -> None:
        self.coordinator = coordinator
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.holiday")
        self._state: dict[str, Any] = {"active": False, "saved": {}}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data:
            self._state = data

    @property
    def active(self) -> bool:
        return bool(self._state.get("active"))

    def _funcs(self) -> list[WriteFunc]:
        data = self.coordinator.data
        funcs = [FUNC_FAN_LEVEL, FUNC_LIVING_TARGET, FUNC_ZONE2_TARGET, FUNC_LIVING_PTC]
        for room in data.rooms.values():
            if room.connected and not room.is_master:
                funcs += [room_offset_func(room.index), room_ptc_func(room.index)]
        return funcs

    async def async_enter(self) -> None:
        async with self._lock:
            if self.active:
                return
            data = self.coordinator.data
            funcs = self._funcs()
            if self._state.get("saved"):
                saved = self._state["saved"]  # keep the snapshot of an earlier, failed attempt
            else:
                saved = {f.key: data.sp.get(f.read) for f in funcs + [FUNC_T300_MODE, FUNC_HEATER_ENABLE]}
                if any(v is None for v in saved.values()):
                    raise ProxonWriteError("Urlaubsmodus: aktuelle Werte noch nicht vollständig gelesen")
            # Mark active before writing: if a write fails, switching off restores the snapshot.
            self._state = {"active": True, "saved": saved}
            await self._store.async_save(self._state)

            main: list[tuple[WriteFunc, int]] = [
                (FUNC_FAN_LEVEL, 1),
                (FUNC_LIVING_TARGET, int(HOLIDAY_TARGET * 100)),
                (FUNC_ZONE2_TARGET, int(HOLIDAY_TARGET * 100)),
                (FUNC_LIVING_PTC, 0),
            ]
            for room in data.rooms.values():
                if room.connected and not room.is_master:
                    main.append((room_offset_func(room.index), ROOM_OFFSET_MIN))
                    main.append((room_ptc_func(room.index), 0))
            await self.coordinator.async_write_batch(main)
            await self.coordinator.async_write_batch([(FUNC_T300_MODE, 0)])
            _LOGGER.info("Holiday mode entered; %d values saved", len(saved))

    async def async_leave(self) -> None:
        async with self._lock:
            saved: dict[str, int] = self._state.get("saved") or {}
            if not saved:
                self._state = {"active": False, "saved": {}}
                await self._store.async_save(self._state)
                return
            # App exit order: T300 on, then (after 2 s) heater enable, then the rest.
            await self.coordinator.async_write_batch([(FUNC_T300_MODE, saved.get(FUNC_T300_MODE.key, 1))])
            await asyncio.sleep(2.0)
            await self.coordinator.async_write_batch([(_HEATER_RESTORE, saved.get(FUNC_HEATER_ENABLE.key, 1))])
            rest = [(f, saved[f.key]) for f in self._funcs() if f.key in saved]
            await self.coordinator.async_write_batch(rest)
            self._state = {"active": False, "saved": {}}
            await self._store.async_save(self._state)
            _LOGGER.info("Holiday mode left; %d values restored", len(rest) + 2)
