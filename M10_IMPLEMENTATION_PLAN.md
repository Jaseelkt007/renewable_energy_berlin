# M10 Implementation Plan — Roof Quality Gate + Single-Building Selection

## Goal
Insert a selection/filter layer between M5 (plane clustering) and M6/M7 (usable + panels) so the canonical `out/<project_id>.roof.json` reflects **one customer's building**, not the whole tile. Keep tile-wide output available for debug. No schema break.

## Non-goals
- No changes to M4/M5/M6/M7 internals.
- No changes to `RoofDesign` field names (schema is frozen v1.0.0).
- No frontend overhaul — minimal viewer toggle only.
- No RANSAC, no per-azimuth ranking, no fire-code setbacks (M10-stretch).

---

## Step 1 — `roof3d/quality.py` (new module, ~120 LoC)

Pure functions over `mesh` + `DetectedPlane`. No I/O.

```python
@dataclass
class GateParams:
    min_height_above_ground_m: float = 2.5
    ground_radius_m: float = 12.0
    ground_percentile: float = 5.0
    min_area_m2: float = 8.0
    min_usable_area_m2: float = 4.0
    max_tilt_deg: float = 60.0
    min_confidence: float = 0.7

@dataclass
class Selection:
    mode: str  # "tile_wide" | "roi_circle" | "selected_plane_ids"
    center_xy: tuple[float, float] | None = None
    radius_m: float | None = None
    selected_plane_ids: tuple[str, ...] | None = None

@dataclass
class GateDecision:
    plane_id: str
    accepted: bool
    roof_score: float
    height_above_ground_m: float
    reasons: dict[str, bool]  # per-criterion pass/fail

def local_ground_z(mesh, centroid_xy, radius_m, percentile=5.0) -> float: ...

def roof_score(plane, height_above_ground, in_roi: bool) -> float: ...

def apply_quality_gate(
    mesh, planes, *, params: GateParams, selection: Selection
) -> tuple[list[DetectedPlane], list[GateDecision]]: ...
```

`local_ground_z`: filter `mesh.vertices` to those within `radius_m` of `centroid_xy`, return `np.percentile(z, percentile)`. Cache by quantized (x,y) for the per-project run.

`roof_score`: weighted sum of normalized (height_above_ground, usable_area, confidence) with hard-fail short-circuits. Used for *ranking* after the gate. Carried internally; not written to schema.

`apply_quality_gate`: applies (in order) ground-height filter → universal quality (`area`, `tilt`, `confidence`) → project selection (ROI containment or id whitelist). Returns the surviving planes (sorted by roof_score descending) and a `GateDecision` list for every input plane (for warnings + debug).

## Step 2 — Extend `roof3d/project_glb_map.json`

Backward-compatible. Add optional `selection` per project:

```json
{
  "project_id": "98b53eaa68c0eeeb",
  "glb_file": "3D_Modell Brandenburg.glb",
  "selection": {
    "mode": "roi_circle",
    "center_xy": [X, Y],
    "radius_m": 14
  }
}
```

Modes: `tile_wide` (default if omitted), `roi_circle`, `selected_plane_ids`.

ROI center+radius for the two real projects will be hand-tuned by reading the existing `out/<id>.planes.png` top-down debug images and the GLB bbox in the existing JSON. Demo projects (`demo_north`, `demo_ruhr`) stay `tile_wide` with a warning.

Schema bump on the **map file** (not RoofDesign): `schema_version` 1.0.0 → 1.1.0.

## Step 3 — Wire gate into `scripts/emit_auto.py`

Two changes:

1. Add `selection: Selection | None = None` and `gate_params: GateParams | None = None` kwargs to `emit(...)`.
2. After `detected = cluster_planes(...)`, call `apply_quality_gate`. The existing `for p in detected:` loop iterates over the *gated* list.
3. Append rejection summary to `summary.warnings`:
   - `"Selected-building result: N of M planes accepted (ROI: r=14m)"` or
   - `"Tile-wide result: multiple buildings may be included."`
4. Add per-plane reject reason counts as a single warning string (e.g. `"Gate rejects: ground=12, area=3, tilt=1"`).

`emit` continues to write `out/<id>.auto.roof.json`. **No filename changes here** — `build_all.py` controls the canonical output.

## Step 4 — `scripts/build_all.py` orchestration

The map is the source of truth for which mode each project uses. Always produce both:

- **Tile-wide debug**: run `emit(...)` with `selection=Selection(mode="tile_wide")` → copy to `out/<id>.tile.roof.json`.
- **Canonical**: run `emit(...)` with the project's `selection` from the map → copy to `out/<id>.roof.json`.

