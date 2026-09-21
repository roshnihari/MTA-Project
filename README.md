https://mta-project-docker.onrender.com

# NYC Subway Live

A single web app (FastAPI backend + a plain HTML/JS frontend it serves itself) that shows
live NYC subway arrivals, delay/reroute flags, service alerts, and a simple line-connection
helper between two stations. Built entirely on MTA's free, no-API-key-required open data:

- **Real-time trip data**: MTA's GTFS-realtime subway feeds (`api-endpoint.mta.info`),
  parsed with the [`nyct-gtfs`](https://pypi.org/project/nyct-gtfs/) Python library.
- **Service alerts**: MTA's GTFS-realtime alerts feed.
- **Station/line reference data**: NY State's open dataset,
  [MTA Subway Stations and Complexes](https://data.ny.gov/resource/5f5g-n3cz.json)
  (names, boroughs, which lines serve each station, ADA status, structure type).

No API keys, no paid services, no rate-limit signup anywhere in this stack.

## Why this needs its own deployment (it can't run as a Claude Artifact)

Claude's built-in "Artifact" web pages run in a sandbox that only allows outbound requests
to a short allow-list of script CDNs — it can't call MTA's servers directly, with or without
a backend in front of it. So this app is a normal, small web service you deploy yourself.
Every option below is free.

## Project layout

```
mta-app/
├── backend/           FastAPI app (also serves the frontend)
│   ├── main.py
│   ├── mta_client.py  live feeds + alerts
│   ├── stations.py    station/line reference data
│   └── requirements.txt
├── frontend/
│   └── index.html     the whole UI, single file, no build step
└── Dockerfile          optional, for container-based hosts
```

## Run it locally first (optional but recommended)

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000 — you should see live arrivals once you search a station.

## Deploy for free — pick one

### Option A: Render.com (easiest, no Docker needed)

1. Push this folder to a new **GitHub repo** (GitHub is free).
2. Go to [render.com](https://render.com) → sign up free → **New +** → **Web Service** →
   connect your GitHub repo.
3. Settings:
   - **Root Directory**: `backend`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: Free
4. Deploy. Render gives you a URL like `https://your-app.onrender.com` — open that on your
   phone or laptop.

**Free-tier caveat**: Render's free web services spin down after ~15 minutes of no traffic
and take 30–60 seconds to wake back up on the next request. Fine for a personal commute app;
just expect the first load of the day to be slow.

### Option B: Fly.io (free allowance, uses the included Dockerfile, no spin-down on their smallest always-on config)

1. Install the Fly CLI and `fly auth signup` (free).
2. From the `mta-app/` folder: `fly launch` (it'll detect the Dockerfile — accept defaults,
   decline a database).
3. `fly deploy`.
4. Fly gives you a URL like `https://your-app.fly.dev`.

### Option C: Railway.app

Similar flow to Render: connect the GitHub repo, set root directory to `backend`, same
build/start commands as Option A. Railway's free tier is usage-credit based rather than
always-free, but comfortably covers a low-traffic personal app.

### Option D: Your own AWS (since you already work with AWS)

Simplest free path there is **AWS App Runner** or an **EC2 t3.micro/t2.micro on the Free
Tier** running the Dockerfile with `docker run -p 8000:8000 ...`. More setup than A–C, but
zero third-party accounts if you'd rather keep everything under your own AWS account.

## After deploying

Open the URL on your phone, add it to your home screen (Safari/Chrome → "Add to Home
Screen") and it behaves like a lightweight app icon.

## Known limitations / honest caveats

- **"Station layout"** here means *which lines and platforms serve a station* (from MTA's
  official complex dataset), not a visual entrance/exit map. MTA does publish a separate
  [Subway Entrances and Exits dataset](https://data.ny.gov) with entrance coordinates if you
  want to extend this later.
- **Delay detection** uses the same mechanism MTA's own countdown clocks use: live ETAs plus
  the feed's `has_delay_alert` flag on each train, and a reroute flag when a train's actual
  track differs from its scheduled one. It's not a precise "X minutes late" number — the
  realtime feed doesn't reliably expose the original static schedule to diff against.
- **Trip Helper** is a lightweight "what lines connect these two stations, live" tool, not a
  full shortest-path trip planner (no walking directions, no optimal-transfer search across
  multiple hops). Good enough to answer "should I transfer or just wait", not meant to
  replace the MTA app's full journey planner.
- The MTA open-data endpoint (`data.ny.gov`) is occasionally slow/rate-limited; the app
  caches it in memory for the life of the process and falls back to a minimal
  station list (built from bundled GTFS data) if it's briefly unreachable.
