# Phase 1 Summary — Roof Intelligence (M0–M9)

Single context document for handing off to the next phase. Covers what shipped,
what's still open, where every file lives, and how to run it.

---

## 1. What this module does in the larger product

The Reonic Track challenge: AI Renewable Designer. Customer profile + roof
constraints + historical components → renewable-energy offer.

This module is **the roof constraint engine**. Person 1 owns CSV-driven profile
+ recommendation. Person 3 owns the main product frontend. We own the roof
intelligence: GLB photogrammetry → roof planes → solar panel placement → JSON.

The recommendation engine proposes "X kWp" from demand. We answer "the roof
fits Y kWp; here are the panel positions." The final offer respects our number.

---

## 2. End-to-end runbook

```bash
# One-time setup
cd /mnt/d/Berlin_hackathon
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Build canonical roof JSONs for every project in the map (M9)
.venv/bin/python scripts/build_all.py

# Inspect any output
ls out/
# 297be54c5e7e4aad.roof.json  (Hamburg, real CSV project)
# 98b53eaa68c0eeeb.roof.json  (Brandenburg, real CSV project + EV)
# demo_north.roof.json         (showcase tile)
# demo_ruhr.roof.json          (showcase tile)

# Run the interactive viewer
cd viewer_app/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8001
# in another terminal:
cd viewer_app/frontend && npm install && npm run dev
# open http://localhost:3000
```

---

## 3. Repo layout (what's where)

```
/mnt/d/Berlin_hackathon
├── 3D_Modell *.glb                Photogrammetry tiles (Draco-compressed,
│                                  CESIUM_RTC offsets, Z-up, meters)
├── projects_status_quo.csv        Customer profiles  (Person 1)
├── project_options_parts.csv      Historical offers   (Person 1)
├── ROOF_3D_IMPLEMENTATION_PLAN.md Original plan that drove M0–M10
├── PHASE1_SUMMARY.md              This file
│
├── roof3d/                        Pipeline package (no I/O orchestration)
│   ├── __init__.py
│   ├── loader.py                  M1  GLB Draco-aware loader, CESIUM_RTC aware
│   ├── glb_metadata.json          M1  units / up-axis audit per GLB
│   ├── candidates.py              M4  upward+local-elevated face filter
│   ├── planes.py                  M5  cluster + per-plane PCA / alpha-shape
│   ├── usable.py                  M6  eaves erosion + bump (obstruction) detection
│   ├── placement.py               M3+M7  panel grid placer (rect + polygon)
│   ├── seeded.py                  M8  fit_plane_from_seed (region grow)
│   ├── contract.py                M2  pydantic schema (frozen v1.0.0)
│   ├── manual_config.py           M3  hand-tuned plane defs per GLB
│   └── project_glb_map.json       M9  canonical project_id ↔ GLB map
│
├── scripts/                       CLI orchestrators
│   ├── inspect_glb.py             M0  smoke test
│   ├── audit_glb.py               M1  produces glb_metadata.json + topdown PNGs
│   ├── emit_mock.py               M2  hand-authored Hamburg roof for handshake
│   ├── emit_manual.py             M3  manual_config → RoofDesign JSON
│   ├── visualize_candidates.py    M4  red-vs-grey debug render
│   ├── visualize_planes.py        M5  per-plane colored polygons
│   ├── visualize_usable.py        M6  raw / usable / bumps overlay
│   ├── emit_auto.py               M7  full auto pipeline → JSON
│   ├── preview_overlay.py         shared visual sanity test
│   └── build_all.py               M9  batch runner (auto + manual fallback)
│
├── out/                           Generated artefacts (PNGs + JSON)
│   ├── *.roof.json                CANONICAL per-project output (M9)
│   ├── *.auto.roof.json           Auto-only output (M7)
│   ├── *.overlay.png              top-down overlay
│   ├── *.candidates.png           M4 viz
│   ├── *.planes.png               M5 viz
│   ├── *.usable.png               M6 viz
│   └── *.topdown.png              M1 audit
│
└── viewer_app/                    Browser viewer (post-M7 bridge + M8 interactivity)
    ├── README.md                  Run/debug instructions, CORS notes
    ├── backend/                   FastAPI on :8001
    │   ├── main.py                /api/health, /api/projects, /roof, /model, /seed
    │   ├── project_map.json       Viewer-side mapping (points at canonical files)
    │   └── requirements.txt
    └── frontend/                  Next.js 14 App Router + R3F + drei
        ├── app/page.jsx, layout.jsx, globals.css
        ├── components/
        │   ├── RoofScene.jsx           Canvas + GLB + overlays + hover/click
        │   ├── PolygonOverlay.jsx      drei <Line> loops
        │   ├── ObstructionOverlay.jsx  red lines for bumps
        │   ├── PanelOverlay.jsx        single batched BufferGeometry
        │   ├── ProjectSelector.jsx
        │   ├── SummaryPanel.jsx
        │   ├── OverlayControls.jsx
        │   └── planeLookup.js          point-in-plane-polygon test for hover
        ├── package.json, next.config.js, .env.local.example
```

