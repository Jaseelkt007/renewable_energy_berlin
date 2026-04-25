"use client";

import { Line } from "@react-three/drei";

/**
 * Render an array of polygon point lists as closed line loops.
 *
 * Each entry in `polygons` is an array of [x, y, z] triplets; if the loop is
 * not already closed (first == last), we close it by repeating the first point.
 * Empty / too-short polygons are skipped so missing JSON fields don't crash.
 */
export default function PolygonOverlay({ polygons = [], color = "white", lineWidth = 2, opacity = 0.9 }) {
  return (
    <group>
      {polygons.map((pts, i) => {
        if (!Array.isArray(pts) || pts.length < 2) return null;
        const closed = samePoint(pts[0], pts[pts.length - 1]) ? pts : [...pts, pts[0]];
        // drei <Line> accepts either Vector3[] or [x,y,z][] — pass arrays directly.
        return (
          <Line
            key={i}
            points={closed}
            color={color}
            lineWidth={lineWidth}
            transparent
            opacity={opacity}
            depthTest
          />
        );
      })}
    </group>
  );
}

function samePoint(a, b) {
  return Array.isArray(a) && Array.isArray(b)
    && a[0] === b[0] && a[1] === b[1] && a[2] === b[2];
}
