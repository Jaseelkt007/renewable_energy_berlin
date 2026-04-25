# M11 Implementation Plan — Interactive ROI-Driven Roof Design

## Goal
Move the customer-facing flow from "offline-built JSON files" to "user marks a house in the 3D viewer and the backend runs the full M4→M7 pipeline live on that ROI." The user clicks the target roof, sets a radius, and sees a fresh `RoofDesign` (planes, usable polygons, obstructions, panels, kWp) within ~1 s.

## Non-goals
- Not removing the offline `build_all.py` pipeline. `out/<id>.roof.json` stays as the contract surface for Person 1's recommendation engine.
- Not changing the `RoofDesign` JSON schema (frozen v1.0.0).
- Not implementing polygon/lasso/face-paint selection in v1. Single click + radius slider only.
- Not auto-detecting the customer's building from address/footprint data.
- Not "Save as canonical" overwrite of the existing JSON in v1 — live results are in-memory only.

---

## Architectural decision (already settled in analysis)

**Option B' — keep the full mesh, mask candidates.** Per-click pipeline:

1. AND M4's candidate mask with an XY-distance ROI mask.
2. Run M5 (`cluster_planes`) on the ROI-masked candidates against the full mesh (so `face_adjacency` stays intact).
3. Run M6 (`compute_usable`) + M7 (`place_panels_in_polygon`) per surviving plane.
4. Apply M10 quality gate (height/area/tilt/confidence) for floor/courtyard rejection.
5. Assemble a `RoofDesign` with `quality.method = "interactive_roi"` and return.

Caches: per-project `_MESH_CACHE` (M8 already), parallel `_CAND_CACHE` (new) so M4 runs once per project. M5 runs every click but only on the ROI-masked candidate subset.

Expected latency: **~1 s warm**, 5–15 s cold first click (GLB load + M4). UI shows a "Computing…" indicator while pending.

---

## Step 1 — `roof3d/seeded.py`: add `design_for_roi`

`seeded.py` already has the "live single-plane fit" pattern (`design_for_seed`). Add a sibling function for whole-pipeline ROI runs.

```python
@dataclass
class RoiDesignResult:
    roof_planes: list[contract.RoofPlane]
    panels: list[contract.Panel]
    obstructions: list[contract.Obstruction]
    n_candidates_in_roi: int
    n_planes_detected: int
    n_planes_accepted: int
    elapsed_ms: int
    warnings: list[str]

def design_for_roi(
    mesh, candidate_result, *,
    roi_center_xy: tuple[float, float],
    roi_radius_m: float,
    gate_params: GateParams | None = None,
    module: ModuleSpec = ModuleSpec(),
    max_planes: int = 12,
    detect_bumps: bool = True,
) -> RoiDesignResult: ...
```

Internals (≈80 LoC):

- AND `candidate_result.mask` with `dist² ≤ r²` over `face_centroids`.
- If `n_candidates_in_roi < MIN_PLANE_FACES`: raise `RoiTooSmall` (caller maps to HTTP 422).
- Build a shallow copy of `CandidateResult` with the ROI-narrowed mask; pass to `cluster_planes(mesh, cand)`.
- Apply `apply_quality_gate` with `Selection(mode="roi_circle", center_xy=..., radius_m=...)` so the gate's per-plane rejection still runs.
- Loop accepted planes → `compute_usable` → `place_panels_in_polygon` (reusing M7 exactly as today).
- Build pydantic `RoofPlane` / `Panel` / `Obstruction` objects via the same code path emit_auto.py uses.

Module-level: factor the "DetectedPlane → contract.RoofPlane + panels" loop out of `emit_auto.emit` into a small helper (`_planes_to_contract`) so `seeded.py` and `emit_auto.py` share it. Pure refactor, no behavior change.

## Step 2 — `viewer_app/backend/main.py`: new endpoint + caches

Add a `_CAND_CACHE: dict[str, CandidateResult]` parallel to `_MESH_CACHE`:

```python
def _get_cached_candidates(proj):
    pid = proj["project_id"]
    cached = _CAND_CACHE.get(pid)
    if cached is not None:
        return cached
    loaded = _get_cached_mesh(proj)
    cand = select_roof_candidates(loaded.mesh)
    _CAND_CACHE[pid] = cand
    return cand
```

New endpoint:

