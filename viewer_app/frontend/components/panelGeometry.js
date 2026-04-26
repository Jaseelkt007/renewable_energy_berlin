/**
 * Build a batched BufferGeometry for the four side faces + the bottom face
 * of a set of panels, given their top corners (`corners_3d`) and a uniform
 * extrusion `thickness` (meters) along the panel's plane normal.
 *
 * The "top" face is rendered separately by the textured layer; this geometry
 * exists only to give the user a sense of physical thickness from oblique
 * angles. We render it with a flat dark material (mimicking the silver-grey
 * frame edges and the dark backsheet) so we don't need a second texture.
 *
 * Verts per panel: 5 quads (4 sides + bottom) × 6 verts = 30 verts → 90
 * floats. We use `THREE.DoubleSide` on the consumer material so winding is
 * irrelevant.
 */
import * as THREE from "three";

function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
function scale(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }

export function buildSidesGeometry(panels, thicknessM) {
  if (!Array.isArray(panels) || panels.length === 0 || thicknessM <= 0) return null;

  const valid = panels.filter(
    (p) =>
      Array.isArray(p?.corners_3d) &&
      p.corners_3d.length === 4 &&
      Array.isArray(p?.normal) &&
      p.normal.length === 3,
  );
  if (valid.length === 0) return null;

  // 5 quads × 2 tris × 3 verts × 3 floats = 90 floats per panel.
  const positions = new Float32Array(valid.length * 90);

  let off = 0;
  const writeTri = (a, b, c) => {
    positions[off++] = a[0]; positions[off++] = a[1]; positions[off++] = a[2];
    positions[off++] = b[0]; positions[off++] = b[1]; positions[off++] = b[2];
    positions[off++] = c[0]; positions[off++] = c[1]; positions[off++] = c[2];
  };

  for (const p of valid) {
    const c = p.corners_3d;
    // Drop along the *negative* normal to extrude downward into the roof —
    // the textured top sits at the lifted (+3 cm) position; thickness now
    // gives panels visible depth below that top face.
    const drop = scale(p.normal, -thicknessM);
    const b0 = [c[0][0] + drop[0], c[0][1] + drop[1], c[0][2] + drop[2]];
    const b1 = [c[1][0] + drop[0], c[1][1] + drop[1], c[1][2] + drop[2]];
    const b2 = [c[2][0] + drop[0], c[2][1] + drop[1], c[2][2] + drop[2]];
    const b3 = [c[3][0] + drop[0], c[3][1] + drop[1], c[3][2] + drop[2]];

    // Side c0-c1
    writeTri(c[0], b0, b1);
    writeTri(c[0], b1, c[1]);
    // Side c1-c2
    writeTri(c[1], b1, b2);
    writeTri(c[1], b2, c[2]);
    // Side c2-c3
    writeTri(c[2], b2, b3);
    writeTri(c[2], b3, c[3]);
    // Side c3-c0
    writeTri(c[3], b3, b0);
    writeTri(c[3], b0, c[0]);
    // Bottom face (b0,b1,b2,b3) — winding doesn't matter (DoubleSide).
    writeTri(b0, b2, b1);
    writeTri(b0, b3, b2);
  }

  // Defensive: we sized for `valid.length`; `off` should equal the array.
  // Trim if any panel was filtered between sizing and writing (it can't,
  // but the assertion costs nothing).
  const used = positions.subarray(0, off);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(used, 3));
  g.computeVertexNormals();
  return g;
}
