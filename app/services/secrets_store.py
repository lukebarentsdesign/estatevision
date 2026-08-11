"""Encryption for credentials stored in `IntegrationCredential` (admin panel).

Values are encrypted with Fernet (AES-128-CBC + HMAC) using a key kept in a
local file outside the database and outside version control. Losing the key
file means every stored credential must be re-entered -- there is no recovery
path by design, since a recoverable-without-the-key scheme isn't real
encryption.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_KEY_PATH = Path(os.environ.get("PROPERTY_STUDIO_SECRET_KEY_FILE", "secret.key"))


class SecretsStoreError(Exception):
    """Raised when a credential cannot be encrypted or decrypted."""


def _load_or_create_key(key_path: Path) -> bytes:
    if key_path.exists():
        return key_path.read_bytes()

    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600; best-effort on Windows
    except OSError:
        pass
    return key


class SecretsStore:
    """Encrypts/decrypts credential values with a locally held key."""

    def __init__(self, key_path: Path | None = None) -> None:
        self.key_path = key_path or DEFAULT_KEY_PATH
        self._fernet = Fernet(_load_or_create_key(self.key_path))

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode("utf-8")
        except InvalidToken as exc:
            raise SecretsStoreError(
                "Could not decrypt a stored credential -- the key file may have "
                "changed or the data is corrupt. The credential will need to be "
                "re-entered."
            ) from exc


_default_store: SecretsStore | None = None


def get_secrets_store() -> SecretsStore:
    global _default_store
    if _default_store is None:
        _default_store = SecretsStore()
    return _default_store


def mask(value: str) -> str:
    """Last-4-visible mask for display, e.g. `sk-••••••••1234`."""
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * max(len(value) - 4, 4) + value[-4:]
