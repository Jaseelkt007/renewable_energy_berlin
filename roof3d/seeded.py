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

from roof3d.candidates import _smooth_normals
from roof3d.contract import Obstruction, Panel, RoofPlane
from roof3d.placement import ModuleSpec, place_panels_in_polygon
from roof3d.planes import DetectedPlane, _build_plane
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
