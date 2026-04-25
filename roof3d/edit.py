"""M12 — geometry validation for manually-placed panels.

The frontend sends a candidate panel (4 corners in 3D, plane id, plane basis,
and the existing panels' centers/half-extents on that plane). This module
projects everything into the plane's (u, v) frame and runs three checks:

    1. The candidate rectangle lies entirely inside the usable polygon.
    2. The candidate does not overlap any existing panel on the same plane.
    3. (3) is implicitly enforced because the usable polygon already excludes
       obstructions detected upstream — we don't re-check them here.

The function is stateless; the frontend supplies the relevant plane polygon
(in 3D, plus the plane's axes) so the backend doesn't need to load the GLB.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import shapely.geometry as sg


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str


def _project_uv(pt, centroid, u_axis, v_axis):
    d = np.asarray(pt, dtype=float) - np.asarray(centroid, dtype=float)
    return (float(np.dot(d, u_axis)), float(np.dot(d, v_axis)))


def validate_panel_placement(
    *,
    plane_centroid,
    plane_u_axis,
    plane_v_axis,
    usable_polygon_3d,
    candidate_corners_3d,
    existing_panels_corners_3d,
) -> ValidationResult:
    """Return ok=True if the candidate fits inside the supplied plane polygon
    and does not overlap any existing panel on this plane.

    The `usable_polygon_3d` parameter is named for the wire format; the
    frontend sends whichever polygon it wants enforced. For M12 manual edits
    that's the *raw* plane polygon (so the user can place panels in the
    placement-setback margin the AI greedy reserves). For other callers it
    can still be the inset polygon.
    """
    centroid = np.asarray(plane_centroid, dtype=float)
    u = np.asarray(plane_u_axis, dtype=float)
    v = np.asarray(plane_v_axis, dtype=float)

    if len(candidate_corners_3d) != 4:
        return ValidationResult(False, "candidate must have exactly 4 corners")
    if not usable_polygon_3d or len(usable_polygon_3d) < 3:
        return ValidationResult(False, "plane has no usable polygon")

    poly_uv = [_project_uv(p, centroid, u, v) for p in usable_polygon_3d]
    usable = sg.Polygon(poly_uv)
    if not usable.is_valid:
        usable = usable.buffer(0)

    cand_uv = [_project_uv(p, centroid, u, v) for p in candidate_corners_3d]
    cand = sg.Polygon(cand_uv)
    if not cand.is_valid or cand.area < 1e-6:
        return ValidationResult(False, "candidate panel is degenerate")

    # 2 cm tolerance to match the frontend snap module — accommodates
    # photogrammetry noise at the polygon boundary without letting panels
    # drift far past the visible roof edge.
    if not usable.buffer(0.02).contains(cand):
        return ValidationResult(False, "outside usable area")

    for existing in existing_panels_corners_3d or []:
        if not existing or len(existing) != 4:
            continue
        ex_uv = [_project_uv(p, centroid, u, v) for p in existing]
        ex = sg.Polygon(ex_uv)
        if not ex.is_valid or ex.area < 1e-6:
            continue
        # Touching edges are fine; only flag real overlap.
        if cand.intersection(ex).area > 1e-4:
            return ValidationResult(False, "overlaps existing panel")

    return ValidationResult(True, "ok")
