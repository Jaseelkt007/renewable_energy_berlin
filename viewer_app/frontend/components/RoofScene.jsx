"use client";

import { Suspense, useMemo, useState } from "react";
import { Canvas, useLoader } from "@react-three/fiber";
import { Html, OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader";
import { DRACOLoader } from "three/examples/jsm/loaders/DRACOLoader";

import PolygonOverlay from "./PolygonOverlay";
import ObstructionOverlay from "./ObstructionOverlay";
import PanelOverlay from "./PanelOverlay";
import ManualPanelOverlay from "./ManualPanelOverlay";
import RoiOverlay from "./RoiOverlay";
import { findPlaneAtPoint } from "./planeLookup";

const dracoLoader = new DRACOLoader();
dracoLoader.setDecoderPath("https://www.gstatic.com/draco/versioned/decoders/1.5.6/");

function GltfModel({ url }) {
  const gltf = useLoader(GLTFLoader, url, (loader) => {
    loader.setDRACOLoader(dracoLoader);
  });
  return <primitive object={gltf.scene} />;
}

function bboxView(bbox) {
  const fallback = { center: [0, 0, 0], size: 100 };
  if (!bbox || !bbox.min || !bbox.max) return fallback;
  const center = [
    (bbox.min[0] + bbox.max[0]) / 2,
    (bbox.min[1] + bbox.max[1]) / 2,
    (bbox.min[2] + bbox.max[2]) / 2,
  ];
  const size = Math.max(
    bbox.max[0] - bbox.min[0],
    bbox.max[1] - bbox.min[1],
    bbox.max[2] - bbox.min[2],
    1
  );
  return { center, size };
}

export default function RoofScene({
  modelUrl,
  roof,
  overlays,
  onSeedClick,
  seedingState,
  roi,
  // M12 — manual editing
  editMode = "off",
  effectivePanels,
  manualAddedPanels = [],
  removedIds = [],
  onAddPanelClick,
  onRemovePanelClick,
  // M12.1 — hover preview
  onAddHover,
  onAddHoverClear,
  previewCandidate,        // { candidate, valid, reason } | null
  // M12.1 move mode
  selectedPanelId = null,
  onSelectPanel,
}) {
  const { center, size } = useMemo(() => bboxView(roof?.bbox), [roof]);
  const camPos = useMemo(
    () => [center[0] + size, center[1] - size, center[2] + size * 0.7],
    [center, size]
  );

  // Split planes by source so we can color seeded ones distinctly.
  const planes = roof?.roof_planes || [];
  const autoPlanes = planes.filter((p) => p.source !== "click_seeded");
  const seededPlanes = planes.filter((p) => p.source === "click_seeded");

  const polygonsOf = (arr, key) =>
    arr.map((p) => p[key]).filter((pts) => Array.isArray(pts) && pts.length >= 3);

  const obstructions = roof?.obstructions || [];
  const basePanels = roof?.panels || [];
  // M12 — split panels for rendering. `autoPanels` go through the batched
  // PanelOverlay (one draw call); `manualAddedPanels` get the green
  // ManualPanelOverlay; `removedIds` are filtered out of both.
  const removedSet = new Set(removedIds);
  const autoPanels = basePanels.filter((p) => !removedSet.has(p.id));
  const panels = effectivePanels || basePanels;

  return (
    <Canvas
      camera={{ position: camPos, fov: 45, near: 0.1, far: size * 20 }}
      gl={{ antialias: true }}
      onCreated={({ camera }) => {
        camera.up.set(0, 0, 1);
        camera.lookAt(center[0], center[1], center[2]);
        camera.updateProjectionMatrix();
      }}
    >
      <color attach="background" args={["#0e0f14"]} />
      <ambientLight intensity={0.65} />
      <directionalLight position={[size, size, size * 2]} intensity={0.9} />
      <directionalLight position={[-size, -size, size]} intensity={0.35} />

      <group>
        <Suspense fallback={null}>
          {modelUrl && (
            <InteractiveModel
              url={modelUrl}
              show={overlays.showModel}
              roofPlanes={planes}
              onSeed={editMode === "add" ? onAddPanelClick : onSeedClick}
              onAddHover={editMode === "add" ? onAddHover : null}
              onAddHoverClear={editMode === "add" ? onAddHoverClear : null}
              editMode={editMode}
            />
          )}
        </Suspense>

        {overlays.showRawPlanes && (
          <>
            <PolygonOverlay polygons={polygonsOf(autoPlanes, "polygon_3d")}
                            color="#f59e0b" lineWidth={2} opacity={0.85} />
            <PolygonOverlay polygons={polygonsOf(seededPlanes, "polygon_3d")}
                            color="#22d3ee" lineWidth={2.4} opacity={0.95} />
          </>
        )}
        {overlays.showUsable && (
          <>
            <PolygonOverlay polygons={polygonsOf(autoPlanes, "usable_polygon_3d")}
                            color="#facc15" lineWidth={1.6} opacity={0.95} />
            <PolygonOverlay polygons={polygonsOf(seededPlanes, "usable_polygon_3d")}
                            color="#67e8f9" lineWidth={1.8} opacity={0.95} />
          </>
        )}
        {overlays.showObstructions && (
          <ObstructionOverlay obstructions={obstructions} />
        )}
        {overlays.showPanels && (
          <>
            <PanelOverlay panels={autoPanels} />
            <ManualPanelOverlay
              panels={manualAddedPanels}
              source="manual"
              clickable={editMode === "remove"}
              onPanelClick={onRemovePanelClick}
            />
            {editMode === "remove" && (
              <PanelClickTargets
                panels={autoPanels}
                onPanelClick={onRemovePanelClick}
              />
            )}
            {editMode === "move" && (
              <>
                <PanelClickTargets
                  panels={panels}
                  onPanelClick={onSelectPanel}
                />
                {selectedPanelId && (() => {
                  const sel = panels.find((p) => p.id === selectedPanelId);
                  return sel ? (
                    <ManualPanelOverlay panels={[sel]} source="selected" />
                  ) : null;
                })()}
              </>
            )}
            {editMode === "add" && previewCandidate?.candidate && (
              <ManualPanelOverlay
                panels={[previewCandidate.candidate]}
                source={previewCandidate.valid ? "candidate-valid" : "candidate-invalid"}
              />
            )}
          </>
        )}

        {seedingState?.busy && seedingState.point && (
          <mesh position={seedingState.point}>
            <sphereGeometry args={[size * 0.005, 16, 16]} />
            <meshBasicMaterial color="#22d3ee" />
          </mesh>
        )}

        {/* M11 — picked ROI marker (cyan ring + disc). */}
        <RoiOverlay roi={roi} />
      </group>

      <OrbitControls makeDefault target={center} maxPolarAngle={Math.PI * 0.95} enableDamping />
    </Canvas>
  );
}

/**
 * Wraps the GLB scene so we can hover-test against the detected planes and
 * forward clicks for the seed pipeline. R3F bubbles pointer events from any
 * descendant mesh up to this group, so we get face-accurate raycasts on every
 * triangle of the photogrammetry mesh.
 */
function InteractiveModel({ url, show, roofPlanes, onSeed, onAddHover, onAddHoverClear, editMode }) {
  const [hover, setHover] = useState(null);

  const handleMove = (e) => {
    e.stopPropagation();
    const point = [e.point.x, e.point.y, e.point.z];
    const plane = findPlaneAtPoint(point, roofPlanes);
    setHover({ point, plane, faceIndex: e.faceIndex ?? null });
    // M12.1 — in add-mode, fire the hover up so page.jsx can compute the
    // snapped preview candidate. We forward even when `plane` is null so the
    // preview clears as soon as the cursor leaves a known plane.
    if (onAddHover) onAddHover({ point, plane });
  };

  const handleClick = (e) => {
    e.stopPropagation();
    // In remove/move modes, GLB clicks do nothing — only panel clicks matter.
    if (editMode === "remove" || editMode === "move") return;
    const point = [e.point.x, e.point.y, e.point.z];
    const normal = e.face?.normal
      ? [e.face.normal.x, e.face.normal.y, e.face.normal.z]
      : null;
    onSeed?.({ point, normal, faceIndex: e.faceIndex ?? null });
  };

  return (
    <group
      onPointerMove={handleMove}
      onPointerOut={() => {
        setHover(null);
        onAddHoverClear?.();
      }}
      onClick={handleClick}
      visible={show}
    >
      <GltfModel url={url} />
      {hover && editMode !== "add" && editMode !== "move" && (
        <Html
          position={hover.point}
          style={{ pointerEvents: "none", transform: "translate(8px, -100%)" }}
          zIndexRange={[100, 0]}
        >
          <HoverTooltip hover={hover} />
        </Html>
      )}
    </group>
  );
}

/**
 * M12 — invisible per-panel click targets used only when editMode === "remove".
 * Each panel becomes its own mesh with userData.panelId so a click goes
 * straight to the right ID without raycasting against the batched
 * PanelOverlay geometry.
 */
function PanelClickTargets({ panels, onPanelClick }) {
  return (
    <group>
      {panels
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
          const geom = new THREE.BufferGeometry();
          geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
          return (
            <mesh
              key={`click-${p.id}`}
              geometry={geom}
              userData={{ panelId: p.id }}
              onClick={(e) => {
                e.stopPropagation();
                onPanelClick?.(p.id);
              }}
            >
              <meshBasicMaterial
                color="#ef4444"
                transparent
                opacity={0.001}
                side={THREE.DoubleSide}
                depthWrite={false}
              />
            </mesh>
          );
        })}
    </group>
  );
}

