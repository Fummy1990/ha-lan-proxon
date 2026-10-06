"""Constants and verified address mapping for the Proxon FWT integration.

Address semantics (firmware 7.4 / T300 3.5, verified via Nabto, 2026-10):

* ``SPx:y``  setpoint read point  (query 41/42, object x, index y)
* ``DPx:y``  datapoint read point (query 44/45, object x, index y)
* writes use query 43 with a *separate* (object, alias) pair; the write
  alias is never a valid read address and vice versa.
"""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.const import Platform

DOMAIN = "proxon_fwt"
MANUFACTURER = "Proxon"
MODEL = "FWT 2.0 / T300"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

DEFAULT_PORT = 5570
DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 600

CONF_DEVICE_ID = "device_id"
CONF_PASSWORD = "password"
CONF_EMAIL = "email"
CONF_CERT_PEM = "cert_pem"
CONF_KEY_PEM = "key_pem"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ROOM_AREAS = "room_areas"  # options: {"<room index>": "<area_id>"}

EXPECTED_FIRMWARE_MAIN = 74   # DP0:5  -> "7.4"
EXPECTED_FIRMWARE_T300 = 35   # DP3:81 -> "3.5"

MAX_ROOMS = 21  # index 0 = Wohnzimmer (master), 1..20 = Nebenräume


def email_for_device(device_id: str) -> str:
    """Nabto client identity used by the original app for a device id."""
    return f"user-{device_id.strip()}@phc.proxon.de"


# ── Poll blocks (query, obj, start, count) ───────────────────────────
# Full profile reads as verified against the device (28 blocks). Room names (SP6)
# are refreshed less often; the weekly schedule (SP5) is not polled.
POLL_READS_DP: tuple[tuple[int, int, int], ...] = (
    (0, 0, 52),
    (1, 0, 60), (1, 60, 60), (1, 120, 16),
    (2, 0, 60), (2, 60, 21),
    (3, 0, 60), (3, 60, 50),
)
POLL_READS_SP: tuple[tuple[int, int, int], ...] = (
    (0, 0, 60), (0, 60, 60), (0, 120, 17), (0, 177, 33),
    (1, 0, 60), (1, 60, 58),
    (3, 56, 7),                     # device clock only (SP3:54 is a password - never read)
    (4, 0, 13),                     # operating hour counters / filter runtime
    (5, 0, 60), (5, 60, 53),        # weekly ventilation schedule (105 fields + flags)
    (7, 0, 60), (7, 60, 4), (7, 66, 45), (7, 121, 6),   # SP7:65 = profile password - skipped
)
NAME_READS_SP: tuple[tuple[int, int, int], ...] = (
    (6, 0, 60), (6, 60, 60), (6, 120, 60), (6, 180, 30),
)
NAME_REFRESH_EVERY_N_POLLS = 30

# ── Guard points ─────────────────────────────────────────────────────
FAULT_POINTS: tuple[tuple[int, int], ...] = (
    (0, 2), (0, 48), (0, 49), (0, 50), (0, 51), (1, 7), (2, 78), (2, 79), (2, 80),
)
# Known, tolerated bits for non-critical writes (room setpoint, fan level)
TOLERATED_FAULT_BITS: dict[tuple[int, int], int] = {(0, 50): 64, (2, 79): 16}

DP_FIRMWARE_MAIN = (0, 5)
DP_FIRMWARE_T300 = (3, 81)
DP_T300_FAULT = (3, 61)
DP_PTC_RELAY_A = (2, 4)
DP_PTC_RELAY_B = (2, 13)
DP_T300_RELAYS = ((3, 24), (3, 25), (3, 26), (3, 27))

# ── Named functions ──────────────────────────────────────────────────
@dataclass(frozen=True)
class WriteFunc:
    """A writable setpoint: read point (SP obj, idx) and write alias."""

    key: str
    read: tuple[int, int]
    write: tuple[int, int]
    settle: float = 3.0      # max seconds to wait for the read point to show the target
    policy: str = "default"  # guard policy name (see coordinator)


