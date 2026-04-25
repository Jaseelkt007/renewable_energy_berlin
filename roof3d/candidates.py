"""M4 — roof candidate face filter.

Photogrammetry meshes are noisy: per-face normals jitter even on a single roof
plane, and the four hackathon GLBs are *multi-building tiles* (M1 finding), not
single houses. So the filter has to:

1. Smooth normals (average each face's normal with its 1-ring neighbors).
2. Reject downward / steep walls / ground (`normal_z > NORMAL_Z_MIN`).
3. Reject tiny degenerate faces (`face_area > AREA_MIN`).
4. Keep faces that are *locally* elevated — i.e., near the top of their own XY
   cell. This is the M1-aware twist on the plan: a global "top 55%" cutoff would
   only catch the tallest building's roof; the local test catches every roof in
   the tile.

The function returns a boolean face mask plus debug info (smoothed normals, the
local-max grid) so visualization scripts can render side-by-side.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh


# Tuning knobs — surfaced as constants so the visualization script can sweep them.
NORMAL_Z_MIN = 0.35           # cos(angle from vertical) >= this  => mostly upward
AREA_MIN_M2 = 0.05            # drop slivers (< 5 cm^2-ish triangles)
LOCAL_CELL_M = 3.0            # XY cell size for the local-max test
LOCAL_TOLERANCE_M = 1.5       # face kept if its z is within this of the cell max
SMOOTH_ITERATIONS = 1         # 1 pass is usually enough; bump to 2 if jittery


@dataclass
class CandidateResult:
    mask: np.ndarray               # bool, len == n_faces
    face_centroids: np.ndarray     # (n_faces, 3)
    face_normals: np.ndarray       # smoothed, (n_faces, 3) unit
    face_areas: np.ndarray         # (n_faces,)
    cell_max_z: dict[tuple[int, int], float]
    rejection_reasons: dict[str, int]   # diagnostics


def _smooth_normals(mesh: trimesh.Trimesh, iterations: int) -> np.ndarray:
    """1-ring face-normal averaging via face_adjacency."""
    n = mesh.face_normals.copy().astype(np.float64)
    if iterations <= 0:
        return n
    adj = mesh.face_adjacency  # (m, 2) face index pairs
    if adj is None or len(adj) == 0:
        return n
    n_faces = len(n)
    for _ in range(iterations):
        accum = n.copy()
        counts = np.ones(n_faces, dtype=np.int64)
        np.add.at(accum, adj[:, 0], n[adj[:, 1]])
        np.add.at(accum, adj[:, 1], n[adj[:, 0]])
        np.add.at(counts, adj[:, 0], 1)
        np.add.at(counts, adj[:, 1], 1)
        n = accum / counts[:, None]
        norms = np.linalg.norm(n, axis=1, keepdims=True)
        norms[norms < 1e-9] = 1.0
        n = n / norms
    return n


def select_roof_candidates(
    mesh: trimesh.Trimesh,
    *,
    normal_z_min: float = NORMAL_Z_MIN,
    area_min_m2: float = AREA_MIN_M2,
    local_cell_m: float = LOCAL_CELL_M,
    local_tolerance_m: float = LOCAL_TOLERANCE_M,
    smooth_iterations: int = SMOOTH_ITERATIONS,
) -> CandidateResult:
    """Return a boolean mask over `mesh.faces` of plausible-roof triangles."""
    if len(mesh.faces) == 0:
        return CandidateResult(
            mask=np.zeros(0, dtype=bool),
            face_centroids=np.zeros((0, 3)),
            face_normals=np.zeros((0, 3)),
            face_areas=np.zeros(0),
            cell_max_z={},
            rejection_reasons={},
        )

    centroids = mesh.triangles_center.astype(np.float64)
    areas = mesh.area_faces.astype(np.float64)
    normals = _smooth_normals(mesh, smooth_iterations)

    upward = normals[:, 2] >= normal_z_min
    big_enough = areas >= area_min_m2

    # Local-max test: bin centroids into XY cells, take per-cell max Z.
    ix = np.floor(centroids[:, 0] / local_cell_m).astype(np.int64)
    iy = np.floor(centroids[:, 1] / local_cell_m).astype(np.int64)
    cell_max_z: dict[tuple[int, int], float] = {}
    # Use np.maximum.at for vectorized per-cell max.
    keys = ix * 1_000_003 + iy  # cheap hash for grouping
    order = np.argsort(keys)
    sorted_keys = keys[order]
    sorted_z = centroids[order, 2]
    # Find run starts
    starts = np.concatenate(([0], np.where(np.diff(sorted_keys) != 0)[0] + 1, [len(sorted_keys)]))
    cell_max_z_arr = np.full(len(centroids), -np.inf)
    for s, e in zip(starts[:-1], starts[1:]):
        mx = sorted_z[s:e].max()
        cell_max_z_arr[order[s:e]] = mx
        # Record a representative entry for diagnostics
        kx = int(ix[order[s]])
        ky = int(iy[order[s]])
        cell_max_z[(kx, ky)] = float(mx)

    locally_high = centroids[:, 2] >= cell_max_z_arr - local_tolerance_m

    mask = upward & big_enough & locally_high

    rejection_reasons = {
        "rejected_downward": int((~upward).sum()),
        "rejected_too_small": int((~big_enough & upward).sum()),
        "rejected_not_local_top": int((~locally_high & upward & big_enough).sum()),
        "kept": int(mask.sum()),
        "total": int(len(mesh.faces)),
    }

    return CandidateResult(
        mask=mask,
        face_centroids=centroids,
        face_normals=normals,
        face_areas=areas,
        cell_max_z=cell_max_z,
        rejection_reasons=rejection_reasons,
    )
