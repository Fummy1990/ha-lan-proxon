"""Data coordinator and verified write path for Proxon FWT."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import struct
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ALLOW_UNVERIFIED_FIRMWARE,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    DP_FIRMWARE_MAIN,
    DP_FIRMWARE_T300,
    DP_LIVING_CURRENT,
    DP_PTC_RELAY_A,
    DP_PTC_RELAY_B,
    DP_ROOM_CURRENT_BASE,
    DP_T300_FAULT,
    DP_T300_RELAYS,
    EXPECTED_FIRMWARE_MAIN,
    EXPECTED_FIRMWARE_T300,
    VERIFIED_FIRMWARE,
    allowed_on_unverified_firmware,
    firmware_text,
    FAULT_POINTS,
    MAX_ROOMS,
    NAME_READS_SP,
    NAME_REFRESH_EVERY_N_POLLS,
    POLL_READS_DP,
    POLL_READS_SP,
    SP_CONNECTION_MASK,
    SP_LIVING_TARGET,
    SP_ROOM_MEAN_BASE,
    SP_ROOM_OFFSET_BASE,
    SP_ROOM_PTC_BASE,
    TOLERATED_FAULT_BITS,
    WriteFunc,
    FUNC_LIVING_PTC,
    FUNC_MODE,
)
from .nabto import (
    NabtoClient,
    NabtoConnectionError,
    NabtoError,
    NabtoSession,
    NabtoWriteNotAcknowledged,
    QUERY_DP_RANGE,
    QUERY_SP_RANGE,
)

_LOGGER = logging.getLogger(__name__)

Point = tuple[int, int]
MAX_WRITE_RETRIES = 2  # automatic resends per value before giving up
FAST_READBACK_INTERVAL = 2.0  # s, read-back cadence right after a slow (T300) write
FAST_READBACK_WINDOW = 15.0   # s, then fall back to SLOW_READBACK_INTERVAL
SLOW_READBACK_INTERVAL = 5.0  # s


@dataclass
class Room:
    """One room controller (Nebenplatine / NBE)."""

    index: int
    name: str
    connected: bool
    target: float | None
    current: float | None
    ptc: bool
    offset: int | None = None
    mean: int | None = None

    @property
    def is_master(self) -> bool:
        return self.index == 0


@dataclass
class ProxonData:
    """Raw and derived device state."""

    sp: dict[Point, int] = field(default_factory=dict)
    dp: dict[Point, int] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)
    rooms: dict[int, Room] = field(default_factory=dict)
    updated: datetime | None = None

    def sp_get(self, point: Point) -> int | None:
        return self.sp.get(point)

    def dp_get(self, point: Point) -> int | None:
        return self.dp.get(point)


def decode_name(words: list[int]) -> str:
    """Room names: 10 words, 2 latin-1 chars each, NUL bytes skipped."""
    raw = b"".join(struct.pack("!H", w & 0xFFFF) for w in words)
    return bytes(b for b in raw if b).decode("latin-1", "replace").strip()


def connection_mask(sp: dict[Point, int]) -> int:
    w0 = sp.get(SP_CONNECTION_MASK[0], 0) & 0xFFFF
    w1 = sp.get(SP_CONNECTION_MASK[1], 0) & 0xFFFF
    return w0 | (w1 << 16)


def room_connected(mask: int, i: int) -> bool:
    """Bit order: NBE2..NBE20 = bits 0..18, NBE1 = bit 19, NBE0 always connected."""
    if i == 0:
        return True
    if i == 1:
        return bool((mask >> 19) & 1)
    return bool((mask >> (i - 2)) & 1)


def build_rooms(sp: dict[Point, int], dp: dict[Point, int], names: dict[int, str]) -> dict[int, Room]:
    mask = connection_mask(sp)
    rooms: dict[int, Room] = {}
    # master / Wohnzimmer
    t = sp.get(SP_LIVING_TARGET)
    c = dp.get(DP_LIVING_CURRENT)
    rooms[0] = Room(
        index=0,
        name=names.get(0) or "Wohnzimmer",
        connected=True,
        target=None if t is None else t / 100,
        current=None if c in (None, 0) else c / 100,
        ptc=bool(sp.get(FUNC_LIVING_PTC.read, 0)),
    )
    for i in range(1, MAX_ROOMS):
        offset = sp.get((1, SP_ROOM_OFFSET_BASE + i))
        mean = sp.get((1, SP_ROOM_MEAN_BASE + i))
        cur = dp.get((2, DP_ROOM_CURRENT_BASE + 3 * i))
        rooms[i] = Room(
            index=i,
            name=names.get(i) or f"Raum {i + 1}",
            connected=room_connected(mask, i),
            target=None if offset is None or mean is None else float(mean + offset),
            current=None if cur in (None, 0) else cur / 10,
            ptc=bool(sp.get((1, SP_ROOM_PTC_BASE + i), 0)),
            offset=offset,
            mean=mean,
        )
    return rooms


class ProxonWriteError(HomeAssistantError):
    """A verified write failed (guard, no ACK, or read-back mismatch)."""


class ProxonCoordinator(DataUpdateCoordinator[ProxonData]):
    """Polls the device (one Nabto session per poll) and performs verified writes."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: NabtoClient) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self._device_lock = asyncio.Lock()  # one open Nabto session at a time
        self._poll_counter = 0
        self._names: dict[int, str] = {}
        self.firmware_ok: bool | None = None       # main firmware == verified main firmware
        self.firmware_verified: bool | None = None  # (main, T300) pair in VERIFIED_FIRMWARE
        self.allow_unverified = bool(entry.options.get(CONF_ALLOW_UNVERIFIED_FIRMWARE, False))
        self._write_retries = MAX_WRITE_RETRIES
        self.unverified_write_locked: str | None = None  # reason, set after an unconfirmed opt-in write
        self.holiday = None  # HolidayController, set in __init__.py
        self.last_write_error: str | None = None
        self.last_write_error_at: datetime | None = None
        self.write_error_count = 0

    # ── polling ───────────────────────────────────────────────────────
    async def _async_update_data(self) -> ProxonData:
        async with self._device_lock:
            try:
                return await self._poll()
            except NabtoError as err:
                raise UpdateFailed(f"Nabto error: {err}") from err

    async def _poll(self) -> ProxonData:
        session = await self.client.open()
        sp: dict[Point, int] = {}
        dp: dict[Point, int] = {}
        try:
            for obj, start, count in POLL_READS_DP:
                vals = await self.client.call(session, session.read_range, QUERY_DP_RANGE, obj, start, count)
                dp.update({(obj, start + i): v for i, v in enumerate(vals)})
            for obj, start, count in POLL_READS_SP:
                vals = await self.client.call(session, session.read_range, QUERY_SP_RANGE, obj, start, count)
                sp.update({(obj, start + i): v for i, v in enumerate(vals)})
            if not self._names or self._poll_counter % NAME_REFRESH_EVERY_N_POLLS == 0:
                words: dict[int, int] = {}
                for obj, start, count in NAME_READS_SP:
                    vals = await self.client.call(session, session.read_range, QUERY_SP_RANGE, obj, start, count)
                    words.update({start + i: v for i, v in enumerate(vals)})
                self._names = {
                    i: decode_name([words.get(10 * i + k, 0) for k in range(10)])
                    for i in range(MAX_ROOMS)
                }
        finally:
            await self.client.close(session)
        self._poll_counter += 1

        fw = dp.get(DP_FIRMWARE_MAIN)
        fw_t300 = dp.get(DP_FIRMWARE_T300)
        self.firmware_ok = fw == EXPECTED_FIRMWARE_MAIN
        verified = (fw, fw_t300) in VERIFIED_FIRMWARE
        if self.firmware_verified is None and not verified:
            _LOGGER.warning(
                "Firmware %s / T300 %s is not verified for writes (verified: %s / %s); writes are %s",
                firmware_text(fw), firmware_text(fw_t300), firmware_text(EXPECTED_FIRMWARE_MAIN),
                firmware_text(EXPECTED_FIRMWARE_T300),
                "enabled by the user's opt-in (single attempt, no resends)" if self.allow_unverified else "blocked",
            )
        self.firmware_verified = verified

        data = ProxonData(sp=sp, dp=dp, names=dict(self._names), updated=datetime.now(timezone.utc))
        data.rooms = build_rooms(sp, dp, data.names)
        return data

    # ── verified writes ───────────────────────────────────────────────
    async def async_write(self, func: WriteFunc, value: int) -> None:
        """Write one setpoint and verify it by reading the separate read point back."""
        await self.async_write_batch([(func, value)])

    async def async_write_batch(self, items: list[tuple[WriteFunc, int]]) -> None:
        """Write several setpoints in ONE session (guard once, verify each).

        Raises ProxonWriteError on any guard violation, missing ACK or mismatch.
        Values already present on the device are skipped.
        """
        if not items:
            return
        items = [(func, int(value)) for func, value in items]
        # Optimistic: show the target immediately (T300 writes take up to 60-90 s to verify);
        # reverted if the write fails. No poll can interfere - it shares _device_lock.
        previous = self._apply_optimistic(items)
        try:
            async with self._device_lock:
                session = await self.client.open()
                try:
                    await self._guard(session, items)
                    for func, value in items:
                        await self._write_one(session, func, value)
                except NabtoError as err:
                    raise ProxonWriteError(f"{items[0][0].key}: Nabto-Fehler: {err}") from err
                finally:
                    await self.client.close(session)
        except BaseException:
            self._revert_optimistic(previous)
            raise
        await self.async_request_refresh()

    def _apply_optimistic(self, items: list[tuple[WriteFunc, int]]) -> dict[tuple[int, int], int | None]:
        if not self.data:
            return {}
        previous = {func.read: self.data.sp.get(func.read) for func, _ in items}
        for func, value in items:
            self.data.sp[func.read] = value
        self.data.rooms = build_rooms(self.data.sp, self.data.dp, self.data.names)
        self.async_set_updated_data(self.data)
        return previous

    def _revert_optimistic(self, previous: dict[tuple[int, int], int | None]) -> None:
        if not self.data or not previous:
            return
        for point, old in previous.items():
            if old is None:
                self.data.sp.pop(point, None)
            else:
                self.data.sp[point] = old
        self.data.rooms = build_rooms(self.data.sp, self.data.dp, self.data.names)
        self.async_set_updated_data(self.data)

    async def _write_one(self, session: NabtoSession, func: WriteFunc, value: int) -> None:
        """Write one value with up to MAX_WRITE_RETRIES automatic resends, then verify.

        On unverified firmware (opt-in) there is exactly one attempt and an unconfirmed write
        locks all further writes until the integration is reloaded.
        """
        retries = self._write_retries
        current = (await self.client.call(session, session.read_sp_list, [func.read]))[0]
        if current == value:
            _LOGGER.debug("%s already %s - no write", func.key, value)
            return
        _LOGGER.info("Write %s: %s -> %s (alias %s:%s)", func.key, current, value, *func.write)
        rb1 = current
        last_problem = ""
        for attempt in range(1, retries + 2):
            try:
                await self.client.call(session, session.write, func.write[0], func.write[1], value)
            except NabtoWriteNotAcknowledged as err:
                # 98 = secondary controller (T300) busy, nothing written; other codes: unknown refusal
                last_problem = f"Status {err.status}"
                _LOGGER.warning("Write %s attempt %d: %s", func.key, attempt, last_problem)
                if attempt <= retries:
                    await asyncio.sleep(5.0)
                    continue
                self._note_write_error(func, f"{last_problem} nach {attempt} Versuchen")
                raise ProxonWriteError(
                    f"{func.key}: Anlage hat den Schreibbefehl nicht bestätigt ({last_problem}, {attempt} Versuche)"
                ) from err
            except NabtoConnectionError as err:
                last_problem = f"keine Antwort ({err})"
                _LOGGER.warning("Write %s attempt %d: %s", func.key, attempt, last_problem)
                if attempt <= retries:
                    await asyncio.sleep(2.0)
                    continue
                self._note_write_error(func, f"{last_problem} nach {attempt} Versuchen")
                raise ProxonWriteError(f"{func.key}: keine Antwort der Anlage ({attempt} Versuche)") from err
            # Read back quickly at first (most writes land within seconds), then relax the
            # cadence so a long settle window (T300: 60-90 s) stays within the RPC budget.
            started = time.monotonic()
            deadline = started + func.settle
            while True:
                elapsed = time.monotonic() - started
                if func.settle < 20:
                    interval = 1.0
                else:
                    interval = FAST_READBACK_INTERVAL if elapsed < FAST_READBACK_WINDOW else SLOW_READBACK_INTERVAL
                await asyncio.sleep(interval)
                rb1 = (await self.client.call(session, session.read_sp_list, [func.read]))[0]
                if rb1 == value or time.monotonic() >= deadline:
                    break
            if rb1 == value:
                break
            last_problem = f"ACK ohne Wirkung (gelesen {rb1})"
            _LOGGER.warning("Write %s attempt %d: acknowledged but not applied after %.0f s", func.key, attempt, func.settle)
        await asyncio.sleep(0.5)
        rb2 = (await self.client.call(session, session.read_sp_list, [func.read]))[0]
        if rb1 != value or rb2 != value:
            self._note_write_error(func, f"{last_problem}; Rücklesung {rb1}/{rb2}, erwartet {value}")
            if not self.firmware_verified:
                # The alias may map to a different register on this firmware: do not try again.
                self.unverified_write_locked = f"{func.key}: Rücklesung {rb1}/{rb2}, erwartet {value}"
                _LOGGER.error(
                    "Write %s on unverified firmware was acknowledged but not applied - all further writes "
                    "are locked until the integration is reloaded", func.key
                )
            raise ProxonWriteError(
                f"{func.key}: Rücklesung ergab {rb1}/{rb2}, erwartet {value} (vorher {current}, "
                f"{retries + 1} Versuche, je {func.settle:.0f} s)"
            )
        if attempt > 1:
            _LOGGER.info("Write %s verified after %d attempts", func.key, attempt)
        else:
            _LOGGER.info("Write %s verified: %s", func.key, value)

    def _note_write_error(self, func: WriteFunc, problem: str) -> None:
        self.last_write_error = f"{func.key}: {problem}"
        self.last_write_error_at = datetime.now(timezone.utc)
        self.write_error_count += 1
        self.async_update_listeners()  # push the diagnostic sensor immediately

    async def _guard(self, session: NabtoSession, items: list[tuple[WriteFunc, int]]) -> None:
        """Read firmware and fault words in the write session and apply the policy of the first item."""
        func, value = items[0]
        points = list(FAULT_POINTS) + [DP_FIRMWARE_MAIN, DP_FIRMWARE_T300, DP_PTC_RELAY_A, DP_PTC_RELAY_B]
        if func.policy in ("t300", "heater"):
            points += [DP_T300_FAULT, *DP_T300_RELAYS]
        vals = await self.client.call(session, session.read_dp_list, points)
        dpv = dict(zip(points, vals))

        fw, fw_t300 = dpv.get(DP_FIRMWARE_MAIN), dpv.get(DP_FIRMWARE_T300)
        self.firmware_ok = fw == EXPECTED_FIRMWARE_MAIN
        self.firmware_verified = (fw, fw_t300) in VERIFIED_FIRMWARE
        if self.firmware_verified:
            self._write_retries = MAX_WRITE_RETRIES
        else:
            shown = f"{firmware_text(fw)} / T300 {firmware_text(fw_t300)}"
            if not self.allow_unverified:
                raise ProxonWriteError(
                    f"Firmware {shown} nicht verifiziert (verifiziert: {firmware_text(EXPECTED_FIRMWARE_MAIN)} / "
                    f"T300 {firmware_text(EXPECTED_FIRMWARE_T300)}): Schreiben gesperrt. In den Integrationsoptionen "
                    "kann Schreiben auf eigenes Risiko freigegeben werden."
                )
            if self.unverified_write_locked:
                raise ProxonWriteError(
                    f"Schreiben gesperrt: ein Write auf nicht verifizierter Firmware wurde nicht bestätigt "
                    f"({self.unverified_write_locked}). Integration neu laden, um es erneut zu versuchen."
                )
            blocked = [f.key for f, _ in items if not allowed_on_unverified_firmware(f)]
            if blocked:
                raise ProxonWriteError(
                    f"{blocked[0]}: auf nicht verifizierter Firmware ({shown}) nicht freigegeben – nur Betriebsart, "
                    "Lüfterstufe, Raumsoll, Raum-PTC, Kühlfreigabe, Intensivlüftung und Grenzwerte sind möglich"
                )
            self._write_retries = 0
            _LOGGER.warning("Write %s on unverified firmware %s (user opt-in): single attempt, no resend", func.key, shown)
        faults = {p: dpv.get(p, 0) for p in FAULT_POINTS}
        if func.policy == "tolerant":
            bad = {p: v for p, v in faults.items() if v & ~TOLERATED_FAULT_BITS.get(p, 0)}
        else:
            bad = {p: v for p, v in faults.items() if v}
        # switching PTC *off* is always allowed
        if bad and not (func.policy == "ptc" and value == 0):
            raise ProxonWriteError(f"Fehlerbits aktiv {bad}: Schreiben gesperrt")

        if func.policy in ("t300", "heater"):
            if dpv.get(DP_T300_FAULT):
                raise ProxonWriteError(f"T300-Fehlerwort {dpv.get(DP_T300_FAULT)}: Schreiben gesperrt")
        if func.policy == "heater" and any(dpv.get(p) for p in DP_T300_RELAYS):
            raise ProxonWriteError("T300-Relais aktiv (kein Ruhezustand): Heizstab-Änderung gesperrt")

        if func.policy in ("mode", "cooling", "ptc"):
            sp_vals = await self.client.call(session, session.read_sp_list, [FUNC_MODE.read, (0, 185)])
            mode, boost = sp_vals
            if func.policy == "mode" and boost:
                raise ProxonWriteError("Intensivlüftung aktiv: Betriebsart-Wechsel gesperrt")
            if func.policy == "cooling":
                if mode not in (1, 2, 3):
                    raise ProxonWriteError(f"Kühlfreigabe nur in Sommer/Winter/ECO (Betriebsart {mode})")
                if mode in (1, 3) and (dpv.get(DP_PTC_RELAY_A) or dpv.get(DP_PTC_RELAY_B)):
                    raise ProxonWriteError("PTC-Relais aktiv: Kühlfreigabe gesperrt")
            if func.policy == "ptc" and value != 0 and mode not in (1, 2):
                raise ProxonWriteError(f"PTC-Freigabe nur in Sommer/Winter (Betriebsart {mode})")

    async def async_write_switch(self, func: WriteFunc, on: bool) -> None:
        await self.async_write(func, 1 if on else 0)
