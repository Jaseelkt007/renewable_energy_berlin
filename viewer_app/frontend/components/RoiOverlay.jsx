"use client";

import { useMemo } from "react";
import { Line } from "@react-three/drei";

/**
 * M11 — interactive ROI marker.
 *
 * Renders a cyan circular ring + a faint translucent disc at the user-picked
 * ROI center, in GLB local space (Z-up, meters). Lives inside the same parent
 * group as the panels/plane overlays so it inherits any shared transform.
 *
 * Props:
 *   roi:    { center: [x, y, z], radius: number } | null
 *   color:  ring color (default cyan)
 *   segments: number of polygon segments (default 64)
 */
export default function RoiOverlay({ roi, color = "#22d3ee", segments = 64 }) {
  const points = useMemo(() => {
    if (!roi || !Array.isArray(roi.center) || !(roi.radius > 0)) return null;
    const [cx, cy, cz] = roi.center;
    const r = roi.radius;
    // Lift the ring 5 cm above the click so it doesn't z-fight the roof.
    const z = (cz ?? 0) + 0.05;
    const ring = [];
    for (let i = 0; i <= segments; i++) {
      const t = (i / segments) * Math.PI * 2;
      ring.push([cx + Math.cos(t) * r, cy + Math.sin(t) * r, z]);
    }
    return { ring, cx, cy, z, r };
  }, [roi, segments]);

  if (!points) return null;

  return (
    <group>
      {/* Cyan outer ring — always visible regardless of roof opacity. */}
      <Line
        points={points.ring}
        color={color}
        lineWidth={2.5}
        transparent
        opacity={0.95}
        depthTest={false}
      />
      {/* Faint translucent disc so the user can see the ROI extent at a glance.
          Sits 1 cm below the ring to avoid z-fighting with the line. */}
      <mesh position={[points.cx, points.cy, points.z - 0.01]} rotation={[0, 0, 0]}>
        <circleGeometry args={[points.r, segments]} />
        <meshBasicMaterial color={color} transparent opacity={0.12} depthWrite={false} />
      </mesh>
      {/* Center dot — confirms exactly where the click landed. */}
      <mesh position={[points.cx, points.cy, points.z + 0.05]}>
        <sphereGeometry args={[Math.max(0.15, points.r * 0.02), 16, 16]} />
        <meshBasicMaterial color={color} />
      </mesh>
    </group>
  );
}
