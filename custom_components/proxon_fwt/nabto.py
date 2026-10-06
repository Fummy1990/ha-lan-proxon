"""Nabto v4 (uNabto) UDP client for Proxon FWT ventilation systems.

Direct LAN communication only (no manufacturer cloud). The wire format
follows the findings verified against a real unit (September/October 2026):

* U_CONNECT with IPX + CP_ID (e-mail identity) + optional FP payload
  (truncated SHA-256 fingerprint of the paired client certificate).
* DATA packets with NULL crypto (0x000A), simple padding and a 16-bit
  additive checksum trailer.
* Query 41/44: range reads, query 42/45: list reads, query 43: single
  setpoint write (count=1), ACK status 1.
* One session per task; max. 160 RPCs per session, 250 ms pacing.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import socket
import struct
import time
from dataclasses import dataclass
from random import randint

_LOGGER = logging.getLogger(__name__)

PKT_U_CONNECT = b"\x83"
PKT_DATA = b"\x16"

PL_NONCE = 0x32
PL_NOTIFY = 0x34
PL_IPX = 0x35
PL_CRYPT = 0x36
PL_CAPABILITY = 0x3B
PL_CP_ID = 0x3F
PL_FP = 0x4B

CRYPT_W_NULL_DATA = 0x000A
NOTIFY_CONNECT_OK = 0x0001
FP_TYPE_SHA256_TRUNCATED = 0x01
FP_TRUNCATED_LENGTH = 16

QUERY_PING = 17
QUERY_SP_RANGE = 41
QUERY_SP_LIST = 42
QUERY_SP_WRITE = 43
QUERY_DP_RANGE = 44
QUERY_DP_LIST = 45

MAX_RPCS_PER_SESSION = 160
MIN_SEND_INTERVAL = 0.25
CONNECT_TIMEOUT = 3.0
RPC_TIMEOUT = 2.0


class NabtoError(Exception):
    """Base exception."""


class NabtoConnectionError(NabtoError):
    """Connection / handshake failed."""


class NabtoQueryError(NabtoError):
    """Query failed or returned an unexpected status."""


class NabtoWriteNotAcknowledged(NabtoQueryError):
    """Query 43 returned a status other than 1."""

    def __init__(self, status: int) -> None:
        super().__init__(f"write status {status} (expected 1)")
        self.status = status


@dataclass
class DiscoveredDevice:
    host: str
    port: int


def compute_fingerprint(cert_pem: str | bytes) -> bytes:
    """Truncated SHA-256 (16 bytes) of the DER certificate."""
    if isinstance(cert_pem, bytes):
        cert_pem = cert_pem.decode()
    der_b64 = "".join(
        line.strip() for line in cert_pem.splitlines() if not line.startswith("-----")
    )
    der = base64.b64decode(der_b64)
    return hashlib.sha256(der).digest()[:FP_TRUNCATED_LENGTH]


def _payload(ptype: int, data: bytes) -> bytes:
    return struct.pack("!BBH", ptype, 0, 4 + len(data)) + data


def _ipx_payload() -> bytes:
    return _payload(PL_IPX, b"\x00" * 12 + b"\x80")


def _cp_id_payload(email: bytes) -> bytes:
    return _payload(PL_CP_ID, b"\x01" + email)


def _fp_payload(fingerprint: bytes) -> bytes:
    return _payload(PL_FP, bytes([FP_TYPE_SHA256_TRUNCATED]) + fingerprint)


def _crypt_null_payload(data: bytes) -> bytes:
    size = len(data)
    padded = ((size // 2) + 1) * 2
    pad = padded - size
    body = data + bytes([pad]) * pad
    length = 4 + 2 + len(body) + 2  # header + crypto code + body + checksum trailer
    return struct.pack("!BBH", PL_CRYPT, 0, length) + struct.pack("!H", CRYPT_W_NULL_DATA) + body


def _build_packet(
    client_id: bytes, server_id: bytes, ptype: bytes, seq: int, payloads: bytes,
    checksum: bool, flags: int = 0,
) -> bytes:
    length = 16 + len(payloads) + (2 if checksum else 0)
    pkt = b"".join([
        client_id, server_id, ptype, b"\x02\x00", bytes([flags]),
        seq.to_bytes(2, "big"), length.to_bytes(2, "big"), payloads,
    ])
    if checksum:
        pkt += (sum(pkt) & 0xFFFF).to_bytes(2, "big")
    return pkt


# ── Discovery ─────────────────────────────────────────────────────────

def _discover_sync(port: int, timeout: float) -> list[DiscoveredDevice]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", 0))
    client_id = randint(0, 0xFFFFFFFF).to_bytes(4, "big")
    pkt = _build_packet(
        client_id, b"\x00" * 4, PKT_U_CONNECT, 1,
        _ipx_payload() + _cp_id_payload(b"discover@probe"), False,
    )
    found: dict[str, DiscoveredDevice] = {}
    try:
        sock.sendto(pkt, ("255.255.255.255", port))
    except OSError:
        sock.close()
        return []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            sock.settimeout(max(0.05, deadline - time.monotonic()))
            data, addr = sock.recvfrom(4096)
        except (socket.timeout, OSError):
            break
        if len(data) >= 16 and data[:4] == client_id and addr[0] not in found:
            found[addr[0]] = DiscoveredDevice(addr[0], port)
    sock.close()
    return list(found.values())


async def discover_devices(port: int = 5570, timeout: float = 3.0) -> list[DiscoveredDevice]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _discover_sync, port, timeout)


# ── Session ───────────────────────────────────────────────────────────

class NabtoSession:
    """One UDP session: handshake, up to 160 RPCs, close.

    All methods are blocking and must be executed in an executor.
    """

    def __init__(self, host: str, port: int, email: str, fingerprint: bytes | None) -> None:
        self.host = host
        self.port = port
        self.email = email.encode()
        self.fingerprint = fingerprint
        self.client_id = randint(0, 0xFFFFFFFF).to_bytes(4, "big")
        self.server_id = b"\x00\x00\x00\x00"
        self.seq = 0
        self.rpcs = 0
        self.connected = False
        self.opened_at = 0.0
        self._last_send = 0.0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("", 0))

    # -- helpers --
    def _pace(self) -> None:
        wait = MIN_SEND_INTERVAL - (time.monotonic() - self._last_send)
        if wait > 0:
            time.sleep(wait)
        self._last_send = time.monotonic()

    def _recv(self, timeout: float, want_type: bytes, want_seq: int | None) -> bytes:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._sock.settimeout(min(0.25, max(0.05, deadline - time.monotonic())))
            try:
                data, _ = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            if len(data) < 16 or data[:4] != self.client_id or data[8:9] != want_type:
                continue
            if want_seq is not None and struct.unpack("!H", data[12:14])[0] != want_seq:
                continue
            return data
        raise NabtoConnectionError(f"timeout waiting for {want_type.hex()} seq={want_seq}")

    # -- lifecycle --
    def connect(self) -> None:
        payloads = _ipx_payload() + _cp_id_payload(self.email)
        if self.fingerprint:
            if len(self.fingerprint) != FP_TRUNCATED_LENGTH:
                raise NabtoConnectionError("fingerprint must be 16 bytes")
            payloads += _fp_payload(self.fingerprint)
        pkt = _build_packet(self.client_id, self.server_id, PKT_U_CONNECT, 1, payloads, False)
        self._pace()
        self._sock.sendto(pkt, (self.host, self.port))
        data = self._recv(CONNECT_TIMEOUT, PKT_U_CONNECT, None)
        pos = 16
        notify = None
        session_id = None
        while pos + 4 <= len(data):
            ptype = data[pos]
            plen = struct.unpack("!H", data[pos + 2:pos + 4])[0]
            if plen < 4:
                break
            body = data[pos + 4:pos + plen]
            if ptype == PL_NOTIFY and len(body) >= 8:
                notify = struct.unpack("!I", body[:4])[0]
                session_id = body[4:8]
            pos += plen
        if notify != NOTIFY_CONNECT_OK or not session_id or session_id == b"\x00" * 4:
            self.close()
            raise NabtoConnectionError(f"connection rejected: NOTIFY {notify!r}")
        self.server_id = session_id
        self.connected = True
        self.opened_at = time.monotonic()
        _LOGGER.debug("Nabto session %s opened (fp=%s)", session_id.hex(), bool(self.fingerprint))

    def close(self) -> None:
        if self.connected and self.rpcs > 0:
            # Framing close (flags 0x40, tag 0x0003, command word 3). Best effort.
            try:
                self.seq += 1
                body = b"\x00\x03" + _crypt_null_payload(b"\x00\x00\x00\x03")
                pkt = _build_packet(self.client_id, self.server_id, PKT_DATA, self.seq, body, True, flags=0x40)
                self._pace()
                self._sock.sendto(pkt, (self.host, self.port))
                self._recv(0.5, PKT_DATA, self.seq)
            except (NabtoError, OSError):
                pass
        self.connected = False
        try:
            self._sock.close()
        except OSError:
            pass

    # -- RPC --
    def rpc(self, query: bytes, timeout: float = RPC_TIMEOUT) -> bytes:
        if not self.connected:
            raise NabtoConnectionError("session not connected")
        if self.rpcs >= MAX_RPCS_PER_SESSION:
            raise NabtoConnectionError("session RPC budget exhausted")
        self.seq += 1
        self.rpcs += 1
        pkt = _build_packet(self.client_id, self.server_id, PKT_DATA, self.seq, _crypt_null_payload(query), True)
        self._pace()
        self._sock.sendto(pkt, (self.host, self.port))
        data = self._recv(timeout, PKT_DATA, self.seq)
        if len(data) < 22 or data[16] != PL_CRYPT:
            raise NabtoQueryError("response without CRYPT payload")
        plen = struct.unpack("!H", data[18:20])[0]
        if struct.unpack("!H", data[20:22])[0] != CRYPT_W_NULL_DATA:
            raise NabtoQueryError("unexpected crypto suite in response")
        raw = data[22:16 + plen - 2]
        if not raw:
            raise NabtoQueryError("empty response")
        pad = raw[-1]
        if not 1 <= pad <= 2 or raw[-pad:] != bytes([pad]) * pad:
            raise NabtoQueryError(f"bad padding in response {raw.hex()}")
        return raw[:-pad]

    def read_range(self, query_id: int, obj: int, start: int, count: int) -> list[int]:
        resp = self.rpc(struct.pack("!IBIH", query_id, obj, start, count))
        if len(resp) < 3 or resp[0] != 0:
            raise NabtoQueryError(f"read q{query_id} {obj}:{start}+{count} status {resp[0] if resp else None}")
        n = struct.unpack("!H", resp[1:3])[0]
        if len(resp) < 3 + 2 * n:
            raise NabtoQueryError("short read response")
        return [struct.unpack("!h", resp[3 + 2 * i:5 + 2 * i])[0] for i in range(n)]

    def read_sp_list(self, points: list[tuple[int, int]]) -> list[int]:
        """Query 42: list of (obj, addr) setpoints. Response has a status byte."""
        q = struct.pack("!IH", QUERY_SP_LIST, len(points))
        for obj, addr in points:
            q += struct.pack("!BH", obj, addr)
        q += b"\x01"
        resp = self.rpc(q)
        if len(resp) < 3 or resp[0] != 0:
            raise NabtoQueryError(f"q42 status {resp[0] if resp else None}")
        n = struct.unpack("!H", resp[1:3])[0]
        return [struct.unpack("!h", resp[3 + 2 * i:5 + 2 * i])[0] for i in range(n)]

    def read_dp_list(self, points: list[tuple[int, int]]) -> list[int]:
        """Query 45: list of (obj, addr) datapoints. Response has NO status byte."""
        q = struct.pack("!IH", QUERY_DP_LIST, len(points))
        for obj, addr in points:
            q += struct.pack("!BI", obj, addr)
        q += b"\x01"
        resp = self.rpc(q)
        if len(resp) < 2:
            raise NabtoQueryError("short q45 response")
        n = struct.unpack("!H", resp[0:2])[0]
        return [struct.unpack("!h", resp[2 + 2 * i:4 + 2 * i])[0] for i in range(n)]

    def write(self, obj: int, alias: int, value: int) -> None:
        """Query 43 single write. Raises unless the device acknowledges with 1."""
        q = struct.pack("!IHBIH", QUERY_SP_WRITE, 1, obj, alias, value & 0xFFFF) + b"\x01"
        resp = self.rpc(q)
        if len(resp) != 1:
            raise NabtoQueryError(f"unexpected write response {resp.hex()}")
        if resp[0] != 1:
            raise NabtoWriteNotAcknowledged(resp[0])

    def ping(self) -> bytes:
        return self.rpc(struct.pack("!I", QUERY_PING))


class NabtoClient:
    """Async facade creating one session per task (executor based)."""

    def __init__(self, host: str, port: int, email: str, fingerprint: bytes | None) -> None:
        self.host = host
        self.port = port
        self.email = email
        self.fingerprint = fingerprint

    async def open(self) -> NabtoSession:
        session = NabtoSession(self.host, self.port, self.email, self.fingerprint)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, session.connect)
        except OSError as err:
            session.close()
            raise NabtoConnectionError(str(err)) from err
        return session

    async def close(self, session: NabtoSession) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, session.close)

    async def call(self, session: NabtoSession, func, *args):
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, func, *args)
        except OSError as err:
            raise NabtoConnectionError(str(err)) from err