---

## 4. Milestones — what shipped and why

### M0  Repo & env setup
Python venv, `roof3d/` package, `scripts/`. Smoke-test loader (`inspect_glb.py`).

### M1  Scale, units, coordinate audit  ⚠️ critical
Discovered GLBs are **Draco-compressed photogrammetry tiles** (~80–242 m wide)
with **CESIUM_RTC** offsets, Z-up, meters. Plain trimesh returned zero geometry.
Built `roof3d/loader.py` using `pygltflib` + `DracoPy`. Audit results frozen in
`roof3d/glb_metadata.json`. **Plan amendment:** GLBs are multi-building tiles,
not single houses → all later steps had to handle that fact. Per-house
selection is still open (see §7).

### M2  JSON contract + frontend handshake (mock)
Pydantic `RoofDesign` schema in `roof3d/contract.py` — schema_version frozen at
`1.0.0`. Hand-authored mock JSON for Hamburg + overlay PNG sanity check before
any geometry was implemented. Caught one azimuth sign bug here while everything
was still cheap.

**Coordinate-frame contract:** all panel/polygon coordinates are in the
**original GLB local space** (RTC offset NOT applied). Frontend keeps GLB and
overlays inside one parent `<group>`. Camera is configured Z-up
(`camera.up = (0,0,1)`) — no per-child rotation.

### M3  Manual-config fallback
`roof3d/manual_config.py` with frozen plane definitions per GLB (centroids
derived from a one-off rooftop-cluster probe), `scripts/emit_manual.py` writes
canonical-named JSONs. **This is the demo's insurance policy** — even if auto
detection fails on demo day, every GLB has a working roof JSON.

### M4  Roof candidate filter
`select_roof_candidates(mesh)` picks faces that are upward (`normal_z > 0.35`),
substantive (`area > 0.05 m²`), and locally elevated. **Plan amendment:**
replaced the original global-Z threshold with a **3 m XY-cell local-max test**,
because the global threshold would only catch the tallest building's roof in a
multi-building tile.

### M5  Plane clustering + per-plane geometry
DBSCAN on smoothed normals → DBSCAN on plane offset → connected-components on
`face_adjacency`. Per-plane: PCA refit, project to 2D, alpha-shape (fallback
convex hull), tilt/azimuth/area, confidence with 4 reasons. **Plan amendment:**
the `height_valid` reason was meaningless on tile data so it was repurposed to
mean "substantive" (≥80 faces). All 4 GLBs produce 29–382 planes; top planes
hit confidence 1.0.

### M6  Usable area + obstruction detection
`compute_usable(mesh, plane)` does eaves erosion (`buffer(-0.30 m)`) plus
bump detection — vertices > 15 cm above the plane, DBSCAN-clustered, hulled,
buffered, and subtracted from the usable polygon. Live shapely Polygons are
returned alongside coord lists so M7 can call `.contains(rect)` directly.

### M7  Polygon-aware panel placement
`place_panels_in_polygon(plane, usable, module)`. Greedy grid in plane (u,v),
tries portrait + landscape, picks the higher count. Lifts each panel 3 cm
along the normal (z-fight prevention). One BufferGeometry per Canvas covers
all panels (Brandenburg has 573).

### Bridge: viewer_app
After M7 we built a minimal FastAPI + Next.js + R3F viewer to prove
GLB+JSON alignment. Endpoints: `/api/health`, `/api/projects`, `/roof`,
`/model`. Frontend renders model + raw / usable / obstruction / panel
overlays with toggle controls. **Z-up handled by `camera.up` only — single
parent group, no per-child transforms.**

### M8  Interactive verification
`roof3d/seeded.py::fit_plane_from_seed` — region-grow BFS from a clicked face
(normal within 18°, plane distance < 15 cm), then run M5 PCA + M6 + M7.
`POST /api/projects/{id}/seed` returns a fresh plane / panels / obstructions
payload tagged `source="click_seeded"`. Mesh cached in-process, so warm
clicks fit in ~0.3 s.