```
POST /api/projects/{project_id}/design
body: { center_xy: [x, y], radius_m: float,
        max_planes?: int, detect_bumps?: bool }
→ 200 RoofDesign  |  422 "ROI too small / off-roof"  |  404 unknown project
```

Server:
1. Look up project + cached mesh + cached candidates.
2. Build `Selection(mode="roi_circle", center_xy=..., radius_m=...)`.
3. Pull `gate_overrides` from `roof3d/project_glb_map.json` if present (so Brandenburg's plateau override applies on live ROI too).
4. Call `design_for_roi(...)`.
5. Wrap in `RoofDesign` (reuse `emit_auto.py` helper) with `quality.method = "interactive_roi"`, `summary.warnings` carrying gate-reject reason counts (same shape M10 produces).
6. Log `roi_request project=X center=... r=... candidates=N planes=M elapsed=Yms` (mirrors `/seed`).

The existing `GET /api/projects/{id}/roof?mode=selected|tile` stays unchanged. The "live" result lives in frontend state only.

## Step 3 — Frontend `RoofScene.jsx`: ROI marker + click handler

Add three pieces of state in `app/page.jsx`:

```js
const [roi, setRoi] = useState(null);          // { center: [x,y,z], radius: 15 }
const [liveDesign, setLiveDesign] = useState(null); // server response replacement for `roof`
const [designPending, setDesignPending] = useState(false);
```

Click handler in `RoofScene` already produces a `point` in GLB local space (M8 path). On *Shift+click* or when "ROI mode" is active, set `roi.center = point` instead of seeding a single plane. Preserve M8 single-plane click as the default; require an explicit toggle to switch into ROI mode.

New component `RoiOverlay.jsx`:
- A cyan torus / line-loop circle at `roi.center` with radius `roi.radius`.
- A semi-transparent disc filled inside the circle for visibility.
- Drawn in the same parent `<group>` as panels — coords are GLB local, no extra transforms.

New control in `OverlayControls.jsx`:
- Mode tabs: `[Saved canonical] [Live ROI]`.
- When Live ROI: a slider (5 m – 40 m, default 15) and a small "Run on ROI" button.
- The slider live-updates the visual circle. The button (or 250 ms slider-release debounce) fires the POST.

When the response lands, set `liveDesign` and render that instead of `roof`. The `RoofScene`, `SummaryPanel`, `PanelsByPlane` components already accept the same shape — they don't care whether the `RoofDesign` came from disk or from `/design`.

## Step 4 — Errors & UX feedback

Required UX states:
- **Cold first click:** show "Loading model + finding roof candidates…" (cold ≈ 5–15 s). Subsequent clicks just show "Computing…" (≈ 1 s).
- **422 / no roof in ROI:** red banner "ROI contained no usable roof planes (radius=15 m). Try a larger radius or click closer to the roof centre." Same pattern as M8's seed errors.
- **Backend down:** existing `describeNetworkError` helper in `page.jsx` already covers this.
- **Switching back to Saved canonical:** clears `liveDesign`, reverts to the GET `/roof?mode=selected` payload. Free undo.

## Step 5 — Wire `gate_overrides` into the live path

Brandenburg's `gate_overrides.min_height_above_ground_m: 0.0` is in `roof3d/project_glb_map.json`. The new endpoint must apply it the same way `build_all.py` does, otherwise live ROI on Brandenburg silently rejects every plane (M10's known plateau case). One-line lookup at request time; pass through to `design_for_roi`.

## Step 6 — Verification

```bash
# 1. Backend smoke (cold + warm)
viewer_app/backend/.venv/bin/uvicorn viewer_app.backend.main:app --port 8001
curl -s -X POST http://127.0.0.1:8001/api/projects/297be54c5e7e4aad/design \
  -H 'Content-Type: application/json' \
  -d '{"center_xy":[0,0],"radius_m":12}' | python -m json.tool | head -40

# 2. Off-roof ROI returns 422
curl -i -X POST http://127.0.0.1:8001/api/projects/297be54c5e7e4aad/design \
  -d '{"center_xy":[200,200],"radius_m":5}'

# 3. Brandenburg with gate_overrides
curl -s -X POST http://127.0.0.1:8001/api/projects/98b53eaa68c0eeeb/design \
  -d '{"center_xy":[43,49],"radius_m":10}' \
  | python -c "import sys,json; d=json.load(sys.stdin); print(d['summary'])"

# 4. Frontend
cd viewer_app/frontend && npm run dev
# In browser: switch to "Live ROI" mode, click on Hamburg roof, drag slider.
# Confirm: cyan circle appears, panels regenerate within ~1s, kWp updates.
```

