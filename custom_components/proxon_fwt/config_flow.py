"""Config and options flow for Proxon FWT (direct LAN, no cloud).

First configuration asks only for the Device-ID and the password (from the
device card). The client certificate is fetched automatically from the
manufacturer portal once (sign-only), then every connection runs directly over
the LAN. The host is auto-discovered; a certificate can still be pasted manually
as a fallback (advanced option).
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    MAX_ROOMS,
    NAME_READS_SP,
    CONF_CERT_PEM,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_KEY_PEM,
    CONF_PASSWORD,
    CONF_ROOM_AREAS,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EXPECTED_FIRMWARE_MAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    email_for_device,
)
from .coordinator import decode_name, room_connected
from .nabto import (
    NabtoClient,
    NabtoConnectionError,
    NabtoError,
    QUERY_DP_RANGE,
    QUERY_SP_RANGE,
    compute_fingerprint,
    discover_devices,
)
from .provision import InvalidCredentials, ProvisioningError, async_fetch_certificate

_LOGGER = logging.getLogger(__name__)


async def _validate(host: str, port: int, device_id: str, cert_pem: str) -> dict[str, Any]:
    """Open a session with the certificate fingerprint; read firmware, room mask and names."""
    try:
        fingerprint = compute_fingerprint(cert_pem)
    except Exception as err:  # noqa: BLE001
        raise ValueError("invalid_cert") from err
    client = NabtoClient(host, port, email_for_device(device_id), fingerprint)
    session = await client.open()
    try:
        fw = await client.call(session, session.read_range, QUERY_DP_RANGE, 0, 5, 1)
        mask_words = await client.call(session, session.read_range, QUERY_SP_RANGE, 1, 0, 2)
        words: dict[int, int] = {}
        for obj, start, count in NAME_READS_SP:
            vals = await client.call(session, session.read_range, QUERY_SP_RANGE, obj, start, count)
            words.update({start + i: v for i, v in enumerate(vals)})
    finally:
        await client.close(session)
    mask = (mask_words[0] & 0xFFFF) | ((mask_words[1] & 0xFFFF) << 16)
    rooms = [
        (i, decode_name([words.get(10 * i + k, 0) for k in range(10)]) or f"Raum {i + 1}")
        for i in range(MAX_ROOMS)
        if room_connected(mask, i)
    ]
    return {"firmware": fw[0] if fw else None, "rooms": rooms}


class ProxonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for device id + password, fetch the certificate, then map rooms to areas."""

    VERSION = 2
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._discovered_host: str | None = None
        self._data: dict[str, Any] = {}
        self._rooms: list[tuple[int, str]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._discovered_host is None:
            try:
                found = await discover_devices()
                self._discovered_host = found[0].host if found else ""
            except OSError:
                self._discovered_host = ""

        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID].strip()
            password = user_input.get(CONF_PASSWORD, "") or ""
            cert = (user_input.get(CONF_CERT_PEM, "") or "").strip()
            key_pem = ""
            await self.async_set_unique_id(device_id)
            self._abort_if_unique_id_configured()

            # Fetch the certificate from the portal unless one was pasted manually.
            if not cert:
                if not password:
                    errors[CONF_PASSWORD] = "password_required"
                else:
                    try:
                        session = async_get_clientsession(self.hass)
                        cert, key_pem = await async_fetch_certificate(session, device_id, password)
                    except InvalidCredentials:
                        errors[CONF_PASSWORD] = "invalid_auth"
                    except ProvisioningError:
                        errors["base"] = "portal_unreachable"

            if not errors:
                try:
                    info = await _validate(user_input[CONF_HOST], user_input[CONF_PORT], device_id, cert)
                except ValueError:
                    errors[CONF_CERT_PEM] = "invalid_cert"
                except NabtoConnectionError:
                    errors["base"] = "cannot_connect"
                except NabtoError:
                    errors["base"] = "unknown"
                else:
                    if info["firmware"] != EXPECTED_FIRMWARE_MAIN:
                        _LOGGER.warning("Unexpected firmware %s", info["firmware"])
                    self._data = {
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input[CONF_PORT],
                        CONF_DEVICE_ID: device_id,
                        CONF_EMAIL: email_for_device(device_id),
                        CONF_CERT_PEM: cert,
                        CONF_KEY_PEM: key_pem,
                    }
                    self._rooms = info["rooms"]
                    return await self.async_step_rooms()

        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID, default=(user_input or {}).get(CONF_DEVICE_ID, "")): str,
                vol.Optional(CONF_PASSWORD, default=(user_input or {}).get(CONF_PASSWORD, "")): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, self._discovered_host or "")): str,
                vol.Required(CONF_PORT, default=(user_input or {}).get(CONF_PORT, DEFAULT_PORT)): int,
                vol.Optional(CONF_CERT_PEM, default=(user_input or {}).get(CONF_CERT_PEM, "")): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"discovered": self._discovered_host or "–"},
        )

    async def async_step_rooms(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Assign each detected room controller to a Home Assistant area."""
        if user_input is not None:
            self._abort_if_unique_id_configured()  # guard against a parallel/duplicate flow
            areas = {str(i): user_input[f"area_{i}"] for i, _ in self._rooms if user_input.get(f"area_{i}")}
            return self.async_create_entry(
                title=f"Proxon FWT ({self._data[CONF_HOST]})",
                data=self._data,
                options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL, CONF_ROOM_AREAS: areas},
            )
        fields: dict[Any, Any] = {
            vol.Optional(f"area_{i}"): selector.AreaSelector() for i, _ in self._rooms
        }
        return self.async_show_form(
            step_id="rooms",
            data_schema=vol.Schema(fields),
            description_placeholders={"rooms": ", ".join(f"{i}: {name}" for i, name in self._rooms) or "–"},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ProxonOptionsFlow:
        return ProxonOptionsFlow()


class ProxonOptionsFlow(OptionsFlow):
    """Scan interval and room → area assignment."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        coordinator = self.config_entry.runtime_data
        rooms = [r for r in coordinator.data.rooms.values() if r.connected] if coordinator and coordinator.data else []
        current_areas: dict[str, str] = self.config_entry.options.get(CONF_ROOM_AREAS, {})

        if user_input is not None:
            areas = {
                str(r.index): user_input[f"area_{r.index}"]
                for r in rooms
                if user_input.get(f"area_{r.index}")
            }
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    CONF_ROOM_AREAS: areas,
                },
            )

        fields: dict[Any, Any] = {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL, step=10,
                    unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
        for r in rooms:
            key = f"area_{r.index}"
            default = current_areas.get(str(r.index))
            fields[vol.Optional(key, description={"suggested_value": default})] = selector.AreaSelector()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "rooms": ", ".join(f"{r.index}: {r.name}" for r in rooms) or "–",
            },
        )
