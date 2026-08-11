"""Proves the multi-vendor mechanism end-to-end: register a second vendor in
an existing category, switch to it via the service layer, and confirm
dispatch picks it up -- without any pipeline step code change.

This uses a monkeypatched registry entry rather than a real second HTTP
client module, since adding a real competing vendor is a separate, larger
task left for whenever an actual competitor is chosen (see the design spec's
"adding a new vendor" non-goal).
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.models  # noqa: F401 -- registers ActiveVendorChoice etc. on SQLModel.metadata
from app.services import integration_registry
from app.services.integration_registry import (
    CredentialField,
    FieldKind,
    IntegrationDefinition,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def second_avatar_vendor(monkeypatch):
    """Registers a fake second vendor in the 'avatar' category for this test only."""
    fake = IntegrationDefinition(
        slug="fake_avatar_vendor",
        name="Fake Avatar Vendor",
        category="Avatar & Voice",
        category_key="avatar",
        description="Test-only second avatar vendor.",
        fields=(CredentialField("api_key", "API Key", FieldKind.API_KEY),),
    )
    new_list = integration_registry.INTEGRATIONS + (fake,)
    monkeypatch.setattr(integration_registry, "INTEGRATIONS", new_list)
    monkeypatch.setattr(integration_registry, "_BY_SLUG", {i.slug: i for i in new_list})
    return fake


def test_switching_active_vendor_changes_get_active_vendor_result(session, second_avatar_vendor) -> None:
    from app.services.active_vendor import get_active_vendor, set_active_vendor

    assert get_active_vendor(session, "avatar") == "heygen"  # first-registered default

    set_active_vendor(session, "avatar", "fake_avatar_vendor")
    assert get_active_vendor(session, "avatar") == "fake_avatar_vendor"

    set_active_vendor(session, "avatar", "heygen")
    assert get_active_vendor(session, "avatar") == "heygen"


def test_dispatch_raises_clearly_for_a_vendor_with_no_client_branch(session, second_avatar_vendor) -> None:
    from app.clients.dispatch import get_active_avatar_client
    from app.services.active_vendor import set_active_vendor

    set_active_vendor(session, "avatar", "fake_avatar_vendor")
    with pytest.raises(ValueError, match="fake_avatar_vendor"):
        get_active_avatar_client(session=session)
