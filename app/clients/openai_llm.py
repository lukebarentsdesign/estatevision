"""OpenAI (or compatible) client for script generation (spec §1.1, §1.2, §4 `standard`).

Callers must build prompts through `services.script_prompt.build_prompt` --
this client trusts that the prompt it is given has already been assembled at
the one permitted choke point and simply completes it.
"""

from __future__ import annotations

from typing import Protocol

import httpx
from sqlmodel import Session

from .credential_lookup import resolve_field

_REQUEST_TIMEOUT = 60.0
_DEFAULT_MODEL = "gpt-4.1"


class OpenAIError(RuntimeError):
    """Raised when the OpenAI (or compatible) API rejects a completion request."""


class OpenAILLMClient(Protocol):
    def complete(self, prompt: str) -> str:
        ...


class StubOpenAILLMClient:
    """Deterministic stand-in: returns the numbered source sentences, joined.

    This trivially satisfies §1.1 grounding (it uses only source sentences) and
    is good enough to exercise the pipeline without an LLM key.
    """

    def complete(self, prompt: str) -> str:
        marker = "SOURCE SENTENCES (your only permitted material):\n"
        _, _, tail = prompt.partition(marker)
        lines = [line.split(". ", 1)[-1] for line in tail.strip().splitlines() if line.strip()]
        return " ".join(lines)


class HttpOpenAILLMClient:
    """Confirmed against live OpenAI API docs: `POST /chat/completions` with
    `Authorization: Bearer <key>`, an OpenAI-compatible request/response shape
    also used by Groq, OpenRouter, Together AI, and Cerebras (per this app's
    `OPENAI_COMPATIBLE_PRESETS`), which is why `base_url` and `model` are both
    configurable rather than hardcoded to api.openai.com.
    """

    def __init__(self, api_key: str, *, base_url: str = "https://api.openai.com/v1", model: str = _DEFAULT_MODEL) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def complete(self, prompt: str) -> str:
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise OpenAIError(f"OpenAI request failed: {exc}") from exc

        if resp.status_code != 200:
            raise OpenAIError(f"OpenAI completion failed ({resp.status_code}): {resp.text[:500]}")

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as exc:
            raise OpenAIError(f"OpenAI response had no completion content: {data!r}") from exc


def get_openai_llm_client(*, session: Session | None = None) -> OpenAILLMClient:
    """Resolve the OpenAI (or compatible) client from stored/admin-panel
    credentials, falling back to a stub if no API key is configured anywhere
    (admin panel or env)."""
    api_key = resolve_field("openai", "api_key", session=session)
    if api_key:
        base_url = resolve_field("openai", "base_url", session=session) or "https://api.openai.com/v1"
        model = resolve_field("openai", "model", session=session) or _DEFAULT_MODEL
        return HttpOpenAILLMClient(api_key=api_key, base_url=base_url, model=model)
    return StubOpenAILLMClient()
