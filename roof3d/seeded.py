"""M8 — fit a roof plane locally from a user click on the mesh.

This is the "human-in-the-loop" fallback when the automatic M4-M5 detection
either misses a roof or picks the wrong plane. The user clicks a point on the
mesh; we region-grow from the nearest face, refit a plane (M5), then run the
M6 usable shrink and the M7 panel placement on that single plane.

Returned planes are tagged `source="click_seeded"` so the UI / installer can
clearly distinguish operator-confirmed planes from auto-detected ones.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np
import trimesh

from roof3d.assemble import AssembledDesign, planes_to_contract
from roof3d.candidates import CandidateResult, _smooth_normals
from roof3d.contract import Obstruction, Panel, RoofPlane
from roof3d.placement import ModuleSpec, place_panels_in_polygon
from roof3d.planes import DetectedPlane, _build_plane, cluster_planes
from roof3d.quality import (
    GateParams,
    Selection,
    apply_quality_gate,
    summarise_decisions,
)
from roof3d.usable import compute_usable

# Region-grow tuning
NORMAL_TOLERANCE_DEG = 18.0
DISTANCE_TOLERANCE_M = 0.15
MIN_FACES = 30


@dataclass
class SeedResult:
    plane: RoofPlane
    panels: list[Panel]
    obstructions: list[Obstruction]
    n_faces: int


def fit_plane_from_seed(
    mesh: trimesh.Trimesh,
    hit_point,
    hit_normal=None,
    *,
    face_index: Optional[int] = None,
    plane_id: str = "roof_seeded",
    normal_tolerance_deg: float = NORMAL_TOLERANCE_DEG,
    distance_tolerance_m: float = DISTANCE_TOLERANCE_M,
    min_faces: int = MIN_FACES,
) -> Optional[DetectedPlane]:
    """Region-grow from the face nearest hit_point and refit a plane.

    `hit_point` is in the same coordinate space as `mesh.vertices` (the GLB
    local space — RTC offset NOT applied). `hit_normal` (optional) is the
    surface normal at the click; if it agrees with the seed face's smoothed
    normal, we use it as the seed direction (helps when the click lands on a
    noisy triangle).
    """
    if len(mesh.faces) == 0:
        return None
    hit = np.asarray(hit_point, dtype=np.float64)

    centroids = mesh.triangles_center
    smoothed = _smooth_normals(mesh, iterations=1)

    if face_index is not None and 0 <= int(face_index) < len(mesh.faces):
        # Trust the raycaster — the click landed on this exact triangle.
        seed_face = int(face_index)
    else:
        # No face index supplied: among the K nearest faces, prefer the one
        # with the most upward-facing smoothed normal. A pure nearest-face
        # pick lands on walls whenever the hit point is near a roof edge.
        distances = np.linalg.norm(centroids - hit, axis=1)
        K = min(60, len(mesh.faces))
        nearest_k = np.argpartition(distances, K - 1)[:K]
        flipped_z = np.where(smoothed[nearest_k, 2] >= 0,
                             smoothed[nearest_k, 2],
                             -smoothed[nearest_k, 2])
        scores = flipped_z - 0.05 * distances[nearest_k]
        seed_face = int(nearest_k[int(np.argmax(scores))])

    seed_normal = smoothed[seed_face].copy()
    if seed_normal[2] < 0:
        seed_normal = -seed_normal
    seed_normal /= np.linalg.norm(seed_normal) + 1e-12

    if hit_normal is not None:
        hn = np.asarray(hit_normal, dtype=np.float64)
        if np.linalg.norm(hn) > 1e-6:
            hn = hn / np.linalg.norm(hn)
            if hn[2] < 0:
                hn = -hn
            # Trust the picked normal if it's plausibly roof-like (upward) and
            # roughly agrees with the seed face — handles the common case where
            # the raycaster's normal is more accurate than the noisy seed face.
            if hn[2] > 0.3 and abs(float(np.dot(hn, seed_normal))) > 0.6:
                seed_normal = hn

    seed_centroid = centroids[seed_face].astype(np.float64)
    cos_thresh = float(np.cos(np.radians(normal_tolerance_deg)))

    n_faces = len(mesh.faces)
    nbrs: list[list[int]] = [[] for _ in range(n_faces)]
    for a, b in mesh.face_adjacency:
        nbrs[int(a)].append(int(b))
        nbrs[int(b)].append(int(a))

    visited = np.zeros(n_faces, dtype=bool)
    accepted: list[int] = [seed_face]
    visited[seed_face] = True

    queue = deque([seed_face])
    while queue:
        f = queue.popleft()
        for nb in nbrs[f]:
            if visited[nb]:
                continue
            visited[nb] = True
            n_norm = smoothed[nb]
            if n_norm[2] < 0:
                n_norm = -n_norm
            if float(np.dot(n_norm, seed_normal)) < cos_thresh:
                continue
            d = abs(float(np.dot(centroids[nb] - seed_centroid, seed_normal)))
            if d > distance_tolerance_m:
                continue
            accepted.append(nb)
            queue.append(nb)

    if len(accepted) < min_faces:
        return None

    face_indices = np.asarray(accepted, dtype=np.int64)
    return _build_plane(mesh, face_indices, plane_id=plane_id)


def design_for_seed(
    mesh: trimesh.Trimesh,
    hit_point,
    hit_normal=None,
    *,
    face_index: Optional[int] = None,
    plane_id: str = "roof_seeded",
    module: ModuleSpec = ModuleSpec(),
    detect_bumps: bool = True,
) -> Optional[SeedResult]:
    """Full per-click pipeline: seed -> M5 refit -> M6 usable -> M7 panels."""
    detected = fit_plane_from_seed(
        mesh, hit_point, hit_normal,
        face_index=face_index, plane_id=plane_id,
    )
    if detected is None:
        return None
    usable = compute_usable(mesh, detected, detect_bumps=detect_bumps)
    contract_plane, panels, obstructions = place_panels_in_polygon(
        plane_id, detected, usable, module=module, source="click_seeded",
    )
    return SeedResult(
        plane=contract_plane,
        panels=panels,
        obstructions=obstructions,
        n_faces=int(len(detected.face_indices)),
    )


# ---------------------------------------------------------------------------
# M11 — interactive ROI-driven design (full M4..M7 pipeline on a marked region)
# ---------------------------------------------------------------------------

# Smallest candidate count we'll send into M5 from an ROI. Below this, the
# clustering thresholds (MIN_PLANE_FACES=30 in M5) reliably yield zero planes,
# and a fast 422 from the caller is much friendlier than a vague empty result.
ROI_MIN_CANDIDATES = 30


class RoiTooSmall(ValueError):
    """Raised when the ROI mask leaves too few candidate faces for M5 to work.

    The caller (HTTP endpoint) should map this to a 422 with an actionable
    message — typically "click closer to the roof centre or increase the
    radius".
    """


@dataclass
class RoiDesignResult:
    """Full pipeline result for an interactive ROI request.

    Mirrors the per-project AssembledDesign shape but adds diagnostics the
    frontend uses to display loading state / fallback hints.
    """
    assembled: AssembledDesign
    n_candidates_in_roi: int
    n_planes_detected: int
    n_planes_accepted: int


def _roi_circle_mask(centroids: np.ndarray, center_xy, radius_m: float) -> np.ndarray:
    """Boolean mask over face indices: centroid XY within `radius_m` of center."""
    cx = float(center_xy[0])
    cy = float(center_xy[1])
    dx = centroids[:, 0] - cx
    dy = centroids[:, 1] - cy
    return (dx * dx + dy * dy) <= (float(radius_m) * float(radius_m))


def design_for_roi(
    mesh: trimesh.Trimesh,
    candidate_result: CandidateResult,
    *,
    roi_center_xy,
    roi_radius_m: float,
    gate_params: Optional[GateParams] = None,
    module: ModuleSpec = ModuleSpec(),
    max_planes: int = 12,
    min_usable_area_m2: float = 4.0,
    detect_bumps: bool = True,
) -> RoiDesignResult:
    """Run M4 (mask) -> M5 -> M10 gate -> M6 -> M7 restricted to an XY circle.

    Inputs:
        mesh              — full GLB mesh (NOT submeshed; face_adjacency stays
                            intact so M5's connected-components step is correct
                            across the ROI boundary).
        candidate_result  — output of `select_roof_candidates(mesh)`. The caller
                            is expected to cache this per project.
        roi_center_xy     — (x, y) in GLB local space.
        roi_radius_m      — radius of the selection circle (meters).
        gate_params       — optional override for M10 gate (e.g. lowered
                            min_height_above_ground_m for plateau projects).

    Raises RoiTooSmall if fewer than ROI_MIN_CANDIDATES candidate faces fall
    inside the circle — surfaced as 422 by the HTTP layer.
    """
    if candidate_result.mask.size == 0:
        raise RoiTooSmall("mesh has no candidate faces")

    # Mask candidates by ROI XY-distance. Keep the same CandidateResult shape
    # (cluster_planes only reads `.mask`, `.face_normals`, `.face_centroids`).
    roi_mask = _roi_circle_mask(candidate_result.face_centroids, roi_center_xy, roi_radius_m)
    combined_mask = candidate_result.mask & roi_mask
    n_in_roi = int(combined_mask.sum())
    if n_in_roi < ROI_MIN_CANDIDATES:
        raise RoiTooSmall(
            f"ROI contains {n_in_roi} candidate faces "
            f"(need >= {ROI_MIN_CANDIDATES}); try a larger radius "
            "or click closer to the roof centre"
        )

    roi_cand = CandidateResult(
        mask=combined_mask,
        face_centroids=candidate_result.face_centroids,
        face_normals=candidate_result.face_normals,
        face_areas=candidate_result.face_areas,
        cell_max_z=candidate_result.cell_max_z,
        rejection_reasons={
            **candidate_result.rejection_reasons,
            "kept_in_roi": n_in_roi,
        },
    )

    detected = cluster_planes(mesh, roi_cand)
    n_detected = len(detected)

    selection = Selection(
        mode="roi_circle",
        center_xy=(float(roi_center_xy[0]), float(roi_center_xy[1])),
        radius_m=float(roi_radius_m),
    )
    gp = gate_params or GateParams()
    accepted, decisions = apply_quality_gate(
        mesh, detected, params=gp, selection=selection,
    )
    gate_warnings = summarise_decisions(decisions, selection)
    # Replace the M10 prefix so the frontend shows "Live ROI" instead of
    # the static "Selected-building result" wording.
    if gate_warnings and gate_warnings[0].startswith("Selected-building result"):
        gate_warnings[0] = gate_warnings[0].replace(
            "Selected-building result",
            f"Live ROI result (r={roi_radius_m:.1f}m)",
            1,
        )

    assembled = planes_to_contract(
        mesh, accepted,
        module=module,
        max_planes=max_planes,
        min_usable_area_m2=min_usable_area_m2,
        detect_bumps=detect_bumps,
        extra_warnings=gate_warnings,
        method="interactive_roi",
        panel_source="auto",
    )

    return RoiDesignResult(
        assembled=assembled,
        n_candidates_in_roi=n_in_roi,
        n_planes_detected=n_detected,
        n_planes_accepted=len(accepted),
    )
