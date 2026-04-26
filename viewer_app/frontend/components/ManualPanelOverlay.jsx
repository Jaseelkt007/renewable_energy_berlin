"use client";

import { useMemo } from "react";
import { useLoader } from "@react-three/fiber";
import * as THREE from "three";

import { buildSidesGeometry } from "./panelGeometry";

const FRAME_COLOR = "#1f2937";

/**
 * Renders the small set of panels that need a per-panel color: manual
 * (committed user-added), candidate (preview), rejected (flash), selected
 * (move-mode highlight).
 *
 * Each panel is its own mesh (rather than one batched BufferGeometry) so the
 * color can vary per-panel and so the click-to-remove path can attach
 * `userData.panelId` on the mesh — `event.object.userData.panelId` is then a
 * direct lookup. We never render more than ~50 of these at once in practice
 * (manual edits are interactive), so the cost of N draw calls is negligible.
 *
 * The committed `manual` source uses the same panel texture as PanelOverlay
 * so user-added panels look identical to AI-placed ones. Transient UI states
 * (candidate, rejected, selected) keep flat colors — a green-tinted texture
 * reads worse than a clean colored rect when the panel is supposed to call
 * attention to itself.
 */
const PANEL_TEXTURE_URL = "/textures/panel.png";

const COLORS = {
  manual: "#0b0e2c",        // ignored when textured; kept for fallback
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

const TEXTURED_SOURCES = new Set(["manual"]);

function uvsForPanel(longSideAlongV) {
  if (longSideAlongV) {
    return new Float32Array([
      0, 1,
      0, 0,
      1, 0,
      0, 1,
      1, 0,
      1, 1,
    ]);
  }
  return new Float32Array([
    0, 0,
    1, 0,
    1, 1,
    0, 0,
    1, 1,
    0, 1,
  ]);
}

export default function ManualPanelOverlay({
  panels = [],
  source = "manual",
  onPanelClick,
  clickable = false,
  thicknessM = 0,
}) {
  const texture = useLoader(THREE.TextureLoader, PANEL_TEXTURE_URL);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;

  const textured = TEXTURED_SOURCES.has(source);

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
        if (textured) {
          const uvs = uvsForPanel((p.height_m ?? 0) >= (p.width_m ?? 0));
          g.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
        }
        g.computeVertexNormals();
        return { id: p.id, geometry: g };
      });
  }, [panels, textured]);

  const color = COLORS[source] || COLORS.manual;
  const isTransparent = TRANSPARENT_SOURCES.has(source);

  // Sides only render for committed textured panels — transient UI states
  // (candidate, rejected, selected) stay perfectly flat so the color signal
  // is unambiguous.
  const sidesGeometry = useMemo(
    () => (textured ? buildSidesGeometry(panels, thicknessM) : null),
    [panels, thicknessM, textured],
  );

  return (
    <group>
      {sidesGeometry && (
        <mesh geometry={sidesGeometry}>
          <meshStandardMaterial
            color={FRAME_COLOR}
            side={THREE.DoubleSide}
            metalness={0.6}
            roughness={0.4}
          />
        </mesh>
      )}
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
            map={textured ? texture : null}
            color={textured ? "#ffffff" : color}
            side={THREE.DoubleSide}
            metalness={textured ? 0.5 : 0.35}
            roughness={textured ? 0.35 : 0.55}
            transparent={isTransparent}
            opacity={isTransparent ? 0.55 : 1.0}
            depthWrite={!isTransparent}
          />
        </mesh>
      ))}
    </group>
  );
}