FUNC_MODE = WriteFunc("mode", (0, 16), (0, 42), settle=5.0, policy="mode")
FUNC_FAN_LEVEL = WriteFunc("fan_level", (0, 22), (0, 54), policy="tolerant")
FUNC_COOLING = WriteFunc("cooling", (0, 62), (0, 134), settle=5.0, policy="cooling")
FUNC_LIVING_TARGET = WriteFunc("living_target", (0, 70), (0, 150), policy="tolerant")
FUNC_LIVING_PTC = WriteFunc("living_ptc", (0, 187), (0, 384), settle=5.0, policy="ptc")
FUNC_BOOST = WriteFunc("boost", (0, 185), (0, 380), settle=5.0)
FUNC_BOOST_DURATION = WriteFunc("boost_duration", (0, 186), (0, 382))
FUNC_WATER_TARGET = WriteFunc("water_target", (7, 1), (1, 0), settle=60.0, policy="t300")
FUNC_HEATER_ENABLE = WriteFunc("heater_enable", (7, 2), (1, 1), settle=60.0, policy="heater")
FUNC_HEATER_TARGET = WriteFunc("heater_target", (7, 4), (1, 3), settle=60.0, policy="t300")
FUNC_LEGIONELLA = WriteFunc("legionella", (7, 26), (1, 25), settle=60.0, policy="t300")
# App model (Proxon Control 1.7.2): zone-2 target, CO2/humidity limits, T300 operating mode
FUNC_ZONE2_TARGET = WriteFunc("zone2_target", (0, 75), (0, 160), policy="tolerant")
FUNC_CO2_LIMIT = WriteFunc("co2_limit", (0, 117), (0, 244), policy="tolerant")
FUNC_HUMIDITY_LIMIT = WriteFunc("humidity_limit", (0, 118), (0, 246), policy="tolerant")
FUNC_T300_MODE = WriteFunc("t300_mode", (7, 3), (1, 2), settle=90.0, policy="t300")


def room_offset_func(i: int) -> WriteFunc:
    """Target offset of secondary room i (1..20): target = mean + offset."""
    return WriteFunc(f"room{i}_offset", (1, 2 + i), (0, 434 + 2 * i), policy="tolerant")


def room_ptc_func(i: int) -> WriteFunc:
    """PTC (electric heater) enable of secondary room i (1..20)."""
    return WriteFunc(f"room{i}_ptc", (1, 42 + i), (0, 514 + 2 * i), settle=5.0, policy="ptc")


# Read-only points used by entities
SP_ROOM_MEAN_BASE = 22        # SP1:(22+i) reference/mean temperature of room i
SP_ROOM_OFFSET_BASE = 2       # SP1:(2+i)
SP_ROOM_PTC_BASE = 42         # SP1:(42+i)
SP_CONNECTION_MASK = ((1, 0), (1, 1))
SP_GLOBAL_PTC = (1, 115)
SP_BOOST_REMAINING = (0, 133)
SP_BOOST_E1E2 = (0, 189)
SP_T300_MODE = (7, 3)
SP_PV_ENABLE = (7, 11)
SP_PV_MODE = (7, 89)
SP_LIVING_TARGET = (0, 70)
SP_CLOCK = tuple((3, i) for i in range(56, 63))  # DST, min, hour, weekday, day, month, year

DP_LIVING_CURRENT = (1, 113)  # /100 °C
DP_ROOM_CURRENT_BASE = 17     # DP2:(17+3i) /10 °C, 0 = not connected
DP_RPM_SUPPLY = (0, 0)
DP_RPM_EXHAUST = (0, 1)
DP_FAN_LEVEL_ACTUAL = (1, 4)
DP_HUMIDITY = (0, 22)
DP_CO2 = (0, 21)
DP_COMPRESSOR_RPM = (1, 40)
DP_FOUR_WAY_VALVE = (1, 73)
DP_WATER_BOTTOM = (3, 13)     # (raw-1000)/10 °C
DP_WATER_MIDDLE = (3, 14)
DP_T300_PRE_EVAP = (3, 11)
DP_T300_EVAP = (3, 12)
DP_T300_COMPRESSOR = (3, 15)
DP_T300_SUCTION = (3, 16)
DP_T300_FAN_RPM = (3, 62)
DP_PV_LEVEL = (3, 98)

