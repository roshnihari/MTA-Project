"""
Builds a station registry combining:
  1. MTA's official "Stations and Complexes" open dataset (data.ny.gov, Socrata id
     5f5g-n3cz) -- gives us, per station complex: name, borough, daytime routes
     (lines), structure type, ADA status, lat/lon, and (critically) the exact GTFS
     stop IDs belonging to that complex.
  2. nyct-gtfs's bundled GTFS static stops.txt -- gives us the platform-level stop
     IDs (with N/S suffixes, e.g. "127N") that the realtime feed keys arrivals by,
     found via each parent stop's parent_station relationship.

Cached in-memory after first load (refresh happens on process restart, which is
fine -- this data changes maybe a few times a year).
"""
import threading
import requests

from nyct_gtfs.gtfs_static_types import Stations as _BundledStations

STATIONS_COMPLEXES_URL = "https://data.ny.gov/resource/5f5g-n3cz.json?$limit=1000"

BOROUGH_NAMES = {"Bk": "Brooklyn", "M": "Manhattan", "Q": "Queens", "Bx": "Bronx", "SI": "Staten Island"}

_lock = threading.Lock()
_cache = {"complexes": None, "platforms": None, "bundled_stops": None}


def _load_bundled_stops_txt():
    """Returns dict: stop_id -> {stop_name, stop_lat, stop_lon, parent_station, location_type}"""
    return _BundledStations().stops


def _load_complexes_from_mta():
    resp = requests.get(STATIONS_COMPLEXES_URL, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    complexes = {}
    for row in rows:
        complex_id = row.get("complex_id")
        if not complex_id:
            continue
        # The Socrata API returns "number"-typed columns as JSON numbers, but this id is
        # used as a URL path segment everywhere else -- keep it a string consistently so
        # lookups by the id FastAPI parses out of a URL always hit.
        complex_id = str(complex_id)
        gtfs_stop_ids = [s.strip() for s in row.get("gtfs_stop_ids", "").split(";") if s.strip()]
        routes = row.get("daytime_routes", "")
        borough_code = row.get("borough", "")
        complexes[complex_id] = {
            "complex_id": complex_id,
            "name": row.get("stop_name", ""),
            "borough": BOROUGH_NAMES.get(borough_code, borough_code),
            "routes": routes.split() if routes else [],
            "structure": row.get("structure_type", ""),
            "ada": row.get("ada") in ("1", "2"),
            "lat": _safe_float(row.get("latitude")),
            "lon": _safe_float(row.get("longitude")),
            "gtfs_stop_ids": gtfs_stop_ids,
        }
    return complexes


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_all(force=False):
    with _lock:
        if _cache["complexes"] is not None and not force:
            return

        bundled_stops = _load_bundled_stops_txt()

        # Map every parent (station-level, no N/S suffix) stop id to its platform-level
        # (N/S-suffixed) children, using the bundled static GTFS's parent_station field.
        parent_to_platforms = {}
        for stop_id, row in bundled_stops.items():
            parent_station = row.get("parent_station", "")
            if parent_station:
                parent_to_platforms.setdefault(parent_station, []).append(stop_id)

        try:
            complexes = _load_complexes_from_mta()
        except Exception:
            complexes = {}

        if not complexes:
            # Fallback if the MTA open-data endpoint is unreachable: build a minimal
            # registry straight from the bundled static GTFS (no borough/ADA/route
            # metadata, but station search/arrivals still work).
            complexes = {}
            for stop_id, row in bundled_stops.items():
                if row.get("parent_station") or row.get("location_type") not in ("1", ""):
                    continue
                complexes[f"gtfs-{stop_id}"] = {
                    "complex_id": f"gtfs-{stop_id}",
                    "name": row.get("stop_name", ""),
                    "borough": "",
                    "routes": [],
                    "structure": "",
                    "ada": False,
                    "lat": _safe_float(row.get("stop_lat")),
                    "lon": _safe_float(row.get("stop_lon")),
                    "gtfs_stop_ids": [stop_id],
                }

        _cache["complexes"] = complexes
        _cache["platforms"] = parent_to_platforms
        _cache["bundled_stops"] = bundled_stops


def get_all_stations():
    load_all()
    # The upstream dataset's row order is arbitrary (it happens to cluster numbered-line
    # stations first) -- sort alphabetically so every line is represented right away
    # rather than only whatever the raw JSON order front-loads.
    return sorted(_cache["complexes"].values(), key=lambda c: c["name"].lower())


def search_stations(query):
    load_all()
    q = query.strip().lower()
    if not q:
        return get_all_stations()
    matches = [c for c in _cache["complexes"].values() if q in c["name"].lower()]
    return sorted(matches, key=lambda c: c["name"].lower())


def get_complex(complex_id):
    load_all()
    return _cache["complexes"].get(complex_id)


def get_platform_stop_ids(complex_id):
    """All realtime-feed stop IDs (e.g. '127N', '127S') that belong to this station complex."""
    load_all()
    c = _cache["complexes"].get(complex_id)
    if not c:
        return []
    platform_ids = []
    for parent_id in c["gtfs_stop_ids"]:
        platform_ids.append(parent_id)
        platform_ids.extend(_cache["platforms"].get(parent_id, []))
    return platform_ids


def get_stop_name(stop_id):
    load_all()
    row = _cache["bundled_stops"].get(stop_id)
    return row["stop_name"] if row else stop_id
