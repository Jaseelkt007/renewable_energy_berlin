"use client";

export default function SummaryPanel({ roof }) {
  if (!roof) return null;
  const s = roof.summary || {};
  const fmt = (v, suffix = "") => (v == null ? "—" : `${v}${suffix}`);
  const angle = (a, t) =>
    a == null || t == null ? "—" : `${Number(a).toFixed(1)}° / ${Number(t).toFixed(1)}°`;

  const cards = [
    ["Panel count", fmt(s.panel_count)],
    ["System kWp", fmt(s.system_kwp)],
    ["Module Wp", fmt(s.module_wp)],
    ["Roof planes", fmt(roof.roof_planes?.length ?? 0)],
    ["Obstructions", fmt(roof.obstructions?.length ?? 0)],
    ["Best plane", fmt(s.best_plane_id)],
    ["Best az / tilt", angle(s.best_plane_azimuth, s.best_plane_tilt)],
    ["Method", fmt(s.method)],
    ["Confidence", fmt(s.confidence)],
  ];

  return (
    <div style={{ marginTop: 12 }}>
      <h4>Summary</h4>
      {cards.map(([k, v]) => (
        <div className="kv-row" key={k}>
          <span>{k}</span>
          <strong>{String(v)}</strong>
        </div>
      ))}
      {Array.isArray(s.warnings) && s.warnings.length > 0 && (
        <div className="warn-banner" style={{ marginTop: 6 }}>
          {s.warnings.join(" · ")}
        </div>
      )}
    </div>
  );
}
