"""Reusable plane axis math + greedy panel grid placer.

Used in two places:

  * `place_panels_in_rect`  — M3 hand-tuned rectangular roofs (manual_config).
  * `place_panels_in_polygon` — M7 auto pipeline that fills the
    M6 usable polygon (which may have holes from bumps and may be
    non-rectangular from alpha-shape boundary fitting).

The plane axis math (`build_axes`) and the actual rectangle-tiling logic are
shared; the difference is just whether the containment test is a fixed (u, v)
bound check or `shapely.Polygon.contains(rect)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sin
from typing import TYPE_CHECKING

import numpy as np
import shapely.geometry as sg

from roof3d.contract import (
    ConfidenceReasons,
    Obstruction,
    Panel,
    RoofPlane,
)

if TYPE_CHECKING:
    from roof3d.planes import DetectedPlane
    from roof3d.usable import UsableResult


@dataclass(frozen=True)
class ModuleSpec:
    width_m: float = 1.13
    height_m: float = 1.72
    watt_peak: int = 440
    gap_m: float = 0.02
    lift_m: float = 0.03


def build_axes(tilt_deg: float, azimuth_deg: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (u_axis, v_axis, normal) for a roof at the given tilt and azimuth.

    Convention:
        azimuth 0  = +Y (north),  90 = +X (east),  180 = -Y (south),  270 = -X (west)
        tilt 0     = flat (normal = +Z)
        tilt > 0   = roof slopes downward in the azimuth direction
        u_axis     = along the ridge (horizontal)
        v_axis     = up the slope (so +v moves uphill)
    """
    az = radians(azimuth_deg)
    tilt = radians(tilt_deg)
    horiz = np.array([sin(az), cos(az), 0.0])
    normal = np.array([
        horiz[0] * sin(tilt),
        horiz[1] * sin(tilt),
        cos(tilt),
    ])
    u_axis = np.cross(normal, np.array([0.0, 0.0, 1.0]))
    if np.linalg.norm(u_axis) < 1e-6:
        u_axis = np.array([1.0, 0.0, 0.0])
    u_axis /= np.linalg.norm(u_axis)
    v_axis = np.cross(normal, u_axis)
    v_axis /= np.linalg.norm(v_axis)
    return u_axis, v_axis, normal


def place_panels_in_rect(
    plane_id: str,
    centroid: tuple[float, float, float],
    tilt_deg: float,
    azimuth_deg: float,
    width_m: float,
    height_m: float,
    module: ModuleSpec = ModuleSpec(),
    setback_m: float = 0.30,
    source: str = "manual_config",
    confidence: float = 1.0,
) -> tuple[RoofPlane, list[Panel], Obstruction]:
    """Build a RoofPlane + greedy panel grid + safety-margin obstruction.

    The roof rectangle is `width_m` (along u, ridge-aligned) by `height_m` (along
    v, up-slope). Setback shrinks every edge by `setback_m`; panels are tiled in
    the larger orientation (the one yielding more modules)."""
    u_axis, v_axis, normal = build_axes(tilt_deg, azimuth_deg)
    c = np.asarray(centroid, dtype=float)

    hu, hv = width_m / 2.0, height_m / 2.0
    polygon_uv = [(-hu, -hv), (hu, -hv), (hu, hv), (-hu, hv)]
    polygon_3d = [tuple(c + u * u_axis + v * v_axis) for u, v in polygon_uv]

    uu, vv = max(0.0, hu - setback_m), max(0.0, hv - setback_m)
    usable_uv = [(-uu, -vv), (uu, -vv), (uu, vv), (-uu, vv)]
    usable_polygon_3d = [tuple(c + u * u_axis + v * v_axis) for u, v in usable_uv]

    panels = _grid_fill(c, u_axis, v_axis, normal,
                        bounds_uv=(uu, vv), module=module, plane_id=plane_id)

    plane = RoofPlane(
        id=plane_id,
        source=source,
        confidence=confidence,
        confidence_reasons=ConfidenceReasons(
            area_large_enough=True, normal_stable=True,
            height_valid=True, polygon_clean=True,
        ),
        centroid=tuple(c.tolist()),
        normal=tuple(normal.tolist()),
        u_axis=tuple(u_axis.tolist()),
        v_axis=tuple(v_axis.tolist()),
        tilt_deg=tilt_deg,
        azimuth_deg=azimuth_deg,
        area_m2=width_m * height_m,
        usable_area_m2=(2 * uu) * (2 * vv),
        panel_count=len(panels),
        polygon_3d=polygon_3d,
        usable_polygon_3d=usable_polygon_3d,
    )

    obstruction = Obstruction(
        id=f"obs_{plane_id}",
        plane_id=plane_id,
        source="reserve",
        type="safety_margin",
        area_m2=plane.area_m2 - plane.usable_area_m2,
        polygon_3d=[],
    )
    return plane, panels, obstruction


