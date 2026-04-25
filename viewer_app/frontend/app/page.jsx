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
  const [seeding, setSeeding] = useState({ busy: false, point: null, lastError: null, lastResult: null });

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

  // Re-fetch roof JSON when the selection changes.
  useEffect(() => {
    if (!selected) return;
    setRoof(null);
    setSeeding({ busy: false, point: null, lastError: null, lastResult: null });
    setLoadingRoof(true);
    const url = `${API_BASE}/api/projects/${selected.project_id}/roof`;
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
  }, [selected]);

  const modelUrl = selected ? `${API_BASE}/api/projects/${selected.project_id}/model` : null;

  // M8 — click-to-seed handler. POSTs the click to the backend, merges the
  // returned plane/panels/obstructions into the current roof state, and updates
  // the summary so the kWp counter reflects the addition.
  async function handleSeedClick({ point, normal, faceIndex }) {
    if (!selected) return;
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
          M0–M8: hover for plane info, click to refit a plane locally.
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

        <SummaryPanel roof={roof} />

        {error && <div className="error-banner">{error}</div>}

        <div className="warn-banner" style={{ marginTop: 14 }}>
          Current auto result is tile-wide. M9 will bind/select one customer building or roof
          subset for realistic per-home output.
        </div>
      </aside>

      {/* Center: 3D viewer */}
      <main style={{ position: "relative", background: "#0e0f14" }}>
        {!selected && <CenterMessage>Select a project to start.</CenterMessage>}
        {selected && !roof && !error && <CenterMessage>Loading…</CenterMessage>}
        {selected && roof && (
          <RoofScene
            modelUrl={modelUrl}
            roof={roof}
            overlays={overlays}
            onSeedClick={handleSeedClick}
            seedingState={seeding}
          />
        )}
        <SeedStatus seeding={seeding} />
        {selected && roof && (
          <div style={{
            position: "absolute", bottom: 10, left: 10,
            background: "rgba(15,17,24,0.7)", color: "#cbd5e1",
            padding: "6px 10px", fontSize: 11, borderRadius: 4,
            fontFamily: "ui-monospace, monospace",
          }}>
            hover the model for plane info · click to seed a refit
          </div>
        )}
      </main>

      {/* Right: overlays + legend + per-plane breakdown */}
      <aside style={{ padding: 14, borderLeft: "1px solid #e5e7eb", background: "#fff", overflow: "auto" }}>
        <OverlayControls overlays={overlays} setOverlays={setOverlays} />
        <Legend />
        <PanelsByPlane roof={roof} />
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
