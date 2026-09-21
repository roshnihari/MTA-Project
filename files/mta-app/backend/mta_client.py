"""
Wraps the MTA's public GTFS-realtime subway feeds (no API key required as of 2024).

Real-time trip data comes via the `nyct_gtfs` library (handles the NYCT protobuf
extension for us). Service alerts come via the plain GTFS-realtime `Alert` feed,
parsed with `gtfs_realtime_bindings` since alerts don't need the NYCT trip extension.
"""
import time
import threading
import requests
from datetime import datetime

# NOTE: we deliberately reuse nyct_gtfs's *bundled* copy of the standard GTFS-realtime
# proto (rather than also importing the separate `gtfs-realtime-bindings` package) because
# both packages compile a proto file with the same internal name, and protobuf's global
# descriptor pool raises on a duplicate registration if both get imported in one process.
from nyct_gtfs import NYCTFeed
from nyct_gtfs.compiled_gtfs import gtfs_realtime_pb2

import stations

FEED_URLS = {
    "123456S": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs",
    "ACEHFS": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-ace",
    "BDFM": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-bdfm",
    "G": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-g",
    "JZ": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-jz",
    "NQRW": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-nqrw",
    "L": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-l",
    "SIR": "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs-si",
}

ALERTS_FEED_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts"

CACHE_TTL_SECONDS = 25  # MTA republishes roughly every 30s; no point polling faster

_feed_lock = threading.Lock()
_feed_cache = {}  # group -> {"feed": NYCTFeed, "trips": [...], "fetched_at": ts}

_alerts_lock = threading.Lock()
_alerts_cache = {"alerts": [], "fetched_at": 0}


def _get_feed_trips(group_key):
    now = time.time()
    with _feed_lock:
        cached = _feed_cache.get(group_key)
        if cached and now - cached["fetched_at"] < CACHE_TTL_SECONDS:
            return cached["trips"], cached["generated_at"]

        feed = NYCTFeed(FEED_URLS[group_key])
        trips = feed.trips
        generated_at = feed.last_generated
        _feed_cache[group_key] = {"trips": trips, "fetched_at": now, "generated_at": generated_at}
        return trips, generated_at


def _all_trips():
    """Fetches all 8 feeds (each cached independently) and returns a combined trip list."""
    combined = []
    freshest = None
    for group_key in FEED_URLS:
        try:
            trips, generated_at = _get_feed_trips(group_key)
        except Exception:
            continue
        combined.extend(trips)
        if freshest is None or (generated_at and generated_at > freshest):
            freshest = generated_at
    return combined, freshest


def _serialize_stop_time_update(stu, now):
    eta = stu.arrival or stu.departure
    seconds_away = (eta - now).total_seconds() if eta else None
    return {
        "stop_id": stu.stop_id,
        "stop_name": stu.stop_name,
        "arrival": eta.isoformat() if eta else None,
        "minutes_away": round(seconds_away / 60, 1) if seconds_away is not None else None,
        "scheduled_track": stu.scheduled_track,
        "actual_track": stu.actual_track,
        "rerouted": bool(stu.scheduled_track and stu.actual_track and stu.scheduled_track != stu.actual_track),
    }


def get_arrivals_for_station(complex_id, limit_per_direction=6):
    """
    Returns upcoming arrivals for every platform belonging to this station complex,
    grouped by route + direction, each annotated with a live delay flag.
    """
    stop_ids = set(stations.get_platform_stop_ids(complex_id))
    if not stop_ids:
        return {"stop_ids": [], "generated_at": None, "arrivals": []}

    trips, generated_at = _all_trips()
    now = datetime.now()

    results = []
    for trip in trips:
        for stu in trip.stop_time_updates:
            if stu.stop_id not in stop_ids:
                continue
            eta = stu.arrival or stu.departure
            if eta is None or eta < now:
                continue
            results.append({
                "route_id": trip.route_id,
                "direction": trip.direction,
                "headsign": trip.headsign_text,
                "underway": trip.underway,
                "location_status": trip.location_status,
                "has_delay_alert": trip.has_delay_alert,
                **_serialize_stop_time_update(stu, now),
            })

    results.sort(key=lambda r: (r["route_id"], r["direction"], r["minutes_away"] if r["minutes_away"] is not None else 999))

    # Trim to `limit_per_direction` per (route, direction) so the board stays readable
    trimmed = []
    seen_counts = {}
    for r in results:
        key = (r["route_id"], r["direction"])
        seen_counts[key] = seen_counts.get(key, 0) + 1
        if seen_counts[key] <= limit_per_direction:
            trimmed.append(r)

    return {
        "stop_ids": list(stop_ids),
        "generated_at": generated_at.isoformat() if generated_at else None,
        "arrivals": trimmed,
    }


def get_line_status(route_id):
    """All currently-tracked trips for a given line, with delay/reroute flags."""
    trips, generated_at = _all_trips()
    line_trips = [t for t in trips if t.route_id == route_id]
    return {
        "route_id": route_id,
        "generated_at": generated_at.isoformat() if generated_at else None,
        "trip_count": len(line_trips),
        "delayed_count": sum(1 for t in line_trips if t.has_delay_alert),
        "trips": [
            {
                "direction": t.direction,
                "headsign": t.headsign_text,
                "underway": t.underway,
                "location": t.location,
                "location_status": t.location_status,
                "has_delay_alert": t.has_delay_alert,
            }
            for t in line_trips
        ],
    }


def _fetch_alerts():
    resp = requests.get(ALERTS_FEED_URL, timeout=20)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)

    alerts = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        header = alert.header_text.translation[0].text if alert.header_text.translation else ""
        description = alert.description_text.translation[0].text if alert.description_text.translation else ""
        routes = sorted({ie.route_id for ie in alert.informed_entity if ie.route_id})

        # GTFS-realtime doesn't have an explicit "posted at" field, but each alert's
        # active_period.start is the standard signal for when it took effect -- the
        # earliest one across periods is the closest equivalent to a post time.
        starts = [p.start for p in alert.active_period if p.start]
        posted_at = datetime.fromtimestamp(min(starts)).isoformat() if starts else None

        alerts.append({
            "id": entity.id,
            "header": header,
            "description": description,
            "routes": routes,
            "effect": gtfs_realtime_pb2.Alert.Effect.Name(alert.effect) if alert.effect is not None else None,
            "posted_at": posted_at,
        })

    # Most recent first, so any "top N" trimming downstream keeps the freshest alerts.
    alerts.sort(key=lambda a: a["posted_at"] or "", reverse=True)
    return alerts


def get_alerts(route_id=None):
    now = time.time()
    with _alerts_lock:
        if now - _alerts_cache["fetched_at"] > CACHE_TTL_SECONDS:
            try:
                _alerts_cache["alerts"] = _fetch_alerts()
                _alerts_cache["fetched_at"] = now
            except Exception:
                pass  # serve stale cache rather than failing the request
        alerts = _alerts_cache["alerts"]

    if route_id:
        alerts = [a for a in alerts if route_id in a["routes"]]
    return alerts
