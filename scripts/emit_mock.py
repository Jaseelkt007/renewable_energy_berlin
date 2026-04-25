"""M2 — emit a mock roof design for one GLB before any geometry detection exists.

This proves the full pipeline (loader -> contract -> JSON -> renderer) end-to-end.
We hand-author a south-tilted (30 deg) roof plane sitting on top of the Hamburg tile
and lay out a 4 x 3 grid of 1.13 x 1.72 m, 440 W modules.

All coordinates are in the GLB's original local coordinate space (RTC offset NOT
applied), so the frontend can use them as-is when loading the GLB.

Usage: python scripts/emit_mock.py
Output: out/mock_hamburg.roof.json
"""
from __future__ import annotations

import json
import sys
from math import cos, radians, sin
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roof3d.contract import (
    BBox,
    ConfidenceReasons,
    CoordinateSystem,
    Obstruction,
    Panel,
    Quality,
    RoofDesign,
    RoofPlane,
    Summary,
)
from roof3d.loader import load_glb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)

GLB = ROOT / "3D_Modell Hamburg.glb"
PROJECT_ID = "297be54c5e7e4aad"

# Roof geometry (hand-authored, sitting near the top of the Hamburg tile)
TILT_DEG = 30.0
AZIMUTH_DEG = 180.0       # pointing south
ROOF_WIDTH_M = 8.0        # along u (east-west, perpendicular to slope)
ROOF_HEIGHT_M = 6.0       # along v (slope direction)

# Module spec
PANEL_W = 1.13
PANEL_H = 1.72
PANEL_WP = 440
GRID_COLS = 4
GRID_ROWS = 3
GAP = 0.02
PANEL_LIFT = 0.03         # avoid z-fighting with mesh