Expected numbers (matching M10 or close):
- Hamburg ROI (0,0,r=12): ~30–40 panels, ~13–18 kWp.
- Brandenburg ROI (43,49,r=10): ~40–55 panels, ~17–24 kWp.
- ROI on a road / courtyard (e.g. (-50,0,r=8) on Hamburg): 422.

## Files changed (summary)

| File | Change |
|---|---|
| `roof3d/seeded.py` | + `design_for_roi`, `RoiDesignResult`, `RoiTooSmall` exception |
| `roof3d/quality.py` | + small helper `roi_circle_mask(centroids, center, r)` (or inline in seeded) |
| `scripts/emit_auto.py` | refactor: extract `_planes_to_contract` helper for shared use |
| `viewer_app/backend/main.py` | + `_CAND_CACHE`, `POST /api/projects/{id}/design`, gate_overrides lookup |
| `viewer_app/frontend/app/page.jsx` | + `roi`, `liveDesign`, `designPending` state; mode-switch logic |
| `viewer_app/frontend/components/RoofScene.jsx` | + ROI click handler when in Live ROI mode |
| `viewer_app/frontend/components/RoiOverlay.jsx` | **NEW** — cyan circle + disc at ROI center |
| `viewer_app/frontend/components/OverlayControls.jsx` | + mode tabs, radius slider, "Run on ROI" button |

No edits to: `loader.py`, `candidates.py`, `planes.py`, `usable.py`, `placement.py`, `contract.py`, `manual_config.py`, `project_glb_map.json` schema (only consumes `gate_overrides` already there), `build_all.py`.

## Order of execution

1. **Backend pure logic** — `_planes_to_contract` refactor, `design_for_roi` in `seeded.py`. Unit-smoke standalone via the existing pytest pattern.
2. **Backend endpoint** — `POST /design` + caches + gate_overrides pass-through. Curl-smoke (cold/warm/422).
3. **Frontend state plumbing** — add `roi`/`liveDesign`/`designPending`, route through existing `SummaryPanel`/`PanelsByPlane`.
4. **Frontend ROI mode UX** — mode tabs, slider, click handler swap, `RoiOverlay` component.
5. **Brandenburg `gate_overrides` smoke** — confirm the live path matches the pre-built `out/98b53eaa68c0eeeb.roof.json` numbers.
6. **End-to-end browser test** — three projects, three ROIs each, confirm latency + kWp ranges.

## Risks & mitigations

- **Cold-start latency on Brandenburg (≥10 s).** Mitigation: clear "Loading model…" indicator distinct from "Computing…". Optional: warm caches at backend startup with a background task that loads all four GLBs.
- **M5 finds 0 planes on small ROIs.** Mitigation: 422 with actionable error text. Slider min=5 m, default=15 m chosen to avoid this case for typical residential roofs.
- **Slider drag spamming POSTs.** Mitigation: 250 ms debounce on slider release, abort previous in-flight request via `AbortController`.
- **`face_adjacency` rebuild on first GLB load is slow.** It already runs (M5 uses it on every offline build). No new cost.
- **Brandenburg `gate_overrides` forgotten in live path.** Tested explicitly in Step 6.
- **Coordinate frame drift if frontend ever wraps GLB in transformed parent.** One-line guard: assert `roi.center` is in GLB local space at the boundary (M2 lesson).

## Acceptance criteria

M11 v1 is done when:

1. Frontend "Live ROI" mode lets the user click a roof, see a cyan circle, drag a radius slider, and within ~1 s see new panels rendered with updated kWp.
2. Three projects work end-to-end (Hamburg, Brandenburg, North Germany or Ruhr).
3. Off-roof clicks fail cleanly with a 422 + actionable error.
4. Brandenburg's plateau gate_override is applied automatically on live requests.
5. Switching back to "Saved canonical" mode reverts to the M10 pre-built result without page reload.
6. Existing `GET /api/projects/{id}/roof` GET behavior, `build_all.py`, `out/<id>.roof.json`, and Person 1's read pattern are all unchanged.
7. M0–M10 commands continue to work identically.
