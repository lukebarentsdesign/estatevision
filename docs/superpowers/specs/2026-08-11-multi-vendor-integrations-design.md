# Multi-vendor selection per integration category

Status: approved design, not yet implemented.

## Problem

Each production category (Avatar, TTS/narration, hero-shot animation, script
generation) currently hardcodes exactly one vendor. Pipeline steps import a
specific client factory directly, e.g. `avatar_and_captions.py` calls
`clients.heygen.get_heygen_client()`. Switching to a different vendor (a
HeyGen competitor, an alternative TTS provider, etc.) requires editing
pipeline step code and redeploying.

## Goal

Let the admin register more than one vendor per category and pick which one
is "active" from the admin panel (`/admin/integrations`). Switching the
active vendor takes effect immediately, with no pipeline code changes.
Adding a brand-new vendor to a category remains a normal small coding task
(new client module following the existing pattern + one registry entry) —
this is intentionally *not* a no-code/fully-generic HTTP integration system;
async/polling vendor APIs (like HeyGen's render-and-poll flow) don't fit a
generic config shape well, and a real coding task for genuinely new vendors
is an acceptable, infrequent cost.

Script generation is handled differently (see §6) because it already
supports vendor-swapping today via an OpenAI-compatible `base_url` override,
and most alternative LLM providers are drop-in compatible with that shape.

## Non-goals

- Fully generic, no-code HTTP adapter config (rejected — see above).
- Automatic fallback / priority-ordered vendor lists. Exactly one vendor is
  active per category at a time; no automatic failover.
- Changing anything about the DfE Schools API or Ofcom Broadband categories
  — these are single-source government data with no real alternate vendor
  and are out of scope.
- The photo-sequencing / timed-script feature discussed in the same
  conversation is a separate, unrelated feature and is not covered by this
  spec.
- The script-generation knowledge base / ring-fencing rules feature is also
  separate and not covered by this spec.

## Design

### 1. Category grouping

`IntegrationDefinition` (in `app/services/integration_registry.py`) gains a
new field, `category_key: str`, distinct from the existing free-text
`category` field (which is just a display-grouping label in the admin UI).
`category_key` is the stable machine identifier multiple vendor definitions
share when they compete for the same job, e.g.:

| category_key            | current vendor(s)      |
|--------------------------|-------------------------|
| `avatar`                 | `heygen`                |
| `tts`                    | `elevenlabs`             |
| `hero_shot_animation`    | `gemini_omni`            |
| `script_generation`      | `openai`                 |

Adding a competing vendor to a category means adding one more
`IntegrationDefinition` with the same `category_key` (e.g. a future
`heygen_competitor` entry with `category_key="avatar"`).

`google_3d_tiles` (aerial flyover), `schools_api`, and `ofcom_broadband`
each remain the sole entry in their own `category_key` — the mechanism
supports single-vendor categories without special-casing.

### 2. Active vendor storage & lookup

New module `app/services/active_vendor.py`:

```python
def get_active_vendor(session: Session, category_key: str) -> str:
    """Returns the slug of the active vendor for a category.

    Falls back to the first-registered IntegrationDefinition with that
    category_key if no explicit choice has been stored, so existing
    single-vendor categories keep working unchanged.
    """

def set_active_vendor(session: Session, category_key: str, slug: str) -> None:
    """Stores the active vendor choice. Validates that `slug` is a
    registered integration with a matching category_key."""
```

Storage: a new small table/model (e.g. `ActiveVendorChoice` with
`category_key` primary key and `vendor_slug`), following the existing
pattern used by `IntegrationSettings` for persisted admin-panel state —
not a new subsystem.

### 3. Client dispatch per category

New module `app/clients/dispatch.py` with one function per category:

```python
def get_active_avatar_client(*, session: Session | None = None) -> HeyGenClient: ...
def get_active_tts_client(*, session: Session | None = None) -> ElevenLabsClient: ...
def get_active_hero_shot_client(*, session: Session | None = None) -> GeminiOmniClient: ...
```

Each looks up `get_active_vendor(session, category_key)` and calls that
vendor's existing `get_X_client()` factory. Return types stay the existing
per-category `Protocol` (e.g. `HeyGenClient` Protocol) — every vendor client
in a category must implement that same Protocol, so adding a new vendor
means implementing the existing Protocol, not inventing a new one.

Pipeline step changes are one import line each:

- `app/pipeline/steps/avatar_and_captions.py`: `get_heygen_client()` →
  `get_active_avatar_client()`
- `app/pipeline/steps/motion.py`: `get_gemini_omni_client()` →
  `get_active_hero_shot_client()`
- `app/pipeline/steps/script_and_voice.py`: TTS client call →
  `get_active_tts_client()` (script generation itself stays on the existing
  `base_url`-driven OpenAI client — no dispatch needed there, see §6)

No other pipeline logic changes.

### 4. Admin UI changes

`app/static/admin_integrations.html` + `app/main.py`:

- Within each category section, when more than one vendor shares a
  `category_key`, render a radio button ("Active") next to each vendor's
  name.
- New endpoint: `PUT /api/categories/{category_key}/active-vendor` with
  body `{"slug": "..."}`. Validates the slug belongs to that category,
  stores it via `set_active_vendor`, returns the updated category state.
- New endpoint: `GET /api/categories` (or fold into the existing
  `GET /api/integrations` response) — each integration's serialized status
  gains `category_key` and `is_active` fields so the frontend can render
  the radio state without a second round trip.
- Inactive vendors can still have credentials saved and tested (so a backup
  vendor can be pre-configured before switching) — "fully configured" count
  on the admin page counts only the active vendor per category, not every
  vendor with any credentials saved.

### 5. Backward compatibility

Categories with only one registered vendor behave exactly as today — no
radio buttons shown (nothing to choose between), `get_active_vendor` falls
back to that one vendor automatically, dispatch functions return the same
client they always did. No migration needed for `google_3d_tiles`,
`schools_api`, `ofcom_broadband`.

### 6. Script-generation presets

Script generation is not part of the category-dispatch mechanism above,
because it already supports swapping providers today via the OpenAI
integration's existing `base_url` field (most alternative LLM providers —
Groq, OpenRouter, Together, Cerebras, self-hosted models, etc. — expose an
OpenAI-compatible chat-completions endpoint).

Add a small preset dropdown next to the `base_url` field on the OpenAI
integration's admin card: selecting a preset (e.g. "Groq", "OpenRouter")
pre-fills the `base_url` field with that provider's known endpoint. The
user still supplies their own API key. Presets are a small static list in
`integration_registry.py` (or a sibling module), not a registry entry of
their own — no new category, no new client module, no dispatch function.

## Testing

- Unit tests for `get_active_vendor` / `set_active_vendor`: default
  fallback when unset, validation rejects a slug from the wrong category.
- Unit tests for each `get_active_*_client` dispatcher: returns the right
  vendor's client when active vendor is switched.
- API test for `PUT /api/categories/{category_key}/active-vendor`: rejects
  unknown category, rejects vendor slug from a different category, persists
  and is reflected in `GET /api/integrations`.
- Existing pipeline step tests should continue to pass unchanged (dispatch
  falls back to today's single vendor per category).
