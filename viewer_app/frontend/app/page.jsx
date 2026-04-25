"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";

import ProjectSelector from "../components/ProjectSelector";
import SummaryPanel from "../components/SummaryPanel";
import OverlayControls from "../components/OverlayControls";

// R3F + three.js need the browser. Render the scene only on the client.
const RoofScene = dynamic(() => import("../components/RoofScene"), { ssr: false });

// Browser → WSL networking is much more reliable on 127.0.0.1 than on localhost
// (some setups resolve localhost to ::1 first, which fails when uvicorn binds
// only to 127.0.0.1). Override with NEXT_PUBLIC_API_BASE in .env.local.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8001";

if (typeof window !== "undefined") {
  // One-time log so you can confirm the URL the bundle is calling.
  // Visible in DevTools > Console.
  console.info("[roof-viewer] API_BASE =", API_BASE);
}

async function parseHttpError(response) {
  // Try to surface the FastAPI `detail` field if present, else the status text.
  try {
    const body = await response.json();
    if (body?.detail) return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {}
  return `HTTP ${response.status} ${response.statusText || ""}`.trim();
}

function describeNetworkError(err, url) {
  // fetch() rejects with TypeError for: server unreachable, DNS failure, CORS
  // preflight rejected, mixed-content blocking. None of those carry a status
  // code; we have to infer from the symptom.
  const base = `Cannot reach backend at ${url}.`;
  const hint = "Verify uvicorn is running and CORS allows POST. Try `curl " +
               url.replace(/\/[^/]+$/, "/health") + "`.";
  return `${base} ${hint} (${err.message})`;
}

const DEFAULT_OVERLAYS = {
  showModel: true,
  showRawPlanes: true,
  showUsable: true,
  showObstructions: true,
  showPanels: true,
};

export default function Page() {
  const [projects, setProjects] = useState([]);
  const [selected, setSelected] = useState(null);
  const [roof, setRoof] = useState(null);
  const [error, setError] = useState(null);
  const [loadingRoof, setLoadingRoof] = useState(false);
  const [overlays, setOverlays] = useState(DEFAULT_OVERLAYS);
  const [mode, setMode] = useState("selected"); // M10 — "selected" | "tile"
  const [seeding, setSeeding] = useState({ busy: false, point: null, lastError: null, lastResult: null });
  // M11 — interactive ROI state.
  // `interactionMode` controls what a click on the model does:
  //   "seed" — M8 click-to-refit a single plane (legacy default).
  //   "roi"  — M11 set the ROI center; user then adjusts radius and runs /design.
  // `roi` is the picked region, in GLB local space: { center: [x,y,z], radius }.
  // `liveDesign` is the full RoofDesign payload from /design that, when present,
  // replaces `roof` for rendering. The pending/error states mirror the M8 model.
  const [interactionMode, setInteractionMode] = useState("seed");
  const [roi, setRoi] = useState(null);
  const [liveDesign, setLiveDesign] = useState(null);
  const [designPending, setDesignPending] = useState(false);
  const [designError, setDesignError] = useState(null);

  // Fetch project list once on mount.
  useEffect(() => {
    const url = `${API_BASE}/api/projects`;
    fetch(url)
      .then(async (r) => {
        if (!r.ok) throw new Error(await parseHttpError(r));
        return r.json();
      })
      .then((p) => {
        setProjects(p);
        if (p.length) setSelected(p[0]);
      })
      .catch((e) => {
        if (e instanceof TypeError) setError(describeNetworkError(e, url));
        else setError(`Failed to load projects: ${e.message}`);
      });
  }, []);

  // Re-fetch roof JSON when the selection or mode changes.
  useEffect(() => {
    if (!selected) return;
    setRoof(null);
    setSeeding({ busy: false, point: null, lastError: null, lastResult: null });
    // M11 — drop any previous live ROI result + marker; saved-canonical takes over.
    setRoi(null);
    setLiveDesign(null);
    setDesignPending(false);
    setDesignError(null);
    setLoadingRoof(true);
    const url = `${API_BASE}/api/projects/${selected.project_id}/roof?mode=${mode}`;
    fetch(url)
      .then(async (r) => {
        if (!r.ok) throw new Error(await parseHttpError(r));
        return r.json();
      })
      .then((j) => setRoof(j))
      .catch((e) => {
        if (e instanceof TypeError) setError(describeNetworkError(e, url));
        else setError(`Failed to load roof JSON: ${e.message}`);
      })
      .finally(() => setLoadingRoof(false));
  }, [selected, mode]);

  const modelUrl = selected ? `${API_BASE}/api/projects/${selected.project_id}/model` : null;

  // M8 / M11 — single click handler driven by interactionMode.
  //   "seed" → POST /seed and merge the new plane (M8 legacy).
  //   "roi"  → set ROI center for the M11 design endpoint; no network call yet.
  // The default radius (15 m) is reused if the user hasn't picked an ROI before
  // for this project; subsequent clicks keep the slider's current radius so the
  // user can re-pick a center without losing their tuning.
  async function handleSeedClick({ point, normal, faceIndex }) {
    if (!selected) return;
    if (interactionMode === "roi") {
      const prevRadius = roi?.radius ?? 15;
      setRoi({ center: point, radius: prevRadius });
      setDesignError(null);
      return;
    }
    const planeId = `seeded_${Date.now().toString(36)}`;
    const url = `${API_BASE}/api/projects/${selected.project_id}/seed`;
    const body = {
      hit_point: point,
      hit_normal: normal,
      face_index: faceIndex ?? null,
      plane_id: planeId,
    };
    setSeeding({ busy: true, point, lastError: null, lastResult: null });
    if (typeof window !== "undefined") {
      console.info("[roof-viewer] POST", url, body);
    }
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      // TypeError is thrown for true network failures: server down, CORS
      // rejected the preflight, mixed content, etc. Distinguishing this from
      // a 422 was the whole point of this rewrite — generic "Failed to fetch"
      // told us nothing.
      const msg = describeNetworkError(e, url);
      console.error("[roof-viewer] seed network error:", e);
      setSeeding({ busy: false, point: null, lastError: msg, lastResult: null });
      return;
    }
    if (typeof window !== "undefined") {
      console.info("[roof-viewer] seed response:", response.status, response.statusText);
    }
    if (!response.ok) {
      const msg = await parseHttpError(response);
      setSeeding({ busy: false, point: null, lastError: msg, lastResult: null });
      return;
    }
    try {
      const data = await response.json();
      setRoof((prev) => mergeSeed(prev, data));
      setSeeding({ busy: false, point: null, lastError: null, lastResult: data });
    } catch (e) {
      setSeeding({ busy: false, point: null, lastError: `Bad JSON from /seed: ${e.message}`, lastResult: null });
    }
  }

  // M11 — adjust ROI radius from the slider (live local update, no POST).
  function handleRoiRadiusChange(r) {
    setRoi((prev) => prev ? { ...prev, radius: r } : prev);
    setDesignError(null);
  }

  // M11 — clear the ROI marker AND any live result, reverting to the saved
  // canonical roof JSON for the current project/mode.
  function handleClearRoi() {
    setRoi(null);
    setLiveDesign(null);
    setDesignError(null);
  }

  // M11 — POST /design with the picked ROI. The response is a full RoofDesign
  // shape that takes over `effectiveRoof` for the scene + summary panel.
  async function handleRunRoi() {
    if (!selected || !roi) return;
    const body = {
      center_xy: [roi.center[0], roi.center[1]],
      radius_m: roi.radius,
    };
    const url = `${API_BASE}/api/projects/${selected.project_id}/design`;
    setDesignPending(true);
    setDesignError(null);
    console.info("[roof-viewer] POST /design", url, body);
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      setDesignError(describeNetworkError(e, url));
      setDesignPending(false);
      return;
    }
    if (!response.ok) {
      setDesignError(await parseHttpError(response));
      setDesignPending(false);
      return;
    }
    try {
      const data = await response.json();
      console.info("[roof-viewer] /design response:", {
        panels: data.summary?.panel_count,
        kwp: data.summary?.system_kwp,
        diagnostics: data.diagnostics,
      });
      setLiveDesign(data);
    } catch (e) {
      setDesignError(`Bad JSON from /design: ${e.message}`);
    } finally {
      setDesignPending(false);
    }
  }

  // M11 — what the scene + panels actually render. Live ROI result wins when
  // present; otherwise fall back to the GET roof JSON (M10 selected/tile).
  const effectiveRoof = liveDesign || roof;
  const isLive = Boolean(liveDesign);

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "280px 1fr 280px",
      height: "100vh",
      width: "100vw",
    }}>
      {/* Left: project + summary */}
      <aside style={{ padding: 14, borderRight: "1px solid #e5e7eb", background: "#fff", overflow: "auto" }}>
        <h2>Roof Viewer</h2>
        <p style={{ fontSize: 11, color: "#6b7280", marginTop: -4 }}>
          M0–M11. Pick the customer&apos;s house in the 3D view (Interaction → Pick ROI) to run the
          pipeline live on that region.
        </p>

        <ProjectSelector projects={projects} selected={selected} onSelect={setSelected} />

        {selected && (
          <div style={{ marginTop: 12, fontSize: 12 }}>
            <div className="kv-row"><span>Project ID</span><strong>{selected.project_id}</strong></div>
            <div className="kv-row"><span>Model</span><strong>{selected.model_file}</strong></div>
            <div className="kv-row"><span>Mode</span><strong>{selected.mode}</strong></div>
            {selected.note && <div className="warn-banner" style={{ marginTop: 8 }}>⚠ {selected.note}</div>}
          </div>
        )}

        {loadingRoof && <div style={{ marginTop: 8, fontSize: 12 }}>Loading roof JSON…</div>}

        <SummaryPanel roof={effectiveRoof} />

        {/* M11 — small status row so the user always knows whether they're
            looking at a saved JSON or a live ROI computation. */}
        {selected && effectiveRoof && (
          <div className="kv-row" style={{ marginTop: 8, fontSize: 12 }}>
            <span>Source</span>
            <strong style={{ color: isLive ? "#0e7490" : "#475569" }}>
              {isLive ? "live /design" : "saved canonical"}
            </strong>
          </div>
        )}

        {error && <div className="error-banner">{error}</div>}

      </aside>

      {/* Center: 3D viewer */}
      <main style={{ position: "relative", background: "#0e0f14" }}>
        {!selected && <CenterMessage>Select a project to start.</CenterMessage>}
        {selected && !effectiveRoof && !error && <CenterMessage>Loading…</CenterMessage>}
        {selected && effectiveRoof && (
          <RoofScene
            modelUrl={modelUrl}
            roof={effectiveRoof}
            overlays={overlays}
            onSeedClick={handleSeedClick}
            seedingState={seeding}
            roi={interactionMode === "roi" ? roi : null}
          />
        )}
        <SeedStatus seeding={seeding} />
        {selected && effectiveRoof && (
          <div style={{
            position: "absolute", bottom: 10, left: 10,
            background: "rgba(15,17,24,0.7)", color: "#cbd5e1",
            padding: "6px 10px", fontSize: 11, borderRadius: 4,
            fontFamily: "ui-monospace, monospace",
          }}>
            {interactionMode === "roi"
              ? "click on the customer's roof to set the ROI center"
              : "hover the model for plane info · click to seed a refit"}
          </div>
        )}
      </main>

      {/* Right: overlays + legend + per-plane breakdown */}
      <aside style={{ padding: 14, borderLeft: "1px solid #e5e7eb", background: "#fff", overflow: "auto" }}>
        <OverlayControls
          overlays={overlays}
          setOverlays={setOverlays}
          mode={mode}
          setMode={setMode}
          availableModes={selected?.modes || ["selected"]}
          interactionMode={interactionMode}
          setInteractionMode={setInteractionMode}
          roi={roi}
          setRoiRadius={handleRoiRadiusChange}
          onRunRoi={handleRunRoi}
          onClearRoi={handleClearRoi}
          designPending={designPending}
          isLive={isLive}
          designError={designError}
        />
        <Legend />
        <PanelsByPlane roof={effectiveRoof} />
        <div className="note">
          <strong>Coordinate frame.</strong> JSON coords are in the original GLB local space (Z-up,
          meters). The R3F Canvas is configured with <code>camera.up = (0,0,1)</code> so geometry
          renders upright without rotating individual children. GLB and overlays share one root
          group, so any future shared transform can be applied to that group only.
        </div>
      </aside>
    </div>
  );
}