def build_axes() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build (u_axis, v_axis, normal) for a south-tilted roof.

    Convention: azimuth 180 deg = facing south, normal projected on XY points -Y.
    Tilt 0 deg = flat (normal = +Z); tilt 30 deg = roof sloping down toward south.
    """
    az = radians(AZIMUTH_DEG)
    tilt = radians(TILT_DEG)
    # Compass: az=0 -> +Y (north), az=90 -> +X (east), az=180 -> -Y (south).
    # The roof normal projects onto the horizontal plane in this direction.
    horiz_normal = np.array([sin(az), cos(az), 0.0])
    normal = np.array([
        horiz_normal[0] * sin(tilt),
        horiz_normal[1] * sin(tilt),
        cos(tilt),
    ])
    # u_axis = along ridge (horizontal, east-west when az=180)
    u_axis = np.cross(normal, np.array([0.0, 0.0, 1.0]))
    if np.linalg.norm(u_axis) < 1e-6:
        u_axis = np.array([1.0, 0.0, 0.0])
    u_axis /= np.linalg.norm(u_axis)
    # v_axis = up the slope
    v_axis = np.cross(normal, u_axis)
    v_axis /= np.linalg.norm(v_axis)
    return u_axis, v_axis, normal


def main() -> None:
    g = load_glb(GLB)
    mesh = g.mesh
    bbox_min = mesh.vertices.min(axis=0)
    bbox_max = mesh.vertices.max(axis=0)
    print(f"loaded {GLB.name}: bbox {bbox_min} -> {bbox_max}")

    u_axis, v_axis, normal = build_axes()

    # Place roof center near the (0,0) horizontal center of the tile, at top Z
    centroid = np.array([0.0, 0.0, float(bbox_max[2]) - 0.5])
    print(f"roof centroid: {centroid}, normal: {normal}, tilt {TILT_DEG} deg, az {AZIMUTH_DEG} deg")

    # Roof rectangle corners in plane (u,v) coords, then lifted to 3D
    hu, hv = ROOF_WIDTH_M / 2, ROOF_HEIGHT_M / 2
    corners_uv = [(-hu, -hv), (hu, -hv), (hu, hv), (-hu, hv)]
    polygon_3d = [tuple(centroid + u * u_axis + v * v_axis) for u, v in corners_uv]

    # Usable polygon = 30 cm eaves erosion
    setback = 0.30
    usable_uv = [(-hu + setback, -hv + setback), (hu - setback, -hv + setback),
                 (hu - setback, hv - setback), (-hu + setback, hv - setback)]
    usable_polygon_3d = [tuple(centroid + u * u_axis + v * v_axis) for u, v in usable_uv]

    # Panel grid centered on the roof (in u,v space)
    grid_w = GRID_COLS * PANEL_W + (GRID_COLS - 1) * GAP
    grid_h = GRID_ROWS * PANEL_H + (GRID_ROWS - 1) * GAP
    u0 = -grid_w / 2 + PANEL_W / 2
    v0 = -grid_h / 2 + PANEL_H / 2

    panels: list[Panel] = []
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            cu = u0 + c * (PANEL_W + GAP)
            cv = v0 + r * (PANEL_H + GAP)
            panel_center = centroid + cu * u_axis + cv * v_axis + PANEL_LIFT * normal
            corner_offsets = [
                (-PANEL_W / 2, -PANEL_H / 2),
                ( PANEL_W / 2, -PANEL_H / 2),
                ( PANEL_W / 2,  PANEL_H / 2),
                (-PANEL_W / 2,  PANEL_H / 2),
            ]
            corners = [tuple(panel_center + du * u_axis + dv * v_axis) for du, dv in corner_offsets]
            panels.append(Panel(
                id=f"panel_{r}_{c}",
                plane_id="roof_0",
                center=tuple(panel_center),
                normal=tuple(normal),
                u_axis=tuple(u_axis),
                v_axis=tuple(v_axis),
                width_m=PANEL_W,
                height_m=PANEL_H,
                watt_peak=PANEL_WP,
                corners_3d=corners,
            ))

    plane = RoofPlane(
        id="roof_0",
        source="manual_config",
        confidence=1.0,
        confidence_reasons=ConfidenceReasons(
            area_large_enough=True, normal_stable=True,
            height_valid=True, polygon_clean=True,
        ),
        centroid=tuple(centroid),
        normal=tuple(normal),
        u_axis=tuple(u_axis),
        v_axis=tuple(v_axis),
        tilt_deg=TILT_DEG,
        azimuth_deg=AZIMUTH_DEG,
        area_m2=ROOF_WIDTH_M * ROOF_HEIGHT_M,
        usable_area_m2=(ROOF_WIDTH_M - 2 * setback) * (ROOF_HEIGHT_M - 2 * setback),
        panel_count=len(panels),
        polygon_3d=polygon_3d,
        usable_polygon_3d=usable_polygon_3d,
    )

    obstruction = Obstruction(
        id="obs_0",
        plane_id="roof_0",
        source="reserve",
        type="safety_margin",
        area_m2=plane.area_m2 - plane.usable_area_m2,
        polygon_3d=[],  # ring polygon left empty for mock
    )

    summary = Summary(
        panel_count=len(panels),
        module_wp=PANEL_WP,
        system_kwp=round(len(panels) * PANEL_WP / 1000.0, 3),
        best_plane_id="roof_0",
        best_plane_azimuth=AZIMUTH_DEG,
        best_plane_tilt=TILT_DEG,
        panels_by_plane={"roof_0": len(panels)},
        method="mock_hand_authored",
        confidence=1.0,
        warnings=["mock data — do not use for real recommendations"],
    )

    design = RoofDesign(
        project_id=PROJECT_ID,
        model_file=GLB.name,
        coordinate_system=CoordinateSystem(
            units="meters", up_axis="Z",
            panels_in_original_model_coordinates=True,
            unit_scale_applied=1.0,
        ),
        bbox=BBox(min=tuple(bbox_min.tolist()), max=tuple(bbox_max.tolist())),
        roof_planes=[plane],
        obstructions=[obstruction],
        panels=panels,
        summary=summary,
        quality=Quality(method="mock", confidence=1.0, warnings=[]),
    )

    out_path = OUT / "mock_hamburg.roof.json"
    out_path.write_text(design.to_json())

    # Round-trip validation
    parsed = RoofDesign.from_json(out_path.read_text())
    assert len(parsed.panels) == GRID_ROWS * GRID_COLS
    assert parsed.summary.system_kwp == round(GRID_ROWS * GRID_COLS * PANEL_WP / 1000, 3)

    print(f"\nwrote {out_path}")
    print(f"  panels: {len(parsed.panels)}")
    print(f"  kWp:    {parsed.summary.system_kwp}")
    print(f"  schema: {parsed.schema_version}")


if __name__ == "__main__":
    main()
