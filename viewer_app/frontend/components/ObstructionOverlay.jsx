"use client";

import PolygonOverlay from "./PolygonOverlay";

export default function ObstructionOverlay({ obstructions = [] }) {
  const polygons = obstructions
    .map((o) => o?.polygon_3d)
    .filter((pts) => Array.isArray(pts) && pts.length >= 3);
  return <PolygonOverlay polygons={polygons} color="#dc2626" lineWidth={2} opacity={0.95} />;
}