# Main unit temperatures and states (app model; profile structnum = DP1 index + 55), /100 °C
DP_T1_SUPPLY = (1, 45)
DP_T3_FRESH = (1, 48)
DP_T4_EXHAUST_OUT = (1, 47)
DP_T7_EXTRACT = (1, 46)
DP_T12_PRE_CONDENSER = (1, 28)
DP_T13_COMPRESSOR_MAIN = (1, 30)
DP_T14_SUCTION_GAS = (1, 57)
DP_ZONE2_CURRENT = (1, 114)          # /100 °C
DP_P14_LOW_PRESSURE = (1, 56)        # 3 decimals
DP_P19_DEFROST_DIFF = (1, 17)        # 2 decimals
DP_GEOTHERMAL_RELAY = (1, 70)
DP_SOLENOID_VALVE = (1, 71)
DP_BYPASS = (1, 72)
DP_DAMPER_POSITION = (1, 9)
DP_VALVE_HEATING = (1, 54)
DP_VALVE_PREHEAT = (1, 55)
DP_VALVE_COOLING = (1, 53)
DP_HP_RELAY_WORD = (1, 1)
DP_FAN_STATUS = (1, 2)
DP_COMPRESSOR_STATUS = (1, 12)
SP_HAS_CO2 = (0, 38)
SP_OPERATING_HOURS = (4, 0)          # unsigned
SP_FILTER_RUNTIME = (4, 2)
SP_FILTER_INTERVAL = (7, 25)
ZONE2_TARGET_MIN = 16.0
ZONE2_TARGET_MAX = 24.0
CO2_LIMIT_MIN = 400
CO2_LIMIT_MAX = 2000
HUMIDITY_LIMIT_MIN = 20
HUMIDITY_LIMIT_MAX = 80
DP_PV_FLAG_HEATER = (3, 99)
DP_PV_FLAG_HP = (3, 100)

MODE_OPTIONS: dict[int, str] = {
    0: "Aus",
    1: "Sommer",
    2: "Winter",
    3: "ECO Komfort",
    4: "Ofenbetrieb",
}
FAN_LEVEL_OPTIONS: dict[int, str] = {1: "Stufe 1", 2: "Stufe 2", 3: "Stufe 3", 4: "Stufe 4"}

LIVING_TARGET_MIN = 16.0
LIVING_TARGET_MAX = 24.0
LIVING_TARGET_STEP = 0.5
ROOM_OFFSET_MIN = -3
ROOM_OFFSET_MAX = 3
WATER_TARGET_MIN = 40.0
WATER_TARGET_MAX = 55.0
HEATER_TARGET_MIN = 40.0
HEATER_TARGET_MAX = 55.0
BOOST_DURATION_MIN = 1
BOOST_DURATION_MAX = 1440


# ── Weekly schedule (app model: 7 days × 3 phases × 5 fields) ────────
# field k = 15*day + 5*phase + f; f: 0 start hour, 1 start minute, 2 end hour,
# 3 end minute, 4 level (0 = phase inactive, 1..4). Read SP5:(1+k), write 0:(1026+2k).
SCHEDULE_DAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
SCHEDULE_DAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def schedule_device_identifier(device_id: str, day: int | None = None) -> tuple[str, str]:
    """Device identifier of the schedule parent (day=None) or of one weekday device."""
    return (DOMAIN, f"{device_id}_schedule" if day is None else f"{device_id}_schedule_{day}")
SCHEDULE_PHASES = 3
SCHEDULE_FIELD_MAX = (23, 59, 23, 59, 4)
SP_SCHEDULE_GLOBAL = (5, 106)
SP_SCHEDULE_NIGHT = (5, 112)
SCHEDULE_LEVEL_OPTIONS: dict[int, str] = {0: "Aus", 1: "Stufe 1", 2: "Stufe 2", 3: "Stufe 3", 4: "Stufe 4"}
SCHEDULE_MIN_FUTURE_S = 2 * 3600   # timing edits only for phases starting ≥ 2 h in the future
SCHEDULE_ACTIVE_MARGIN_MIN = 10    # level edits of a running phase need this margin to both ends


def schedule_field_index(day: int, phase: int, field: int) -> int:
    return 15 * day + 5 * phase + field


def schedule_func(day: int, phase: int, field: int) -> WriteFunc:
    k = schedule_field_index(day, phase, field)
    return WriteFunc(f"schedule_{day}_{phase}_{field}", (5, 1 + k), (0, 1026 + 2 * k), settle=5.0, policy="tolerant")
