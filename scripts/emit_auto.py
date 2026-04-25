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

from roof3d.assemble import planes_to_contract
from roof3d.candidates import select_roof_candidates
from roof3d.contract import (
    BBox,
    CoordinateSystem,
    RoofDesign,
)
from roof3d.loader import load_glb
from roof3d.manual_config import MANUAL_CONFIGS, GLBConfig
from roof3d.placement import ModuleSpec
from roof3d.planes import cluster_planes
from roof3d.quality import GateParams, Selection, apply_quality_gate, summarise_decisions

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
    selection: Selection | None = None,
    gate_params: GateParams | None = None,
    out_suffix: str = ".auto.roof.json",
) -> Path:
    glb_path = ROOT / cfg.glb_file
    g = load_glb(glb_path)
    bbox_min = g.mesh.vertices.min(axis=0)
    bbox_max = g.mesh.vertices.max(axis=0)

    cand = select_roof_candidates(g.mesh)
    detected = cluster_planes(g.mesh, cand)

    # M10 — quality gate + project selection. With default Selection (tile_wide)
    # and default GateParams the gate still strips ground/floor/courtyard
    # surfaces; project-specific narrowing kicks in when a Selection is passed.
    sel = selection or Selection(mode="tile_wide")
    params = gate_params or GateParams()
    n_pre_gate = len(detected)
    if sel.mode == "tile_wide":
        # Debug / pre-M10 behaviour: skip the gate so canonical *.tile.roof.json
        # outputs match what M9 produced. Build_all uses this only for the
        # *.tile.roof.json debug file.
        gate_warnings = ["Tile-wide result: multiple buildings may be included."]
    else:
        detected, gate_decisions = apply_quality_gate(
            g.mesh, detected, params=params, selection=sel,
        )
        gate_warnings = summarise_decisions(gate_decisions, sel)

    assembled = planes_to_contract(
        g.mesh, detected,
        module=module,
        max_planes=max_planes,
        min_usable_area_m2=min_usable_area_m2,
        detect_bumps=detect_bumps,
        extra_warnings=gate_warnings,
        method="auto_normal_cluster",
        panel_source="auto",
    )

    design = RoofDesign(
        project_id=cfg.project_id,
        model_file=cfg.glb_file,
        coordinate_system=CoordinateSystem(),
        bbox=BBox(min=tuple(bbox_min.tolist()), max=tuple(bbox_max.tolist())),
        roof_planes=assembled.roof_planes,
        obstructions=assembled.obstructions,
        panels=assembled.panels,
        summary=assembled.summary,
        quality=assembled.quality,
    )

    out_path = OUT / f"{cfg.project_id}{out_suffix}"
    out_path.write_text(design.to_json())

    parsed = RoofDesign.from_json(out_path.read_text())
    assert len(parsed.panels) == len(assembled.panels)

    print(f"  {cfg.glb_file:32s} -> {out_path.name:42s}  "
          f"{n_pre_gate}->{len(assembled.roof_planes)} planes, {len(assembled.panels):>3} panels, "
          f"{assembled.summary.system_kwp:>6.2f} kWp, mode={sel.mode}")
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
