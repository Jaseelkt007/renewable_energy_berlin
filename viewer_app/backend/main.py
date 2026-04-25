"""Visualization-bridge backend (M0-M7) + M8 seeded-plane endpoint.

Serves the four demo GLBs and their auto-pipeline roof JSONs to the Next.js
frontend. Read-only on disk; the seed endpoint is in-memory only.

Endpoints:
    GET  /api/health
    GET  /api/projects
    GET  /api/projects/{project_id}/roof
    GET  /api/projects/{project_id}/model
    POST /api/projects/{project_id}/seed       (M8)
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("roof-viewer")

# Make the roof3d package importable when running uvicorn from anywhere.
_REPO_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_FOR_IMPORT))

from roof3d.candidates import select_roof_candidates  # noqa: E402
from roof3d.contract import (  # noqa: E402
    BBox,
    CoordinateSystem,
    RoofDesign,
)
from roof3d.loader import load_glb  # noqa: E402
from roof3d.placement import ModuleSpec  # noqa: E402
from roof3d.quality import GateParams  # noqa: E402
from roof3d.seeded import (  # noqa: E402
    RoiTooSmall,
    design_for_roi,
    design_for_seed,
)

# viewer_app/backend/main.py -> viewer_app/backend -> viewer_app -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_MAP_PATH = Path(__file__).resolve().parent / "project_map.json"
# Canonical M9/M10/M11 map — source of truth for `selection` and
# `gate_overrides` per project. Read at request time, not at import.
ROOF3D_MAP_PATH = REPO_ROOT / "roof3d" / "project_glb_map.json"

app = FastAPI(title="Roof Viewer Backend", version="0.1.0")

# CORS — broad and explicit. The previous `allow_methods=["GET","OPTIONS"]`
# silently broke POST /api/projects/.../seed in browsers because the preflight
# OPTIONS response did NOT advertise POST, so fetch() rejected with TypeError
# ("Failed to fetch"). curl bypasses CORS entirely, which is why command-line
# smoke tests passed. Whitelist all common Next.js ports + methods.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_project_map() -> list[dict]:
    if not PROJECT_MAP_PATH.is_file():
        raise HTTPException(status_code=500, detail="project_map.json missing")
    return json.loads(PROJECT_MAP_PATH.read_text())


def _get_project(project_id: str) -> dict:
    for p in _load_project_map():
        if p["project_id"] == project_id:
            return p
    raise HTTPException(status_code=404, detail=f"unknown project_id {project_id!r}")


def _safe_repo_path(rel_path: str) -> Path:
    """Resolve a project-map relative path under REPO_ROOT, blocking traversal."""
    candidate = (REPO_ROOT / rel_path).resolve()
    try:
        candidate.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="path traversal blocked") from exc
    return candidate


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


def _tile_path_for(proj: dict) -> Path:
    """M10 — sibling debug file produced by build_all.py: <id>.tile.roof.json."""
    canonical = _safe_repo_path(proj["roof_json"])
    return canonical.with_name(canonical.stem.replace(".roof", "") + ".tile.roof.json")


@app.get("/api/projects")
def projects() -> list[dict]:
    """List projects with which roof modes are available on disk.

    `modes` is one of {["selected","tile"], ["selected"], ["tile"]}. Frontend
    uses this to decide whether to show the tile/selected toggle.
    """
    out = []
    for p in _load_project_map():
        modes = []
        try:
            if _safe_repo_path(p["roof_json"]).is_file():
                modes.append("selected")
            if _tile_path_for(p).is_file():
                modes.append("tile")
        except HTTPException:
            pass
        out.append({**p, "modes": modes})
    return out


@app.get("/api/projects/{project_id}/roof")
def roof(
    project_id: str,
    mode: str = Query(default="selected", pattern="^(selected|tile)$"),
):
    proj = _get_project(project_id)
    if mode == "tile":
        roof_path = _tile_path_for(proj)
        rel_label = roof_path.name
    else:
        roof_path = _safe_repo_path(proj["roof_json"])
        rel_label = proj["roof_json"]
    if not roof_path.is_file():
        raise HTTPException(status_code=404,
                            detail=f"roof JSON not found: {rel_label}")
    return JSONResponse(json.loads(roof_path.read_text()))


@app.get("/api/projects/{project_id}/model")
def model(project_id: str):
    proj = _get_project(project_id)
    model_path = _safe_repo_path(proj["model_file"])
    if not model_path.is_file():
        raise HTTPException(status_code=404,
                            detail=f"model not found: {proj['model_file']}")
    return FileResponse(
        model_path,
        media_type="model/gltf-binary",
        filename=proj["model_file"],
    )


# ---------------------------------------------------------------------------
# M8 — click-to-seed plane fitting
# ---------------------------------------------------------------------------

# Cache loaded meshes so a click doesn't re-read 5-27 MB GLBs on every request.
_MESH_CACHE: dict[str, object] = {}
# M11 — cache M4 candidate results too. M4 is O(n_faces) and runs once per
# project; every ROI click reuses the same CandidateResult.
_CAND_CACHE: dict[str, object] = {}


def _get_cached_mesh(proj: dict):
    pid = proj["project_id"]
    cached = _MESH_CACHE.get(pid)
    if cached is not None:
        return cached
    glb_path = _safe_repo_path(proj["model_file"])
    if not glb_path.is_file():
        raise HTTPException(status_code=404, detail=f"model not found: {proj['model_file']}")
    loaded = load_glb(glb_path)
    _MESH_CACHE[pid] = loaded
    return loaded


def _get_cached_candidates(proj: dict):
    pid = proj["project_id"]
    cached = _CAND_CACHE.get(pid)
    if cached is not None:
        return cached
    loaded = _get_cached_mesh(proj)
    cand = select_roof_candidates(loaded.mesh)
    _CAND_CACHE[pid] = cand
    return cand


def _gate_overrides_for(project_id: str) -> dict:
    """Read per-project gate overrides from roof3d/project_glb_map.json.

    Returns {} if the canonical map is missing or the project isn't listed —
    callers fall back to default GateParams in that case.
    """
    if not ROOF3D_MAP_PATH.is_file():
        return {}
    try:
        data = json.loads(ROOF3D_MAP_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    for entry in data.get("projects", []):
        if entry.get("project_id") == project_id:
            return entry.get("gate_overrides") or {}
    return {}


class SeedRequest(BaseModel):
    hit_point: list[float] = Field(..., min_length=3, max_length=3)
    hit_normal: list[float] | None = Field(default=None, min_length=3, max_length=3)
    face_index: int | None = None
    plane_id: str | None = None


@app.post("/api/projects/{project_id}/seed")
def seed(project_id: str, body: SeedRequest):
    t0 = time.time()
    log.info(
        "seed REQUEST project=%s hit=%s normal=%s face_index=%s plane_id=%s",
        project_id, body.hit_point, body.hit_normal, body.face_index, body.plane_id,
    )
    proj = _get_project(project_id)
    loaded = _get_cached_mesh(proj)

    plane_id = body.plane_id or "seeded"
    res = design_for_seed(
        loaded.mesh,
        hit_point=body.hit_point,
        hit_normal=body.hit_normal,
        face_index=body.face_index,
        plane_id=plane_id,
    )
    elapsed_ms = int((time.time() - t0) * 1000)
    if res is None:
        log.warning("seed FAIL project=%s plane_id=%s elapsed_ms=%d (no plane fitted)",
                    project_id, plane_id, elapsed_ms)
        raise HTTPException(
            status_code=422,
            detail="No plane could be fitted here. Try clicking a flatter roof area or somewhere closer to the centre of a roof surface.",
        )
    log.info(
        "seed OK    project=%s plane_id=%s tilt=%.1f az=%.1f area=%.1f panels=%d faces=%d elapsed_ms=%d",
        project_id, plane_id, res.plane.tilt_deg, res.plane.azimuth_deg,
        res.plane.area_m2, len(res.panels), res.n_faces, elapsed_ms,
    )
    return {
        "plane": json.loads(res.plane.model_dump_json()),
        "panels": [json.loads(p.model_dump_json()) for p in res.panels],
        "obstructions": [json.loads(o.model_dump_json()) for o in res.obstructions],
        "diagnostics": {
            "n_faces_grown": res.n_faces,
            "elapsed_ms": elapsed_ms,
        },
    }


# ---------------------------------------------------------------------------
# M11 — interactive ROI-driven roof design
# ---------------------------------------------------------------------------

class DesignRequest(BaseModel):
    center_xy: list[float] = Field(..., min_length=2, max_length=2)
    radius_m: float = Field(..., gt=0.0, le=200.0)
    max_planes: int = Field(default=12, ge=1, le=50)
    detect_bumps: bool = True


@app.post("/api/projects/{project_id}/design")
def design(project_id: str, body: DesignRequest):
    """Run M4 (cached) -> ROI mask -> M5 -> M10 gate -> M6 -> M7 live.

    Returns a fresh, contract-shaped RoofDesign JSON. Coordinates are in GLB
    local space (same convention as the offline `out/<id>.roof.json`). The
    response is in-memory only — it does NOT overwrite any on-disk file.
    """
    t0 = time.time()
    log.info(
        "design REQUEST project=%s center=%s r=%.2f max_planes=%d",
        project_id, body.center_xy, body.radius_m, body.max_planes,
    )
    proj = _get_project(project_id)
    loaded = _get_cached_mesh(proj)
    cand = _get_cached_candidates(proj)

    overrides = _gate_overrides_for(project_id)
    gate_params = GateParams()
    for k, v in overrides.items():
        if hasattr(gate_params, k):
            setattr(gate_params, k, v)

    try:
        res = design_for_roi(
            loaded.mesh, cand,
            roi_center_xy=tuple(body.center_xy),
            roi_radius_m=body.radius_m,
            gate_params=gate_params,
            max_planes=body.max_planes,
            detect_bumps=body.detect_bumps,
        )
    except RoiTooSmall as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        log.warning("design FAIL project=%s reason=roi_too_small elapsed_ms=%d (%s)",
                    project_id, elapsed_ms, e)
        raise HTTPException(status_code=422, detail=str(e))

    a = res.assembled
    if a.summary.panel_count == 0:
        elapsed_ms = int((time.time() - t0) * 1000)
        log.warning(
            "design FAIL project=%s reason=no_panels detected=%d accepted=%d elapsed_ms=%d",
            project_id, res.n_planes_detected, res.n_planes_accepted, elapsed_ms,
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"ROI produced {res.n_planes_detected} candidate planes but "
                f"the quality gate accepted {res.n_planes_accepted}. "
                "Try a larger radius or click closer to the roof centre."
            ),
        )

    bbox_min = loaded.mesh.vertices.min(axis=0)
    bbox_max = loaded.mesh.vertices.max(axis=0)
    full = RoofDesign(
        project_id=project_id,
        model_file=proj["model_file"],
        coordinate_system=CoordinateSystem(),
        bbox=BBox(min=tuple(bbox_min.tolist()), max=tuple(bbox_max.tolist())),
        roof_planes=a.roof_planes,
        obstructions=a.obstructions,
        panels=a.panels,
        summary=a.summary,
        quality=a.quality,
    )
    elapsed_ms = int((time.time() - t0) * 1000)
    log.info(
        "design OK    project=%s candidates=%d detected=%d accepted=%d panels=%d kwp=%.2f elapsed_ms=%d",
        project_id, res.n_candidates_in_roi, res.n_planes_detected,
        res.n_planes_accepted, a.summary.panel_count, a.summary.system_kwp, elapsed_ms,
    )
    payload = json.loads(full.model_dump_json())
    payload["diagnostics"] = {
        "n_candidates_in_roi": res.n_candidates_in_roi,
        "n_planes_detected": res.n_planes_detected,
        "n_planes_accepted": res.n_planes_accepted,
        "elapsed_ms": elapsed_ms,
        "gate_overrides_applied": dict(overrides),
    }
    return payload
