"""Shared M6 + M7 + summary assembly.

Extracted from `scripts/emit_auto.py` so the same logic is reused by both:

  - the offline pipeline (`emit_auto.emit` / `build_all.py`), and
  - the live ROI endpoint (`/api/projects/{id}/design`, M11), where M4 + M5
    have already been run on a candidate-masked mesh.

The helper is intentionally narrow: it takes a list of `DetectedPlane` (already
filtered/gated by the caller) and produces the per-plane contract objects plus
a `Summary`/`Quality` pair. The caller wraps everything in a `RoofDesign`
together with the project-specific fields (project_id, model_file, bbox,
coordinate_system).
"""
from __future__ import annotations

from dataclasses import dataclass

import trimesh

from roof3d.contract import (
    Obstruction,
    Panel,
    Quality,
    RoofPlane,
    Summary,
)
from roof3d.placement import ModuleSpec, place_panels_in_polygon
from roof3d.planes import DetectedPlane
from roof3d.usable import compute_usable


@dataclass
class AssembledDesign:
    roof_planes: list[RoofPlane]
    panels: list[Panel]
    obstructions: list[Obstruction]
    summary: Summary
    quality: Quality


def planes_to_contract(
    mesh: trimesh.Trimesh,
    detected: list[DetectedPlane],
    *,
    module: ModuleSpec = ModuleSpec(),
    max_planes: int = 12,
    min_usable_area_m2: float = 4.0,
    detect_bumps: bool = True,
    extra_warnings: list[str] | None = None,
    method: str = "auto_normal_cluster",
    panel_source: str = "auto",
) -> AssembledDesign:
    """Run M6 + M7 over the given DetectedPlane list and assemble Summary/Quality.

    Plane IDs are renumbered `roof_0`, `roof_1`, ... in placement order — the
    caller is responsible for sorting/filtering `detected` first if it cares
    about a specific ordering (M5 already sorts by area descending; M10's gate
    re-sorts by roof_score).
    """
    contract_planes: list[RoofPlane] = []
    contract_panels: list[Panel] = []
    contract_obstructions: list[Obstruction] = []
    placed = 0
    for p in detected:
        if placed >= max_planes:
            break
        u = compute_usable(mesh, p, detect_bumps=detect_bumps)
        if u.usable_area_m2 < min_usable_area_m2:
            continue
        plane_id = f"roof_{placed}"
        cp, panels, obs = place_panels_in_polygon(
            plane_id, p, u, module=module, source=panel_source,
        )
        contract_planes.append(cp)
        contract_panels.extend(panels)
        contract_obstructions.extend(obs)
        placed += 1

    panels_by_plane = {cp.id: 0 for cp in contract_planes}
    for pn in contract_panels:
        panels_by_plane[pn.plane_id] = panels_by_plane.get(pn.plane_id, 0) + 1

    best = max(
        contract_planes, key=lambda cp: panels_by_plane.get(cp.id, 0),
    ) if contract_planes else None
    avg_conf = round(
        sum(cp.confidence for cp in contract_planes) / max(1, len(contract_planes)), 3,
    ) if contract_planes else 0.0

    warnings: list[str] = list(extra_warnings or [])
    if not contract_planes:
        warnings.append("no roof planes detected")

    summary = Summary(
        panel_count=len(contract_panels),
        module_wp=module.watt_peak,
        system_kwp=round(len(contract_panels) * module.watt_peak / 1000.0, 3),
        best_plane_id=best.id if best else None,
        best_plane_azimuth=best.azimuth_deg if best else None,
        best_plane_tilt=best.tilt_deg if best else None,
        panels_by_plane=panels_by_plane,
        method=method,
        confidence=avg_conf,
        warnings=warnings,
    )
    quality = Quality(method=method, confidence=avg_conf)

    return AssembledDesign(
        roof_planes=contract_planes,
        panels=contract_panels,
        obstructions=contract_obstructions,
        summary=summary,
        quality=quality,
    )
