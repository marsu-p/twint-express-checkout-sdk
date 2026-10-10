from __future__ import annotations

import os
import stat
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import BestAvailableEncryption, pkcs12
from cryptography.x509.oid import NameOID

from twint_sdk import Certificate, InvalidValue


def make_p12(password: bytes) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-merchant")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(b"test", key, cert, None, BestAvailableEncryption(password))


def test_from_pkcs12_writes_private_temp_file():
    certificate = Certificate.from_pkcs12(make_p12(b"secret"), "secret")

    assert b"BEGIN CERTIFICATE" in certificate.cert_pem
    assert b"BEGIN PRIVATE KEY" in certificate.key_pem
    assert "PRIVATE" not in repr(certificate)

    temp = certificate.to_temp_file()
    assert stat.S_IMODE(os.stat(temp.path).st_mode) == 0o600
    temp.close()
    assert not os.path.exists(temp.path)


def test_from_pkcs12_wrong_password():
    with pytest.raises(InvalidValue):
        Certificate.from_pkcs12(make_p12(b"secret"), "wrong")
