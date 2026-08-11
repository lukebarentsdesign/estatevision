"""UK neighbourhood data aggregator (spec §5).

Combines four independent sources into `job.location_data_json`. Each source is
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

SCHOOLS_API_BASE = "https://get-information-schools.service.gov.uk/api"
OVERPASS_API_BASE = "https://overpass-api.de/api/interpreter"
OFCOM_API_BASE = "https://api.checker.ofcom.org.uk"

_REQUEST_TIMEOUT = 10.0


@dataclass(frozen=True)
class School:
    name: str
    phase: str
    ofsted_rating: str
    distance_km: float


@dataclass(frozen=True)
class Amenity:
    name: str
    category: str
    distance_m: float


@dataclass(frozen=True)
class BroadbandInfo:
    max_download_mbps: float | None
    fttp_available: bool
    ultrafast_available: bool
    five_g_available: bool


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


def get_nearby_schools(
    latitude: float, longitude: float, *, limit: int = 3, client: httpx.Client | None = None
) -> list[School]:
    """3 nearest Outstanding/Good schools (§5.1). Empty list on failure."""
    owns_client = client is None
    client = client or httpx.Client(timeout=_REQUEST_TIMEOUT)
    try:
        resp = client.get(
            f"{SCHOOLS_API_BASE}/schools",
            params={"lat": latitude, "lon": longitude, "radius_km": 5},
        )
        resp.raise_for_status()
        raw = resp.json().get("schools", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("schools lookup failed: %s", exc)
        return []
    finally:
        if owns_client:
            client.close()

    schools = [
        School(
            name=s["name"],
            phase=s.get("phase", "unknown"),
            ofsted_rating=s.get("ofsted_rating", "unknown"),
            distance_km=_haversine_km(latitude, longitude, s["lat"], s["lon"]),
        )
        for s in raw
        if s.get("ofsted_rating") in {"Outstanding", "Good"}
    ]
    return sorted(schools, key=lambda s: s.distance_km)[:limit]


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


def get_broadband_info(
    postcode: str, *, client: httpx.Client | None = None
) -> BroadbandInfo:
    """Max download speed and coverage flags (§5.3). Defaults on failure."""
    owns_client = client is None
    client = client or httpx.Client(timeout=_REQUEST_TIMEOUT)
    try:
        resp = client.get(f"{OFCOM_API_BASE}/coverage", params={"postcode": postcode})
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("broadband lookup failed: %s", exc)
        return BroadbandInfo(None, False, False, False)
    finally:
        if owns_client:
            client.close()

    return BroadbandInfo(
        max_download_mbps=data.get("max_download_mbps"),
        fttp_available=bool(data.get("fttp_available")),
        ultrafast_available=bool(data.get("ultrafast_available")),
        five_g_available=bool(data.get("five_g_available")),
    )


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
    """Aggregate all four sources into the dict stored on `job.location_data_json`."""
    schools: list[School] = []
    amenities: list[Amenity] = []

    if latitude is not None and longitude is not None:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
            schools = get_nearby_schools(latitude, longitude, client=client)
            amenities = get_nearby_amenities(latitude, longitude, client=client)

    broadband = get_broadband_info(postcode)
    daylight = get_daylight_info(garden_orientation)

    return {
        "schools": [asdict(s) for s in schools],
        "amenities": [asdict(a) for a in amenities],
        "broadband": asdict(broadband),
        "daylight": asdict(daylight) if daylight else None,
    }
