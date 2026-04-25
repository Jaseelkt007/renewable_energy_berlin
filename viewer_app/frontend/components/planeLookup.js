/**
 * Client-side helpers for finding which detected roof plane a 3D point lies on.
 *
 * Used by hover inspection: each plane stores `centroid`, `u_axis`, `v_axis`,
 * `normal` (all in the same coord space as the panel coords) and a polygon of
 * 3D points. We project the hit point into the plane's (u, v) basis and run a
 * 2D point-in-polygon test against `polygon_3d` likewise projected.
 *
 * Shapely lives server-side; this is a tiny re-implementation so hover stays
 * snappy and doesn't round-trip to the backend on every pointer move.
 */

function dot3(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function sub3(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function projectToPlaneUV(point3d, plane) {
  const d = sub3(point3d, plane.centroid);
  return [dot3(d, plane.u_axis), dot3(d, plane.v_axis)];
}

function pointInPolygon2D(uv, polyUV) {
  // Crossing-number test.
  let inside = false;
  for (let i = 0, j = polyUV.length - 1; i < polyUV.length; j = i++) {
    const [xi, yi] = polyUV[i];
    const [xj, yj] = polyUV[j];
    const intersects =
      (yi > uv[1]) !== (yj > uv[1]) &&
      uv[0] < ((xj - xi) * (uv[1] - yi)) / (yj - yi + 1e-12) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

/**
 * Find the plane whose surface the 3D `hit` point lies on.
 *
 * Returns the plane object (with index annotated) or null. We pick the plane
 * whose absolute signed-distance is smallest among those whose polygon
 * contains the (u, v) projection of the hit point.
 */
export function findPlaneAtPoint(hit, planes) {
  if (!Array.isArray(planes) || planes.length === 0) return null;
  let best = null;
  let bestSigned = Infinity;
  for (let i = 0; i < planes.length; i++) {
    const p = planes[i];
    if (!p?.polygon_3d || p.polygon_3d.length < 3) continue;
    if (!p.centroid || !p.u_axis || !p.v_axis || !p.normal) continue;
    const polyUV = p.polygon_3d.map((pt) => projectToPlaneUV(pt, p));
    const hitUV = projectToPlaneUV(hit, p);
    if (!pointInPolygon2D(hitUV, polyUV)) continue;
    const signed = Math.abs(dot3(sub3(hit, p.centroid), p.normal));
    if (signed < bestSigned) {
      bestSigned = signed;
      best = { ...p, _index: i, _signed_distance: signed };
    }
  }
  return best;
}