function mergeSeed(prev, seedResp) {
  if (!prev) return prev;
  const newPlane = seedResp.plane;
  const newPanels = seedResp.panels || [];
  const newObs = seedResp.obstructions || [];
  // Replace any existing plane with the same id (so re-clicks don't accumulate).
  const planes = [
    ...prev.roof_planes.filter((p) => p.id !== newPlane.id),
    newPlane,
  ];
  const panels = [
    ...prev.panels.filter((p) => p.plane_id !== newPlane.id),
    ...newPanels,
  ];
  const obstructions = [
    ...(prev.obstructions || []).filter((o) => o.plane_id !== newPlane.id),
    ...newObs,
  ];
  const panelCount = panels.length;
  const moduleWp = prev.summary?.module_wp || 440;
  const panelsByPlane = { ...(prev.summary?.panels_by_plane || {}) };
  panelsByPlane[newPlane.id] = newPanels.length;
  return {
    ...prev,
    roof_planes: planes,
    panels,
    obstructions,
    summary: {
      ...prev.summary,
      panel_count: panelCount,
      system_kwp: Math.round(panelCount * moduleWp) / 1000,
      panels_by_plane: panelsByPlane,
      method: prev.summary?.method ? `${prev.summary.method} + click_seeded` : "click_seeded",
    },
  };
}

