# Agency & Admin Authentication — Design Spec

## Purpose

The dashboard currently has no authentication anywhere. Any visitor can view, create, or modify any job, upload photos to any job, or reach the admin integrations panel. This is fine for a single-operator local tool, but the product direction is to sell this as a service to multiple separate local estate agencies, each managing their own listings. This spec adds:

1. A login for each agency (one shared login per agency, not per staff member)
2. A separate admin login for the platform owner (Luke), covering the existing `/admin` integrations panel plus a new agency-management screen
3. Scoping of all job/photo/segment data to the logged-in agency, closing the current "any ID works for anyone" gap

Out of scope: public self-service signup, multiple staff logins per agency, email notifications, self-service password reset, and hosting/deployment. These may become their own future specs.

## Accounts

### Agency accounts

`AgentProfile` (existing table, currently a pure branding profile) gains two columns:

- `email: str` — unique, used as the login identifier
- `password_hash: str` — bcrypt hash via `passlib`

`AgentProfile` already exists as the natural "tenant" row — `PropertyJob.agent_id` already references it. No new table is needed; this spec extends the existing one rather than introducing a parallel concept.

Agencies are created manually by the admin (see Admin screen below) — no public signup form. Each agency has exactly one login shared by whoever at the agency is using it.

### Admin account

A new `AdminAccount` table:

- `id`, `email` (unique), `password_hash`

Single row expected in practice (just Luke), but modeled as a table rather than an env-var credential so it can be changed without a redeploy, and so the pattern extends cleanly if a second admin is ever needed.

## Passwords

Hashed with `bcrypt` via `passlib.hash.bcrypt`. Plaintext passwords are never stored or logged.

No self-service reset flow in this spec. If an agency forgets their password, the admin resets it manually via the admin "Agencies" screen (sets a new password directly, agency is told the new password out-of-band — phone/email/in person). This matches the sales-led, high-touch relationship implied by "touting local agencies directly."

## Sessions

Signed, HTTP-only cookie session — no server-side session table. The cookie payload is `{"account_type": "agency" | "admin", "account_id": int}`, signed with `itsdangerous.URLSafeTimedSerializer` (or FastAPI's `SessionMiddleware`, which does the same thing) using a secret key read from an environment variable.

- Cookie expires after 14 days of inactivity; each authenticated request slides the expiry forward.
- `HttpOnly` always set. `Secure` flag conditional on whether the app is served over HTTPS (deployment concern, not decided in this spec — default `False` for local HTTP dev, must be `True` once deployed).
- No CSRF token in this spec — all mutating routes are same-origin form/fetch calls from the dashboard itself, and the app has no cross-origin embedding use case today. Revisit if that changes.

## Route protection

Two FastAPI dependencies, mirroring the existing dependency-injection style already used elsewhere in `app/main.py`:

- `require_agency(request) -> AgentProfile` — reads the session cookie, verifies `account_type == "agency"`, loads and returns the `AgentProfile` row. Raises a redirect to `/login` (HTML routes) or 401 (API routes) if missing/invalid/expired.
- `require_admin(request) -> AdminAccount` — same pattern for `account_type == "admin"`.

Every existing route that reads or writes `PropertyJob`, `Photo`, or `ScriptSegment` data adds `agency: AgentProfile = Depends(require_agency)` and filters/verifies against `agency.id` instead of trusting a client-supplied job ID alone. Concretely: a route like `GET /api/jobs/{job_id}/segments` must additionally check `job.agent_id == agency.id`, returning 404 (not 403, to avoid confirming the ID exists) if it belongs to a different agency.

`/admin/*` routes (existing integrations panel, plus the new Agencies screen) switch from unauthenticated to `Depends(require_admin)`.

## Pages

- `GET /login` — simple email+password form, single page serving both agency and admin login (tries agency lookup first, then admin; on match, hashes-compare and sets the session cookie). On success, redirects to `/` for agencies or `/admin` for the admin account.
- `POST /logout` — clears the session cookie, redirects to `/login`.
- New admin screen, `/admin/agencies` — list existing agencies, create one (agency name + email + initial password, hashed on save), deactivate one (adds an `is_active` bool to `AgentProfile`; `require_agency` rejects inactive agencies), reset an agency's password (sets a new hash directly).

The existing dashboard (`app/static/index.html`) is otherwise unchanged by this spec — it already only ever operates on "the current job," and will continue to do so; the only difference is that the job list and job detail routes it calls are now scoped server-side to whichever agency is logged in.

## Data model changes summary

- `AgentProfile`: add `email: str` (unique, indexed), `password_hash: str`, `is_active: bool = True`
- New `AdminAccount`: `id`, `email` (unique, indexed), `password_hash`
- No changes to `PropertyJob`, `Photo`, or `ScriptSegment` — they already carry `agent_id`/relationship back to the job's agent; scoping is enforced in route handlers, not new columns.

## Testing approach

- Unit tests for `require_agency`/`require_admin`: valid cookie, missing cookie, expired cookie, wrong account type, inactive agency.
- Integration test: agency A cannot read/write agency B's job via direct ID guessing (404 expected).
- Integration test: admin routes reject agency-session cookies and vice versa.
- Login flow test: correct credentials set a cookie and redirect; incorrect credentials show an error without revealing whether the email exists.
