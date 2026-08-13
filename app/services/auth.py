"""Agency/admin login: password hashing and signed session cookies
(spec: agency/admin auth design, 2026-08-13).

Sessions are a signed, HttpOnly cookie -- no server-side session table.
The signing key is a locally held file, generated on first use, mirroring
`services.secrets_store`'s key-file pattern so the two follow the same
operational model (lose the file, every session/credential needs
re-issuing).
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from typing import Literal, Optional, TypedDict

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.hash import bcrypt

SESSION_COOKIE_NAME = "ps_session"
SESSION_MAX_AGE_SECONDS = 14 * 24 * 60 * 60  # 14 days, sliding on each request

_DEFAULT_KEY_PATH = Path(os.environ.get("PROPERTY_STUDIO_SESSION_KEY_FILE", "session.key"))

_session_serializer: Optional[URLSafeTimedSerializer] = None


class SessionPayload(TypedDict):
    account_type: Literal["agency", "admin"]
    account_id: int


def hash_password(plaintext: str) -> str:
    return bcrypt.hash(plaintext)


def verify_password(plaintext: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.verify(plaintext, password_hash)
    except ValueError:
        return False


def _load_or_create_key(key_path: Path) -> bytes:
    if key_path.exists():
        return key_path.read_bytes()

    key = secrets.token_bytes(32)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600; best-effort on Windows
    except OSError:
        pass
    return key


def _get_serializer() -> URLSafeTimedSerializer:
    global _session_serializer
    if _session_serializer is None:
        key_path = Path(os.environ.get("PROPERTY_STUDIO_SESSION_KEY_FILE", str(_DEFAULT_KEY_PATH)))
        key = _load_or_create_key(key_path)
        _session_serializer = URLSafeTimedSerializer(key.hex())
    return _session_serializer


def encode_session_cookie(*, account_type: Literal["agency", "admin"], account_id: int) -> str:
    serializer = _get_serializer()
    return serializer.dumps({"account_type": account_type, "account_id": account_id})


def decode_session_cookie(
    token: str, max_age_seconds: int = SESSION_MAX_AGE_SECONDS
) -> Optional[SessionPayload]:
    serializer = _get_serializer()
    try:
        data = serializer.loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or "account_type" not in data or "account_id" not in data:
        return None
    return {"account_type": data["account_type"], "account_id": data["account_id"]}
