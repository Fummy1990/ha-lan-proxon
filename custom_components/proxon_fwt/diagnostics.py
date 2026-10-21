"""Diagnostics download (Settings → Integration → ⋮ → Download diagnostics).

Contains the raw SP/DP snapshot of the last poll so other firmware versions can be analysed
without a packet capture. Device id, host, certificate, key, room names and the panel lock
code are redacted; room names are never part of the snapshot (SP6 is not included).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CERT_PEM,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_KEY_PEM,
    CONF_PASSWORD,
    CONF_ROOM_AREAS,
    DP_FIRMWARE_MAIN,
    DP_FIRMWARE_T300,
    EXPECTED_FIRMWARE_MAIN,
    EXPECTED_FIRMWARE_T300,
    SP_PANEL_LOCK,
    firmware_text,
)
from .coordinator import ProxonCoordinator

TO_REDACT = {CONF_DEVICE_ID, CONF_EMAIL, CONF_CERT_PEM, CONF_KEY_PEM, CONF_PASSWORD, CONF_HOST, CONF_ROOM_AREAS, "unique_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator: ProxonCoordinator = entry.runtime_data
    data = coordinator.data
    sp = {f"{obj}:{idx}": v for (obj, idx), v in sorted(data.sp.items())} if data else {}
    dp = {f"{obj}:{idx}": v for (obj, idx), v in sorted(data.dp.items())} if data else {}
    sp.pop(f"{SP_PANEL_LOCK[0]}:{SP_PANEL_LOCK[1]}", None)
    return {
        "entry": {
            "version": f"{entry.version}.{entry.minor_version}",
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "firmware": {
            "main": firmware_text(data.dp.get(DP_FIRMWARE_MAIN)) if data else None,
            "main_raw": data.dp.get(DP_FIRMWARE_MAIN) if data else None,
            "t300": firmware_text(data.dp.get(DP_FIRMWARE_T300)) if data else None,
            "t300_raw": data.dp.get(DP_FIRMWARE_T300) if data else None,
            "verified_for_writes": coordinator.firmware_verified,
            "verified_pairs": [f"{firmware_text(EXPECTED_FIRMWARE_MAIN)} / T300 {firmware_text(EXPECTED_FIRMWARE_T300)}"],
            "allow_unverified_opt_in": coordinator.allow_unverified,
            "unverified_write_locked": coordinator.unverified_write_locked,
        },
        "writes": {
            "last_error": coordinator.last_write_error,
            "last_error_at": coordinator.last_write_error_at.isoformat() if coordinator.last_write_error_at else None,
            "error_count": coordinator.write_error_count,
        },
        "rooms": [
            {
                "index": r.index, "connected": r.connected, "is_master": r.is_master,
                "target": r.target, "current": r.current, "ptc": r.ptc,
            }
            for r in (data.rooms.values() if data else [])
        ],
        "sp": sp,
        "dp": dp,
    }
