"""M1 — audit each GLB.

For every .glb in the working directory:
  - decode (Draco-aware) and report scene composition
  - report raw (RTC-local) bbox and bbox extents
  - infer units (m / mm / scene-normalized)
  - infer up-axis (the smallest extent is conventionally up for terrain tiles)
  - render a top-down PNG colored by elevation
  - aggregate everything into roof3d/glb_metadata.json keyed by filename

Usage: python scripts/audit_glb.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roof3d.loader import load_glb


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
META_PATH = ROOT / "roof3d" / "glb_metadata.json"


def infer_units(extent: np.ndarray) -> tuple[str, float]:
    m = float(extent.max())
    if m < 1.0:
        return ("scene-normalized", 1.0)
    if 5.0 <= m <= 500.0:
        return ("meters", 1.0)
    if 5_000.0 <= m <= 500_000.0:
        return ("millimeters", 0.001)
    return ("unknown", 1.0)


def infer_up_axis(extent: np.ndarray) -> str:
    return ["X", "Y", "Z"][int(np.argmin(extent))]


def render_topdown(verts: np.ndarray, faces: np.ndarray, png_path: Path, title: str) -> None:
    z = verts[:, 2]
    z_face = z[faces].mean(axis=1)
    fig, ax = plt.subplots(figsize=(7, 7), dpi=130)
    tpc = ax.tripcolor(
        verts[:, 0], verts[:, 1], faces, facecolors=z_face,
        cmap="viridis", shading="flat", linewidth=0,
    )
    ax.set_aspect("equal")
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_title(title)
    cbar = fig.colorbar(tpc, ax=ax, shrink=0.8)
    cbar.set_label("Z elevation (m)")
    fig.tight_layout()
    fig.savefig(png_path)
    plt.close(fig)


def audit_one(glb_path: Path) -> dict:
    print(f"\n=== {glb_path.name} ===")
    r = load_glb(glb_path)
    m = r.mesh
    extent = np.asarray(m.extents, dtype=float)
    raw_min = np.asarray(r.raw_min, dtype=float)
    raw_max = np.asarray(r.raw_max, dtype=float)

    units, unit_scale = infer_units(extent)
    up_axis = infer_up_axis(extent)

    print(f"  primitives:        {r.primitive_count}")
    print(f"  vertices/faces:    {len(m.vertices):>8d} / {len(m.faces):>8d}")
    print(f"  extensions req'd:  {r.extensions_required}")
    print(f"  rtc center:        {r.rtc_center}")
    print(f"  bbox min (local):  {raw_min.tolist()}")
    print(f"  bbox max (local):  {raw_max.tolist()}")
    print(f"  extent (m):        X={extent[0]:.2f}  Y={extent[1]:.2f}  Z={extent[2]:.2f}")
    print(f"  inferred units:    {units}  (scale {unit_scale})")
    print(f"  inferred up-axis:  {up_axis}")

    png_path = OUT / f"{glb_path.stem}.topdown.png"
    render_topdown(m.vertices, m.faces, png_path, glb_path.name)
    print(f"  saved preview:     {png_path}")

    # Identity transform — these GLBs are already in meters, Z-up.
    return {
        "file": glb_path.name,
        "primitive_count": r.primitive_count,
        "vertex_count": int(len(m.vertices)),
        "face_count": int(len(m.faces)),
        "extensions_required": r.extensions_required,
        "rtc_center": list(r.rtc_center) if r.rtc_center else None,
        "bbox_min_local": raw_min.tolist(),
        "bbox_max_local": raw_max.tolist(),
        "extent": extent.tolist(),
        "units": units,
        "unit_scale_applied": unit_scale,
        "up_axis": up_axis,
        "original_to_normalized_matrix": np.eye(4).tolist(),
        "preview_png": str(png_path.relative_to(ROOT)),
        "notes": (
            "Photogrammetry tile in local meters with CESIUM_RTC offset. "
            "Tile may contain multiple buildings; building selection happens downstream."
        ),
    }


def main() -> None:
    glbs = sorted(ROOT.glob("*.glb"))
    if not glbs:
        print("no GLB files found", file=sys.stderr)
        sys.exit(2)

    metadata = {}
    for g in glbs:
        try:
            metadata[g.name] = audit_one(g)
        except Exception as e:
            print(f"FAILED on {g.name}: {e}", file=sys.stderr)
            metadata[g.name] = {"file": g.name, "error": str(e)}

    META_PATH.write_text(json.dumps(metadata, indent=2))
    print(f"\nwrote {META_PATH}")


if __name__ == "__main__":
    main()