Frontend: hover does client-side point-in-polygon over each plane's projected
polygon; tooltip shows tilt/az/area/panel count/confidence/source. Click POSTs
to `/seed`, merges result into roof state, summary kWp updates. Seeded planes
render in cyan; auto/manual stay orange.

**Bug caught and fixed in M8 wiring:** initial CORS `allow_methods=["GET",
"OPTIONS"]` blocked the POST preflight in browsers. Curl bypassed CORS so the
command-line smoke test passed but the browser couldn't talk to /seed. Fixed
by widening to `["*"]`. Also: structured backend logging on `/seed`, frontend
distinguishes `TypeError` (network) from HTTP-level errors and surfaces the
FastAPI `detail` field.

### M9  Project ↔ GLB binding & batch run
`roof3d/project_glb_map.json` (canonical, schema_version 1.0.0). 4 entries —
2 real CSV-bound projects (Hamburg, Brandenburg), 2 demo placeholders (North
Germany, Ruhr). `scripts/build_all.py` reads the map, runs auto pipeline per
project, falls back to M3 manual on failure, writes `out/<project_id>.roof.json`
(canonical filename). Viewer's `project_map.json` repointed at canonical
files.

### M10  Stretch (NOT done)
Optional: real obstruction polygons (M6 already detects bumps; full obstruction
geometry not exported), RANSAC fallback, fire-code setbacks, per-azimuth
ranking in summary, multi-module-spec optimization.

---

## 5. The data contract (frozen v1.0.0)

`roof3d/contract.py::RoofDesign` defines the JSON. Top-level fields:

```
schema_version, project_id, model_file
coordinate_system { units, up_axis, panels_in_original_model_coordinates,
                   unit_scale_applied }
bbox { min, max }
roof_planes[] { id, source, confidence, confidence_reasons,
                centroid, normal, u_axis, v_axis, tilt_deg, azimuth_deg,
                area_m2, usable_area_m2, panel_count,
                polygon_3d, usable_polygon_3d }
obstructions[] { id, plane_id, source, type, area_m2, polygon_3d }
panels[]      { id, plane_id, center, normal, u_axis, v_axis,
                width_m, height_m, watt_peak, corners_3d (4 points) }
summary       { panel_count, module_wp, system_kwp,
                best_plane_id, best_plane_azimuth, best_plane_tilt,
                panels_by_plane, method, confidence, warnings }
quality       { method, confidence, warnings }
```

`source` ∈ {`auto`, `manual_config`, `click_seeded`}.
Coordinates are always in the **original GLB local space** (RTC offset NOT
applied). Z-up. Meters.

**Person 1's read pattern:**
```python
import json
from pathlib import Path
from roof3d.contract import RoofDesign

projects = json.loads(Path("roof3d/project_glb_map.json").read_text())["projects"]
for p in projects:
    design = RoofDesign.from_json(Path(f"out/{p['project_id']}.roof.json").read_text())
    cap_kwp = design.summary.system_kwp        # ← physical roof cap
    panels = design.summary.panel_count
    best_az = design.summary.best_plane_azimuth
    best_tilt = design.summary.best_plane_tilt
```

---

## 6. Current numbers (canonical M9 outputs)

| project_id | GLB | source | planes | panels | system_kwp |
|---|---|---|---|---|---|
| 297be54c5e7e4aad | Hamburg | auto | 12 | 129 | 56.76 |
| 98b53eaa68c0eeeb | Brandenburg | auto | 12 | 573 | 252.12 |
| demo_north | North Germany | auto | 12 | 158 | 69.52 |
| demo_ruhr | Ruhr | auto | 12 | 14 | 6.16 |

These are **tile-wide totals** across multiple buildings — see §7.

---

## 7. Open items / known limitations (read before next phase)

### 7.1 Per-building selection within a tile (the big one)
The 4 GLBs are 80–242 m wide neighborhood tiles. Auto pipeline currently
returns every detected roof in the tile, so kWp totals are unrealistically
high for a "single customer." The next natural step is a building-selection
layer that, given a project_id and a tile, picks one building's roofs only.

Three plausible approaches, in order of effort:
1. **Hardcode a building seed (XY centroid + radius) per project_id** in
   `project_glb_map.json`. `build_all.py` filters detected planes whose
   centroid falls outside the radius. Cheap, demoable, ~15 min of work.
