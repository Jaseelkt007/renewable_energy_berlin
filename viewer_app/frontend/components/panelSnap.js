/**
 * M12.1 — hover-snap candidate panel computation.
 *
 * Pure functions, no React. Called every hover frame in add-mode.
 *
 * Algorithm:
 *   1. Project the cursor's 3D hit point into the plane's (u, v) basis.
 *   2. If no panels exist on this plane yet → free placement at cursor.
 *   3. Otherwise pick the nearest existing panel as the snap anchor and
 *      snap the candidate to that panel's grid: integer multiples of
 *      (width + gap, height + gap) offset from the anchor's center.
 *   4. Validate locally:
 *        a. Each rect corner must lie inside the usable polygon (or the
 *           raw plane polygon if no usable polygon is present).
 *        b. The rect must not overlap any existing panel on the same
 *           plane (AABB test in (u, v) — exact because panels share basis).
 *
 * Output: { candidate, valid, reason } | null. `candidate` always carries
 * a contract-shaped Panel (corners_3d in GLB world space, lifted +3 cm
 * along the plane normal); the caller decides whether to commit it.
 */

import { pointInPolygon2D, projectToPlaneUV } from "./planeLookup";

const DEFAULT_WIDTH_M = 1.13;
const DEFAULT_HEIGHT_M = 1.72;
const DEFAULT_WATT_PEAK = 440;
const PANEL_GAP_M = 0.02;     // matches roof3d ModuleSpec.gap_m
const PANEL_LIFT_M = 0.03;    // matches roof3d ModuleSpec.lift_m
const OVERLAP_EPS = 1e-3;     // 1 mm slack so touching edges count as "fits"
// 2 cm safety inset on the *raw* plane polygon. We deliberately don't use
// the AI's `usable_polygon_3d` for manual edits — that polygon is inset by
// the placement setback (~30 cm) so the auto greedy doesn't run off the
// eaves; for a manual override the user is the authority and the visible
// roof up to the actual mesh boundary is fair game. We still keep 2 cm of
// slack against photogrammetry noise at the polygon edge.
const CONTAIN_EPS = 0.02;

