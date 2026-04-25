"""M3 — emit a manual-config RoofDesign for one or all GLBs.

Usage:
    python scripts/emit_manual.py                       # all 4 GLBs
    python scripts/emit_manual.py "3D_Modell Hamburg.glb"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roof3d.contract import (
    BBox,
    CoordinateSystem,
    Quality,
    RoofDesign,
    Summary,
)
from roof3d.loader import load_glb
from roof3d.manual_config import MANUAL_CONFIGS, GLBConfig
from roof3d.placement import ModuleSpec, place_panels_in_rect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)


def emit(cfg: GLBConfig, module: ModuleSpec = ModuleSpec()) -> Path:
    glb_path = ROOT / cfg.glb_file
    g = load_glb(glb_path)
    bbox_min = g.mesh.vertices.min(axis=0)
    bbox_max = g.mesh.vertices.max(axis=0)

    planes, panels, obstructions = [], [], []
    for i, pdef in enumerate(cfg.planes):
        plane, ps, obs = place_panels_in_rect(
            plane_id=f"roof_{i}",
            centroid=pdef.centroid,
            tilt_deg=pdef.tilt_deg,
            azimuth_deg=pdef.azimuth_deg,
            width_m=pdef.width_m,
            height_m=pdef.height_m,
            module=module,
            source="manual_config",
            confidence=1.0,
        )
        planes.append(plane)
        panels.extend(ps)
        obstructions.append(obs)

    panels_by_plane = {p.id: 0 for p in planes}
    for panel in panels:
        panels_by_plane[panel.plane_id] += 1

    best = max(planes, key=lambda p: panels_by_plane[p.id])
    summary = Summary(
        panel_count=len(panels),
        module_wp=module.watt_peak,
        system_kwp=round(len(panels) * module.watt_peak / 1000.0, 3),
        best_plane_id=best.id,
        best_plane_azimuth=best.azimuth_deg,
        best_plane_tilt=best.tilt_deg,
        panels_by_plane=panels_by_plane,
        method="manual_config",
        confidence=1.0,
        warnings=["hand-tuned roof — used as M3 fallback / demo insurance"],
    )

    design = RoofDesign(
        project_id=cfg.project_id,
        model_file=cfg.glb_file,
        coordinate_system=CoordinateSystem(),
        bbox=BBox(min=tuple(bbox_min.tolist()), max=tuple(bbox_max.tolist())),
        roof_planes=planes,
        obstructions=obstructions,
        panels=panels,
        summary=summary,
        quality=Quality(method="manual_config", confidence=1.0),
    )

    out_path = OUT / f"{cfg.project_id}.roof.json"
    out_path.write_text(design.to_json())
    parsed = RoofDesign.from_json(out_path.read_text())
    assert len(parsed.panels) == len(panels)
    print(f"  {cfg.glb_file:32s} -> {out_path.name}  "
          f"({len(panels)} panels, {summary.system_kwp} kWp)")
    return out_path


def main() -> None:
    if len(sys.argv) > 1:
        keys = [sys.argv[1]]
    else:
        keys = list(MANUAL_CONFIGS.keys())

    for k in keys:
        if k not in MANUAL_CONFIGS:
            print(f"no manual config for {k!r}", file=sys.stderr)
            continue
        emit(MANUAL_CONFIGS[k])


if __name__ == "__main__":
    main()
