"""Shared helper for client factories to resolve a credential field.

Opens a short-lived session against the default engine when the caller
doesn't already have one open, so `get_heygen_client()` and friends can be
called with no arguments from pipeline steps while admin/test-connection code
can pass an existing session to avoid opening a second one.
"""

from __future__ import annotations

from sqlmodel import Session

from ..services.integration_settings import IntegrationSettings


def resolve_field(slug: str, field_key: str, *, session: Session | None = None) -> str | None:
    if session is not None:
        return IntegrationSettings(session).get_field(slug, field_key)

    from ..db import engine

    with Session(engine) as owned_session:
        return IntegrationSettings(owned_session).get_field(slug, field_key)
