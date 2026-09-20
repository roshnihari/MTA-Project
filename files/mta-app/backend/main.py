import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import stations
import mta_client

app = FastAPI(title="NYC Subway Live")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")


@app.on_event("startup")
def _warm_station_cache():
    try:
        stations.load_all()
    except Exception as e:
        # Don't crash the app if the upstream dataset is briefly unavailable --
        # station search/arrivals will just retry lazily on first request.
        print(f"[startup] station data preload failed, will retry lazily: {e}")


@app.get("/api/stations")
def list_stations(q: str = Query("", description="Search text, e.g. 'union sq'")):
    if q:
        return stations.search_stations(q)
    return stations.get_all_stations()


@app.get("/api/stations/{complex_id}")
def station_detail(complex_id: str):
    c = stations.get_complex(complex_id)
    if not c:
        raise HTTPException(404, "Station not found")
    return c


@app.get("/api/stations/{complex_id}/arrivals")
def station_arrivals(complex_id: str):
    c = stations.get_complex(complex_id)
    if not c:
        raise HTTPException(404, "Station not found")
    return mta_client.get_arrivals_for_station(complex_id)


@app.get("/api/lines/{route_id}/status")
def line_status(route_id: str):
    return mta_client.get_line_status(route_id.upper())


@app.get("/api/alerts")
def alerts(route: str = Query(None, description="Filter to a single route, e.g. 'A'")):
    return mta_client.get_alerts(route.upper() if route else None)


@app.get("/api/trip-plan")
def trip_plan(from_id: str = Query(..., alias="from"), to_id: str = Query(..., alias="to")):
    """
    Lightweight trip helper: finds lines common to both stations (a direct ride) or, failing
    that, suggests transfer stations shared by a line from the origin and a line into the
    destination -- then reports live delay status for the relevant lines. This is NOT a full
    shortest-path router; it's meant to answer "what are my options and are they delayed".
    """
    origin = stations.get_complex(from_id)
    dest = stations.get_complex(to_id)
    if not origin or not dest:
        raise HTTPException(404, "Station not found")

    origin_routes = set(origin["routes"])
    dest_routes = set(dest["routes"])
    direct = sorted(origin_routes & dest_routes)

    transfer_options = []
    if not direct:
        all_stations = stations.get_all_stations()
        for s in all_stations:
            s_routes = set(s["routes"])
            if s["complex_id"] in (from_id, to_id):
                continue
            if origin_routes & s_routes and dest_routes & s_routes:
                transfer_options.append({
                    "via_station": s["name"],
                    "via_complex_id": s["complex_id"],
                    "first_leg_routes": sorted(origin_routes & s_routes),
                    "second_leg_routes": sorted(dest_routes & s_routes),
                })
                if len(transfer_options) >= 5:
                    break

    involved_routes = sorted(direct) if direct else sorted({r for t in transfer_options
                                                              for r in t["first_leg_routes"] + t["second_leg_routes"]})
    line_alerts = []
    for r in involved_routes:
        line_alerts.extend(mta_client.get_alerts(r))
    # de-dupe alerts by id
    seen = set()
    unique_alerts = []
    for a in line_alerts:
        if a["id"] not in seen:
            seen.add(a["id"])
            unique_alerts.append(a)

    return {
        "origin": origin["name"],
        "destination": dest["name"],
        "direct_routes": direct,
        "transfer_options": transfer_options,
        "alerts": unique_alerts,
    }


# --- Serve the frontend as a single deployable unit ---
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
