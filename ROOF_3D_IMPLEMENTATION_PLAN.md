# Roof Intelligence Module — Implementation Plan

**Owner:** Person 2 (3D / roof analysis / panel placement)
**Goal:** Turn a `.glb` photogrammetry building into roof planes + automatically placed solar panels + a kWp cap that the recommendation engine and frontend consume.

---

## 0. Guiding principles

1. **Build vertically, not horizontally.** Each milestone is end-to-end runnable and visibly testable. No milestone leaves the system in a half-broken state.
2. **Frontend-first integration.** The JSON contract and a working frontend render with *mock* data come before any clever geometry. If panels render correctly with hand-written JSON, every later improvement is just a better JSON producer.
3. **Layered fallbacks from day one.** Auto detection → click-to-seed → manual config. Never depend on automatic detection alone for the demo.
4. **Coordinate frame is sacred.** All output panel coordinates are in the **original GLB coordinate space**. Internal normalization is fine, but every coordinate that crosses the backend↔frontend boundary is converted back.
5. **Scale is checked once, per file, on day one.** Everything else depends on it.

---

## 1. Problem & contracts

### 1.1 What this module does
- Input: a `.glb` photogrammetry building model + (optional) module spec.
- Output: JSON describing detected roof planes, obstructions/reserves, placed panels, and a system-level summary.
- Role in the product: the **physical constraint engine**. Recommendation engine proposes "X kWp"; this module enforces "roof actually fits Y kWp; here's where the panels go."

### 1.2 JSON output contract (frozen at Milestone 1)

```json
{
  "project_id": "98b53eaa68c0eeeb",
  "model_file": "3D_Modell Hamburg.glb",

  "coordinate_system": {
    "units": "meters",
    "up_axis": "Z",
    "panels_in_original_model_coordinates": true,
    "unit_scale_applied": 1.0
  },

  "bbox": {
    "min": [0, 0, 0],
    "max": [12.4, 9.8, 7.2]
  },

  "roof_planes": [
    {
      "id": "roof_0",
      "source": "auto",                  // "auto" | "click_seeded" | "manual_config"
      "confidence": 0.84,
      "confidence_reasons": {
        "area_large_enough": true,
        "normal_stable": true,
        "height_valid": true,
        "polygon_clean": false
      },
      "centroid": [4.2, 3.1, 6.5],
      "normal":   [0.1, -0.52, 0.85],
      "u_axis":   [0.98, 0.0, -0.12],
      "v_axis":   [0.08, 0.85, 0.52],
      "tilt_deg": 32.5,
      "azimuth_deg": 178.0,
      "area_m2": 48.3,
      "usable_area_m2": 41.0,
      "panel_count": 28,
      "polygon_3d":         [[x,y,z], ...],   // raw detected boundary
      "usable_polygon_3d":  [[x,y,z], ...]    // after eaves erosion / obstructions
    }
  ],

  "obstructions": [
    {
      "id": "obs_0",
      "plane_id": "roof_0",
      "source": "reserve",               // "reserve" | "detected_bump" | "manual"
      "type": "safety_margin",
      "area_m2": 4.2,
      "polygon_3d": [[x,y,z], ...]
    }
  ],

  "panels": [
    {
      "id": "panel_0",
      "plane_id": "roof_0",
      "center":  [4.1, 2.8, 6.7],
      "normal":  [0.1, -0.52, 0.85],
      "u_axis":  [0.98, 0.0, -0.12],
      "v_axis":  [0.08, 0.85, 0.52],
      "width_m": 1.13,
      "height_m": 1.72,
      "watt_peak": 440,
      "corners_3d": [[..],[..],[..],[..]]  // already lifted off roof by ~3 cm
    }
  ],

  "summary": {
    "panel_count": 28,
    "module_wp": 440,
    "system_kwp": 12.32,
    "best_plane_id": "roof_0",
    "best_plane_azimuth": 178,
    "best_plane_tilt": 32,
    "panels_by_plane": { "roof_0": 28, "roof_1": 12 },
    "method": "automatic_with_reserve",
    "confidence": 0.82,
    "warnings": []
  },

  "quality": {
    "method": "auto_normal_cluster",
    "confidence": 0.82,
    "warnings": []
  }
}
```

### 1.3 Coordinate-frame contract (with frontend)
- Backend always emits coordinates in the **original GLB local space**.
- Frontend renders the GLB and the panels inside the **same parent `<group>`**. Any scale/rotation/position is applied to the parent, not separately to model and panels.
- Panels are lifted along the plane normal by **+3 cm** to avoid z-fighting with the roof mesh.

---

## 2. Tech stack

