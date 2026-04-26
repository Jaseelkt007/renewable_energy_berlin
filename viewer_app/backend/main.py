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
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("roof-viewer")

# Make the roof3d package importable when running uvicorn from anywhere.
_REPO_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_FOR_IMPORT))

from roof3d.assemble import planes_to_contract  # noqa: E402
from roof3d.candidates import select_roof_candidates  # noqa: E402
from roof3d.edit import validate_panel_placement  # noqa: E402
from roof3d.contract import (  # noqa: E402
    BBox,
    CoordinateSystem,
    RoofDesign,
)
from roof3d.loader import load_glb  # noqa: E402
from roof3d.placement import ModuleSpec  # noqa: E402
from roof3d.planes import cluster_planes  # noqa: E402
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
    allow_origin_regex=r"https://.*\.(lovable\.app|lovableproject\.com|lovable\.dev)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_project_map() -> list[dict]:
    if not PROJECT_MAP_PATH.is_file():
        raise HTTPException(status_code=500, detail="project_map.json missing")
    return json.loads(PROJECT_MAP_PATH.read_text())


# ---------------------------------------------------------------------------
# Upload sessions (Lovable integration)
#
# A "session" is a transient project created from a user-uploaded GLB. It
# lives only in process memory; Render restarts wipe it. The session_id is
# accepted in place of project_id on every existing route — _get_project
# checks _SESSIONS first and falls back to the on-disk project_map.json.
# ---------------------------------------------------------------------------

SESSION_TTL_MIN = 30
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


@dataclass
class SessionState:
    session_id: str
    glb_path: Path                # tempfile on disk; deleted with the session
    label: str
    loaded: Any                   # roof3d.loader.LoadedMesh
    candidates: Any               # roof3d.candidates.CandidateResult
    design: dict                  # cached RoofDesign as JSON-shaped dict
    panel_count: int = 0
    system_kwp: float = 0.0
    last_panel_source: str | None = None
    panel_count_updated_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_SESSIONS: dict[str, SessionState] = {}


def _evict_expired_sessions() -> None:
    """Lazy TTL sweep — called on every session-touching request."""
    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - SESSION_TTL_MIN * 60
    stale = [sid for sid, s in _SESSIONS.items() if s.last_seen.timestamp() < cutoff]
    for sid in stale:
        s = _SESSIONS.pop(sid, None)
        if s is None:
            continue
        try:
            if s.glb_path.is_file():
                s.glb_path.unlink()
        except OSError:
            pass
        log.info("session EVICTED %s (idle > %d min)", sid, SESSION_TTL_MIN)


def _touch_session(sid: str) -> SessionState:
    _evict_expired_sessions()
    s = _SESSIONS.get(sid)
    if s is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown or expired session {sid!r} — please re-upload the GLB",
        )
    s.last_seen = datetime.now(timezone.utc)
    return s


def _get_project(project_id: str) -> dict:
    """Return a project-shaped dict for either an upload session or a
    pre-baked project from project_map.json. Sessions take priority so a
    UUID can never collide with a hand-picked demo id.
    """
    s = _SESSIONS.get(project_id)
    if s is not None:
        s.last_seen = datetime.now(timezone.utc)
        # The model_file path here is absolute (the tempfile); _safe_repo_path
        # is only used for entries from project_map.json, so callers must
        # detect sessions explicitly when resolving paths.
        return {
            "project_id": s.session_id,
            "label": s.label,
            "model_file": str(s.glb_path),
            "_session": True,
        }
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


@app.get("/")
@app.get("/health")
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "roof-viewer-backend"}


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
    # Upload session: there is exactly one design (computed at /upload time).
    # The `mode` parameter is accepted for API symmetry but ignored.
    if proj.get("_session"):
        return JSONResponse(_SESSIONS[project_id].design)
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
    if proj.get("_session"):
        glb_path = Path(proj["model_file"])
        if not glb_path.is_file():
            raise HTTPException(status_code=404, detail="uploaded model file is gone")
        return FileResponse(
            glb_path,
            media_type="model/gltf-binary",
            filename=f"upload_{project_id}.glb",
        )
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
    # Upload session: mesh already loaded at /upload time, attached to the
    # session state. Skip the on-disk lookup entirely.
    if proj.get("_session"):
        s = _SESSIONS[pid]
        return s.loaded
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
    if proj.get("_session"):
        s = _SESSIONS[pid]
        return s.candidates
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


