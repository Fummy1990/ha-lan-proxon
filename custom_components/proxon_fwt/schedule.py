"""Weekly ventilation schedule: decoding and the edit rules verified against the device."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .const import (
    SCHEDULE_ACTIVE_MARGIN_MIN,
    SCHEDULE_FIELD_MAX,
    SCHEDULE_MIN_FUTURE_S,
    SCHEDULE_PHASES,
    SP_CLOCK,
    SP_SCHEDULE_GLOBAL,
    DP_FAN_LEVEL_ACTUAL,
    schedule_field_index,
    schedule_func,
    WriteFunc,
)
from .coordinator import ProxonData, ProxonWriteError

Point = tuple[int, int]


@dataclass(frozen=True)
class Phase:
    day: int
    phase: int
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    level: int

    @property
    def enabled(self) -> bool:
        return self.level > 0

    @property
    def start(self) -> time:
        return time(self.start_hour, self.start_minute)

    @property
    def end(self) -> time:
        return time(self.end_hour, self.end_minute)

    @property
    def start_min(self) -> int:
        return self.start_hour * 60 + self.start_minute

    @property
    def end_min(self) -> int:
        return self.end_hour * 60 + self.end_minute

    def fields(self) -> list[int]:
        return [self.start_hour, self.start_minute, self.end_hour, self.end_minute, self.level]


def read_phase(data: ProxonData, day: int, phase: int) -> Phase | None:
    vals = [data.sp.get((5, 1 + schedule_field_index(day, phase, f))) for f in range(5)]
    if any(v is None for v in vals):
        return None
    sh, sm, eh, em, lv = vals
    if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59 and 0 <= lv <= 4):
        return None
    return Phase(day, phase, sh, sm, eh, em, lv)


def device_clock(data: ProxonData) -> datetime | None:
    """Device clock SP3:56..62 = [DST, minute, hour, ISO weekday, day, month, year]."""
    vals = [data.sp.get(p) for p in SP_CLOCK]
    if any(v is None for v in vals):
        return None
    _dst, minute, hour, _wd, day, month, year = vals
    try:
        return datetime(2000 + year, month, day, hour, minute)
    except ValueError:
        return None


def validate_day(phases: list[Phase]) -> None:
    """Reject overnight/zero-length phases and overlaps or touching endpoints (unverified)."""
    enabled = sorted((p for p in phases if p.enabled), key=lambda p: p.start_min)
    for p in enabled:
        if p.start_min >= p.end_min:
            raise ProxonWriteError(f"Phase {p.phase + 1}: Ende muss nach dem Start liegen (keine Übernacht-Phasen)")
    for prev, cur in zip(enabled, enabled[1:]):
        if prev.end_min > cur.start_min:
            raise ProxonWriteError(f"Phasen {prev.phase + 1} und {cur.phase + 1} überlappen sich")
        if prev.end_min == cur.start_min:
            raise ProxonWriteError(
                f"Phasen {prev.phase + 1} und {cur.phase + 1} berühren sich (gleiche Minute) – nicht verifiziert"
            )


def _check_phase_timing(data: ProxonData, old: Phase, new: Phase) -> None:
    """Timing rules for replacing `old` by `new` (same day/phase)."""
    clock = device_clock(data)
    if clock is None:
        raise ProxonWriteError("Geräteuhr unbekannt – Zeitprogramm-Änderung abgelehnt")
    now_min = clock.hour * 60 + clock.minute
    same_day = clock.weekday() == old.day
    timing_changed = old.fields()[:4] != new.fields()[:4]
    active = same_day and old.enabled and old.start_min < now_min < old.end_min

    if active:
        # Only the level of a running phase may change, with a clear margin to both ends.
        if timing_changed or not 1 <= new.level <= 4:
            raise ProxonWriteError("Laufende Phase: nur die Lüfterstufe darf geändert werden")
        if min(now_min - old.start_min, old.end_min - now_min) < SCHEDULE_ACTIVE_MARGIN_MIN:
            raise ProxonWriteError(f"Laufende Phase: mindestens {SCHEDULE_ACTIVE_MARGIN_MIN} min Abstand zu Phasengrenzen nötig")
        if data.sp.get(SP_SCHEDULE_GLOBAL) != 1 or data.dp.get(DP_FAN_LEVEL_ACTUAL) != old.level:
            raise ProxonWriteError("Laufende Phase: Zeitprogramm nicht aktiv oder Iststufe weicht ab")
    elif timing_changed:
        delta_days = (old.day - clock.weekday()) % 7
        base = clock.replace(hour=0, minute=0) + timedelta(days=delta_days)
        starts = [base + timedelta(minutes=old.start_min), base + timedelta(minutes=new.start_min)]
        if min((s - clock).total_seconds() for s in starts) < SCHEDULE_MIN_FUTURE_S:
            raise ProxonWriteError("Zeitänderungen nur für Phasen, die mindestens 2 h in der Zukunft beginnen")


def _field_writes(old: Phase, new: Phase) -> list[tuple[WriteFunc, int]]:
    return [
        (schedule_func(old.day, old.phase, f), nv)
        for f, (ov, nv) in enumerate(zip(old.fields(), new.fields()))
        if ov != nv
    ]


def plan_phase_change(data: ProxonData, old: Phase, new: Phase) -> list[tuple[WriteFunc, int]]:
    """Validate a phase edit against the schedule rules and return the field writes."""
    for f, (ov, nv) in enumerate(zip(old.fields(), new.fields())):
        if not 0 <= nv <= SCHEDULE_FIELD_MAX[f]:
            raise ProxonWriteError(f"Feld {f} außerhalb des Bereichs")
    day_phases = [read_phase(data, old.day, p) for p in range(SCHEDULE_PHASES)]
    if any(p is None for p in day_phases):
        raise ProxonWriteError("Zeitprogramm noch nicht vollständig gelesen")
    day_phases[old.phase] = new
    validate_day(day_phases)  # type: ignore[arg-type]
    _check_phase_timing(data, old, new)
    return _field_writes(old, new)


def plan_day_copy(data: ProxonData, source_day: int, target_day: int) -> list[tuple[WriteFunc, int]]:
    """Copy all three phases of `source_day` to `target_day` (same rules per phase).

    Returns the field writes for the target day (empty if already identical).
    """
    if source_day == target_day:
        return []
    src = [read_phase(data, source_day, p) for p in range(SCHEDULE_PHASES)]
    dst = [read_phase(data, target_day, p) for p in range(SCHEDULE_PHASES)]
    if any(p is None for p in src + dst):
        raise ProxonWriteError("Zeitprogramm noch nicht vollständig gelesen")
    new_day = [
        Phase(target_day, p.phase, p.start_hour, p.start_minute, p.end_hour, p.end_minute, p.level)
        for p in src  # type: ignore[union-attr]
    ]
    validate_day(new_day)
    writes: list[tuple[WriteFunc, int]] = []
    for old, new in zip(dst, new_day):
        if old.fields() == new.fields():  # type: ignore[union-attr]
            continue
        _check_phase_timing(data, old, new)  # type: ignore[arg-type]
        writes += _field_writes(old, new)  # type: ignore[arg-type]
    return writes