If a project has no `selection` block, canonical = tile-wide with a warning (preserves M9 behavior).

Add comparison table to existing report:

```
project_id              tile_panels  tile_kwp  sel_panels  sel_kwp  mode
297be54c5e7e4aad        129          56.76     34          14.96    roi_circle
98b53eaa68c0eeeb        573          252.12    47          20.68    roi_circle
demo_north              158          69.52     158         69.52    tile_wide
demo_ruhr                14           6.16      14          6.16    tile_wide
```

New flags:
- `--mode tile|selected|both` (default `both`)
- Existing flags (`--max-planes`, `--no-fallback`) keep working.

Manual fallback (M3) unchanged — runs only if auto produces 0 panels after gating.

## Step 5 — Viewer support (minimal)

**Backend** (`viewer_app/backend/main.py`):
- `/api/projects` returns each project with a `modes` array (e.g. `["tile", "selected"]`) based on which JSON files exist.
- `/roof?project_id=X&mode=tile|selected` (default `selected`) — chooses `<id>.tile.roof.json` vs `<id>.roof.json`.

**Frontend** (`viewer_app/frontend/components/`):
- One small radio toggle in `OverlayControls.jsx`: "Selected building / Tile-wide (debug)".
- Existing `RoofScene.jsx` re-fetches on toggle change. No new components.
- Surface `summary.warnings[0]` as a banner above the summary panel.

## Step 6 — Verification & demo prep

After implementation:

```bash
# Rebuild everything
.venv/bin/python scripts/build_all.py

# Round-trip validate
.venv/bin/python -c "from roof3d.contract import RoofDesign; from pathlib import Path; \
  [print(p, RoofDesign.from_json(p.read_text()).summary.system_kwp) for p in Path('out').glob('*.roof.json')]"

# Visual confirm (panels on one building only)
.venv/bin/python scripts/preview_overlay.py out/297be54c5e7e4aad.roof.json
.venv/bin/python scripts/preview_overlay.py out/98b53eaa68c0eeeb.roof.json

# Browser
cd viewer_app/backend && .venv/bin/uvicorn main:app --port 8001 &
cd viewer_app/frontend && npm run dev
```

**Acceptance checks:**
1. Tile-wide Brandenburg still ≈573 panels in `*.tile.roof.json`.
2. Canonical Brandenburg in 25–60 panel range, panels visually on one building.
3. Hamburg canonical in 25–45 range.
4. `RoofDesign.from_json` round-trips for all 4 canonical files.
5. `summary.system_kwp` still readable by Person 1 with the existing snippet (§5 of PHASE1_SUMMARY).
6. Existing `scripts/emit_auto.py "3D_Modell Hamburg.glb"` (without selection arg) produces same output as before.

## Files changed (summary)

| File | Change |
|---|---|
| `roof3d/quality.py` | **NEW** — `GateParams`, `Selection`, `apply_quality_gate`, `local_ground_z`, `roof_score` |
| `roof3d/project_glb_map.json` | + optional `selection` per project; map schema 1.1.0 |
| `scripts/emit_auto.py` | + `selection`/`gate_params` kwargs; insert gate; warnings |
| `scripts/build_all.py` | + dual-output (tile + canonical); + comparison table; + `--mode` |
| `viewer_app/backend/main.py` | + `mode` query param on `/roof` |
| `viewer_app/backend/project_map.json` | + `modes` per project |
| `viewer_app/frontend/components/OverlayControls.jsx` | + tile/selected toggle |
| `viewer_app/frontend/components/SummaryPanel.jsx` | + warnings banner |

No edits to: `loader.py`, `candidates.py`, `planes.py`, `usable.py`, `placement.py`, `seeded.py`, `contract.py`.

## Order of execution
1. `quality.py` (pure logic; testable standalone via `emit_auto` smoke).
2. Wire into `emit_auto.py` behind a no-op default (`Selection(mode="tile_wide")`) — confirm zero behavior change.
3. Hand-tune ROI for Hamburg + Brandenburg by reading existing `out/*.planes.png`. Add to map.
4. Update `build_all.py`. Run. Inspect numbers + overlay PNGs.
5. Viewer backend + small frontend toggle.
6. Final pass + report.

## Risks
- ROI hand-tuning may need 1–2 iterations per project (cheap — visible immediately in overlay PNG).
- Local ground percentile can mis-estimate on hillside terrain → if a real project sits on a slope, fall back to `selected_plane_ids` for that project.
- If gate rejects everything (ROI miss), canonical falls through to manual M3 config (existing fallback) — preserves demo safety net.