# ---------------------------------------------------------------------------
# M12 — manual panel edit: geometry validation
# ---------------------------------------------------------------------------

class ValidatePanelGeometryRequest(BaseModel):
    plane_id: str
    plane_centroid: list[float] = Field(..., min_length=3, max_length=3)
    plane_u_axis: list[float] = Field(..., min_length=3, max_length=3)
    plane_v_axis: list[float] = Field(..., min_length=3, max_length=3)
    usable_polygon_3d: list[list[float]]
    candidate_corners_3d: list[list[float]] = Field(..., min_length=4, max_length=4)
    existing_panels_corners_3d: list[list[list[float]]] = Field(default_factory=list)
    # M12.1 adjacency-bypass — optional for back-compat with older clients.
    ai_panel_centers: list[list[float]] = Field(default_factory=list)
    panel_width_m: float | None = None
    panel_height_m: float | None = None


@app.post("/api/projects/{project_id}/validate-panel-geometry")
def validate_panel_geometry(project_id: str, body: ValidatePanelGeometryRequest):
    # project_id is accepted for symmetry with other endpoints; the validation
    # is stateless because the frontend ships the relevant plane data.
    _get_project(project_id)
    result = validate_panel_placement(
        plane_centroid=body.plane_centroid,
        plane_u_axis=body.plane_u_axis,
        plane_v_axis=body.plane_v_axis,
        usable_polygon_3d=body.usable_polygon_3d,
        candidate_corners_3d=body.candidate_corners_3d,
        existing_panels_corners_3d=body.existing_panels_corners_3d,
        ai_panel_centers=body.ai_panel_centers,
        panel_width_m=body.panel_width_m,
        panel_height_m=body.panel_height_m,
    )
    return {"ok": result.ok, "reason": result.reason, "plane_id": body.plane_id}


# ---------------------------------------------------------------------------
# Lovable integration — GLB upload + per-session panel-count tracking
# ---------------------------------------------------------------------------


