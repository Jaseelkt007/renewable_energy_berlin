"""M7 — full auto pipeline: load -> M4 candidates -> M5 planes -> M6 usable -> M7 panels.

Writes out/<project_id>.auto.roof.json (kept separate from the M3 manual file
out/<project_id>.roof.json so they can be visually compared).

Usage:
    python scripts/emit_auto.py                            # all manual_config GLBs
    python scripts/emit_auto.py "3D_Modell Hamburg.glb"
    python scripts/emit_auto.py --max-planes 8 --min-usable 6
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roof3d.candidates import select_roof_candidates
from roof3d.contract import (
    BBox,
    CoordinateSystem,
    Quality,
    RoofDesign,
    Summary,
)
from roof3d.loader import load_glb
from roof3d.manual_config import MANUAL_CONFIGS, GLBConfig
from roof3d.placement import ModuleSpec, place_panels_in_polygon
from roof3d.planes import cluster_planes
from roof3d.usable import compute_usable

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)


def emit(
    cfg: GLBConfig,
    *,
    module: ModuleSpec = ModuleSpec(),
    max_planes: int = 12,
    min_usable_area_m2: float = 4.0,
    detect_bumps: bool = True,
) -> Path:
    glb_path = ROOT / cfg.glb_file
    g = load_glb(glb_path)
    bbox_min = g.mesh.vertices.min(axis=0)
    bbox_max = g.mesh.vertices.max(axis=0)

    cand = select_roof_candidates(g.mesh)
    detected = cluster_planes(g.mesh, cand)

    contract_planes = []
    contract_panels = []
    contract_obstructions = []
    placed = 0
    for p in detected:
        if placed >= max_planes:
            break
        u = compute_usable(g.mesh, p, detect_bumps=detect_bumps)
        if u.usable_area_m2 < min_usable_area_m2:
            continue
        plane_id = f"roof_{placed}"
        cp, panels, obs = place_panels_in_polygon(plane_id, p, u, module=module, source="auto")
        contract_planes.append(cp)
        contract_panels.extend(panels)
        contract_obstructions.extend(obs)
        placed += 1

    panels_by_plane = {cp.id: 0 for cp in contract_planes}
    for pn in contract_panels:
        panels_by_plane[pn.plane_id] = panels_by_plane.get(pn.plane_id, 0) + 1

    best = max(contract_planes, key=lambda cp: panels_by_plane.get(cp.id, 0)) \
        if contract_planes else None
    avg_conf = round(sum(cp.confidence for cp in contract_planes)
                     / max(1, len(contract_planes)), 3) if contract_planes else 0.0

    summary = Summary(
        panel_count=len(contract_panels),
        module_wp=module.watt_peak,
        system_kwp=round(len(contract_panels) * module.watt_peak / 1000.0, 3),
        best_plane_id=best.id if best else None,
        best_plane_azimuth=best.azimuth_deg if best else None,
        best_plane_tilt=best.tilt_deg if best else None,
        panels_by_plane=panels_by_plane,
        method="auto_normal_cluster",
        confidence=avg_conf,
        warnings=[] if contract_planes else ["no roof planes detected"],
    )

    design = RoofDesign(
        project_id=cfg.project_id,
        model_file=cfg.glb_file,
        coordinate_system=CoordinateSystem(),
        bbox=BBox(min=tuple(bbox_min.tolist()), max=tuple(bbox_max.tolist())),
        roof_planes=contract_planes,
        obstructions=contract_obstructions,
        panels=contract_panels,
        summary=summary,
        quality=Quality(method="auto_normal_cluster", confidence=avg_conf),
    )

    out_path = OUT / f"{cfg.project_id}.auto.roof.json"
    out_path.write_text(design.to_json())

    parsed = RoofDesign.from_json(out_path.read_text())
    assert len(parsed.panels) == len(contract_panels)

    print(f"  {cfg.glb_file:32s} -> {out_path.name:38s}  "
          f"{len(contract_planes):>2} planes, {len(contract_panels):>3} panels, "
          f"{summary.system_kwp:>6.2f} kWp, avg conf {avg_conf}")
    return out_path


def parse_args(argv: list[str]) -> tuple[list[str], dict]:
    keys: list[str] = []
    opts: dict = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--max-planes":
            opts["max_planes"] = int(argv[i + 1]); i += 2
        elif a == "--min-usable":
            opts["min_usable_area_m2"] = float(argv[i + 1]); i += 2
        elif a == "--no-bumps":
            opts["detect_bumps"] = False; i += 1
        else:
            keys.append(a); i += 1
    return keys, opts


def main() -> None:
    keys, opts = parse_args(sys.argv[1:])
    if not keys:
        keys = list(MANUAL_CONFIGS.keys())
    for k in keys:
        cfg = MANUAL_CONFIGS.get(k)
        if cfg is None:
            print(f"no manual config for {k!r}", file=sys.stderr)
            continue
        emit(cfg, **opts)


if __name__ == "__main__":
    main()
