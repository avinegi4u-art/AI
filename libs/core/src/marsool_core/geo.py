"""Geospatial primitives shared by merchant search, dispatch and tracking.

Heavy geospatial work (containment, nearest-neighbour ordering, distance ranking)
belongs in PostGIS. These helpers cover the in-process cases: validating
coordinates, cheap distance estimates for scoring functions, and ETA heuristics.
"""

from __future__ import annotations

import math
from typing import Final, NamedTuple

EARTH_RADIUS_M: Final = 6_371_008.8

# Abu Dhabi city centre — the default map anchor for the launch market.
ABU_DHABI_CENTER: Final = (24.4539, 54.3773)

# Average effective courier speed in dense urban traffic. Deliberately conservative:
# under-promising ETAs is cheaper than breaking them.
DEFAULT_URBAN_SPEED_KMH: Final = 18.0

# Straight-line distances underestimate real road distance. This factor converts
# haversine metres into a road-distance estimate until we wire in a routing engine.
ROAD_DISTANCE_FACTOR: Final = 1.35


class LatLng(NamedTuple):
    """A WGS84 coordinate pair."""

    lat: float
    lng: float

    def validated(self) -> LatLng:
        validate_coordinates(self.lat, self.lng)
        return self


class BoundingBox(NamedTuple):
    """An axis-aligned latitude/longitude window."""

    min_lat: float
    min_lng: float
    max_lat: float
    max_lng: float


def validate_coordinates(lat: float, lng: float) -> None:
    """Raise ``ValueError`` when the coordinate pair is outside WGS84 bounds."""
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"latitude out of range: {lat}")
    if not -180.0 <= lng <= 180.0:
        raise ValueError(f"longitude out of range: {lng}")


def haversine_meters(origin: LatLng, destination: LatLng) -> float:
    """Great-circle distance in metres between two coordinates."""
    origin_lat, origin_lng = math.radians(origin.lat), math.radians(origin.lng)
    dest_lat, dest_lng = math.radians(destination.lat), math.radians(destination.lng)
    delta_lat = dest_lat - origin_lat
    delta_lng = dest_lng - origin_lng
    inner = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(origin_lat) * math.cos(dest_lat) * math.sin(delta_lng / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(inner))


def road_distance_meters(origin: LatLng, destination: LatLng) -> float:
    """Estimate road distance by inflating the great-circle distance."""
    return haversine_meters(origin, destination) * ROAD_DISTANCE_FACTOR


def travel_time_minutes(
    distance_m: float, *, speed_kmh: float = DEFAULT_URBAN_SPEED_KMH
) -> float:
    """Convert a distance to minutes of travel at the given average speed."""
    if speed_kmh <= 0:
        raise ValueError("speed_kmh must be positive")
    return (distance_m / 1000.0) / speed_kmh * 60.0


def bounding_box(center: LatLng, radius_m: float) -> BoundingBox:
    """Return a lat/lng window that fully contains the circle around ``center``.

    Useful as a cheap index pre-filter before an exact PostGIS distance check.
    """
    if radius_m < 0:
        raise ValueError("radius_m must be non-negative")
    center.validated()
    lat_delta = math.degrees(radius_m / EARTH_RADIUS_M)
    cos_lat = math.cos(math.radians(center.lat))
    # Near the poles the longitude window degenerates; clamp to the full range.
    lng_delta = (
        180.0
        if abs(cos_lat) < 1e-12
        else math.degrees(radius_m / (EARTH_RADIUS_M * cos_lat))
    )
    return BoundingBox(
        min_lat=max(center.lat - lat_delta, -90.0),
        min_lng=max(center.lng - lng_delta, -180.0),
        max_lat=min(center.lat + lat_delta, 90.0),
        max_lng=min(center.lng + lng_delta, 180.0),
    )
