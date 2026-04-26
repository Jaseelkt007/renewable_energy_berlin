# viewer_app — visualization bridge - ONLY FOR VISUALIZATION OF 3D FILE

Browser viewer for the AI Renewable Designer roof pipeline (M0–M8). It proves the
generated roof JSON aligns with each GLB and provides interactive verification:
hover the model to inspect detected plane info, click the model to refit a plane
locally via the M8 seeded pipeline.

The viewer reads the roof JSON and GLBs that the Python pipeline already wrote to
`out/` and the repo root; it does not re-run any geometry.

## Layout

```
viewer_app/
  backend/        FastAPI service that serves project list, roof JSON, and GLBs
    main.py
    project_map.json
    requirements.txt
  frontend/       Next.js 14 (App Router) + React Three Fiber viewer
    app/
      layout.jsx
      page.jsx
      globals.css
    components/
      ProjectSelector.jsx
      SummaryPanel.jsx
      OverlayControls.jsx
      RoofScene.jsx
      PolygonOverlay.jsx
      ObstructionOverlay.jsx
      PanelOverlay.jsx
    package.json
    next.config.js
    .env.local.example
  README.md
```

## Running it

### 1. Backend (FastAPI on port 8001)

```bash
cd viewer_app/backend
python3 -m venv .venv
source .venv/bin/activate          # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
# WSL → Windows browser: bind 0.0.0.0 so the Windows host can reach it.
# Plain 127.0.0.1 inside WSL2 *usually* works through localhost forwarding,
# but binding 0.0.0.0 removes that variable entirely.
uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

Quick smoke test (curl bypasses CORS, so it only verifies routing & the
pipeline — for a full browser-style test see "M8 seed debugging" below):

```bash
curl http://127.0.0.1:8001/api/health
curl http://127.0.0.1:8001/api/projects
```

### 2. Frontend (Next.js on port 3000)

```bash
cd viewer_app/frontend
npm install
npm run dev
```

Open http://localhost:3000.

The frontend defaults to `http://127.0.0.1:8001`. To override (different host
or port), copy `.env.local.example` to `.env.local` and set
`NEXT_PUBLIC_API_BASE`. **Restart `npm run dev` after editing `.env.local`** —
`NEXT_PUBLIC_*` variables are baked into the bundle at build time and stale dev
servers will keep using the old value.

The bundle prints `[roof-viewer] API_BASE = ...` in the browser console at
startup so you can confirm which URL it's calling.

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | `{ "ok": true }` |
| GET | `/api/projects` | List of demo projects from `project_map.json` |
| GET | `/api/projects/{project_id}/roof` | The matching `*.auto.roof.json` |
| GET | `/api/projects/{project_id}/model` | The matching `.glb` (binary, `model/gltf-binary`) |
| POST | `/api/projects/{project_id}/seed` | M8 click-to-seed. Body: `{hit_point: [x,y,z], hit_normal?: [x,y,z], face_index?: int, plane_id?: str}`. Returns `{plane, panels, obstructions, diagnostics}` or 422 if no plane could be fitted. Mesh is cached in-process per project so repeat calls are ~0.3 s. |

The backend resolves the repo root from `__file__` so it works regardless of CWD.
Requested files are restricted to entries in `project_map.json`; path traversal is
blocked.

## What the viewer renders

For the selected project the scene contains:

- the raw GLB photogrammetry mesh (Draco-decompressed by three.js / drei)
- raw roof polygons in **orange** (`roof_planes[].polygon_3d`)
- usable polygons in **gold** (`roof_planes[].usable_polygon_3d`, after eaves erosion + bumps)
- obstructions / bumps in **red** (`obstructions[].polygon_3d`)
- solar panels as dark blue quads (`panels[].corners_3d`, batched into one BufferGeometry)

Each layer can be toggled in the right sidebar.

## Known limitation — tile-wide outputs

The four current GLBs are photogrammetry **tiles** (~80–240 m wide), not single
houses. The auto pipeline therefore reports tile-wide totals — for example
Brandenburg lists 573 panels / 252 kWp because panels are placed on every roof
the pipeline detected in the entire tile. That is expected at this stage and is
flagged inline in the UI:

> *"Current auto result is tile-wide. M9 will bind/select one customer building
> or roof subset for realistic per-home output."*

Nothing about the schema or the rendering needs to change for M9 — only the
selection step that decides which polygons/panels reach the JSON.

## Coordinate frame — the one rule that matters

Both the GLB content and every JSON polygon/panel coordinate live in the
**original GLB local space**: Z-up, meters, with the CESIUM_RTC offset NOT
applied. The viewer keeps them aligned with two simple discipline rules:

