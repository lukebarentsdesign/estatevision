"""Read/write access to stored integration credentials (admin panel backend).

This is the only module that touches `IntegrationCredential` rows directly.
Client factories (`app/clients/*.py`) call `get_field`/`get_credentials`
instead of reading `os.environ`, so a key entered in the admin panel takes
effect immediately without a restart or a `.env` edit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlmodel import Session, select

from ..models import IntegrationCredential
from .integration_registry import (
    CredentialField,
    IntegrationDefinition,
    get_integration,
    list_integrations,
)
from .secrets_store import SecretsStore, get_secrets_store, mask


@dataclass(frozen=True)
class FieldStatus:
    field: CredentialField
    is_set: bool
    masked_value: str | None  # None if not set; masked even for non-secret fields' preview
    source: str  # "database" | "environment" | "unset"


@dataclass(frozen=True)
class IntegrationStatus:
    definition: IntegrationDefinition
    fields: tuple[FieldStatus, ...]

    @property
    def is_fully_configured(self) -> bool:
        return all(fs.is_set for fs in self.fields if fs.field.required)


# Maps a registry field to the legacy env var it can fall back to, so existing
# .env-based setups keep working until credentials are migrated into the panel.
_ENV_FALLBACKS: dict[tuple[str, str], str] = {
    ("heygen", "api_key"): "HEYGEN_API_KEY",
    ("elevenlabs", "api_key"): "ELEVENLABS_API_KEY",
    ("gemini_omni", "api_key"): "GEMINI_API_KEY",
    ("openai", "api_key"): "OPENAI_API_KEY",
}


class IntegrationSettings:
    def __init__(self, session: Session, store: SecretsStore | None = None) -> None:
        self.session = session
        self.store = store or get_secrets_store()

    def _row(self, slug: str, field_key: str) -> IntegrationCredential | None:
        stmt = select(IntegrationCredential).where(
            IntegrationCredential.integration_slug == slug,
            IntegrationCredential.field_key == field_key,
        )
        return self.session.exec(stmt).first()

    def get_field(self, slug: str, field_key: str) -> str | None:
        """Resolve a field's live value: DB first, then env var fallback."""
        row = self._row(slug, field_key)
        if row is not None:
            return self.store.decrypt(row.value_ciphertext)

        env_var = _ENV_FALLBACKS.get((slug, field_key))
        if env_var:
            return os.environ.get(env_var) or None
        return None

    def get_credentials(self, slug: str) -> dict[str, str]:
        """All resolved field values for one integration, keyed by field_key."""
        definition = get_integration(slug)
        result: dict[str, str] = {}
        for f in definition.fields:
            value = self.get_field(slug, f.key)
            if value:
                result[f.key] = value
        return result

    def set_field(self, slug: str, field_key: str, value: str) -> None:
        definition = get_integration(slug)
        if field_key not in {f.key for f in definition.fields}:
            raise ValueError(f"{slug!r} has no field {field_key!r}")

        ciphertext = self.store.encrypt(value)
        row = self._row(slug, field_key)
        if row is None:
            row = IntegrationCredential(integration_slug=slug, field_key=field_key, value_ciphertext=ciphertext)
        else:
            row.value_ciphertext = ciphertext
        self.session.add(row)
        self.session.commit()

    def clear_field(self, slug: str, field_key: str) -> None:
        row = self._row(slug, field_key)
        if row is not None:
            self.session.delete(row)
            self.session.commit()

    def status_for(self, slug: str) -> IntegrationStatus:
        definition = get_integration(slug)
        field_statuses = []
        for f in definition.fields:
            row = self._row(slug, f.key)
            if row is not None:
                value = self.store.decrypt(row.value_ciphertext)
                field_statuses.append(FieldStatus(f, True, mask(value) if f.is_secret else value, "database"))
                continue

            env_var = _ENV_FALLBACKS.get((slug, f.key))
            env_value = os.environ.get(env_var) if env_var else None
            if env_value:
                field_statuses.append(
                    FieldStatus(f, True, mask(env_value) if f.is_secret else env_value, "environment")
                )
            else:
                field_statuses.append(FieldStatus(f, False, None, "unset"))

        return IntegrationStatus(definition, tuple(field_statuses))

    def all_statuses(self) -> tuple[IntegrationStatus, ...]:
        return tuple(self.status_for(d.slug) for d in list_integrations())
