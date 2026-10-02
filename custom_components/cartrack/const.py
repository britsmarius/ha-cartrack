"""Constants for the Cartrack integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "cartrack"
MANUFACTURER: Final = "Cartrack"

CONF_REGION: Final = "region"
CONF_SCAN_INTERVAL_PARKED: Final = "scan_interval_parked"
CONF_SCAN_INTERVAL_MOVING: Final = "scan_interval_moving"
CONF_STALE_TIMEOUT: Final = "stale_timeout"

DEFAULT_REGION: Final = "za"
DEFAULT_SCAN_INTERVAL_PARKED: Final = 60
DEFAULT_SCAN_INTERVAL_MOVING: Final = 10
DEFAULT_STALE_TIMEOUT: Final = 180

FRONTEND_URL_BASE: Final = "/cartrack_frontend"
CARD_FILENAME: Final = "cartrack-route-card.js"

MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 3600

# Regions Cartrack runs a Fleet API in. Kenya and Saudi Arabia are hosted
# on the Karooooo domain instead of cartrack.com.
REGIONS: Final[dict[str, str]] = {
    "za": "South Africa",
    "bw": "Botswana",
    "ke": "Kenya",
    "mw": "Malawi",
    "mz": "Mozambique",
    "na": "Namibia",
    "ng": "Nigeria",
    "sz": "Eswatini",
    "tz": "Tanzania",
    "zw": "Zimbabwe",
    "hk": "Hong Kong",
    "id": "Indonesia",
    "my": "Malaysia",
    "ph": "Philippines",
    "sa": "Saudi Arabia",
    "sg": "Singapore",
    "th": "Thailand",
    "nz": "New Zealand",
    "me": "Middle East",
    "pl": "Poland",
    "pt": "Portugal",
    "es": "Spain",
}
KAROOOOO_REGIONS: Final = frozenset({"ke", "sa"})

REQUEST_TIMEOUT: Final = timedelta(seconds=30)
DEFAULT_RATE_LIMIT_BACKOFF: Final = 60
