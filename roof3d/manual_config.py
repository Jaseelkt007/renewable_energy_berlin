"""M3 — hand-tuned roof configurations per GLB.

This is the demo's insurance policy. Even if the auto-detection pipeline (M4-M7)
fails on demo day, every GLB has a working roof JSON because we wrote it down here.

Centroids were derived once by running the rooftop-cluster probe (see M3 commit log)
which finds the densest XY cell of vertices above the 92nd Z percentile. After that
they are frozen literals and may be tweaked by eyeballing the overlay PNGs.

Each plane definition is the minimal set needed to construct a RoofPlane:
    centroid: (x, y, z) in original GLB local coords (RTC offset NOT applied)
    tilt_deg: 0 = flat, 30-40 typical residential
    azimuth_deg: 0=N, 90=E, 180=S, 270=W
    width_m, height_m: roof rectangle along ridge (u) and slope (v)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlaneDef:
    centroid: tuple[float, float, float]
    tilt_deg: float
    azimuth_deg: float
    width_m: float
    height_m: float


@dataclass(frozen=True)
class GLBConfig:
    glb_file: str
    project_id: str
    planes: tuple[PlaneDef, ...]


# Project IDs — only Hamburg matches a real status_quo project from the dataset
# (297be54c5e7e4aad). The others are demo placeholders for now; revise once the
# team agrees the project<->GLB mapping (M9).
MANUAL_CONFIGS: dict[str, GLBConfig] = {
    "3D_Modell Hamburg.glb": GLBConfig(
        glb_file="3D_Modell Hamburg.glb",
        project_id="297be54c5e7e4aad",
        planes=(
            PlaneDef(centroid=(-1.6, -17.3, 49.8), tilt_deg=30.0,
                     azimuth_deg=180.0, width_m=8.0, height_m=6.0),
        ),
    ),
    "3D_Modell Brandenburg.glb": GLBConfig(
        glb_file="3D_Modell Brandenburg.glb",
        project_id="98b53eaa68c0eeeb",
        planes=(
            PlaneDef(centroid=(39.2, -26.0, 73.5), tilt_deg=30.0,
                     azimuth_deg=180.0, width_m=10.0, height_m=7.0),
        ),
    ),
    "3D_Modell North Germany.glb": GLBConfig(
        glb_file="3D_Modell North Germany.glb",
        project_id="demo_north",
        planes=(
            PlaneDef(centroid=(-25.5, -13.2, 62.1), tilt_deg=30.0,
                     azimuth_deg=180.0, width_m=9.0, height_m=6.5),
        ),
    ),
    "3D_Modell Ruhr.glb": GLBConfig(
        glb_file="3D_Modell Ruhr.glb",
        project_id="demo_ruhr",
        planes=(
            PlaneDef(centroid=(-13.4, 1.8, 192.9), tilt_deg=30.0,
                     azimuth_deg=180.0, width_m=8.0, height_m=6.0),
        ),
    ),
}