function add(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function scale(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }

/**
 * Build the 4 corners (3D, GLB space) of a candidate panel from a center
 * already in 3D and the plane's u/v basis. Lift along the plane normal so
 * the rect doesn't z-fight with the GLB or with the auto panel layer.
 */
function buildCorners3D(center3d, plane, widthM, heightM) {
  const lifted = add(center3d, scale(plane.normal, PANEL_LIFT_M));
  const halfU = scale(plane.u_axis, widthM / 2);
  const halfV = scale(plane.v_axis, heightM / 2);
  return {
    center: lifted,
    corners_3d: [
      sub(sub(lifted, halfU), halfV),
      sub(add(lifted, halfU), halfV),
      add(add(lifted, halfU), halfV),
      add(sub(lifted, halfU), halfV),
    ],
  };
}

/**
 * Convert a (u, v) center back to a 3D point in GLB space:
 *   point3d = centroid + u * u_axis + v * v_axis
 */
function uvToWorld(uv, plane) {
  return [
    plane.centroid[0] + uv[0] * plane.u_axis[0] + uv[1] * plane.v_axis[0],
    plane.centroid[1] + uv[0] * plane.u_axis[1] + uv[1] * plane.v_axis[1],
    plane.centroid[2] + uv[0] * plane.u_axis[2] + uv[1] * plane.v_axis[2],
  ];
}

/**
 * Candidate vs. existing-panel overlap test in (u, v).
 * All panels on a plane share the same axes, so this is just AABB.
 */
function rectsOverlap(centerA, wA, hA, centerB, wB, hB) {
  const du = Math.abs(centerA[0] - centerB[0]);
  const dv = Math.abs(centerA[1] - centerB[1]);
  return (
    du < (wA + wB) / 2 - OVERLAP_EPS &&
    dv < (hA + hB) / 2 - OVERLAP_EPS
  );
}

/**
 * All four corners + the center must lie inside the usable polygon.
 * Edge intersection is implicitly enforced by checking the corners — for
 * convex usable polygons this is exact; for concave ones we accept a tiny
 * risk that an edge crosses a notch with no corner inside, which is
 * extremely rare at panel scale (1.7 m × 1.1 m vs. typical roof notches).
 */
function rectInsidePolygon(centerUV, halfU, halfV, polyUV) {
  const cs = [
    [centerUV[0] - halfU, centerUV[1] - halfV],
    [centerUV[0] + halfU, centerUV[1] - halfV],
    [centerUV[0] + halfU, centerUV[1] + halfV],
    [centerUV[0] - halfU, centerUV[1] + halfV],
    centerUV,
  ];
  for (const c of cs) {
    if (!pointInPolygon2D(c, polyUV)) return false;
  }
  return true;
}

/**
 * Pick the nearest existing same-plane panel as the snap anchor.
 * Distance is in (u, v); panels on different planes were already filtered.
 */
function nearestAnchor(cursorUV, panelsUV) {
  let best = null;
  let bestD2 = Infinity;
  for (const p of panelsUV) {
    const du = p.centerUV[0] - cursorUV[0];
    const dv = p.centerUV[1] - cursorUV[1];
    const d2 = du * du + dv * dv;
    if (d2 < bestD2) {
      bestD2 = d2;
      best = p;
    }
  }
  return best;
}

/**
 * Compute a snap-or-free candidate for the given hit point on the given
 * plane. `samePlanePanels` are the contract-shaped Panel objects whose
 * `plane_id` matches `plane.id` (caller filters).
 *
 * Returns null if the plane is missing required basis info. Otherwise
 * returns { candidate, valid, reason }; `candidate` is always present
 * (we render even invalid candidates, in red, so the user sees why).
 */
export function computeSnapCandidate({ hitPoint, plane, samePlanePanels = [] }) {
  if (!plane?.centroid || !plane?.u_axis || !plane?.v_axis || !plane?.normal) {
    return null;
  }

  // Default dimensions: take the first existing panel as a template if we
  // have one. Otherwise fall back to the ModuleSpec defaults. Width and
  // height are stored on the panel, so this also matches the AI's portrait
  // vs. landscape orientation per plane.
  const tmpl = samePlanePanels[0];
  const widthM = tmpl?.width_m || DEFAULT_WIDTH_M;
  const heightM = tmpl?.height_m || DEFAULT_HEIGHT_M;
  const wattPeak = tmpl?.watt_peak || DEFAULT_WATT_PEAK;

  // Project cursor and existing-panel centers into (u, v).
  const cursorUV = projectToPlaneUV(hitPoint, plane);
  const panelsUV = samePlanePanels
    .filter((p) => Array.isArray(p?.center) && p.center.length === 3)
    .map((p) => ({
      id: p.id,
      centerUV: projectToPlaneUV(p.center, plane),
      width_m: p.width_m || widthM,
      height_m: p.height_m || heightM,
    }));

  let snappedUV;
  let usedAnchor = false;
  if (panelsUV.length === 0) {
    // No anchor → free placement at cursor.
    snappedUV = cursorUV;
  } else {
    const anchor = nearestAnchor(cursorUV, panelsUV);
    const stepU = anchor.width_m + PANEL_GAP_M;
    const stepV = anchor.height_m + PANEL_GAP_M;
    const i = Math.round((cursorUV[0] - anchor.centerUV[0]) / stepU);
    const j = Math.round((cursorUV[1] - anchor.centerUV[1]) / stepV);
    snappedUV = [
      anchor.centerUV[0] + i * stepU,
      anchor.centerUV[1] + j * stepV,
    ];
    usedAnchor = true;
    // i==0 && j==0 means the snap landed *on* the anchor — the user is
    // hovering close enough that the only sensible answer is "you're
    // pointing at the anchor itself". Skip free fallback; the overlap
    // check below will mark this invalid, which matches user intent.
  }

  // Build candidate corners in 3D.
  const center3d = uvToWorld(snappedUV, plane);
  const { center, corners_3d } = buildCorners3D(center3d, plane, widthM, heightM);

  // Validate locally.
  let valid = true;
  let reason = "ok";

  // Containment: prefer the *raw* plane polygon for manual edits — see
  // CONTAIN_EPS comment above. Fall back to the usable polygon only if the
  // plane was emitted without a raw polygon (shouldn't happen for AI planes).
  const polyPts = plane.polygon_3d || plane.usable_polygon_3d || [];
  if (polyPts.length >= 3) {
    const polyUV = polyPts.map((pt) => projectToPlaneUV(pt, plane));
    // Inflate the polygon test by CONTAIN_EPS via a uniform shrink of the
    // candidate rect — cheaper than buffering a polygon by 1 cm.
    const halfU = widthM / 2 - CONTAIN_EPS;
    const halfV = heightM / 2 - CONTAIN_EPS;
    if (!rectInsidePolygon(snappedUV, halfU, halfV, polyUV)) {
      valid = false;
      reason = "outside usable area";
    }
  }

  // Overlap check (only if still valid — first failure reason wins).
  if (valid) {
    for (const p of panelsUV) {
      if (rectsOverlap(snappedUV, widthM, heightM, p.centerUV, p.width_m, p.height_m)) {
        valid = false;
        reason = "overlaps existing panel";
        break;
      }
    }
  }

  // Same id format as before — manual_<plane>_<time36>_<rand36>.
  const id = `manual_${plane.id}_${Date.now().toString(36)}_${Math.floor(Math.random() * 1e4).toString(36)}`;
  const candidate = {
    id,
    plane_id: plane.id,
    center,
    normal: plane.normal,
    u_axis: plane.u_axis,
    v_axis: plane.v_axis,
    width_m: widthM,
    height_m: heightM,
    watt_peak: wattPeak,
    corners_3d,
    _source: "manual",
    _snapped: usedAnchor,
  };
  return { candidate, valid, reason };
}

/**
 * Step size on a plane for arrow-key nudging in move-mode.
 * One module width/height + the inter-module gap = exactly one grid cell.
 */
export function stepSizesForPlane(panel) {
  const widthM = panel?.width_m || DEFAULT_WIDTH_M;
  const heightM = panel?.height_m || DEFAULT_HEIGHT_M;
  return {
    stepU: widthM + PANEL_GAP_M,
    stepV: heightM + PANEL_GAP_M,
  };
}

/**
 * Build a moved copy of `panel` whose center is shifted by (du, dv) in the
 * plane's (u, v) basis. Width/height/dimensions are preserved; corners are
 * recomputed in 3D. The id is preserved so React state diffs cleanly.
 */
export function shiftPanelOnPlane({ panel, plane, du, dv }) {
  const halfU = scale(plane.u_axis, panel.width_m / 2);
  const halfV = scale(plane.v_axis, panel.height_m / 2);
  const newCenter = add(
    panel.center,
    add(scale(plane.u_axis, du), scale(plane.v_axis, dv)),
  );
  return {
    ...panel,
    center: newCenter,
    corners_3d: [
      sub(sub(newCenter, halfU), halfV),
      sub(add(newCenter, halfU), halfV),
      add(add(newCenter, halfU), halfV),
      add(sub(newCenter, halfU), halfV),
    ],
  };
}

/**
 * Validate a panel at its current center on `plane` against the raw plane
 * polygon and a list of other-panel centers (excluding itself, by id).
 * Mirrors the checks in `computeSnapCandidate` so move and add agree.
 */
export function validatePanelOnPlane({ panel, plane, otherPanels }) {
  const polyPts = plane.polygon_3d || plane.usable_polygon_3d || [];
  if (polyPts.length < 3) return { valid: false, reason: "plane has no polygon" };

  const centerUV = projectToPlaneUV(panel.center, plane);
  const polyUV = polyPts.map((pt) => projectToPlaneUV(pt, plane));
  const halfU = panel.width_m / 2 - CONTAIN_EPS;
  const halfV = panel.height_m / 2 - CONTAIN_EPS;
  if (!rectInsidePolygon(centerUV, halfU, halfV, polyUV)) {
    return { valid: false, reason: "outside usable area" };
  }

  for (const other of otherPanels || []) {
    if (other.id === panel.id) continue;
    if (!Array.isArray(other?.center) || other.center.length !== 3) continue;
    const otherUV = projectToPlaneUV(other.center, plane);
    if (rectsOverlap(
      centerUV, panel.width_m, panel.height_m,
      otherUV, other.width_m, other.height_m,
    )) {
      return { valid: false, reason: "overlaps existing panel" };
    }
  }
  return { valid: true, reason: "ok" };
}
