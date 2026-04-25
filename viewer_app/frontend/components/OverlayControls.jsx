"use client";

const LABELS = {
  showModel: "show GLB model",
  showRawPlanes: "show raw roof polygons",
  showUsable: "show usable polygons",
  showObstructions: "show obstructions / bumps",
  showPanels: "show solar panels",
};

export default function OverlayControls({
  overlays,
  setOverlays,
  // M10 — saved-result selector
  mode,
  setMode,
  availableModes,
  // M11 — interactive ROI controls
  interactionMode,            // "seed" | "roi"
  setInteractionMode,
  roi,                         // { center: [x,y,z], radius } | null
  setRoiRadius,
  onRunRoi,
  onClearRoi,
  designPending,
  isLive,
  designError,
  // M12 — manual panel editing
  editMode,
  setEditMode,
  hasEdits,
  onClearEdits,
  editCounts,
}) {
  const showResultModeToggle =
    Array.isArray(availableModes) && availableModes.length > 1 && setMode;
  return (
    <div>
      {/* M11 — interaction mode + ROI workflow */}
      {setInteractionMode && (
        <div style={{ marginBottom: 14 }}>
          <h4>Interaction</h4>
          <label className="toggle-row">
            <input
              type="radio"
              name="interaction-mode"
              value="seed"
              checked={interactionMode === "seed"}
              onChange={() => setInteractionMode("seed")}
            />
            hover &amp; click to seed (M8)
          </label>
          <label className="toggle-row">
            <input
              type="radio"
              name="interaction-mode"
              value="roi"
              checked={interactionMode === "roi"}
              onChange={() => setInteractionMode("roi")}
            />
            pick ROI: click house, set radius (M11)
          </label>
          {interactionMode === "roi" && (
            <div style={{
              marginTop: 8, padding: 10, background: "#f1f5f9",
              border: "1px solid #cbd5e1", borderRadius: 4, fontSize: 12,
            }}>
              <div style={{ marginBottom: 6, color: "#475569" }}>
                {roi
                  ? <>ROI center: <strong>({roi.center[0].toFixed(1)}, {roi.center[1].toFixed(1)})</strong></>
                  : <>Click on the customer&apos;s roof in the 3D view.</>}
              </div>
              <label style={{ display: "block", marginBottom: 4 }}>
                radius: <strong>{roi ? roi.radius.toFixed(1) : "—"} m</strong>
              </label>
              <input
                type="range"
                min="5"
                max="40"
                step="0.5"
                value={roi?.radius ?? 15}
                onChange={(e) => setRoiRadius(parseFloat(e.target.value))}
                disabled={!roi}
                style={{ width: "100%" }}
              />
              <button
                onClick={onRunRoi}
                disabled={!roi || designPending}
                style={{
                  marginTop: 8, padding: "6px 10px", fontSize: 12,
                  cursor: !roi || designPending ? "not-allowed" : "pointer",
                  background: roi ? "#0e7490" : "#94a3b8",
                  color: "white", border: "none", borderRadius: 3, width: "100%",
                }}
              >
                {designPending ? "Computing…" : isLive ? "Re-run on ROI" : "Run on ROI"}
              </button>
              {(isLive || roi) && (
                <button
                  onClick={onClearRoi}
                  disabled={designPending}
                  style={{
                    marginTop: 4, padding: "4px 8px", fontSize: 11,
                    cursor: "pointer", background: "transparent",
                    color: "#475569", border: "1px solid #cbd5e1",
                    borderRadius: 3, width: "100%",
                  }}
                >
                  {isLive ? "Clear ROI &amp; revert to saved" : "Clear ROI marker"}
                </button>
              )}
              {designError && (
                <div className="error-banner" style={{ marginTop: 6, fontSize: 11 }}>
                  {designError}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {showResultModeToggle && (
        <div style={{ marginBottom: 10 }}>
          <h4>Saved result</h4>
          {availableModes.map((m) => (
            <label className="toggle-row" key={m}>
              <input
                type="radio"
                name="result-mode"
                value={m}
                checked={mode === m}
                onChange={() => setMode(m)}
              />
              {m === "selected" ? "Selected building" : "Tile-wide (debug)"}
            </label>
          ))}
        </div>
      )}

      {setEditMode && (
        <div style={{ marginBottom: 14 }}>
          <h4>Edit panels (M12)</h4>
          {[
            ["off", "off"],
            ["add", "add panel (hover preview, click)"],
            ["remove", "remove panel (click panel)"],
            ["move", "move panel (click to select, ←↑→↓)"],
          ].map(([value, label]) => (
            <label className="toggle-row" key={value}>
              <input
                type="radio"
                name="edit-mode"
                value={value}
                checked={editMode === value}
                onChange={() => setEditMode(value)}
              />
              {label}
            </label>
          ))}
          {hasEdits && (
            <div style={{ marginTop: 6, fontSize: 11, color: "#475569" }}>
              <div>+{editCounts?.added ?? 0} added · −{editCounts?.removed ?? 0} removed</div>
              <button
                onClick={onClearEdits}
                style={{
                  marginTop: 4, padding: "4px 8px", fontSize: 11,
                  cursor: "pointer", background: "transparent",
                  color: "#475569", border: "1px solid #cbd5e1",
                  borderRadius: 3, width: "100%",
                }}
              >
                Reset to AI proposal
              </button>
            </div>
          )}
        </div>
      )}

      <h4>Overlays</h4>
      {Object.keys(LABELS).map((k) => (
        <label className="toggle-row" key={k}>
          <input
            type="checkbox"
            checked={Boolean(overlays[k])}
            onChange={(e) => setOverlays({ ...overlays, [k]: e.target.checked })}
          />
          {LABELS[k]}
        </label>
      ))}
    </div>
  );
}