def _build_design_from_mesh(loaded, candidates, project_id: str, model_file: str) -> dict:
    """Run M5 cluster -> M7 assembly on a freshly-loaded mesh and return a
    contract-shaped dict. Mirrors `scripts.emit_auto.emit` but without writing
    to disk and without the M10 quality gate (selection=tile_wide), so the
    user sees every detected plane on their uploaded model.
    """
    detected = cluster_planes(loaded.mesh, candidates)
    gate_warnings = ["Tile-wide result: multiple buildings may be included."]

    assembled = planes_to_contract(
        loaded.mesh, detected,
        module=ModuleSpec(),
        max_planes=12,
        min_usable_area_m2=4.0,
        detect_bumps=True,
        extra_warnings=gate_warnings,
        method="auto_normal_cluster",
        panel_source="auto",
    )

    bbox_min = loaded.mesh.vertices.min(axis=0)
    bbox_max = loaded.mesh.vertices.max(axis=0)
    design = RoofDesign(
        project_id=project_id,
        model_file=model_file,
        coordinate_system=CoordinateSystem(),
        bbox=BBox(min=tuple(bbox_min.tolist()), max=tuple(bbox_max.tolist())),
        roof_planes=assembled.roof_planes,
        obstructions=assembled.obstructions,
        panels=assembled.panels,
        summary=assembled.summary,
        quality=assembled.quality,
    )
    return json.loads(design.model_dump_json())


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    label: str | None = None,
):
    """Accept a GLB, run the auto pipeline, register an in-memory session.

    Returns:
        {
          "session_id": "<uuid>",
          "model_file": "<original filename>",
          "design":     <RoofDesign>,
          "diagnostics": {
            "load_ms": int, "pipeline_ms": int,
            "n_faces": int, "n_planes": int, "n_panels": int
          }
        }

    Errors:
        400  — not a GLB / empty body.
        413  — > 50 MB.
        422  — pipeline produced no roof planes.
    """
    _evict_expired_sessions()

    name = file.filename or "upload.glb"
    if not name.lower().endswith(".glb"):
        raise HTTPException(status_code=400, detail="file must be a .glb")

    # Stream into a tempfile so we don't hold the full bytes in memory twice.
    tmp = tempfile.NamedTemporaryFile(suffix=".glb", delete=False)
    tmp_path = Path(tmp.name)
    total = 0
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                tmp.close()
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"upload too large (>{MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
                )
            tmp.write(chunk)
    finally:
        tmp.close()

    if total == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="empty upload")

    session_id = uuid.uuid4().hex
    log.info("upload START session=%s file=%s bytes=%d", session_id, name, total)

    t0 = time.time()
    try:
        loaded = load_glb(tmp_path)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        log.exception("upload FAIL session=%s load_glb error", session_id)
        raise HTTPException(status_code=400, detail=f"could not parse GLB: {e}")
    load_ms = int((time.time() - t0) * 1000)

    t1 = time.time()
    try:
        candidates = select_roof_candidates(loaded.mesh)
        design = _build_design_from_mesh(
            loaded, candidates,
            project_id=session_id,
            model_file=name,
        )
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        log.exception("upload FAIL session=%s pipeline error", session_id)
        raise HTTPException(status_code=500, detail=f"pipeline error: {e}")
    pipeline_ms = int((time.time() - t1) * 1000)

    n_planes = len(design.get("roof_planes", []))
    n_panels = len(design.get("panels", []))
    if n_planes == 0:
        tmp_path.unlink(missing_ok=True)
        log.warning("upload FAIL session=%s no_planes", session_id)
        raise HTTPException(
            status_code=422,
            detail=(
                "No roof planes detected in this model. Make sure the GLB is "
                "a building scan (not just terrain) and that roof surfaces "
                "are exposed in the mesh."
            ),
        )

    summary = design.get("summary", {}) or {}
    state = SessionState(
        session_id=session_id,
        glb_path=tmp_path,
        label=label or name,
        loaded=loaded,
        candidates=candidates,
        design=design,
        panel_count=int(summary.get("panel_count", 0)),
        system_kwp=float(summary.get("system_kwp", 0.0)),
        last_panel_source="auto",
        panel_count_updated_at=datetime.now(timezone.utc),
    )
    _SESSIONS[session_id] = state

    log.info(
        "upload OK    session=%s file=%s load_ms=%d pipeline_ms=%d "
        "faces=%d planes=%d panels=%d kwp=%.2f",
        session_id, name, load_ms, pipeline_ms,
        len(loaded.mesh.faces), n_planes, n_panels, state.system_kwp,
    )

    return {
        "session_id": session_id,
        "model_file": name,
        "design": design,
        "diagnostics": {
            "load_ms": load_ms,
            "pipeline_ms": pipeline_ms,
            "n_faces": int(len(loaded.mesh.faces)),
            "n_planes": n_planes,
            "n_panels": n_panels,
        },
    }


class PanelCountUpdate(BaseModel):
    panel_count: int = Field(..., ge=0)
    system_kwp: float | None = Field(default=None, ge=0.0)
    source: str | None = None  # "auto" | "roi" | "seed" | "manual_add" | ...


@app.post("/api/sessions/{session_id}/panel-count")
def post_panel_count(session_id: str, body: PanelCountUpdate):
    s = _touch_session(session_id)
    s.panel_count = body.panel_count
    if body.system_kwp is not None:
        s.system_kwp = body.system_kwp
    s.last_panel_source = body.source
    s.panel_count_updated_at = datetime.now(timezone.utc)
    log.info(
        "panel-count session=%s count=%d kwp=%.2f source=%s",
        session_id, s.panel_count, s.system_kwp, s.last_panel_source,
    )
    return {"ok": True, "panel_count": s.panel_count}


@app.get("/api/sessions/{session_id}/panel-count")
def get_panel_count(session_id: str):
    s = _touch_session(session_id)
    return {
        "panel_count": s.panel_count,
        "system_kwp": s.system_kwp,
        "source": s.last_panel_source,
        "updated_at": s.panel_count_updated_at.isoformat() if s.panel_count_updated_at else None,
    }


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    s = _SESSIONS.pop(session_id, None)
    if s is None:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id!r}")
    try:
        if s.glb_path.is_file():
            s.glb_path.unlink()
    except OSError:
        pass
    log.info("session DELETED %s", session_id)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════
# AI RENEWABLE DESIGNER — 3-layer engine (rules + kNN + Gemini)
# Imports kept local to avoid touching the roof-viewer's import surface.
# See viewer_app/backend/engine/ for catalog, knn, llm, pipeline modules.
# ═══════════════════════════════════════════════════════════════════════════

