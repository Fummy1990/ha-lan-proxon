"""Button entities: copy the Monday schedule to other weekdays."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SCHEDULE_DAY_NAMES, schedule_device_identifier
from .coordinator import ProxonCoordinator, ProxonWriteError
from .entity import ProxonEntity
from .schedule import plan_day_copy

_LOGGER = logging.getLogger(__name__)

WORKDAYS = (1, 2, 3, 4)          # Di–Fr
ALL_OTHER_DAYS = (1, 2, 3, 4, 5, 6)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ProxonCoordinator = entry.runtime_data
    async_add_entities(
        [
            ProxonScheduleCopyButton(coordinator, "schedule_copy_mo_workdays", 0, WORKDAYS),
            ProxonScheduleCopyButton(coordinator, "schedule_copy_mo_all", 0, ALL_OTHER_DAYS),
        ]
    )


async def async_copy_schedule_day(coordinator: ProxonCoordinator, source_day: int, target_days: tuple[int, ...]) -> None:
    """Copy one weekday's three phases to the target days, one Nabto session per day.

    Days that violate a schedule rule (e.g. a phase running right now) are skipped and
    reported together at the end; the other days are still written.
    """
    if coordinator.data is None:
        raise ProxonWriteError("Keine Daten der Anlage")
    problems: list[str] = []
    done: list[str] = []
    for day in target_days:
        if day == source_day:
            continue
        try:
            writes = plan_day_copy(coordinator.data, source_day, day)
            if writes:
                await coordinator.async_write_batch(writes)
            done.append(SCHEDULE_DAY_NAMES[day])
        except ProxonWriteError as err:
            problems.append(f"{SCHEDULE_DAY_NAMES[day]}: {err}")
            _LOGGER.warning("Zeitprogramm %s -> %s nicht übernommen: %s", SCHEDULE_DAY_NAMES[source_day], SCHEDULE_DAY_NAMES[day], err)
    _LOGGER.info("Zeitprogramm %s übernommen für: %s", SCHEDULE_DAY_NAMES[source_day], ", ".join(done) or "–")
    if problems:
        raise ProxonWriteError("Nicht übernommen – " + "; ".join(problems))


class ProxonScheduleCopyButton(ProxonEntity, ButtonEntity):
    _attr_icon = "mdi:content-copy"

    def __init__(self, coordinator: ProxonCoordinator, key: str, source_day: int, targets: tuple[int, ...]) -> None:
        super().__init__(coordinator, key, translation_key=key)
        self._source, self._targets = source_day, targets

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={schedule_device_identifier(self._device_id)})

    async def async_press(self) -> None:
        await async_copy_schedule_day(self.coordinator, self._source, self._targets)
