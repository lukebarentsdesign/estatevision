# Remove Schools/Broadband Location Data — Design Spec

## Purpose

`app/services/uk_location.py` aggregates four data sources into `PropertyJob.location_data_json`: nearby schools (DfE/Ofsted API), broadband coverage (Ofcom API), nearby amenities (OpenStreetMap/Overpass), and daylight/garden-orientation (computed locally, no API). The user judged schools and broadband to be two disconnected data points not worth the screen time in a property video, while amenities (cafes, transport, parks, shops) and daylight are directly relevant and worth keeping. This spec removes schools and broadband only.

Wiring the surviving amenities/daylight data into actual script generation (so an agent's script can reference "2 minutes from the station") is explicitly out of scope — that's a separate future piece of work. Today none of `location_data_json` reaches script generation; this spec doesn't change that, it only removes two fields from what's fetched and displayed.

## Changes

**`app/services/uk_location.py`**: remove `get_nearby_schools`, `get_broadband_info`, the `School` and `BroadbandInfo` dataclasses, and the `SCHOOLS_API_BASE`/`OFCOM_API_BASE` constants. `build_location_data` drops the `schools = ...` and `broadband = ...` calls and the corresponding keys from its returned dict — it now returns only `{"amenities": [...], "daylight": {...} | None}`.

**`app/services/integration_registry.py`**: remove the `schools_api` (DfE/Ofsted) and `ofcom_broadband` integration entries. They disappear from the admin integrations panel; no other registered integration depends on them.

**`app/static/index.html`**: `renderLocationData` drops its Schools and Broadband lines. Daylight remains. (Amenities was never rendered here and stays that way — out of scope per the earlier scoping decision.)

**`app/pipeline/steps/cinematic.py`**: the microsite/export HTML builder (`_render_html`/whatever renders schools/broadband into the property pack) drops its schools list and broadband-speed line. Any hardcoded copy referencing "excellent schools and transport links" in export copy is reviewed and adjusted to not reference schools specifically (transport is amenities-derived and can stay).

## Data shape change

`location_data_json`'s shape changes from `{schools, amenities, broadband, daylight}` to `{amenities, daylight}`. This is a breaking change to the stored JSON shape for any job whose `location_data_json` was already populated before this change ships — old jobs keep their old (now-unused) `schools`/`broadband` keys sitting in already-stored JSON blobs; nothing reads them anymore, so they're harmless dead data, not a migration concern (SQLite JSON columns don't enforce shape).

## Testing approach

Existing tests asserting on `schools`/`broadband` keys in `location_data_json`, or referencing the `schools_api`/`ofcom_broadband` integration slugs, are removed or updated to match the new two-key shape. No new tests are needed beyond confirming `build_location_data` returns only `amenities`/`daylight` and that the two integration slugs no longer appear in `list_integrations()`.
