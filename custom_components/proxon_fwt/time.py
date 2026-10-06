"""Time entities for the weekly ventilation schedule (start/end per day and phase)."""
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SCHEDULE_DAYS, SCHEDULE_PHASES, schedule_device_identifier
from .coordinator import ProxonCoordinator, ProxonWriteError
from .entity import ProxonEntity
from .schedule import Phase, plan_phase_change, read_phase


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ProxonCoordinator = entry.runtime_data
    async_add_entities(
        ProxonScheduleTime(coordinator, day, phase, is_end)
        for day in range(len(SCHEDULE_DAYS))
        for phase in range(SCHEDULE_PHASES)
        for is_end in (False, True)
    )


class ProxonScheduleEntityMixin:
    """Schedule settings live on one device per weekday ("Proxon Zeitprogramm Montag", ...)."""

    day: int

    @property
    def device_info(self) -> DeviceInfo:  # type: ignore[override]
        return DeviceInfo(identifiers={schedule_device_identifier(self._device_id, self.day)})  # type: ignore[attr-defined]


class ProxonScheduleTime(ProxonScheduleEntityMixin, ProxonEntity, TimeEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator: ProxonCoordinator, day: int, phase: int, is_end: bool) -> None:
        kind = "end" if is_end else "start"
        super().__init__(
            coordinator, f"schedule_{day}_{phase}_{kind}",
            translation_key=f"schedule_{kind}",
            placeholders={"phase": str(phase + 1)},
        )
        self.day, self.phase, self.is_end = day, phase, is_end
        if is_end:
            self._attr_icon = "mdi:clock-end"

    @property
    def native_value(self) -> time | None:
        p = read_phase(self.coordinator.data, self.day, self.phase)
        if p is None:
            return None
        return p.end if self.is_end else p.start

    async def async_set_value(self, value: time) -> None:
        old = read_phase(self.coordinator.data, self.day, self.phase)
        if old is None:
            raise ProxonWriteError("Zeitprogramm noch nicht gelesen")
        if self.is_end:
            new = Phase(old.day, old.phase, old.start_hour, old.start_minute, value.hour, value.minute, old.level)
        else:
            new = Phase(old.day, old.phase, value.hour, value.minute, old.end_hour, old.end_minute, old.level)
        writes = plan_phase_change(self.coordinator.data, old, new)
        await self.coordinator.async_write_batch(writes)