- **Python 3.11**
- `trimesh` — GLB loading, scene-graph baking, face normals, areas
- `numpy` — vector math
- `scikit-learn` — DBSCAN for normal clustering
- `shapely` — 2D polygon ops (hull, buffer, contains)
- `alphashape` — concave hull for irregular roofs
- `pyransac3d` — RANSAC plane fallback
- `pyglet` or `trimesh.Scene.show()` — quick local visualization for debugging
- Output: `.json` files written next to each GLB (`<glb>.roof.json`)
- Frontend: `react-three-fiber` + `@react-three/drei` (`<Gltf>`), separate teammate

---

## 3. Milestones

Each milestone has: **goal · deliverable · how we test it · exit criteria**. Don't move on until exit criteria are met.

---

### **M0 — Repo & environment setup** *(30 min)*

**Goal:** Clean Python project that loads a GLB.

**Deliverable:**
- `roof3d/` package directory
- `requirements.txt`
- `scripts/inspect_glb.py` that takes a path and prints bbox, triangle count, scene-graph node count, materials/textures count.

**Test:** Run on all 4 GLBs in `/mnt/d/Berlin_hackathon`. Each should print without error.

**Exit:** All 4 GLBs load and produce sane stats.

---

### **M1 — Scale, units, and coordinate audit** *(45 min — DO NOT SKIP)*

**Goal:** Know exactly what coordinate system each GLB lives in. This is the highest-risk integration bug; we kill it first.

