"""M6 — usable area: eaves erosion + optional obstruction (bump) detection.

For each detected RoofPlane the recommendation engine needs to know how much of
the roof is actually safe to install panels on. Two effects shrink the raw
polygon:

1. **Eaves clearance.** A uniform inward buffer (default 30 cm) keeps panels
   off the very edge — fire-code, walking room, mounting tolerances. This is
   always applied.

2. **Bumps / obstructions.** Optional. We look at the vertices of the plane's
   own face cluster, measure each one's signed distance to the fitted plane,
   and treat anything more than `bump_distance_m` (default 15 cm) above the
   plane as a candidate obstruction. Those candidate points are projected into
   the plane's (u, v) basis, DBSCAN-clustered, hulled, buffered (default
   30 cm), and subtracted from the eaves-eroded polygon.

The returned `UsableResult` has both 2D (plane coords) and 3D (original GLB
local coords) polygon forms ready for the JSON contract and frontend rendering.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import shapely.geometry as sg
import trimesh
from scipy.spatial import ConvexHull
from shapely.ops import unary_union
from sklearn.cluster import DBSCAN

from roof3d.planes import DetectedPlane


# Tuning knobs
EAVES_SETBACK_M = 0.30
BUMP_DISTANCE_M = 0.15
BUMP_CLUSTER_EPS_M = 0.40
BUMP_MIN_SAMPLES = 4
BUMP_BUFFER_M = 0.30
BUMP_MIN_AREA_M2 = 0.05      # discard tiny clusters that are likely noise


@dataclass
class Bump:
    bump_id: str
    polygon_2d: list[tuple[float, float]]
    polygon_3d: list[tuple[float, float, float]]
    area_m2: float
    n_points: int


@dataclass
class UsableResult:
    plane_id: str
    raw_polygon_2d: list[tuple[float, float]]
    raw_polygon_3d: list[tuple[float, float, float]]
    usable_polygon_2d: list[tuple[float, float]]
    usable_polygon_3d: list[tuple[float, float, float]]
    raw_area_m2: float
    usable_area_m2: float
    eaves_setback_m: float
    bumps: list[Bump] = field(default_factory=list)
    bump_detection: bool = False
    # Live shapely polygons — kept alongside coord lists so M7 (panel
    # placement) can call `.contains(rect)` without re-parsing.
    raw_polygon: sg.Polygon | None = None
    usable_polygon: sg.Polygon | None = None


def _to_3d(poly_2d_coords, centroid: np.ndarray, u_axis: np.ndarray, v_axis: np.ndarray):
    return [
        tuple((centroid + u * u_axis + v * v_axis).tolist())
        for (u, v) in poly_2d_coords
    ]


def _largest(poly):
    if poly.is_empty:
        return poly
    if isinstance(poly, sg.MultiPolygon):
        return max(poly.geoms, key=lambda g: g.area)
    return poly


def compute_usable(
    mesh: trimesh.Trimesh,
    plane: DetectedPlane,
    *,
    eaves_setback_m: float = EAVES_SETBACK_M,
    detect_bumps: bool = True,
    bump_distance_m: float = BUMP_DISTANCE_M,
    bump_cluster_eps_m: float = BUMP_CLUSTER_EPS_M,
    bump_buffer_m: float = BUMP_BUFFER_M,
) -> UsableResult:
    raw_polygon = sg.Polygon(plane.polygon_2d)
    raw_area = float(raw_polygon.area)

    eaves = _largest(raw_polygon.buffer(-eaves_setback_m))
    bumps_detected: list[Bump] = []

    if detect_bumps and not eaves.is_empty:
        bumps_detected = _detect_bumps(
            mesh, plane,
            distance_threshold=bump_distance_m,
            cluster_eps=bump_cluster_eps_m,
            buffer_m=bump_buffer_m,
        )
        if bumps_detected:
            bump_polys = [sg.Polygon(b.polygon_2d) for b in bumps_detected]
            bump_union = unary_union(bump_polys) if len(bump_polys) > 1 else bump_polys[0]
            usable = _largest(eaves.difference(bump_union))
            if usable.is_empty:
                # too aggressive — fall back to eaves only
                usable = eaves
        else:
            usable = eaves
    else:
        usable = eaves

    if usable.is_empty:
        usable_polygon_2d: list[tuple[float, float]] = []
        usable_polygon_3d: list[tuple[float, float, float]] = []
        usable_area = 0.0
    else:
        usable_polygon_2d = [(float(x), float(y)) for x, y in usable.exterior.coords]
        usable_polygon_3d = _to_3d(usable_polygon_2d, plane.centroid, plane.u_axis, plane.v_axis)
        usable_area = float(usable.area)

    return UsableResult(
        plane_id=plane.plane_id,
        raw_polygon_2d=plane.polygon_2d,
        raw_polygon_3d=plane.polygon_3d,
        usable_polygon_2d=usable_polygon_2d,
        usable_polygon_3d=usable_polygon_3d,
        raw_area_m2=raw_area,
        usable_area_m2=usable_area,
        eaves_setback_m=eaves_setback_m,
        bumps=bumps_detected,
        bump_detection=detect_bumps,
        raw_polygon=raw_polygon,
        usable_polygon=None if usable.is_empty else usable,
    )


def _detect_bumps(
    mesh: trimesh.Trimesh,
    plane: DetectedPlane,
    *,
    distance_threshold: float,
    cluster_eps: float,
    buffer_m: float,
) -> list[Bump]:
    if len(plane.face_indices) == 0:
        return []

    verts_idx = np.unique(mesh.faces[plane.face_indices].flatten())
    pts_world = mesh.vertices[verts_idx]
    centered = pts_world - plane.centroid
    signed_dist = centered @ plane.normal
    above = signed_dist > distance_threshold
    if int(above.sum()) < BUMP_MIN_SAMPLES:
        return []

    pts_uv = np.column_stack([
        centered[above] @ plane.u_axis,
        centered[above] @ plane.v_axis,
    ])

    labels = DBSCAN(eps=cluster_eps, min_samples=BUMP_MIN_SAMPLES).fit(pts_uv).labels_

    bumps: list[Bump] = []
    for lbl in sorted(set(labels)):
        if lbl == -1:
            continue
        cluster_pts = pts_uv[labels == lbl]
        n = len(cluster_pts)
        poly_2d = _hull_or_buffer(cluster_pts, buffer_m)
        poly_2d = _largest(poly_2d)
        if poly_2d.is_empty or poly_2d.area < BUMP_MIN_AREA_M2:
            continue
        coords_2d = [(float(x), float(y)) for x, y in poly_2d.exterior.coords]
        coords_3d = _to_3d(coords_2d, plane.centroid, plane.u_axis, plane.v_axis)
        bumps.append(Bump(
            bump_id=f"{plane.plane_id}_bump_{lbl}",
            polygon_2d=coords_2d,
            polygon_3d=coords_3d,
            area_m2=float(poly_2d.area),
            n_points=int(n),
        ))
    return bumps


def _hull_or_buffer(pts_uv: np.ndarray, buffer_m: float) -> sg.Polygon:
    if len(pts_uv) < 3:
        ctr = pts_uv.mean(axis=0)
        return sg.Point(float(ctr[0]), float(ctr[1])).buffer(buffer_m)
    try:
        hull = ConvexHull(pts_uv)
    except Exception:
        ctr = pts_uv.mean(axis=0)
        return sg.Point(float(ctr[0]), float(ctr[1])).buffer(buffer_m)
    return sg.Polygon(pts_uv[hull.vertices]).buffer(buffer_m)
