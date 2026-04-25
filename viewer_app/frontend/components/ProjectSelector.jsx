"use client";

export default function ProjectSelector({ projects, selected, onSelect }) {
  if (!projects || projects.length === 0) {
    return <div style={{ fontSize: 12, color: "#6b7280" }}>Loading projects…</div>;
  }
  return (
    <select
      style={{
        width: "100%",
        padding: "6px 8px",
        marginTop: 6,
        border: "1px solid #cbd5e1",
        borderRadius: 4,
        background: "#fff",
      }}
      value={selected?.project_id || ""}
      onChange={(e) => {
        const next = projects.find((p) => p.project_id === e.target.value);
        if (next) onSelect(next);
      }}
    >
      {projects.map((p) => (
        <option key={p.project_id} value={p.project_id}>
          {p.label}
        </option>
      ))}
    </select>
  );
}