import asyncio as _asyncio  # alias to avoid colliding if file later imports asyncio
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
from typing import Optional as _Optional

from .engine.pipeline import (
    cache_stats as _design_cache_stats,
    clear_cache as _design_clear_cache,
    corpus_stats as _design_corpus_stats,
    design_system as _design_system,
)
from .engine.price_catalog import (
    CATALOG_VERSION as _CATALOG_VERSION,
    INVERTER_PRICES as _INVERTER_PRICES,
    PRICE_BY_CATEGORY as _PRICE_BY_CATEGORY,
    PRICE_CATALOG as _PRICE_CATALOG,
    PV_MODULE_450WP as _PV_MODULE_450WP,
)


class DesignRequest(BaseModel):
    profile: dict = Field(
        description="Customer profile. Required: energy_demand_wh. "
                    "Optional: has_ev, heating_existing_type, etc."
    )
    max_panels: int = Field(ge=0, le=200, description="Roof capacity. 0 = no PV.")
    mode: str = Field(default="balanced", description="budget | balanced | premium")
    overrides: _Optional[dict] = Field(default=None,
        description="Refinement constraints: {battery_kwh, include_hp, "
                    "include_wallbox, include_surge}")
    use_refine_model: bool = Field(default=False,
        description="If True, use cheaper/faster Flash Lite (good for refinement)")


# Thread pool for parallel LLM calls (Gemini SDK is synchronous)
_design_executor = _ThreadPoolExecutor(max_workers=4)


@app.get("/api/design/info")
def design_info():
    """Health + corpus stats for the design engine."""
    return {
        "service": "Reonic AI Renewable Designer",
        "corpus": _design_corpus_stats(),
        "catalog_version": _CATALOG_VERSION,
        "endpoints": [
            "POST /api/design",
            "POST /api/design/all-modes",
            "POST /api/design/refine",
            "GET  /api/catalog",
            "GET  /api/design/cache/stats",
            "POST /api/design/cache/clear",
        ],
    }


@app.get("/api/catalog")
def get_catalog():
    """
    Canonical price catalog. Used by the frontend's Installation Estimator
    (deterministic path) so its prices stay aligned with what the Model
    Calculator (backend pricing path) would charge for the same SKUs.

    Run a build-time codegen step (e.g. scripts/sync-prices.ts) that fetches
    this endpoint and writes the data into src/lib/componentPrices.ts. Drift
    becomes visible in the diff.
    """
    return {
        "version": _CATALOG_VERSION,
        "pv_module_450wp": _PV_MODULE_450WP,
        "inverter_prices": _INVERTER_PRICES,
        "catalog": _PRICE_CATALOG,
        "category_fallback": _PRICE_BY_CATEGORY,
    }


@app.post("/api/design")
async def post_design(req: DesignRequest):
    """Generate one BoM for the requested mode."""
    loop = _asyncio.get_event_loop()
    return await loop.run_in_executor(
        _design_executor,
        _design_system,
        req.profile, req.max_panels, req.mode, req.overrides, req.use_refine_model,
    )


@app.post("/api/design/all-modes")
async def post_design_all_modes(req: DesignRequest):
    """
    Generate Budget + Balanced + Premium BoMs in parallel.
    Cold cache: ~25-30s (vs ~60-75s sequential). Warm cache: <100ms.
    """
    loop = _asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(
            _design_executor,
            _design_system,
            req.profile, req.max_panels, mode, req.overrides, req.use_refine_model,
        )
        for mode in ("budget", "balanced", "premium")
    ]
    budget, balanced, premium = await _asyncio.gather(*tasks)
    return {"budget": budget, "balanced": balanced, "premium": premium}


@app.post("/api/design/refine")
async def post_design_refine(req: DesignRequest):
    """
    Convenience endpoint for the Refinement drawer.
    Forces use_refine_model=True (Flash Lite) for cheaper/faster iteration.
    """
    loop = _asyncio.get_event_loop()
    return await loop.run_in_executor(
        _design_executor,
        _design_system,
        req.profile, req.max_panels, req.mode, req.overrides, True,
    )


@app.get("/api/design/cache/stats")
def get_design_cache_stats():
    return _design_cache_stats()


@app.post("/api/design/cache/clear")
def post_design_cache_clear():
    n = _design_clear_cache()
    return {"cleared": n}
