"use client";

const LABELS = {
  showModel: "show GLB model",
  showRawPlanes: "show raw roof polygons",
  showUsable: "show usable polygons",
  showObstructions: "show obstructions / bumps",
  showPanels: "show solar panels",
};

export default function OverlayControls({ overlays, setOverlays }) {
  return (
    <div>
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
