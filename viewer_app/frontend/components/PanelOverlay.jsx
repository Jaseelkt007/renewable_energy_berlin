"use client";

import { useMemo } from "react";
import { useLoader } from "@react-three/fiber";
import * as THREE from "three";

import { buildSidesGeometry } from "./panelGeometry";

const PANEL_TEXTURE_URL = "/textures/panel.png";
const FRAME_COLOR = "#1f2937";   // dark grey for sides + backsheet

/**
 * UVs for one panel's two triangles. The triangles are emitted as
 *   tri1: c0, c1, c2
 *   tri2: c0, c2, c3
 * matching the position layout below.
 *
 * `longSideAlongV` is true when the panel is portrait (height_m >= width_m).
 * The bundled texture is landscape (~10×4 cells), so for portrait panels we
 * rotate the UVs 90° to keep the texture's long axis aligned with the
 * panel's long axis. Without this, a portrait panel would show a stretched
 * 10-wide × 4-tall cell pattern, which doesn't read as "real panel".
 */
function uvsForPanel(longSideAlongV) {
  if (longSideAlongV) {
    // c0 bottom-left of panel → top-left of texture, c1 → bottom-left, etc.
    return [
      0, 1,   // c0
      0, 0,   // c1
      1, 0,   // c2
      0, 1,   // c0 (tri2 starts)
      1, 0,   // c2
      1, 1,   // c3
    ];
  }
  return [
    0, 0,   // c0
    1, 0,   // c1
    1, 1,   // c2
    0, 0,   // c0
    1, 1,   // c2
    0, 1,   // c3
  ];
}

/**
 * Render every panel in a SINGLE batched BufferGeometry — one draw call total
 * regardless of panel count. The texture is sampled per-panel via UVs so the
 * cell grid + frame look right at any zoom level.
 */
export default function PanelOverlay({ panels = [], thicknessM = 0 }) {
  const texture = useLoader(THREE.TextureLoader, PANEL_TEXTURE_URL);
  // One-time texture setup. Idempotent: useLoader caches the texture, so
  // these flags persist across re-renders.
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;

  const geometry = useMemo(() => {
    const valid = panels.filter(
      (p) => Array.isArray(p?.corners_3d) && p.corners_3d.length === 4,
    );
    if (valid.length === 0) return null;

    const positions = new Float32Array(valid.length * 18);
    const uvs = new Float32Array(valid.length * 12);

    valid.forEach((p, i) => {
      const c = p.corners_3d;
      const off = i * 18;
      positions[off + 0] = c[0][0]; positions[off + 1] = c[0][1]; positions[off + 2] = c[0][2];
      positions[off + 3] = c[1][0]; positions[off + 4] = c[1][1]; positions[off + 5] = c[1][2];
      positions[off + 6] = c[2][0]; positions[off + 7] = c[2][1]; positions[off + 8] = c[2][2];
      positions[off + 9]  = c[0][0]; positions[off + 10] = c[0][1]; positions[off + 11] = c[0][2];
      positions[off + 12] = c[2][0]; positions[off + 13] = c[2][1]; positions[off + 14] = c[2][2];
      positions[off + 15] = c[3][0]; positions[off + 16] = c[3][1]; positions[off + 17] = c[3][2];

      const uv = uvsForPanel((p.height_m ?? 0) >= (p.width_m ?? 0));
      const uoff = i * 12;
      for (let k = 0; k < 12; k++) uvs[uoff + k] = uv[k];
    });

    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    g.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
    g.computeVertexNormals();
    return g;
  }, [panels]);

  const sidesGeometry = useMemo(
    () => buildSidesGeometry(panels, thicknessM),
    [panels, thicknessM],
  );

  if (!geometry) return null;

  return (
    <group>
      <mesh geometry={geometry}>
        <meshStandardMaterial
          map={texture}
          side={THREE.DoubleSide}
          metalness={0.5}
          roughness={0.35}
        />
      </mesh>
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
    </group>
  );
}