1. **Single root `<group>`.** GLB and overlays are children of one R3F group.
   Any future transform (scale, position, rotation) goes on that group — never
   on individual children.
2. **Camera is configured Z-up** (`camera.up.set(0, 0, 1)` in `onCreated`).
   No per-object rotation is needed; geometry renders upright as-is.

If something ever looks rotated, the fix is on the root group — not on the GLB
node and not on the overlays.

## M8 seed debugging — when the browser shows "Failed to fetch"

`Failed to fetch` is what `fetch()` throws for **network-level** failures. The
backend is fine; the request never reached it. The likely causes, in order:

1. **CORS preflight blocked POST.** Verify with:
   ```bash
   curl -i -X OPTIONS http://127.0.0.1:8001/api/projects/297be54c5e7e4aad/seed \
     -H "Origin: http://localhost:3000" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: content-type"
   ```
   The response must include
   `access-control-allow-methods: ... POST ...`. If it doesn't, restart uvicorn —
   `main.py` already lists `allow_methods=["*"]` after the M8 fix.
2. **Backend not bound where the browser can reach it.** WSL2 plus Windows
   browsers sometimes can't reach a `127.0.0.1`-only bind. Restart with
   `--host 0.0.0.0`.
3. **Stale `.env.local`.** Restart `npm run dev` after edits or the bundle keeps
   the old API base.
4. **Mixed content.** If you serve the frontend from `https://` and the API from
   `http://`, browsers block. For local dev keep both on `http://`.

End-to-end seed smoke test (works around CORS by sending an explicit Origin):

```bash
curl -X POST http://127.0.0.1:8001/api/projects/297be54c5e7e4aad/seed \
  -H "Content-Type: application/json" \
  -H "Origin: http://localhost:3000" \
  -d '{"hit_point":[8.5,-10.3,42.4],"hit_normal":[0,0,1]}'
```

A 422 with a JSON `{"detail": "..."}` body is **good** — it means the route is
reachable and CORS is fine, the click just landed on a non-roof location. A
real success (200) needs a hit point on an actual roof; the easiest source of
those is to click the model in the browser.

The browser DevTools Console shows three useful lines from the frontend:
- `[roof-viewer] API_BASE = ...` (at startup)
- `[roof-viewer] POST <url> {hit_point: ..., ...}` (on each click)
- `[roof-viewer] seed response: <status> <statusText>`

The backend prints structured log lines for every seed request:

```
[2026-04-25 18:21:12] INFO  roof-viewer: seed REQUEST project=...
[2026-04-25 18:21:12] INFO  roof-viewer: seed OK    project=... tilt=30.0 az=225.1 area=26.4 panels=4 faces=232 elapsed_ms=588
```

Use those side by side to triangulate. If the browser logs the POST but no
`seed REQUEST` line appears in uvicorn's stdout, it's a CORS/networking issue;
if both appear and the response is `seed FAIL ... no plane fitted`, it's a
geometry-quality issue and the user should pick a different click location.

## What M8 added

- **Hover inspection.** Move the cursor over the GLB; R3F's raycaster reports the
  hit point, and the frontend projects it into each detected plane's (u, v) basis
  and runs a 2D crossing-number point-in-polygon test (`components/planeLookup.js`).
  Tooltip shows plane id, source (`auto` / `manual_config` / `click_seeded`),
  tilt, azimuth, area, usable area, panel count, confidence, and which of the
  four reasons passed.
- **Click-to-seed.** Click the model and the frontend POSTs the exact hit point,
  surface normal, and face index to `/api/projects/{id}/seed`. Backend region-grows
  from that triangle (BFS via `face_adjacency`, normal within ~18°, plane distance
  &lt; 15 cm), refits via M5's `_build_plane`, runs M6 usable + M7 placement, and
  returns a fresh `{plane, panels, obstructions}` payload. The frontend merges
  it into the current roof state by replacing any plane with the same id, so
  re-clicks update in place. Seeded planes render in cyan to distinguish them
  from the orange auto/manual planes.
- **In-flight feedback.** A small status banner shows "Fitting plane at click…",
  the result summary on success ("Seeded user_pick_3 · 30.4° · 12 panels"), or
  the error message on failure ("could not fit a plane at this location").

## Things this app does NOT do (intentional)

- No re-running of M4 candidates / M5 clustering on the full mesh; only the
  per-click `roof3d.seeded.design_for_seed()` runs server-side, and only on a
  user click. The pre-computed `*.auto.roof.json` is the authoritative starting
  state.
- No persistence — the seeded planes live only in the open browser tab. Refresh
  resets to the on-disk JSON.
- No project CRUD from the UI.