function HoverTooltip({ hover }) {
  const p = hover.plane;
  const r = p?.confidence_reasons;
  return (
    <div style={{
      background: "rgba(15,17,24,0.92)",
      color: "#e5e7eb",
      padding: "8px 10px",
      borderRadius: 6,
      fontSize: 11,
      lineHeight: 1.4,
      minWidth: 200,
      border: "1px solid #374151",
      fontFamily: "ui-monospace, SFMono-Regular, monospace",
      whiteSpace: "nowrap",
    }}>
      <div style={{ fontFamily: "inherit" }}>
        <span style={{ color: "#9ca3af" }}>hit:</span>{" "}
        ({hover.point[0].toFixed(1)}, {hover.point[1].toFixed(1)}, {hover.point[2].toFixed(1)})
      </div>
      {p ? (
        <>
          <div style={{ marginTop: 4 }}>
            <strong style={{ color: p.source === "click_seeded" ? "#22d3ee" : "#fbbf24" }}>
              {p.id}
            </strong>{" "}
            <span style={{ color: "#9ca3af" }}>· {p.source}</span>
          </div>
          <div>tilt {p.tilt_deg.toFixed(1)}° · az {p.azimuth_deg.toFixed(1)}°</div>
          <div>area {p.area_m2.toFixed(1)} m² · usable {p.usable_area_m2.toFixed(1)} m²</div>
          <div>panels {p.panel_count} · conf {p.confidence}</div>
          {r && (
            <div style={{ color: "#9ca3af" }}>
              {[
                r.area_large_enough && "area",
                r.normal_stable && "normal",
                r.height_valid && "subst.",
                r.polygon_clean && "poly",
              ].filter(Boolean).join(" · ")}
            </div>
          )}
          <div style={{ color: "#9ca3af", marginTop: 2 }}>click to refit locally</div>
        </>
      ) : (
        <div style={{ marginTop: 4, color: "#9ca3af" }}>
          no detected plane here · click to seed a fit
        </div>
      )}
    </div>
  );
}
