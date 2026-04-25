"""M5 — cluster candidate faces into discrete roof planes.

Pipeline:
1. DBSCAN on smoothed face normals → group by orientation.
2. DBSCAN on plane-offset d = n_avg . centroid → split parallel roofs at
   different heights (e.g. main house vs. garage with same azimuth).
3. Face-adjacency connected components → split spatially-separated roofs that
   happen to share orientation AND height.
4. Per-plane refit: PCA → refined normal + (u, v) basis, project vertices to
   2D, build a boundary polygon (alpha-shape; fall back to convex hull),
   compute area / tilt / azimuth / confidence.

Returns DetectedPlane objects with everything M6/M7 need (basis, polygon, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import shapely.geometry as sg
import trimesh
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import ConvexHull
from sklearn.cluster import DBSCAN

try:
    import alphashape
    _HAS_ALPHASHAPE = True
except Exception:
    _HAS_ALPHASHAPE = False

from roof3d.candidates import CandidateResult


# Tuning knobs
NORMAL_EPS = 0.15           # DBSCAN radius on unit normals
NORMAL_MIN_SAMPLES = 25
OFFSET_EPS_M = 1.5          # DBSCAN radius on plane offset (meters)
OFFSET_MIN_SAMPLES = 15
MIN_PLANE_FACES = 30
MIN_PLANE_AREA_M2 = 4.0
ALPHA_SHAPE_ALPHA = 0.5     # 1/m; smaller = looser, larger = tighter
SUBSTANTIVE_MIN_FACES = 80  # confidence flag: plane is non-fragmentary


@dataclass
class DetectedPlane:
    plane_id: str
    face_indices: np.ndarray
    centroid: np.ndarray              # (3,)
    normal: np.ndarray                # (3,) unit
    u_axis: np.ndarray                # (3,) unit
    v_axis: np.ndarray                # (3,) unit
    polygon_2d: list[tuple[float, float]]
    polygon_3d: list[tuple[float, float, float]]
    area_m2: float
    tilt_deg: float
    azimuth_deg: float
    confidence: float
    confidence_reasons: dict[str, bool]
    boundary_method: str              # "alpha_shape" | "convex_hull"


def _basis_from_normal(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if abs(normal[2]) > 0.999:
        u = np.array([1.0, 0.0, 0.0])
    else:
        u = np.cross(normal, np.array([0.0, 0.0, 1.0]))
        u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    v /= np.linalg.norm(v)
    return u, v


def _azimuth_from_normal(normal: np.ndarray) -> float:
    """Compass bearing of the normal projected onto XY (0=N=+Y, 90=E=+X)."""
    horiz = normal[:2]
    if np.linalg.norm(horiz) < 1e-6:
        return 0.0
    az = np.degrees(np.arctan2(horiz[0], horiz[1])) % 360.0
    return float(az)


def _alpha_shape_polygon(pts_uv: np.ndarray, alpha: float) -> Optional[sg.Polygon]:
    if not _HAS_ALPHASHAPE or len(pts_uv) < 4:
        return None
    try:
        geom = alphashape.alphashape(pts_uv.tolist(), alpha)
    except Exception:
        return None
    if isinstance(geom, sg.MultiPolygon):
        geom = max(geom.geoms, key=lambda g: g.area)
    if isinstance(geom, sg.Polygon) and geom.is_valid and geom.area > 0:
        return geom
    return None


def _convex_hull_polygon(pts_uv: np.ndarray) -> Optional[sg.Polygon]:
    if len(pts_uv) < 3:
        return None
    try:
        hull = ConvexHull(pts_uv)
    except Exception:
        return None
    poly = sg.Polygon(pts_uv[hull.vertices])
    return poly if poly.is_valid and poly.area > 0 else None


def _build_plane(
    mesh: trimesh.Trimesh,
    face_indices: np.ndarray,
    plane_id: str,
) -> Optional[DetectedPlane]:
    if len(face_indices) < MIN_PLANE_FACES:
        return None

    verts_idx = np.unique(mesh.faces[face_indices].flatten())
    pts = mesh.vertices[verts_idx]
    centroid = pts.mean(axis=0)
    centered = pts - centroid

    cov = (centered.T @ centered) / len(pts)
    eigvals, eigvecs = np.linalg.eigh(cov)
    normal = eigvecs[:, 0]                      # smallest eigenvalue
    if normal[2] < 0:
        normal = -normal
    normal /= np.linalg.norm(normal)

    u_axis, v_axis = _basis_from_normal(normal)
    pts_uv = np.column_stack([centered @ u_axis, centered @ v_axis])

    poly = _alpha_shape_polygon(pts_uv, ALPHA_SHAPE_ALPHA)
    boundary_method = "alpha_shape"
    if poly is None or poly.area < MIN_PLANE_AREA_M2:
        poly = _convex_hull_polygon(pts_uv)
        boundary_method = "convex_hull"
    if poly is None or poly.area < MIN_PLANE_AREA_M2:
        return None

    polygon_2d = [(float(x), float(y)) for x, y in poly.exterior.coords]
    polygon_3d = [
        tuple(centroid + u * u_axis + v * v_axis)
        for (u, v) in polygon_2d
    ]

    tilt_deg = float(np.degrees(np.arccos(np.clip(normal[2], -1.0, 1.0))))
    azimuth_deg = _azimuth_from_normal(normal)
    area_m2 = float(poly.area)

    normal_std = float(np.std(mesh.face_normals[face_indices], axis=0).mean())

    reasons = {
        "area_large_enough": area_m2 >= MIN_PLANE_AREA_M2,
        "normal_stable": normal_std < 0.20,
        "substantive": len(face_indices) >= SUBSTANTIVE_MIN_FACES,
        "polygon_clean": poly.is_valid and len(polygon_2d) >= 4,
    }
    confidence = round(sum(reasons.values()) / len(reasons), 3)

    return DetectedPlane(
        plane_id=plane_id,
        face_indices=face_indices,
        centroid=centroid,
        normal=normal,
        u_axis=u_axis,
        v_axis=v_axis,
        polygon_2d=polygon_2d,
        polygon_3d=[tuple(p) for p in polygon_3d],
        area_m2=area_m2,
        tilt_deg=tilt_deg,
        azimuth_deg=azimuth_deg,
        confidence=confidence,
        confidence_reasons=reasons,
        boundary_method=boundary_method,
    )


def _connected_components(
    mesh: trimesh.Trimesh, face_indices: np.ndarray
) -> list[np.ndarray]:
    if len(face_indices) == 0:
        return []
    n = len(face_indices)
    f_to_local = -np.ones(len(mesh.faces), dtype=np.int64)
    f_to_local[face_indices] = np.arange(n)

    adj = mesh.face_adjacency
    if len(adj) == 0:
        return [face_indices]

    a = f_to_local[adj[:, 0]]
    b = f_to_local[adj[:, 1]]
    edge_mask = (a >= 0) & (b >= 0)
    a, b = a[edge_mask], b[edge_mask]

    if len(a) == 0:
        return [face_indices[i:i + 1] for i in range(n)]

    rows = np.concatenate([a, b])
    cols = np.concatenate([b, a])
    data = np.ones(len(rows), dtype=np.int8)
    graph = csr_matrix((data, (rows, cols)), shape=(n, n))
    n_comp, labels = connected_components(graph, directed=False)
    return [face_indices[labels == c] for c in range(n_comp)]


def cluster_planes(
    mesh: trimesh.Trimesh,
    cand: CandidateResult,
) -> list[DetectedPlane]:
    if not cand.mask.any():
        return []

    cand_face_idx = np.where(cand.mask)[0]
    normals = cand.face_normals[cand_face_idx]
    centroids = cand.face_centroids[cand_face_idx]

    db_n = DBSCAN(eps=NORMAL_EPS, min_samples=NORMAL_MIN_SAMPLES).fit(normals)
    n_labels = db_n.labels_

    detected: list[DetectedPlane] = []
    plane_counter = 0

    for nlbl in sorted(set(n_labels)):
        if nlbl == -1:
            continue
        sel_n = n_labels == nlbl
        sub_face_idx = cand_face_idx[sel_n]
        sub_normals = normals[sel_n]
        sub_centroids = centroids[sel_n]
        n_avg = sub_normals.mean(axis=0)
        n_avg /= np.linalg.norm(n_avg) + 1e-9

        d_vals = sub_centroids @ n_avg
        db_o = DBSCAN(eps=OFFSET_EPS_M, min_samples=OFFSET_MIN_SAMPLES).fit(d_vals.reshape(-1, 1))
        o_labels = db_o.labels_

        for olbl in sorted(set(o_labels)):
            if olbl == -1:
                continue
            sel_o = o_labels == olbl
            sub2_face_idx = sub_face_idx[sel_o]

            for comp_face_idx in _connected_components(mesh, sub2_face_idx):
                if len(comp_face_idx) < MIN_PLANE_FACES:
                    continue
                plane = _build_plane(
                    mesh, comp_face_idx,
                    plane_id=f"roof_{plane_counter}",
                )
                if plane is not None:
                    detected.append(plane)
                    plane_counter += 1

    detected.sort(key=lambda p: -p.area_m2)
    # Re-id after sorting so roof_0 is the largest plane
    for i, p in enumerate(detected):
        p.plane_id = f"roof_{i}"
    return detected
