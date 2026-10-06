"""Fetch a portal-signed Nabto client certificate from Device-ID + password.

This is the only contact with the manufacturer portal: one HTTPS request that signs a
locally generated certificate, exactly as the original app does on its first login.
All device control afterwards runs directly over the LAN (no cloud).

Request:  POST PORTAL_SIGN_URL, form fields password / email / csr (PEM CSR with the
          single subject field emailAddress=user-<deviceID>@phc.proxon.de, RSA key).
Response: newline-separated text - status ("0" = ok), message, then the PEM certificate.
Only the certificate is needed for the LAN connection (its truncated SHA-256 is the ACL
fingerprint); the private key is stored alongside it.
"""
from __future__ import annotations

import logging

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .const import email_for_device

_LOGGER = logging.getLogger(__name__)

PORTAL_SIGN_URL = "https://lscontrol.nabto.com/service/register"
RSA_KEY_BITS = 1024
_CERT_BEGIN = "-----BEGIN CERTIFICATE-----"
_CERT_END = "-----END CERTIFICATE-----"


class ProvisioningError(Exception):
    """Base error while fetching the certificate from the portal."""


class InvalidCredentials(ProvisioningError):
    """The portal rejected the Device-ID / password combination."""


def _generate_key_and_csr(email: str) -> tuple[str, str]:
    """Generate an RSA key and a CSR (subject emailAddress=email). CPU-bound/blocking."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_BITS)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.EMAIL_ADDRESS, email)]))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()
    return key_pem, csr_pem


def _extract_certificate(body: str) -> str:
    """Pull the PEM certificate out of the portal response."""
    if _CERT_BEGIN not in body or _CERT_END not in body:
        raise ProvisioningError("Portal-Antwort enthielt kein Zertifikat")
    start = body.index(_CERT_BEGIN)
    end = body.index(_CERT_END) + len(_CERT_END)
    return body[start:end] + "\n"


async def async_fetch_certificate(
    session: aiohttp.ClientSession, device_id: str, password: str
) -> tuple[str, str]:
    """Return (cert_pem, key_pem) for the given credentials, signed by the portal.

    Runs the key/CSR generation in the event loop's executor and performs one
    HTTPS POST. Raises InvalidCredentials on a portal rejection, ProvisioningError
    on any other failure.
    """
    import asyncio

    email = email_for_device(device_id)
    loop = asyncio.get_running_loop()
    key_pem, csr_pem = await loop.run_in_executor(None, _generate_key_and_csr, email)

    data = {"password": password, "email": email, "csr": csr_pem}
    try:
        async with session.post(
            PORTAL_SIGN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            body = await resp.text()
            http_status = resp.status
    except aiohttp.ClientError as err:
        raise ProvisioningError(f"Portal nicht erreichbar: {err}") from err

    if http_status != 200:
        raise ProvisioningError(f"Portal antwortete mit HTTP {http_status}")

    lines = body.splitlines()
    status = lines[0].strip() if lines else ""
    message = lines[1].strip() if len(lines) > 1 else ""
    if status != "0":
        _LOGGER.debug("Portal signup failed: status=%s message=%s", status, message)
        # The portal returns a non-zero status for a wrong password / unknown device.
        raise InvalidCredentials(message or f"Portal-Fehlercode {status}")

    cert_pem = _extract_certificate(body)
    return cert_pem, key_pem
