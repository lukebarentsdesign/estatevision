# tests/test_uk_location.py
from __future__ import annotations

from app.services import uk_location


def test_build_location_data_returns_only_amenities_and_daylight():
    result = uk_location.build_location_data(
        latitude=None,
        longitude=None,
        postcode="TE1 1ST",
        garden_orientation="south",
    )
    assert set(result.keys()) == {"amenities", "daylight"}
    assert result["daylight"]["orientation"] == "south"


def test_build_location_data_has_no_schools_or_broadband_functions():
    assert not hasattr(uk_location, "get_nearby_schools")
    assert not hasattr(uk_location, "get_broadband_info")
    assert not hasattr(uk_location, "School")
    assert not hasattr(uk_location, "BroadbandInfo")