**Deliverable:** `scripts/audit_glb.py` that for each GLB prints:
- raw bbox dimensions (no transforms applied)
- bbox dimensions **after** baking the scene graph (`scene.dump(concatenate=True)`)
- whether the model is one mesh or many
- inferred units: meters / millimeters / unknown (heuristic: if bbox max dimension is 5–50, meters; 5000–50000, millimeters; <1, scene-normalized)
- inferred up-axis (largest bbox dimension that *isn't* the gravity direction; cross-check with face normals — ground plane normal points along up-axis)
- a saved top-down PNG render for eyeballing

**Test:** All four GLBs produce a building footprint that looks like 8–20 m wide. Save results to `roof3d/glb_metadata.json` keyed by filename.

**Exit:** A frozen `glb_metadata.json` with `units`, `up_axis`, `unit_scale_applied`, `original_to_normalized_matrix` per file. **Every later step reads from this.**

---

### **M2 — JSON contract + frontend handshake (mock data)** *(1 hour, in parallel with frontend)*

**Goal:** End-to-end pipeline working with **fake** roof/panel data, before any geometry code exists. Proves the integration before we invest in detection.

**Deliverable:**
- `roof3d/contract.py` — pydantic models for the JSON above; serializer.
- `scripts/emit_mock.py` — for one GLB (start with Hamburg, smallest), hand-author one roof plane (a flat south-facing rectangle on top of the bbox) and a 4×3 panel grid on it. Emit valid JSON.
- Frontend teammate loads GLB + mock JSON, renders panels as quads inside the same parent `<group>`.

**Test:** Open frontend. Panels visibly sit on/near the roof. They move with the building when the parent group is rotated. No z-fighting (3 cm lift works).

**Exit:** Frontend confirms panels render in the correct frame for at least one GLB. JSON schema is **frozen** from this point — only additive changes allowed.

> **Why this milestone is critical:** if M2 fails, no amount of beautiful roof detection will save the demo. We catch the integration bug while there's still time.

---

### **M3 — Manual-config fallback path** *(45 min)*

**Goal:** Guarantee demo success regardless of detection quality. For each of the 4 demo GLBs, produce a hand-tuned JSON.

**Deliverable:**
- `roof3d/manual_config.py` — for each GLB filename, a dict: list of plane definitions `(centroid, normal, u_axis, half_extents_uv)` written by eyeballing the model in trimesh's viewer.
- `scripts/emit_manual.py <glb>` — uses the config to produce JSON, including panel placement using the M5 placer (stub for now, just dumps the plane rectangle as one big "panel" until M5 lands).
- `source: "manual_config"`, `confidence: 1.0` (we wrote it).

**Test:** Run on all 4 GLBs. Frontend renders correctly for all 4.

**Exit:** Even with auto detection completely broken, every demo GLB has a working JSON. **This is your insurance policy.**

---

### **M4 — Roof candidate filter (upward + high)** *(45 min)*

**Goal:** From a baked mesh, select the subset of triangles that could plausibly be roof.

**Deliverable:** `roof3d/candidates.py::select_roof_candidates(mesh) -> face_indices`
- Bake scene transforms (`scene.dump(concatenate=True)`).
- Optional smoothing pass: average each face normal with its 1-ring neighbors (helps with photogrammetry noise).
- Keep faces with `face_centroid_z > z_min + 0.45 * bbox_height` AND `normal_z > 0.35` AND `face_area > area_threshold`.
- Return mask + a debug PLY/PNG showing kept faces in red, rejected in grey.

**Test:** Visualize on each GLB. Roof faces should be red, walls/ground/trees grey. Save side-by-side PNGs.

**Exit:** On at least 3/4 GLBs, the kept face set is visually dominated by roof surfaces. (One stubborn model is acceptable — that's what M3 and M7 are for.)

---

### **M5 — Plane clustering + per-plane geometry** *(2 hours)*

**Goal:** Group candidate faces into discrete roof planes; compute each plane's geometry.

**Deliverable:** `roof3d/planes.py::cluster_planes(mesh, face_mask) -> List[RoofPlane]`

Pipeline:
1. **Cluster by normal direction** — DBSCAN on unit normals, `eps≈0.15`.
2. **Split by plane offset** — within a normal cluster, secondary cluster on `d = n · centroid` (separates parallel roof faces at different heights).
3. **Split by spatial connectivity** — within an offset cluster, connected-components on adjacency (separates main-house roof from garage roof at same orientation).
4. **Per-plane:**
   - PCA fit → refined plane normal + `(u, v)` basis.
   - Project cluster vertices to 2D.
   - Boundary polygon: try `alphashape` first, fall back to convex hull if alpha shape fails or has holes.
   - Compute area, tilt (vs world up), azimuth (compass bearing of the projected normal; 180° = south).
   - Compute `confidence` from: area > 5 m²? normal std-dev low? height in upper half? polygon valid (not self-intersecting)?

**Test:** For each GLB, dump per-plane debug renders (each plane in a different color). Compare against M3 manual config — they should roughly match. Print a table: `plane_id | tilt | azimuth | area | confidence`.

**Exit:** ≥1 GLB produces clean planes (≥0.7 confidence on at least one south-ish plane). Use `source: "auto"` for these; fall through to M3 for the rest.

---

### **M6 — Obstruction reserve + usable polygon** *(45 min)*

**Goal:** Shrink each roof polygon to a defensible "usable" area.

**Deliverable:** `roof3d/usable.py::compute_usable(plane) -> usable_polygon`
- `usable = polygon.buffer(-0.30)` (eaves clearance, 30 cm).
- Optional: subtract an additional 8% area as obstruction reserve, applied as a uniform inward buffer rather than a percentage off the area (so it's visible).
- Emit `obstructions[]` entries for the visible reserve ring.
- **Stronger version (only if time):** find vertices with `|signed_distance_to_plane| > 0.15 m` within a plane cluster, project to 2D, buffer by 0.30 m, subtract from usable polygon → real chimney/dormer detection.

**Test:** Frontend visualizes `polygon_3d` (outer outline) and `usable_polygon_3d` (inner). Eyeball: usable polygon is inset from the roof edge by ~30 cm.

**Exit:** Visible inset is rendered correctly on at least one GLB.

---

### **M7 — Panel placement (greedy grid, both orientations)** *(1.5 hours)*

**Goal:** Fill the usable polygon with module rectangles.

**Deliverable:** `roof3d/placement.py::place_panels(plane, usable_polygon, module_spec) -> List[Panel]`

Algorithm:
1. Pick module size, e.g. 1.13 × 1.72 m, 440 W.
2. Get oriented bounding rect of the usable polygon (in the plane's `u,v` frame).
3. For each orientation (portrait, landscape):
   - Generate a grid with row gap 2 cm, column gap 2 cm.
   - For each cell, build the rectangle; keep iff `usable_polygon.contains(rect)`.
4. Pick orientation with the higher count (tiebreak: higher kWp).
5. For each kept rectangle: compute 4 corners in the plane's `(u,v)` frame, lift by `+0.03` along plane normal, transform to **original GLB coordinates** using the M1 inverse transform.
6. Aggregate: `panel_count`, `system_kwp = panel_count * watt_peak / 1000`, `panels_by_plane`.

**Test:** Frontend renders panels. They are grid-aligned, do not overlap, do not poke outside the polygon, do not z-fight. Counts and kWp print to console.

**Exit:** At least one GLB shows a clean panel layout matching expectations (~30–50 panels for a typical house).

---

### **M8 — Interactive verification: hover + click-to-seed** *(1.5 hours, partly frontend)*

**Goal:** Demo polish + reliability. Cursor inspection and human-in-the-loop seeding.

**Deliverables:**

A. **Hover inspection (frontend mostly):**
   - Frontend raycasts the cursor against the roof mesh.
   - Looks up which `roof_plane.id` the hit triangle belongs to (backend exports a `face_to_plane.json` lookup or the frontend uses point-in-polygon-3D against `polygon_3d`).
   - Shows tooltip: tilt, azimuth, area, panel count, confidence, source.

B. **Click-to-seed (backend endpoint or CLI):**
   - Frontend sends `{glb, hit_point, hit_normal}` to backend.
   - Backend `roof3d/seeded.py::fit_plane_from_seed(mesh, point, normal)`:
     1. Find the face nearest `hit_point`.
     2. Region-grow neighbors whose normal is within 15° and whose distance to the seed plane is < 0.10 m.
     3. Run M5 per-plane geometry on the grown set.
     4. Run M6 + M7.
   - Returns a fresh JSON entry with `source: "click_seeded"`, replacing or augmenting the auto plane.

C. **Toggle overlays (frontend):** raw model / detected planes / usable polygon / obstruction reserve / panels — independently switchable.

**Test:**
- Hover any roof face → tooltip shows correct plane info.
- On a GLB where M5 clusters poorly, click the roof → a clean plane and panel grid appear within ~1 s.

**Exit:** Hover works for all 4 GLBs. Click-to-seed produces a usable plane on the worst-detected GLB.

---

### **M9 — Project ↔ GLB binding & batch run** *(20 min)*

**Goal:** Tie the 3D module to specific projects so Person 1's recommendation engine can call us by `project_id`.

**Deliverable:**
- `roof3d/project_glb_map.json` — explicit mapping for the 4 demo projects (decided with the team; if no real link in the data, hardcode):
  ```json
  {
    "297be54c5e7e4aad": "3D_Modell Hamburg.glb",
    "98b53eaa68c0eeeb": "3D_Modell Brandenburg.glb",
    "...": "...",
    "...": "..."
  }
  ```
- `scripts/build_all.py` — runs the full pipeline (M4–M7, with M3 fallback on failure) for every mapped project, writing `<project_id>.roof.json`.

**Test:** Run once. All 4 JSONs exist and are non-empty.

**Exit:** Person 1 can `open("<project_id>.roof.json")` and read `summary.system_kwp`.

---

### **M10 — Stretch goals (only if M0–M9 done with time left)** *(time-permitting)*

In priority order:
1. **Real obstruction detection** (the stronger M6 variant — chimneys/dormers from off-plane vertices).
2. **RANSAC fallback** for noisy GLBs (`pyransac3d.Plane().fit` iteratively, replaces M5 internals when DBSCAN fails).
3. **Setback rules**: ≥0.5 m from ridge edges, configurable.
4. **Multi-module-spec optimization**: try 410 W and 440 W modules, pick max kWp.
5. **South-orientation ranking** in `summary` so the recommendation engine prefers the best plane.

---

## 4. Risk register

| Risk | Mitigation milestone |
|---|---|
| GLB units inconsistent across files | M1 (audit and freeze) |
| GLB has nested scene graph, vertices look at origin | M1 (bake transforms) |
| Frontend and backend disagree on coordinate frame | M2 (mock end-to-end before geometry) |
| Photogrammetry noise breaks normal clustering | M5 normal smoothing + M8 click-to-seed + M10 RANSAC |
| Convex hull overestimates L-shaped roofs | M5 alpha shape first, hull only as fallback |
| Auto detection fails on demo day | M3 manual config insurance + M8 click-to-seed |
| Panels z-fight with roof in viewer | M7 +3 cm normal offset |
| Project↔GLB mapping unclear | M9 (decide explicitly with team) |

---

## 5. Demo storyline (what we tell judges)

> "We pick customer 98b53… — high consumption, has an EV, no solar yet. The recommendation engine wants ~18 kWp. We load their 3D house model. The system automatically detects three roof planes — here's the south-facing 32° plane in green, confidence 84%. Hover any face — the tooltip shows tilt, azimuth, and which plane it belongs to. The eaves and a safety margin are reserved automatically. We place 28 panels at 440 W = **12.3 kWp**. The offer is automatically capped to physical roof reality, and every panel you see is at its real position on this real house. If our detection is uncertain, the installer just clicks the roof — the system re-fits locally. That's the AI Renewable Designer's roof intelligence."

---

## 6. Order of operations on day one

1. **M0 + M1** before lunch — without these, nothing else is reliable.
2. **M2 + M3 in parallel with frontend teammate** — guarantees a working demo path before any detection code exists.
3. **M4 → M5 → M6 → M7** — the auto pipeline.
4. **M8** — interactive layer; pairs nicely with frontend.
5. **M9** — wiring to Person 1.
6. **M10** only if everything else is green.

**Definition of "done for demo":** M0–M9 complete, frontend renders panels for all 4 demo GLBs, recommendation engine reads `system_kwp` from at least one project's JSON.