def _grid_fill(
    centroid: np.ndarray,
    u_axis: np.ndarray,
    v_axis: np.ndarray,
    normal: np.ndarray,
    bounds_uv: tuple[float, float],
    module: ModuleSpec,
    plane_id: str,
) -> list[Panel]:
    bu, bv = bounds_uv
    best: list[Panel] = []
    for orient in ("portrait", "landscape"):
        if orient == "portrait":
            pw, ph = module.width_m, module.height_m
        else:
            pw, ph = module.height_m, module.width_m

        cols = int((2 * bu + module.gap_m) // (pw + module.gap_m))
        rows = int((2 * bv + module.gap_m) // (ph + module.gap_m))
        if cols <= 0 or rows <= 0:
            continue
        grid_w = cols * pw + (cols - 1) * module.gap_m
        grid_h = rows * ph + (rows - 1) * module.gap_m
        u0 = -grid_w / 2 + pw / 2
        v0 = -grid_h / 2 + ph / 2

        panels: list[Panel] = []
        for r in range(rows):
            for col in range(cols):
                cu = u0 + col * (pw + module.gap_m)
                cv = v0 + r * (ph + module.gap_m)
                pc = centroid + cu * u_axis + cv * v_axis + module.lift_m * normal
                offsets = [(-pw / 2, -ph / 2), (pw / 2, -ph / 2),
                           (pw / 2, ph / 2), (-pw / 2, ph / 2)]
                corners = [tuple(pc + du * u_axis + dv * v_axis) for du, dv in offsets]
                panels.append(Panel(
                    id=f"{plane_id}_p{r}_{col}",
                    plane_id=plane_id,
                    center=tuple(pc.tolist()),
                    normal=tuple(normal.tolist()),
                    u_axis=tuple(u_axis.tolist()),
                    v_axis=tuple(v_axis.tolist()),
                    width_m=pw,
                    height_m=ph,
                    watt_peak=module.watt_peak,
                    corners_3d=corners,
                ))
        if len(panels) > len(best):
            best = panels
    return best


# ---------------------------------------------------------------------------
# M7 — polygon-aware placement
# ---------------------------------------------------------------------------


def place_panels_in_polygon(
    plane_id: str,
    plane: "DetectedPlane",
    usable: "UsableResult",
    module: ModuleSpec = ModuleSpec(),
    source: str = "auto",
) -> tuple[RoofPlane, list[Panel], list[Obstruction]]:
    """Fill the plane's usable polygon with module rectangles.

    Returns a ready-to-serialise contract.RoofPlane, the list of Panels, and
    obstructions (eaves reserve + any bumps from M6). All coordinates are in
    the original GLB local space (RTC offset NOT applied), with each panel's
    corners lifted by `module.lift_m` along the plane normal to avoid z-fight.
    """
    if usable.usable_polygon is None or usable.usable_polygon.is_empty:
        panels: list[Panel] = []
    else:
        panels = _grid_fill_polygon(plane, usable.usable_polygon, module, plane_id)

    contract_plane = _detected_to_contract_plane(
        plane, usable, panel_count=len(panels),
        plane_id_override=plane_id, source=source,
    )

    obstructions: list[Obstruction] = []
    for b in usable.bumps:
        obstructions.append(Obstruction(
            id=b.bump_id,
            plane_id=plane_id,
            source="detected_bump",
            type="obstruction",
            area_m2=b.area_m2,
            polygon_3d=list(b.polygon_3d),
        ))
    eaves_area = max(0.0,
                     usable.raw_area_m2 - usable.usable_area_m2 - sum(b.area_m2 for b in usable.bumps))
    if eaves_area > 0.5:
        obstructions.append(Obstruction(
            id=f"obs_{plane_id}_eaves",
            plane_id=plane_id,
            source="reserve",
            type="safety_margin",
            area_m2=eaves_area,
            polygon_3d=[],
        ))

    return contract_plane, panels, obstructions


def _grid_fill_polygon(
    plane: "DetectedPlane",
    polygon: sg.Polygon,
    module: ModuleSpec,
    plane_id: str,
) -> list[Panel]:
    minu, minv, maxu, maxv = polygon.bounds
    best: list[Panel] = []

    for orient in ("portrait", "landscape"):
        if orient == "portrait":
            pw, ph = module.width_m, module.height_m
        else:
            pw, ph = module.height_m, module.width_m

        step_u = pw + module.gap_m
        step_v = ph + module.gap_m
        n_cols = max(0, int(((maxu - minu) - module.gap_m) // step_u))
        n_rows = max(0, int(((maxv - minv) - module.gap_m) // step_v))
        if n_cols == 0 or n_rows == 0:
            continue

        grid_w = n_cols * pw + (n_cols - 1) * module.gap_m
        grid_h = n_rows * ph + (n_rows - 1) * module.gap_m
        u0 = minu + ((maxu - minu) - grid_w) / 2.0 + pw / 2.0
        v0 = minv + ((maxv - minv) - grid_h) / 2.0 + ph / 2.0

        candidates: list[Panel] = []
        for r in range(n_rows):
            for c in range(n_cols):
                cu = u0 + c * step_u
                cv = v0 + r * step_v
                corners_uv = [
                    (cu - pw / 2, cv - ph / 2),
                    (cu + pw / 2, cv - ph / 2),
                    (cu + pw / 2, cv + ph / 2),
                    (cu - pw / 2, cv + ph / 2),
                ]
                rect = sg.Polygon(corners_uv)
                if not polygon.contains(rect):
                    continue
                pc = (plane.centroid
                      + cu * plane.u_axis + cv * plane.v_axis
                      + module.lift_m * plane.normal)
                corners_3d = [
                    tuple((plane.centroid
                           + u * plane.u_axis + v * plane.v_axis
                           + module.lift_m * plane.normal).tolist())
                    for (u, v) in corners_uv
                ]
                candidates.append(Panel(
                    id=f"{plane_id}_p{r}_{c}",
                    plane_id=plane_id,
                    center=tuple(pc.tolist()),
                    normal=tuple(plane.normal.tolist()),
                    u_axis=tuple(plane.u_axis.tolist()),
                    v_axis=tuple(plane.v_axis.tolist()),
                    width_m=pw,
                    height_m=ph,
                    watt_peak=module.watt_peak,
                    corners_3d=corners_3d,
                ))
        if len(candidates) > len(best):
            best = candidates
    return best


def _detected_to_contract_plane(
    plane: "DetectedPlane",
    usable: "UsableResult",
    panel_count: int,
    plane_id_override: str,
    source: str,
) -> RoofPlane:
    reasons = plane.confidence_reasons
    return RoofPlane(
        id=plane_id_override,
        source=source,
        confidence=plane.confidence,
        confidence_reasons=ConfidenceReasons(
            area_large_enough=bool(reasons.get("area_large_enough", True)),
            normal_stable=bool(reasons.get("normal_stable", True)),
            # The contract field "height_valid" carries M5's "substantive" signal
            # (plane has enough faces to be a real roof rather than a fragment).
            # M4's local-cell-max filter already enforces actual height.
            height_valid=bool(reasons.get("substantive", True)),
            polygon_clean=bool(reasons.get("polygon_clean", True)),
        ),
        centroid=tuple(plane.centroid.tolist()),
        normal=tuple(plane.normal.tolist()),
        u_axis=tuple(plane.u_axis.tolist()),
        v_axis=tuple(plane.v_axis.tolist()),
        tilt_deg=plane.tilt_deg,
        azimuth_deg=plane.azimuth_deg,
        area_m2=plane.area_m2,
        usable_area_m2=usable.usable_area_m2,
        panel_count=panel_count,
        polygon_3d=[tuple(p) for p in usable.raw_polygon_3d],
        usable_polygon_3d=[tuple(p) for p in usable.usable_polygon_3d],
    )
