"""UK neighbourhood data aggregator (spec §5).

Combines independent sources into `job.location_data_json`. Each source is
fetched defensively -- one API being down must not prevent the others from
returning data, since this is marketing colour, not a compliance surface.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import asdict, dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

OVERPASS_API_BASE = "https://overpass-api.de/api/interpreter"

_REQUEST_TIMEOUT = 10.0


@dataclass(frozen=True)
class Amenity:
    name: str
    category: str
    distance_m: float


@dataclass(frozen=True)
class DaylightInfo:
    orientation: str
    statement: str


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


_OVERPASS_CATEGORIES: dict[str, str] = {
    "cafe": 'node["amenity"="cafe"]',
    "station": 'node["railway"="station"]',
    "park": 'node["leisure"="park"]',
    "supermarket": 'node["shop"="supermarket"]',
}


def get_nearby_amenities(
    latitude: float, longitude: float, *, radius_m: int = 1000, client: httpx.Client | None = None
) -> list[Amenity]:
    """Cafes, stations, parks, supermarkets within `radius_m` (§5.2)."""
    query_parts = "\n".join(
        f'{selector}(around:{radius_m},{latitude},{longitude});'
        for selector in _OVERPASS_CATEGORIES.values()
    )
    query = f"[out:json][timeout:10];(\n{query_parts}\n);out body;"

    owns_client = client is None
    client = client or httpx.Client(timeout=_REQUEST_TIMEOUT)
    try:
        resp = client.post(OVERPASS_API_BASE, data={"data": query})
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("overpass lookup failed: %s", exc)
        return []
    finally:
        if owns_client:
            client.close()

    amenities: list[Amenity] = []
    for el in elements:
        tags = el.get("tags", {})
        category = next(
            (cat for cat, sel in _OVERPASS_CATEGORIES.items() if _matches_category(tags, cat)),
            "other",
        )
        amenities.append(
            Amenity(
                name=tags.get("name", category.title()),
                category=category,
                distance_m=_haversine_km(latitude, longitude, el["lat"], el["lon"]) * 1000,
            )
        )
    return sorted(amenities, key=lambda a: a.distance_m)


def _matches_category(tags: dict[str, str], category: str) -> bool:
    return {
        "cafe": tags.get("amenity") == "cafe",
        "station": tags.get("railway") == "station",
        "park": tags.get("leisure") == "park",
        "supermarket": tags.get("shop") == "supermarket",
    }.get(category, False)


def get_daylight_info(garden_orientation: str | None) -> DaylightInfo | None:
    """Solar-position-derived daylight statement from garden orientation (§5.4)."""
    if not garden_orientation:
        return None

    orientation = garden_orientation.strip()
    statements: dict[str, str] = {
        "south": "The garden enjoys sun for most of the day.",
        "south-west": "The garden catches afternoon and evening sun.",
        "south-east": "The garden catches morning and midday sun.",
        "west": "The garden is best in the afternoon and evening.",
        "east": "The garden is best in the morning.",
        "north": "The garden is shaded for much of the day.",
        "north-west": "The garden gets late-afternoon and evening light.",
        "north-east": "The garden gets early-morning light.",
    }
    key = orientation.lower()
    statement = statements.get(key, f"The garden faces {orientation}.")
    return DaylightInfo(orientation=orientation, statement=statement)


def build_location_data(
    *,
    latitude: float | None,
    longitude: float | None,
    postcode: str,
    garden_orientation: str | None,
) -> dict[str, Any]:
    """Aggregate amenities and daylight into the dict stored on `job.location_data_json`.

    Schools and broadband were removed from this aggregation (spec: remove
    schools/broadband design, 2026-08-13) -- they added marketing colour the
    user judged not worth the API surface and screen time. `postcode` is kept
    as a parameter for signature stability even though it's unused here,
    since `app.main.refresh_location_data` already calls this by keyword and
    daylight/amenities may grow a postcode-based source later.
    """
    amenities: list[Amenity] = []

    if latitude is not None and longitude is not None:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            amenities = get_nearby_amenities(latitude, longitude, client=client)

    daylight = get_daylight_info(garden_orientation)

    return {
        "amenities": [asdict(a) for a in amenities],
        "daylight": asdict(daylight) if daylight else None,
    }
