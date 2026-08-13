# Staff Members (Phase 1: Data Model + Admin UI) — Design Spec

## Purpose

Removing the unauthenticated `/api/agents` endpoint (agency/admin auth branch) also removed the only way to set an `AgentProfile`'s branding fields (colors) and AI-service identifiers (HeyGen avatar ID, ElevenLabs voice ID). While restoring that, the user clarified the underlying need is bigger than "one voice/avatar per agency": an agency has up to five staff members, each with their own face (HeyGen avatar) and voice (ElevenLabs), onboarded once and reused across every future property job — the agency picks which staff member presents a given listing, rather than the agency having one fixed presenter.

This spec covers **Phase 1 only**: the `StaffMember` data model and an admin-only UI/API to manage it, plus restoring agency branding-color editing. It deliberately does **not** touch the video-generation pipeline, which continues to read voice/avatar IDs from `AgentProfile` exactly as it does today. Wiring a job to an actual chosen staff member (the job-creation picker, and rewiring `services/consent.py`/`pipeline/registry.py`/pipeline steps to read from the selected staff member instead of the agency) is an explicit, separate follow-up spec once this phase is proven in place.

## Data model

New `StaffMember` table:

- `id`, `agent_id` (foreign key to `AgentProfile`, the owning agency)
- `staff_name: str`
- `heygen_avatar_id: Optional[str]`
- `elevenlabs_voice_id: Optional[str]`
- `voice_consent_confirmed: bool = False`
- `created_at`

A maximum of 5 `StaffMember` rows per `AgentProfile`, enforced at the API layer (not a DB constraint — SQLite has no portable row-count check) by counting existing rows before insert and rejecting the 6th with a 400.

`AgentProfile` keeps `primary_color`, `secondary_color`, `logo_path` (agency-wide branding) and drops nothing — `staff_name`, `heygen_avatar_id`, `elevenlabs_voice_id`, `voice_consent_confirmed` remain on `AgentProfile` too, unused by any new code path in this phase, since the pipeline still reads them there. They become dead/superseded once the Phase 2 pipeline rewiring lands and reads from the job's chosen `StaffMember` instead; removing them is out of scope for this phase to avoid breaking the still-functioning pipeline.

Headshot image upload (`staff_headshot_path`) is out of scope for this phase — text/ID fields only. When added later, it should reuse the existing per-job photo-upload pattern (`app/main.py`'s `upload_photos` route: capped read, image-content-type allowlist, `uuid`-named files under a per-owner upload directory).

## Consent

Voice consent (`voice_consent_confirmed`) is tracked per staff member, since consent is inherently about a specific person's voice being cloned, not the agency as a whole. The existing `services/consent.py` pattern (`set_elevenlabs_voice`, `require_voice_for_narration`, `require_avatar`, `ConsentError`) is reused, re-targeted to operate on a `StaffMember` instead of an `AgentProfile` — same refuse-without-consent behavior, same "clearing the voice ID also clears consent" behavior. These re-targeted functions are only exercised by the new admin staff-management routes in this phase; the pipeline's existing calls into `consent.py` (still operating on `AgentProfile`) are untouched.

## Admin UI

Extends the existing `/admin/agencies` page rather than adding a new page. Each agency row gets an expandable/linked detail view showing:

- The agency's branding fields (primary/secondary color) — editable, restoring what `/api/agents` used to allow.
- A staff roster (0–5 `StaffMember` rows): name, HeyGen avatar ID, ElevenLabs voice ID, a consent checkbox. Add staff (blocked at 5 with a clear message), edit, remove.

Consistent with the existing admin_agencies.html style (Tailwind via CDN, vanilla JS fetch calls, `escapeHtml()` for any interpolated user-supplied text — required here since `staff_name` is free text, following the precedent already fixed for `agency_name`/`email` on this same page).

## API

- `PATCH /api/admin/agencies/{agency_id}` — extended to also accept `primary_color`/`secondary_color` (optional fields, alongside the existing `is_active`/`new_password`).
- `GET /api/admin/agencies/{agency_id}/staff` — list staff for an agency.
- `POST /api/admin/agencies/{agency_id}/staff` — create a staff member (`staff_name` required; `heygen_avatar_id`/`elevenlabs_voice_id`/`voice_consent_confirmed` optional, consent-checked via the re-targeted `consent.py` if a voice ID is supplied). Rejects with 400 if the agency already has 5.
- `PATCH /api/admin/agencies/{agency_id}/staff/{staff_id}` — update any field; setting `elevenlabs_voice_id` requires `voice_consent_confirmed: true` in the same request (mirrors `set_elevenlabs_voice`'s refuse-without-consent rule).
- `DELETE /api/admin/agencies/{agency_id}/staff/{staff_id}` — remove a staff member.

All routes admin-gated (`Depends(require_admin)`), matching every other route under `/api/admin/*`.

## Testing approach

- Unit tests for the re-targeted consent functions operating on `StaffMember`.
- Integration tests: create up to 5 staff, 6th rejected with 400; create/update/delete staff via the admin API; branding-color PATCH on `/api/admin/agencies/{id}`; non-admin session rejected on all new routes (401).
- No changes needed to existing pipeline tests — this phase doesn't touch `pipeline/`, `services/consent.py`'s existing `AgentProfile`-facing functions, or any route outside `/api/admin/*`.
