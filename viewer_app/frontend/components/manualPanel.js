/**
 * M12 — build a candidate panel rectangle on a detected plane from a 3D click.
 *
 * Inputs:
 *   plane     — RoofPlane object from the canonical JSON (centroid, u_axis,
 *               v_axis, normal as 3-tuples).
 *   hitPoint  — [x, y, z] in GLB local space (the picked point on the GLB).
 *   template  — optional existing panel on the same plane; if present, its
 *               u/v dimensions are reused so manual panels match the AI grid's
 *               orientation. Otherwise falls back to ModuleSpec defaults.
 *
 * Output (matches the contract Panel shape so it slots straight into the
 * panels array used by PanelOverlay/SummaryPanel):
 *   { id?, plane_id, center, normal, u_axis, v_axis, width_m, height_m,
 *     watt_peak, corners_3d }
 */

const DEFAULT_WIDTH_M = 1.13;
const DEFAULT_HEIGHT_M = 1.72;
const DEFAULT_WATT_PEAK = 440;
const PANEL_LIFT_M = 0.03;

function add(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function scale(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }
function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

function projectPointOntoPlane(point, plane) {
  // Project point onto the plane defined by plane.centroid + plane.normal.
  const d = sub(point, plane.centroid);
  const n = plane.normal;
  const along = dot(d, n);
  return sub(point, scale(n, along));
}

export function buildCandidatePanel({ plane, hitPoint, template, idPrefix = "manual" }) {
  if (!plane?.centroid || !plane?.u_axis || !plane?.v_axis || !plane?.normal) {
    return null;
  }
  const widthM = template?.width_m || DEFAULT_WIDTH_M;
  const heightM = template?.height_m || DEFAULT_HEIGHT_M;
  const wattPeak = template?.watt_peak || DEFAULT_WATT_PEAK;

  const center = projectPointOntoPlane(hitPoint, plane);
  // Lift the rectangle slightly off the roof plane along the normal so it
  // doesn't z-fight with the GLB or with auto panels.
  const liftedCenter = add(center, scale(plane.normal, PANEL_LIFT_M));

  const halfU = scale(plane.u_axis, widthM / 2);
  const halfV = scale(plane.v_axis, heightM / 2);

  const c0 = sub(sub(liftedCenter, halfU), halfV);
  const c1 = sub(add(liftedCenter, halfU), halfV);
  const c2 = add(add(liftedCenter, halfU), halfV);
  const c3 = add(sub(liftedCenter, halfU), halfV);

  const id = `${idPrefix}_${plane.id}_${Date.now().toString(36)}_${Math.floor(Math.random() * 1e4).toString(36)}`;

  return {
    id,
    plane_id: plane.id,
    center: liftedCenter,
    normal: plane.normal,
    u_axis: plane.u_axis,
    v_axis: plane.v_axis,
    width_m: widthM,
    height_m: heightM,
    watt_peak: wattPeak,
    corners_3d: [c0, c1, c2, c3],
    _source: "manual",
  };
}
