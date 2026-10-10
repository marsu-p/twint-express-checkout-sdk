from __future__ import annotations

import os
import tempfile
import weakref
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
    pkcs12,
)

from .exceptions import InvalidValue


@dataclass(frozen=True, repr=False)
class Certificate:
    """The merchant client certificate used for mutual TLS against the TWINT API.

    TWINT hands out the certificate as a password-protected ``.p12`` file in the
    merchant portal; load it with :meth:`from_pkcs12`.
    """

    cert_pem: bytes
    key_pem: bytes  # unencrypted

    def __repr__(self) -> str:
        return "Certificate(<redacted>)"

    @classmethod
    def from_pkcs12(cls, data: bytes | str | Path, password: str | bytes | None) -> Certificate:
        if isinstance(data, (str, Path)):
            data = Path(data).read_bytes()
        if isinstance(password, str):
            password = password.encode()
        try:
            key, cert, extra = pkcs12.load_key_and_certificates(data, password)
        except ValueError as e:
            raise InvalidValue(f"Cannot read PKCS#12 certificate: {e}") from e
        if key is None or cert is None:
            raise InvalidValue("PKCS#12 file must contain a private key and a certificate")
        chain = b"".join(c.public_bytes(Encoding.PEM) for c in [cert, *(extra or [])])
        key_pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        return cls(chain, key_pem)

    @classmethod
    def from_pem(
        cls, cert: bytes | str | Path, key: bytes | str | Path, password: str | bytes | None = None
    ) -> Certificate:
        cert_pem = Path(cert).read_bytes() if isinstance(cert, (str, Path)) else cert
        key_pem = Path(key).read_bytes() if isinstance(key, (str, Path)) else key
        if isinstance(password, str):
            password = password.encode()
        if password is not None:
            private_key = load_pem_private_key(key_pem, password)
            key_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        return cls(cert_pem, key_pem)

    def to_temp_file(self) -> TempCertFile:
        """Write cert and key to a private temp file, since ``requests`` only accepts paths."""
        return TempCertFile(self.cert_pem + self.key_pem)


class TempCertFile:
    """A ``0600`` PEM file that is deleted when this object is garbage-collected or closed."""

    def __init__(self, content: bytes) -> None:
        fd, path = tempfile.mkstemp(prefix="twint-", suffix=".pem")
        try:
            os.write(fd, content)
        finally:
            os.close(fd)
        self.path = path
        self._finalizer = weakref.finalize(self, _unlink, path)

    def close(self) -> None:
        self._finalizer()


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
