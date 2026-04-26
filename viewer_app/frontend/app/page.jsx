"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";

import ProjectSelector from "../components/ProjectSelector";
import SummaryPanel from "../components/SummaryPanel";
import OverlayControls from "../components/OverlayControls";
import { findPlaneAtPoint } from "../components/planeLookup";
import {
  computeSnapCandidate,
  shiftPanelOnPlane,
  stepSizesForPlane,
  validatePanelOnPlane,
} from "../components/panelSnap";

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
  // M12 — manual panel editing. `editMode` is "off" | "add" | "remove".
  // `manualEdits.added` are full Panel objects we layer on top of the AI
  // proposal; `manualEdits.removedIds` is the set of base panel IDs the user
  // removed. Both reset on project change and on `liveDesign` change.
  const [editMode, setEditMode] = useState("off");
  const [manualEdits, setManualEdits] = useState({ added: [], removedIds: [] });
  const [editStatus, setEditStatus] = useState(null); // { kind, message } | null
  // M12.1 — hover preview. `previewCandidate` is { candidate, valid, reason }
  // recomputed every hover frame in add-mode. Click commits if valid.
  const [previewCandidate, setPreviewCandidate] = useState(null);
  // M12.1 — move mode: id of the currently-selected panel (auto or manual).
  const [selectedPanelId, setSelectedPanelId] = useState(null);

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
    setManualEdits({ added: [], removedIds: [] });
    setEditStatus(null);
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

  // M12 — running an ROI counts as a new design; clear manual edits.
  useEffect(() => {
    setManualEdits({ added: [], removedIds: [] });
    setEditStatus(null);
  }, [liveDesign]);

  // M12.1 — drop the preview as soon as add-mode is left.
  useEffect(() => {
    if (editMode !== "add") setPreviewCandidate(null);
    if (editMode !== "move") setSelectedPanelId(null);
  }, [editMode]);

  // M12.1 — selection follows project/design swaps.
  useEffect(() => {
    setSelectedPanelId(null);
  }, [selected, liveDesign]);

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
      // M12.1 — once the design committed, the ROI picker has done its job.
      // Drop the marker so the user can edit panels without the cyan ring
      // covering the roof. Re-running ROI is still possible from the
      // "Pick ROI" interaction mode (the user just clicks again).
      setRoi(null);
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

  // M12 — effective panels = base panels minus removed + manual additions.
  // The contract isn't touched: source lives in React state only and is used
  // for color coding via `panelSourceMap`.
  const removedSet = useMemo(
    () => new Set(manualEdits.removedIds),
    [manualEdits.removedIds],
  );
  const basePanels = effectiveRoof?.panels || [];
  const effectivePanels = useMemo(
    () => [
      ...basePanels.filter((p) => !removedSet.has(p.id)),
      ...manualEdits.added,
    ],
    [basePanels, removedSet, manualEdits.added],
  );
  const hasEdits =
    manualEdits.added.length > 0 || manualEdits.removedIds.length > 0;

  // Recompute summary from effectivePanels when edits exist; otherwise pass
  // through the canonical summary so M0–M11 numbers are byte-identical.
  const effectiveRoofForView = useMemo(() => {
    if (!effectiveRoof) return null;
    if (!hasEdits) return effectiveRoof;
    const moduleWp = effectiveRoof.summary?.module_wp || 440;
    const panelsByPlane = {};
    for (const p of effectivePanels) {
      panelsByPlane[p.plane_id] = (panelsByPlane[p.plane_id] || 0) + 1;
    }
    return {
      ...effectiveRoof,
      panels: effectivePanels,
      summary: {
        ...effectiveRoof.summary,
        panel_count: effectivePanels.length,
        system_kwp: Math.round(effectivePanels.length * moduleWp) / 1000,
        panels_by_plane: panelsByPlane,
        method: effectiveRoof.summary?.method
          ? `${effectiveRoof.summary.method} + manual_edit`
          : "manual_edit",
      },
    };
  }, [effectiveRoof, hasEdits, effectivePanels]);

  const manualAddedPanels = manualEdits.added;

  function handleClearEdits() {
    setManualEdits({ added: [], removedIds: [] });
    setEditStatus(null);
  }

  function handleRemovePanel(panelId) {
    setEditStatus(null);
    setManualEdits((prev) => {
      // If the panel is one we just added, drop it from `added` instead of
      // adding to `removedIds` — keeps the diff clean.
      if (prev.added.some((p) => p.id === panelId)) {
        return { ...prev, added: prev.added.filter((p) => p.id !== panelId) };
      }
      if (prev.removedIds.includes(panelId)) return prev;
      return { ...prev, removedIds: [...prev.removedIds, panelId] };
    });
  }

  // M12.1 — move mode handlers ------------------------------------------------

  function handleSelectPanel(panelId) {
    setEditStatus(null);
    setSelectedPanelId((prev) => (prev === panelId ? null : panelId));
  }

  // Apply a (du, dv) nudge in plane (u, v) to the currently-selected panel.
  // Auto panels become manual on first nudge: we add the moved copy to
  // `manualEdits.added` and put the original id into `removedIds`. Manual
  // panels are updated in place (the id is preserved by `shiftPanelOnPlane`).
  function nudgeSelectedPanel(direction) {
    if (!effectiveRoof || !selectedPanelId) return;
    const panel = effectivePanels.find((p) => p.id === selectedPanelId);
    if (!panel) return;
    const plane = (effectiveRoof.roof_planes || []).find(
      (rp) => rp.id === panel.plane_id,
    );
    if (!plane) {
      setEditStatus({ kind: "error", message: "Plane for this panel not found." });
      return;
    }
    const { stepU, stepV } = stepSizesForPlane(panel);
    const dirToOffset = {
      "u-": [-stepU, 0],
      "u+": [stepU, 0],
      "v-": [0, -stepV],
      "v+": [0, stepV],
    };
    const [du, dv] = dirToOffset[direction] || [0, 0];
    if (du === 0 && dv === 0) return;

    const moved = shiftPanelOnPlane({ panel, plane, du, dv });
    const others = effectivePanels.filter(
      (p) => p.plane_id === plane.id && p.id !== panel.id,
    );
    const manualIds = new Set(manualEdits.added.map((m) => m.id));
    const aiPanelsOnPlane = others.filter((p) => !manualIds.has(p.id));
    const v = validatePanelOnPlane({
      panel: moved,
      plane,
      otherPanels: others,
      aiPanelsOnPlane,
    });
    if (!v.valid) {
      setEditStatus({ kind: "error", message: `Cannot move: ${v.reason}` });
      return;
    }
    const isManual = manualEdits.added.some((p) => p.id === panel.id);
    setManualEdits((prev) => {
      if (isManual) {
        return {
          ...prev,
          added: prev.added.map((p) => (p.id === panel.id ? moved : p)),
        };
      }
      // Auto → manual override: hide the original, add the moved copy with
      // a fresh manual id so future state is consistent.
      const newId = `manual_${plane.id}_${Date.now().toString(36)}_${Math.floor(Math.random() * 1e4).toString(36)}`;
      const promoted = { ...moved, id: newId, _source: "manual" };
      // Selection follows the new id so subsequent nudges keep working.
      setSelectedPanelId(newId);
      return {
        added: [...prev.added, promoted],
        removedIds: prev.removedIds.includes(panel.id)
          ? prev.removedIds
          : [...prev.removedIds, panel.id],
      };
    });
    setEditStatus(null);
  }

  // Arrow-key listener: only active in move mode with a selection.
  useEffect(() => {
    if (editMode !== "move") return;
    function onKey(e) {
      if (!selectedPanelId) return;
      const tag = (e.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      switch (e.key) {
        case "ArrowLeft":
          nudgeSelectedPanel("u-"); e.preventDefault(); break;
        case "ArrowRight":
          nudgeSelectedPanel("u+"); e.preventDefault(); break;
        case "ArrowUp":
          nudgeSelectedPanel("v+"); e.preventDefault(); break;
        case "ArrowDown":
          nudgeSelectedPanel("v-"); e.preventDefault(); break;
        case "Escape":
          setSelectedPanelId(null); e.preventDefault(); break;
        case "Delete":
        case "Backspace":
          handleRemovePanel(selectedPanelId);
          setSelectedPanelId(null);
          e.preventDefault();
          break;
        default:
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // nudgeSelectedPanel/handleRemovePanel close over current state via the
    // surrounding render; re-bind on every relevant change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editMode, selectedPanelId, effectivePanels, effectiveRoof, manualEdits]);

  // M12.1 — recompute the snapped preview every hover frame. The plane lookup
  // and snap math is pure; we keep the work in JS and avoid round-tripping
  // to the backend on hover. The backend safety check only runs on commit.
  function handleAddHover({ point, plane }) {
    if (!effectiveRoof) return;
    // The InteractiveModel does its own findPlaneAtPoint; trust it but
    // re-verify here in case of stale roof references during a state swap.
    const p = plane || findPlaneAtPoint(point, effectiveRoof.roof_planes || []);
    if (!p) {
      setPreviewCandidate(null);
      return;
    }
    const samePlanePanels = (effectivePanels || []).filter(
      (panel) => panel.plane_id === p.id,
    );
    // AI panels = base panels (post-removal) on this plane, excluding manual
    // additions. These are the only ones we trust for the "adjacency bypass"
    // — manual ones can't extend buildability transitively.
    const manualIds = new Set(manualEdits.added.map((m) => m.id));
    const aiPanelsOnPlane = samePlanePanels.filter(
      (panel) => !manualIds.has(panel.id),
    );
    const result = computeSnapCandidate({
      hitPoint: point,
      plane: p,
      samePlanePanels,
      aiPanelsOnPlane,
    });
    setPreviewCandidate(result);
  }

  function handleAddHoverClear() {
    setPreviewCandidate(null);
  }

  // M12.1 — click commits the *currently previewed* candidate. The hover
  // handler already validated locally; the backend call is a safety net so
  // any drift (or a not-yet-implemented edge case) still surfaces.
  async function handleAddPanelClick() {
    if (!selected || !effectiveRoof) return;
    const preview = previewCandidate;
    if (!preview?.candidate) {
      setEditStatus({
        kind: "error",
        message:
          "Click on a detected roof plane (orange/yellow polygon). Hover to preview, then click to commit.",
      });
      return;
    }
    if (!preview.valid) {
      setEditStatus({ kind: "error", message: `Cannot place: ${preview.reason}` });
      return;
    }
    const candidate = preview.candidate;
    const samePlanePanels = (effectivePanels || []).filter(
      (panel) => panel.plane_id === candidate.plane_id,
    );
    const plane = (effectiveRoof.roof_planes || []).find(
      (rp) => rp.id === candidate.plane_id,
    );
    if (!plane) {
      setEditStatus({ kind: "error", message: "Plane vanished from current design." });
      return;
    }
    const url = `${API_BASE}/api/projects/${selected.project_id}/validate-panel-geometry`;
    const manualIds = new Set(manualEdits.added.map((m) => m.id));
    const aiPanelCenters = samePlanePanels
      .filter((p) => !manualIds.has(p.id) && Array.isArray(p?.center) && p.center.length === 3)
      .map((p) => p.center);
    const body = {
      plane_id: plane.id,
      plane_centroid: plane.centroid,
      plane_u_axis: plane.u_axis,
      plane_v_axis: plane.v_axis,
      // For manual edits we validate against the raw plane polygon so the
      // ~30 cm placement setback (which the AI greedy uses) doesn't reject
      // user-driven panels visibly inside the roof. Backend matches.
      usable_polygon_3d: plane.polygon_3d || plane.usable_polygon_3d || [],
      candidate_corners_3d: candidate.corners_3d,
      existing_panels_corners_3d: samePlanePanels
        .map((p) => p.corners_3d)
        .filter(Boolean),
      // Adjacency-bypass: if the candidate sits within one grid step of any
      // of these AI-placed centers, the backend skips polygon containment.
      ai_panel_centers: aiPanelCenters,
      panel_width_m: candidate.width_m,
      panel_height_m: candidate.height_m,
    };
    let response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (e) {
      setEditStatus({ kind: "error", message: describeNetworkError(e, url) });
      return;
    }
    if (!response.ok) {
      setEditStatus({ kind: "error", message: await parseHttpError(response) });
      return;
    }
    const data = await response.json();
    if (!data.ok) {
      // Server disagreed with the local check. Surface the reason and
      // do NOT commit — local logic should be tightened to match.
      setEditStatus({ kind: "error", message: `Server rejected: ${data.reason}` });
      return;
    }
    setManualEdits((prev) => ({ ...prev, added: [...prev.added, candidate] }));
    setPreviewCandidate(null);
    setEditStatus({ kind: "success", message: `Added panel on ${plane.id}` });
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

        <SummaryPanel roof={effectiveRoofForView} />

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
            onAddPanelClick={handleAddPanelClick}
            onAddHover={handleAddHover}
            onAddHoverClear={handleAddHoverClear}
            previewCandidate={previewCandidate}
            selectedPanelId={selectedPanelId}
            onSelectPanel={handleSelectPanel}
            onRemovePanelClick={handleRemovePanel}
            seedingState={seeding}
            roi={interactionMode === "roi" ? roi : null}
            editMode={editMode}
            effectivePanels={effectivePanels}
            manualAddedPanels={manualAddedPanels}
            removedIds={manualEdits.removedIds}
          />
        )}
        <SeedStatus seeding={seeding} />
        <EditStatus status={editStatus} />
        {selected && effectiveRoof && (
          <div style={{
            position: "absolute", bottom: 10, left: 10,
            background: "rgba(15,17,24,0.7)", color: "#cbd5e1",
            padding: "6px 10px", fontSize: 11, borderRadius: 4,
            fontFamily: "ui-monospace, monospace",
          }}>
            {editMode === "add"
              ? "hover roof for a snapped preview · click to commit (yellow=valid, red=blocked)"
              : editMode === "move"
              ? selectedPanelId
                ? "use ←↑→↓ to move · Delete to remove · Esc to deselect"
                : "click a panel to select · then ←↑→↓ to nudge"
              : editMode === "remove"
              ? "click an existing panel to remove it"
              : interactionMode === "roi"
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
          editMode={editMode}
          setEditMode={setEditMode}
          hasEdits={hasEdits}
          onClearEdits={handleClearEdits}
          editCounts={{
            added: manualEdits.added.length,
            removed: manualEdits.removedIds.length,
          }}
        />
        <Legend />
        <PanelsByPlane roof={effectiveRoofForView} />
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

function EditStatus({ status }) {
  if (!status) return null;
  const colors = {
    info: "#22d3ee",
    success: "#86efac",
    error: "#fca5a5",
  };
  return (
    <div style={{
      position: "absolute", top: 10, right: 10,
      background: "rgba(15,17,24,0.85)", color: colors[status.kind] || "#cbd5e1",
      padding: "8px 12px", fontSize: 12, borderRadius: 4,
      fontFamily: "ui-monospace, monospace", maxWidth: 360,
    }}>{status.message}</div>
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
