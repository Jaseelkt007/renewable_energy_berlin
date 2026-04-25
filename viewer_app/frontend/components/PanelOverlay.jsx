"use client";

import { useMemo } from "react";
import * as THREE from "three";

/**
 * Render every panel in a SINGLE batched BufferGeometry — one draw call total
 * regardless of panel count. With Brandenburg's 573 panels this matters.
 *
 * Each panel's `corners_3d` is [c0, c1, c2, c3] (in plane (u,v) corners,
 * already lifted +3 cm along the plane normal in the backend pipeline).
 * We emit two triangles per panel: (c0, c1, c2) and (c0, c2, c3).
 */
export default function PanelOverlay({ panels = [], color = "#1e1e44" }) {
  const geometry = useMemo(() => {
    const valid = panels.filter(
      (p) => Array.isArray(p?.corners_3d) && p.corners_3d.length === 4
    );
    if (valid.length === 0) return null;

    const positions = new Float32Array(valid.length * 18); // 6 verts * 3
    valid.forEach((p, i) => {
      const c = p.corners_3d;
      const off = i * 18;
      // tri 1: c0, c1, c2
      positions[off + 0] = c[0][0]; positions[off + 1] = c[0][1]; positions[off + 2] = c[0][2];
      positions[off + 3] = c[1][0]; positions[off + 4] = c[1][1]; positions[off + 5] = c[1][2];
      positions[off + 6] = c[2][0]; positions[off + 7] = c[2][1]; positions[off + 8] = c[2][2];
      // tri 2: c0, c2, c3
      positions[off + 9]  = c[0][0]; positions[off + 10] = c[0][1]; positions[off + 11] = c[0][2];
      positions[off + 12] = c[2][0]; positions[off + 13] = c[2][1]; positions[off + 14] = c[2][2];
      positions[off + 15] = c[3][0]; positions[off + 16] = c[3][1]; positions[off + 17] = c[3][2];
    });

    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    g.computeVertexNormals();
    return g;
  }, [panels]);

  if (!geometry) return null;

  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial
        color={color}
        side={THREE.DoubleSide}
        metalness={0.45}
        roughness={0.55}
      />
    </mesh>
  );
}
