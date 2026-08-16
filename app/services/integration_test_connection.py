"""Connection tests for the admin panel's "Test connection" button.

Two tiers, matching `IntegrationDefinition.test_mode`:

* LIVE -- makes a real, cheap, read-only API call (e.g. list voices) and
  reports what actually happened. This is the only tier that can tell you a
  key is genuinely invalid.
* FORMAT_ONLY -- no safe, free, read-only endpoint is known for this service,
  so this only checks the key is present and roughly the right shape. The
  admin panel must label this differently from a live check so an operator
  does not mistake "looks like a key" for "confirmed working".
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlmodel import Session

from .integration_registry import ConnectionTestMode, get_integration
from .integration_settings import IntegrationSettings

_REQUEST_TIMEOUT = 8.0


@dataclass(frozen=True)
class ConnectionTestResult:
    slug: str
    ok: bool
    mode: ConnectionTestMode
    message: str


def _format_only_check(slug: str, credentials: dict[str, str]) -> ConnectionTestResult:
    definition = get_integration(slug)
    required_keys = {f.key for f in definition.fields if f.required}
    missing = required_keys - credentials.keys()
    if missing:
        return ConnectionTestResult(
            slug, False, ConnectionTestMode.FORMAT_ONLY, f"Missing required field(s): {sorted(missing)}"
        )
    return ConnectionTestResult(
        slug,
        True,
        ConnectionTestMode.FORMAT_ONLY,
        "All required fields are present. No safe read-only endpoint exists for "
        "this service, so this does not confirm the key is valid -- only that "
        "something has been entered.",
    )


def _test_heygen(credentials: dict[str, str]) -> ConnectionTestResult:
    api_key = credentials.get("api_key")
    if not api_key:
        return ConnectionTestResult("heygen", False, ConnectionTestMode.LIVE, "No API key configured.")
    base_url = credentials.get("base_url") or "https://api.heygen.com"
    try:
        resp = httpx.get(
            f"{base_url}/v2/avatars", headers={"X-Api-Key": api_key}, timeout=_REQUEST_TIMEOUT
        )
    except httpx.HTTPError as exc:
        return ConnectionTestResult("heygen", False, ConnectionTestMode.LIVE, f"Request failed: {exc}")

    if resp.status_code == 200:
        return ConnectionTestResult("heygen", True, ConnectionTestMode.LIVE, "Connected -- avatar list retrieved.")
    if resp.status_code in (401, 403):
        return ConnectionTestResult("heygen", False, ConnectionTestMode.LIVE, "Rejected: API key invalid.")
    return ConnectionTestResult("heygen", False, ConnectionTestMode.LIVE, f"Unexpected status {resp.status_code}.")


def _test_elevenlabs(credentials: dict[str, str]) -> ConnectionTestResult:
    api_key = credentials.get("api_key")
    if not api_key:
        return ConnectionTestResult("elevenlabs", False, ConnectionTestMode.LIVE, "No API key configured.")
    base_url = credentials.get("base_url") or "https://api.elevenlabs.io"
    try:
        resp = httpx.get(
            f"{base_url}/v1/user", headers={"xi-api-key": api_key}, timeout=_REQUEST_TIMEOUT
        )
    except httpx.HTTPError as exc:
        return ConnectionTestResult("elevenlabs", False, ConnectionTestMode.LIVE, f"Request failed: {exc}")

    if resp.status_code == 200:
        return ConnectionTestResult("elevenlabs", True, ConnectionTestMode.LIVE, "Connected -- account retrieved.")
    if resp.status_code in (401, 403):
        return ConnectionTestResult("elevenlabs", False, ConnectionTestMode.LIVE, "Rejected: API key invalid.")
    return ConnectionTestResult(
        "elevenlabs", False, ConnectionTestMode.LIVE, f"Unexpected status {resp.status_code}."
    )


def _test_openai(credentials: dict[str, str]) -> ConnectionTestResult:
    api_key = credentials.get("api_key")
    if not api_key:
        return ConnectionTestResult("openai", False, ConnectionTestMode.LIVE, "No API key configured.")
    base_url = (credentials.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    try:
        resp = httpx.get(
            f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=_REQUEST_TIMEOUT
        )
    except httpx.HTTPError as exc:
        return ConnectionTestResult("openai", False, ConnectionTestMode.LIVE, f"Request failed: {exc}")

    if resp.status_code == 200:
        return ConnectionTestResult("openai", True, ConnectionTestMode.LIVE, "Connected -- model list retrieved.")
    if resp.status_code in (401, 403):
        return ConnectionTestResult("openai", False, ConnectionTestMode.LIVE, "Rejected: API key invalid.")
    return ConnectionTestResult("openai", False, ConnectionTestMode.LIVE, f"Unexpected status {resp.status_code}.")


def _test_fish_audio(credentials: dict[str, str]) -> ConnectionTestResult:
    api_key = credentials.get("api_key")
    if not api_key:
        return ConnectionTestResult("fish_audio", False, ConnectionTestMode.LIVE, "No API key configured.")
    base_url = (credentials.get("base_url") or "https://api.fish.audio").rstrip("/")
    try:
        resp = httpx.get(
            f"{base_url}/wallet/self/api-credit",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        return ConnectionTestResult("fish_audio", False, ConnectionTestMode.LIVE, f"Request failed: {exc}")

    if resp.status_code == 200:
        return ConnectionTestResult(
            "fish_audio", True, ConnectionTestMode.LIVE, "Connected -- account credit retrieved."
        )
    if resp.status_code in (401, 403):
        return ConnectionTestResult("fish_audio", False, ConnectionTestMode.LIVE, "Rejected: API key invalid.")
    return ConnectionTestResult(
        "fish_audio", False, ConnectionTestMode.LIVE, f"Unexpected status {resp.status_code}."
    )


def _test_replicate_wan(credentials: dict[str, str]) -> ConnectionTestResult:
    api_key = credentials.get("api_key")
    if not api_key:
        return ConnectionTestResult("replicate_wan", False, ConnectionTestMode.LIVE, "No API token configured.")
    try:
        resp = httpx.get(
            "https://api.replicate.com/v1/account",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        return ConnectionTestResult("replicate_wan", False, ConnectionTestMode.LIVE, f"Request failed: {exc}")

    if resp.status_code == 200:
        return ConnectionTestResult(
            "replicate_wan", True, ConnectionTestMode.LIVE, "Connected -- account retrieved."
        )
    if resp.status_code in (401, 403):
        return ConnectionTestResult("replicate_wan", False, ConnectionTestMode.LIVE, "Rejected: API token invalid.")
    return ConnectionTestResult(
        "replicate_wan", False, ConnectionTestMode.LIVE, f"Unexpected status {resp.status_code}."
    )


_LIVE_CHECKS = {
    "heygen": _test_heygen,
    "elevenlabs": _test_elevenlabs,
    "fish_audio": _test_fish_audio,
    "openai": _test_openai,
    "replicate_wan": _test_replicate_wan,
}


def test_connection(session: Session, slug: str) -> ConnectionTestResult:
    definition = get_integration(slug)
    credentials = IntegrationSettings(session).get_credentials(slug)

    if definition.test_mode is ConnectionTestMode.LIVE and slug in _LIVE_CHECKS:
        return _LIVE_CHECKS[slug](credentials)

    if definition.test_mode is ConnectionTestMode.NONE:
        return ConnectionTestResult(slug, True, ConnectionTestMode.NONE, "Nothing to test for this integration.")

    return _format_only_check(slug, credentials)
