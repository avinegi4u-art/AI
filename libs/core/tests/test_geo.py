"""Geospatial helpers."""

from __future__ import annotations

import pytest

from marsool_core.geo import (
    ABU_DHABI_CENTER,
    LatLng,
    bounding_box,
    haversine_meters,
    road_distance_meters,
    travel_time_minutes,
    validate_coordinates,
)

# Two well-known Abu Dhabi landmarks roughly 8.6 km apart.
CORNICHE = LatLng(24.4672, 54.3216)
MARINA_MALL = LatLng(24.4759, 54.3220)
YAS_ISLAND = LatLng(24.4886, 54.6070)


def test_haversine_matches_known_distance() -> None:
    distance = haversine_meters(LatLng(*ABU_DHABI_CENTER), YAS_ISLAND)
    assert 23_000 < distance < 25_000


def test_haversine_is_symmetric_and_zero_for_same_point() -> None:
    assert haversine_meters(CORNICHE, MARINA_MALL) == pytest.approx(
        haversine_meters(MARINA_MALL, CORNICHE)
    )
    assert haversine_meters(CORNICHE, CORNICHE) == pytest.approx(0.0)


def test_road_distance_exceeds_straight_line() -> None:
    straight = haversine_meters(CORNICHE, YAS_ISLAND)
    assert road_distance_meters(CORNICHE, YAS_ISLAND) > straight


def test_travel_time_scales_with_distance() -> None:
    assert travel_time_minutes(18_000, speed_kmh=18.0) == pytest.approx(60.0)
    assert travel_time_minutes(9_000, speed_kmh=18.0) == pytest.approx(30.0)


def test_travel_time_rejects_non_positive_speed() -> None:
    with pytest.raises(ValueError, match="speed_kmh must be positive"):
        travel_time_minutes(1_000, speed_kmh=0)


def test_bounding_box_contains_points_within_radius() -> None:
    center = LatLng(*ABU_DHABI_CENTER)
    box = bounding_box(center, 5_000)
    assert box.min_lat < center.lat < box.max_lat
    assert box.min_lng < center.lng < box.max_lng
    # A point 1 km north must be inside the 5 km window.
    assert box.min_lat < center.lat + 0.009 < box.max_lat


def test_bounding_box_clamps_at_the_pole() -> None:
    box = bounding_box(LatLng(90.0, 0.0), 10_000)
    assert box.max_lat == 90.0
    assert (box.min_lng, box.max_lng) == (-180.0, 180.0)


@pytest.mark.parametrize(("lat", "lng"), [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)])
def test_invalid_coordinates_rejected(lat: float, lng: float) -> None:
    with pytest.raises(ValueError, match="out of range"):
        validate_coordinates(lat, lng)
