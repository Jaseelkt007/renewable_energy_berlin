"""Reusable plane axis math + greedy panel grid placer.

Used by emit_manual.py (M3) for hand-tuned rectangular roofs and reused later by
the auto pipeline (M5-M7), which will pass detected polygons instead of fixed
rectangles. The math up to `build_axes` is identical in both paths.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sin

import numpy as np

from roof3d.contract import (
    ConfidenceReasons,
    Obstruction,
    Panel,
    RoofPlane,
)


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