function SeedStatus({ seeding }) {
  if (!seeding.busy && !seeding.lastError && !seeding.lastResult) return null;
  let body, color;
  if (seeding.busy) {
    body = "Fitting plane at click…";
    color = "#22d3ee";
  } else if (seeding.lastError) {
    body = `Seed failed: ${seeding.lastError}`;
    color = "#fca5a5";
  } else {
    const p = seeding.lastResult.plane;
    body = `Seeded ${p.id} · ${p.tilt_deg.toFixed(1)}° · ${seeding.lastResult.panels.length} panels (${seeding.lastResult.diagnostics?.n_faces_grown} faces grown)`;
    color = "#86efac";
  }
  return (
    <div style={{
      position: "absolute", top: 10, left: 10,
      background: "rgba(15,17,24,0.85)", color,
      padding: "8px 12px", fontSize: 12, borderRadius: 4,
      fontFamily: "ui-monospace, monospace",
      maxWidth: 420,
    }}>{body}</div>
  );
}

function CenterMessage({ children }) {
  return (
    <div style={{
      position: "absolute", inset: 0,
      display: "flex", alignItems: "center", justifyContent: "center",
      color: "#cbd5e1", fontSize: 14,
    }}>{children}</div>
  );
}

function Legend() {
  const items = [
    { color: "#f59e0b", label: "raw roof polygon" },
    { color: "#facc15", label: "usable area" },
    { color: "#dc2626", label: "obstruction / bump" },
    { color: "#1e1e44", label: "solar panels" },
  ];
  return (
    <div>
      <h4>Legend</h4>
      {items.map((it) => (
        <div className="legend-item" key={it.label}>
          <span className="legend-swatch" style={{ background: it.color }} />
          <span>{it.label}</span>
        </div>
      ))}
    </div>
  );
}

function PanelsByPlane({ roof }) {
  const map = roof?.summary?.panels_by_plane || {};
  const entries = Object.entries(map);
  if (!entries.length) return null;
  return (
    <div style={{ marginTop: 14, fontSize: 12 }}>
      <h4>Panels by plane</h4>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k} style={{ borderTop: "1px solid #f1f5f9" }}>
              <td style={{ padding: "3px 0", fontFamily: "ui-monospace, monospace" }}>{k}</td>
              <td style={{ padding: "3px 0", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
