"use client";

import { useMemo } from "react";
import * as THREE from "three";

/**
 * Renders the small set of panels that need a per-panel color: manual
 * (green), candidate-while-validating (yellow), and rejected-flash (red).
 *
 * Each panel is its own mesh (rather than one batched BufferGeometry) so the
 * color can vary per-panel and so the click-to-remove path can attach
 * `userData.panelId` on the mesh — `event.object.userData.panelId` is then a
 * direct lookup. We never render more than ~50 of these at once in practice
 * (manual edits are interactive), so the cost of N draw calls is negligible.
 */
const COLORS = {
  manual: "#16a34a",
  candidate: "#facc15",
  "candidate-valid": "#facc15",
  "candidate-invalid": "#dc2626",
  rejected: "#dc2626",
  selected: "#22d3ee",
};

const TRANSPARENT_SOURCES = new Set([
  "candidate",
  "candidate-valid",
  "candidate-invalid",
  "rejected",
]);

export default function ManualPanelOverlay({
  panels = [],
  source = "manual",
  onPanelClick,
  clickable = false,
}) {
  const meshes = useMemo(() => {
    return panels
      .filter((p) => Array.isArray(p?.corners_3d) && p.corners_3d.length === 4)
      .map((p) => {
        const c = p.corners_3d;
        const positions = new Float32Array([
          c[0][0], c[0][1], c[0][2],
          c[1][0], c[1][1], c[1][2],
          c[2][0], c[2][1], c[2][2],
          c[0][0], c[0][1], c[0][2],
          c[2][0], c[2][1], c[2][2],
          c[3][0], c[3][1], c[3][2],
        ]);
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
        g.computeVertexNormals();
        return { id: p.id, geometry: g };
      });
  }, [panels]);

  const color = COLORS[source] || COLORS.manual;

  return (
    <group>
      {meshes.map(({ id, geometry }) => (
        <mesh
          key={id}
          geometry={geometry}
          userData={{ panelId: id, panelSource: source }}
          onClick={
            clickable && onPanelClick
              ? (e) => {
                  e.stopPropagation();
                  onPanelClick(id);
                }
              : undefined
          }
        >
          <meshStandardMaterial
            color={color}
            side={THREE.DoubleSide}
            metalness={0.35}
            roughness={0.55}
            transparent={TRANSPARENT_SOURCES.has(source)}
            opacity={TRANSPARENT_SOURCES.has(source) ? 0.55 : 1.0}
            depthWrite={!TRANSPARENT_SOURCES.has(source)}
          />
        </mesh>
      ))}
    </group>
  );
}
