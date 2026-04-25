"""M10 — roof quality gate + single-building selection.

Inserted between M5 (cluster_planes) and M6/M7 (usable + panel placement).

Two filters in sequence:

1. Universal quality / ground rejection.
   - height_above_local_ground >= MIN_HEIGHT (rejects floors, courtyards,
     road slabs, vegetation patches that happen to be planar and elevated
     relative to a 3 m XY cell but not relative to true local terrain).
   - area_m2 / tilt / confidence sanity checks.
2. Project-specific selection.
   - tile_wide        : pass everything (debug / fallback).
   - roi_circle       : keep planes whose XY centroid is within radius_m of
                        center_xy.
   - selected_plane_ids: keep planes whose id is in the whitelist.

The roof_score is computed for accepted planes (used for ranking after the
gate). It is NOT written to the JSON contract — instead, gate decisions are
summarised into summary.warnings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import trimesh

from roof3d.planes import DetectedPlane


# Default tuning knobs
MIN_HEIGHT_ABOVE_GROUND_M = 2.5
GROUND_RADIUS_M = 25.0      # wide enough to clear large building footprints
GROUND_PERCENTILE = 5.0
MIN_AREA_M2 = 8.0
MIN_USABLE_AREA_M2 = 4.0
MAX_TILT_DEG = 60.0
MIN_CONFIDENCE = 0.7


@dataclass
class GateParams:
    min_height_above_ground_m: float = MIN_HEIGHT_ABOVE_GROUND_M
    ground_radius_m: float = GROUND_RADIUS_M
    ground_percentile: float = GROUND_PERCENTILE
    min_area_m2: float = MIN_AREA_M2
    min_usable_area_m2: float = MIN_USABLE_AREA_M2
    max_tilt_deg: float = MAX_TILT_DEG
    min_confidence: float = MIN_CONFIDENCE


@dataclass
class Selection:
    """Project-specific building selector.

    mode == "tile_wide"          → no spatial filter (debug default).
    mode == "roi_circle"         → keep planes inside (center_xy, radius_m).
    mode == "selected_plane_ids" → keep planes whose id is in the whitelist.
    """
    mode: str = "tile_wide"
    center_xy: Optional[tuple[float, float]] = None
    radius_m: Optional[float] = None
    selected_plane_ids: Optional[tuple[str, ...]] = None

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Selection":
        if not d:
            return cls(mode="tile_wide")
        mode = d.get("mode", "tile_wide")
        center = d.get("center_xy")
        if center is not None:
            center = (float(center[0]), float(center[1]))
        radius = d.get("radius_m")
        ids = d.get("selected_plane_ids")
        if ids is not None:
            ids = tuple(ids)
        return cls(mode=mode, center_xy=center,
                   radius_m=float(radius) if radius is not None else None,
                   selected_plane_ids=ids)


@dataclass
class GateDecision:
    plane_id: str
    accepted: bool
    roof_score: float
    height_above_ground_m: float
    reasons: dict[str, bool] = field(default_factory=dict)


def local_ground_z(
    mesh: trimesh.Trimesh,
    centroid_xy: tuple[float, float],
    radius_m: float,
    percentile: float = GROUND_PERCENTILE,
) -> float:
    """Estimate local ground elevation as a low percentile of vertex Z within
    `radius_m` of `centroid_xy`. Falls back to the 5th percentile of all mesh
    vertices if the local neighbourhood is empty."""
    verts = mesh.vertices
    dx = verts[:, 0] - centroid_xy[0]
    dy = verts[:, 1] - centroid_xy[1]
    inside = (dx * dx + dy * dy) <= (radius_m * radius_m)
    if not inside.any():
        return float(np.percentile(verts[:, 2], percentile))
    return float(np.percentile(verts[inside, 2], percentile))


def _in_roi(plane: DetectedPlane, sel: Selection) -> bool:
    if sel.mode == "tile_wide":
        return True
    if sel.mode == "roi_circle":
        if sel.center_xy is None or sel.radius_m is None:
            return True
        cx, cy = sel.center_xy
        dx = plane.centroid[0] - cx
        dy = plane.centroid[1] - cy
        return float(dx * dx + dy * dy) <= float(sel.radius_m * sel.radius_m)
    if sel.mode == "selected_plane_ids":
        if not sel.selected_plane_ids:
            return False
        return plane.plane_id in sel.selected_plane_ids
    # unknown mode → fail safe and pass
    return True


def roof_score(
    plane: DetectedPlane,
    *,
    height_above_ground_m: float,
    usable_area_m2: float,
    in_roi_flag: bool,
) -> float:
    """Bounded [0,1]-ish score combining height, area, planarity, and ROI.

    Used for *ranking* accepted planes; not part of the JSON contract."""
    h_term = float(np.clip(height_above_ground_m / 6.0, 0.0, 1.0))
    a_term = float(np.clip(usable_area_m2 / 80.0, 0.0, 1.0))
    c_term = float(plane.confidence)
    roi_term = 1.0 if in_roi_flag else 0.0
    # Weights tuned by intent: ground-clearance and usable area dominate.
    return round(0.35 * h_term + 0.30 * a_term + 0.20 * c_term + 0.15 * roi_term, 3)


def apply_quality_gate(
    mesh: trimesh.Trimesh,
    planes: list[DetectedPlane],
    *,
    params: GateParams = GateParams(),
    selection: Selection = Selection(),
    usable_areas: Optional[dict[str, float]] = None,
) -> tuple[list[DetectedPlane], list[GateDecision]]:
    """Filter `planes` and rank survivors by roof_score (descending).

    `usable_areas` is optional — if M6 has already been run, pass {plane_id:
    usable_area_m2} so the gate can apply min_usable_area_m2. Otherwise that
    check is skipped (the gate runs before M6 in our default pipeline)."""
    decisions: list[GateDecision] = []
    accepted_with_score: list[tuple[float, DetectedPlane]] = []

    for p in planes:
        ground_z = local_ground_z(
            mesh,
            (float(p.centroid[0]), float(p.centroid[1])),
            params.ground_radius_m,
            params.ground_percentile,
        )
        h = float(p.centroid[2] - ground_z)

        in_roi_flag = _in_roi(p, selection)
        usable = usable_areas.get(p.plane_id) if usable_areas else None

        reasons = {
            "height_above_ground": h >= params.min_height_above_ground_m,
            "area_large_enough": p.area_m2 >= params.min_area_m2,
            "tilt_in_range": 0.0 <= p.tilt_deg <= params.max_tilt_deg,
            "confidence_ok": p.confidence >= params.min_confidence,
            "in_selection": in_roi_flag,
        }
        if usable is not None:
            reasons["usable_area_ok"] = usable >= params.min_usable_area_m2

        accepted = all(reasons.values())
        score = roof_score(
            p,
            height_above_ground_m=h,
            usable_area_m2=usable if usable is not None else p.area_m2,
            in_roi_flag=in_roi_flag,
        )
        decisions.append(GateDecision(
            plane_id=p.plane_id,
            accepted=accepted,
            roof_score=score,
            height_above_ground_m=h,
            reasons=reasons,
        ))
        if accepted:
            accepted_with_score.append((score, p))

    accepted_with_score.sort(key=lambda t: -t[0])
    return [p for _, p in accepted_with_score], decisions


def summarise_decisions(
    decisions: list[GateDecision],
    selection: Selection,
) -> list[str]:
    """Build human-readable warnings for summary.warnings."""
    n_total = len(decisions)
    n_accepted = sum(1 for d in decisions if d.accepted)
    msgs: list[str] = []

    if selection.mode == "tile_wide":
        msgs.append("Tile-wide result: multiple buildings may be included.")
    elif selection.mode == "roi_circle":
        cx, cy = selection.center_xy or (0.0, 0.0)
        r = selection.radius_m or 0.0
        msgs.append(
            f"Selected-building result: {n_accepted} of {n_total} planes accepted "
            f"(ROI circle r={r:.1f}m at ({cx:.1f}, {cy:.1f}))."
        )
    elif selection.mode == "selected_plane_ids":
        msgs.append(
            f"Selected-building result: {n_accepted} of {n_total} planes accepted "
            f"(whitelist of {len(selection.selected_plane_ids or ())} ids)."
        )

    # Per-criterion reject counts
    counts: dict[str, int] = {}
    for d in decisions:
        if d.accepted:
            continue
        for k, v in d.reasons.items():
            if not v:
                counts[k] = counts.get(k, 0) + 1
    if counts:
        msgs.append("Gate rejects: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return msgs
