"""Select entities: operating mode and manual fan level."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.const import EntityCategory

from .const import (
    FAN_LEVEL_OPTIONS,
    FUNC_FAN_LEVEL,
    FUNC_MODE,
    MODE_OPTIONS,
    SCHEDULE_DAYS,
    SCHEDULE_LEVEL_OPTIONS,
    SCHEDULE_PHASES,
    WriteFunc,
)
from .coordinator import ProxonCoordinator, ProxonWriteError
from .entity import ProxonEntity
from .schedule import Phase, plan_phase_change, read_phase
from .time import ProxonScheduleEntityMixin


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ProxonCoordinator = entry.runtime_data
    entities: list[SelectEntity] = [
        ProxonSelect(coordinator, FUNC_MODE, "Betriebsart", MODE_OPTIONS, "mdi:home-thermometer"),
        ProxonSelect(coordinator, FUNC_FAN_LEVEL, "Lüfterstufe", FAN_LEVEL_OPTIONS, "mdi:fan"),
    ]
    entities += [
        ProxonScheduleLevel(coordinator, day, phase)
        for day in range(len(SCHEDULE_DAYS))
        for phase in range(SCHEDULE_PHASES)
    ]
    async_add_entities(entities)


class ProxonSelect(ProxonEntity, SelectEntity):
    def __init__(
        self, coordinator: ProxonCoordinator, func: WriteFunc, name: str, options: dict[int, str], icon: str
    ) -> None:
        super().__init__(coordinator, func.key, name)
        self._func = func
        self._map = options
        self._attr_options = list(options.values())
        self._attr_icon = icon

    @property
    def current_option(self) -> str | None:
        v = self.coordinator.data.sp.get(self._func.read)
        if v is None:
            return None
        return self._map.get(v, f"Unbekannt ({v})")

    async def async_select_option(self, option: str) -> None:
        for value, label in self._map.items():
            if label == option:
                await self.coordinator.async_write(self._func, value)
                return
        raise ProxonWriteError(f"Ungültige Option {option}")


class ProxonScheduleLevel(ProxonScheduleEntityMixin, ProxonEntity, SelectEntity):
    """Fan level of a schedule phase (Aus = phase inactive)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:fan-clock"
    _attr_options = list(SCHEDULE_LEVEL_OPTIONS.values())

    def __init__(self, coordinator: ProxonCoordinator, day: int, phase: int) -> None:
        super().__init__(
            coordinator, f"schedule_{day}_{phase}_level",
            translation_key="schedule_level", placeholders={"phase": str(phase + 1)},
        )
        self.day, self.phase = day, phase

    @property
    def current_option(self) -> str | None:
        p = read_phase(self.coordinator.data, self.day, self.phase)
        return None if p is None else SCHEDULE_LEVEL_OPTIONS.get(p.level)

    async def async_select_option(self, option: str) -> None:
        level = next((v for v, label in SCHEDULE_LEVEL_OPTIONS.items() if label == option), None)
        if level is None:
            raise ProxonWriteError(f"Ungültige Option {option}")
        old = read_phase(self.coordinator.data, self.day, self.phase)
        if old is None:
            raise ProxonWriteError("Zeitprogramm noch nicht gelesen")
        new = Phase(old.day, old.phase, old.start_hour, old.start_minute, old.end_hour, old.end_minute, level)
        await self.coordinator.async_write_batch(plan_phase_change(self.coordinator.data, old, new))