2. **Connected-components on roof planes by spatial proximity** — group planes
   into "buildings" by clustering their centroids and picking the cluster
   nearest the project's seed point.
3. **Click-to-select-building** in the viewer (extend M8 click flow to "select
   building" rather than "seed plane").

### 7.2 demo_north / demo_ruhr are placeholder project IDs
Not bound to real CSV rows. If the team picks two more real customer projects,
swap the IDs in `roof3d/project_glb_map.json` and re-run `build_all.py`.

### 7.3 Hamburg roof_0 has 23 detected bumps
Roof_0 (151 m² flat roof) gets ~30% area lost to bump-subtraction. Either real
HVAC density or photogrammetry noise. Adjustable: bump
`BUMP_DISTANCE_M = 0.15` → `0.25` in `roof3d/usable.py` to be more conservative.

### 7.4 `confidence_reasons.height_valid` is a misnomer
After M5, the field carries the "substantive" signal (≥80 faces) instead of
the original Z-threshold meaning. Documented in `placement._detected_to_contract_plane`.
Leaving it as-is preserves contract back-compat; rename in v1.1 if desired.

### 7.5 Schema is frozen at v1.0.0
Additive changes only. Any rename or removal is a v2.0.

### 7.6 Click-to-seed normal handling
`fit_plane_from_seed` accepts a `hit_normal`. If the user passes [0,0,1] on a
22° tilted roof, the normal-tolerance check rejects it and region-grow yields
< MIN_FACES. Real frontend clicks pass the actual face normal, which works
fine. Worth a tighter contract once the frontend is integrated end-to-end.

### 7.7 `next.config.js` uses CommonJS
For Next 15 / React 19 it should be `next.config.mjs`. Currently we're on
Next 14 + React 18 which is fine. If we upgrade, also switch to `next.config.mjs`
or `next.config.ts`.

### 7.8 GLBs are 4.9–27 MB
No CDN, served directly by FastAPI from disk. Fine for hackathon; in
production you'd want streaming + caching headers.

### 7.9 The frontend hover lookup is O(planes × polygon_vertices) per pointer move
With ~12 planes and ~30 polygon points each, that's ~360 ops per move. Fine.
At ~400+ planes (Brandenburg if --max-planes is raised), it'll lag. Index by XY
bounding box first if so.

---

## 8. Plan amendments worth preserving

The original plan (`ROOF_3D_IMPLEMENTATION_PLAN.md`) had several assumptions
that needed adjustment after contact with the data:

1. **GLBs require Draco** — original plan didn't mention compression; trimesh
   alone returns zero geometry.
2. **GLBs are multi-building tiles, not single houses** — invalidated several
   later "single roof" assumptions; per-house selection is the open
   architectural question.
3. **M4 global-Z threshold replaced with local-cell-max** — multi-building
   tiles have terrain elevation variation that breaks a single global cutoff.
4. **`height_valid` confidence reason repurposed as "substantive"** —
   M4 already enforces height; the original meaning was redundant on tile data.
5. **CORS preflight covers POST** — initial config blocked the M8 click
   endpoint silently. Curl-only smoke testing missed it. Lesson: always
   browser-test POST routes during integration.
6. **API_BASE defaults to 127.0.0.1, not localhost** — more reliable across
   WSL2/Windows networking.

---

## 9. What's good to use as-is for the next phase

- **The contract.** v1.0.0 is in production-ish shape. Build on top, don't
  rename.
- **`build_all.py`.** One command regenerates everything from the canonical
  map. Idempotent.
- **The viewer_app.** Solid R3F foundation with hover + click. The main
  product frontend can either reuse these components or replace them; the
  JSON contract is the actual handoff surface.
- **The pipeline modules** (candidates / planes / usable / placement /
  seeded). Each is independently testable, the visualize_*.py scripts give
  per-step debug PNGs.

## 10. What probably needs revisiting in the next phase

- **Per-building selection from a tile** (§7.1) — the most impactful change
  for realism.
- **Stretch goals (M10)** — RANSAC fallback, real obstruction geometry export,
  fire-code setbacks. Optional.
- **Frontend integration with Person 3's main product UI** — the viewer_app is
  a reference implementation, not the final product UI.
- **Person 1 integration test** — confirm the recommendation engine actually
  reads `summary.system_kwp` correctly and respects it as a cap, with at
  least the 2 real CSV-bound projects.
