"""Proxon FWT – direct LAN (Nabto) control of a Proxon ventilation system."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er

from .const import (
    CONF_CERT_PEM,
    CONF_KEY_PEM,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_ROOM_AREAS,
    DEFAULT_PORT,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    PLATFORMS,
    SCHEDULE_DAY_NAMES,
    schedule_device_identifier,
    email_for_device,
)
from .coordinator import ProxonCoordinator
from .holiday import HolidayController
from .nabto import NabtoClient, NabtoError, compute_fingerprint

_LOGGER = logging.getLogger(__name__)



async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate older entries to v2.2 (device_id + certificate + key, portal-provisioned)."""
    if entry.version == 1:
        data = dict(entry.data)
        email = data.get(CONF_EMAIL, "")
        device_id = email.split("@")[0].removeprefix("user-") if email else ""
        data.setdefault(CONF_DEVICE_ID, device_id)
        data.setdefault(CONF_EMAIL, email_for_device(device_id))
        data.setdefault(CONF_CERT_PEM, "")
        data.setdefault(CONF_KEY_PEM, "")
        data.setdefault(CONF_PORT, DEFAULT_PORT)
        hass.config_entries.async_update_entry(
            entry, data=data, version=2, minor_version=2,
            unique_id=device_id or entry.unique_id,
            title=f"Proxon FWT ({data[CONF_HOST]})",
        )
        _LOGGER.warning(
            "Proxon FWT: Eintrag aus Version 0.1 migriert – ohne Client-Zertifikat ist nur Lesen möglich. "
            "Bitte die Integration entfernen und mit Geräte-ID + Passwort neu einrichten."
        )
    elif entry.version == 2 and entry.minor_version < 2:
        data = dict(entry.data)
        data.setdefault(CONF_KEY_PEM, "")
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
        _LOGGER.info("Migrated Proxon FWT config entry to version 2.2")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    cert = entry.data.get(CONF_CERT_PEM) or ""
    fingerprint = compute_fingerprint(cert) if cert.strip() else None
    if fingerprint is None:
        _LOGGER.warning("No client certificate configured - the device may refuse writes")

    client = NabtoClient(
        host=entry.data[CONF_HOST],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        email=entry.data.get(CONF_EMAIL) or email_for_device(entry.data[CONF_DEVICE_ID]),
        fingerprint=fingerprint,
    )
    coordinator = ProxonCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except NabtoError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator.holiday = HolidayController(hass, coordinator, entry.entry_id)
    await coordinator.holiday.async_load()
    entry.runtime_data = coordinator
    _register_devices(hass, entry, coordinator)
    _remove_stale_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _register_services(hass)
    return True


SERVICE_COPY_SCHEDULE_DAY = "copy_schedule_day"
_DAY_KEYS = ("mo", "di", "mi", "do", "fr", "sa", "so")
_COPY_SCHEMA = vol.Schema(
    {
        vol.Required("source_day", default="mo"): vol.In(_DAY_KEYS),
        vol.Required("target_days"): vol.All(cv.ensure_list, [vol.In(_DAY_KEYS)]),
        vol.Optional("config_entry_id"): cv.string,
    }
)


def _register_services(hass: HomeAssistant) -> None:
    """proxon_fwt.copy_schedule_day: copy one weekday's schedule to other weekdays."""
    if hass.services.has_service(DOMAIN, SERVICE_COPY_SCHEDULE_DAY):
        return

    async def _copy(call: ServiceCall) -> None:
        from .button import async_copy_schedule_day

        entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
        if wanted := call.data.get("config_entry_id"):
            entries = [e for e in entries if e.entry_id == wanted]
        if not entries:
            raise ServiceValidationError("Keine geladene Proxon-FWT-Integration")
        source = _DAY_KEYS.index(call.data["source_day"])
        targets = tuple(_DAY_KEYS.index(d) for d in call.data["target_days"] if d != call.data["source_day"])
        for entry in entries:
            await async_copy_schedule_day(entry.runtime_data, source, targets)

    hass.services.async_register(DOMAIN, SERVICE_COPY_SCHEDULE_DAY, _copy, schema=_COPY_SCHEMA)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options changed: reload (setup re-applies the area assignment)."""
    await hass.config_entries.async_reload(entry.entry_id)


def main_device_identifier(entry: ConfigEntry) -> tuple[str, str]:
    return (DOMAIN, entry.data[CONF_DEVICE_ID])


def room_device_identifier(entry: ConfigEntry, index: int) -> tuple[str, str]:
    return (DOMAIN, f"{entry.data[CONF_DEVICE_ID]}_room_{index}")


def _register_devices(hass: HomeAssistant, entry: ConfigEntry, coordinator: ProxonCoordinator) -> None:
    """Create the main device and one device per connected room; apply area mapping."""
    registry = dr.async_get(hass)
    legacy = registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    if legacy:
        registry.async_remove_device(legacy.id)
    fw_main = coordinator.data.dp.get((0, 5))
    fw_t300 = coordinator.data.dp.get((3, 81))
    sw = f"FWT {fw_main / 10:.1f} / T300 {fw_t300 / 10:.1f}" if fw_main and fw_t300 else None
    main = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={main_device_identifier(entry)},
        manufacturer=MANUFACTURER,
        model=MODEL,
        name="Proxon FWT",
        sw_version=sw,
        serial_number=entry.data[CONF_DEVICE_ID],
    )
    schedule = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={schedule_device_identifier(entry.data[CONF_DEVICE_ID])},
        manufacturer=MANUFACTURER,
        model="Wochenprogramm Lüftung",
        name="Proxon Zeitprogramm",
        via_device_id=main.id,
    )
    for day, day_name in enumerate(SCHEDULE_DAY_NAMES):
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={schedule_device_identifier(entry.data[CONF_DEVICE_ID], day)},
            manufacturer=MANUFACTURER,
            model="Tagesprogramm Lüftung (3 Phasen)",
            name=f"Proxon Zeitprogramm {day_name}",
            via_device_id=schedule.id,
        )
    areas: dict[str, str] = entry.options.get(CONF_ROOM_AREAS, {})
    for room in coordinator.data.rooms.values():
        if not room.connected:
            continue
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={room_device_identifier(entry, room.index)},
            manufacturer=MANUFACTURER,
            model="Raumregler (NBE)",
            name=f"Proxon {room.name}",
            via_device_id=main.id,
        )
        area_id = areas.get(str(room.index))
        if area_id and device.area_id != area_id:
            registry.async_update_device(device.id, area_id=area_id)


def _remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove registry entries left by the pre-2.0 integration (different unique ids)."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not entity.unique_id.startswith(f"{entry.data[CONF_DEVICE_ID]}:"):
            _LOGGER.info("Removing stale entity %s", entity.entity_id)
            registry.async_remove(entity.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
