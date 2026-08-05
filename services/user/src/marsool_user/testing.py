"""Sample data shipped with the user service.

Real Abu Dhabi coordinates, shared by tests and the local seed script so that both
exercise the same geography and a developer's local database looks like the launch market.
"""

from __future__ import annotations

from typing import Any, Final

#: Sky Tower, Shams Abu Dhabi — Al Reem Island.
AL_REEM_ISLAND: Final[dict[str, Any]] = {
    "label": "HOME",
    "line1": "Sky Tower, Shams Abu Dhabi",
    "apartment": "1204",
    "community": "Al Reem Island",
    "city": "Abu Dhabi",
    "emirate": "Abu Dhabi",
    "country_code": "AE",
    "latitude": "24.494640",
    "longitude": "54.399460",
    "delivery_notes": "Leave with the concierge",
}

#: Yas Mall, Yas Island — roughly 24 km east of Al Reem Island.
YAS_ISLAND: Final[dict[str, Any]] = {
    "label": "WORK",
    "line1": "Yas Mall, Yas Island",
    "community": "Yas Island",
    "city": "Abu Dhabi",
    "emirate": "Abu Dhabi",
    "country_code": "AE",
    "latitude": "24.488600",
    "longitude": "54.607000",
}

#: Marina Mall, Corniche — central and close to most launch merchants.
CORNICHE: Final[dict[str, Any]] = {
    "label": "OTHER",
    "line1": "Marina Mall, Breakwater",
    "community": "Al Marina",
    "city": "Abu Dhabi",
    "emirate": "Abu Dhabi",
    "country_code": "AE",
    "latitude": "24.475900",
    "longitude": "54.322000",
}
