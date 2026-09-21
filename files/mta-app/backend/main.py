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


# --- Serve the frontend as a single deployable unit ---
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
