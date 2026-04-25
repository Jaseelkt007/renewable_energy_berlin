"""M5 visual test — render detected roof planes in distinct colors.

For each GLB:
  - run candidates + cluster_planes
  - render top-down with the bare mesh in light grey
  - plot each plane's polygon in a distinct color, labelled with plane_id
  - print a summary table

Usage:
    python scripts/visualize_planes.py                       # all *.glb
    python scripts/visualize_planes.py "3D_Modell Hamburg.glb"
    python scripts/visualize_planes.py --top 12              # only N largest
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.cm as cm
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roof3d.candidates import select_roof_candidates
from roof3d.loader import load_glb
from roof3d.planes import cluster_planes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)


def render_one(glb_path: Path, top_n: int) -> None:
    g = load_glb(glb_path)
    cand = select_roof_candidates(g.mesh)
    planes = cluster_planes(g.mesh, cand)
    print(f"\n=== {glb_path.name} ===")
    print(f"candidates: {cand.mask.sum()} / {len(g.mesh.faces)} faces")
    print(f"detected planes: {len(planes)} (showing top {min(top_n, len(planes))})")
    print(f"  {'id':<10}{'tilt':>6}{'az':>7}{'area':>8}{'conf':>6}  reasons (a/n/s/p)   method     n_faces")
    for p in planes[:top_n]:
        r = p.confidence_reasons
        flags = "".join("y" if r[k] else "." for k in
                        ["area_large_enough", "normal_stable", "substantive", "polygon_clean"])
        print(f"  {p.plane_id:<10}{p.tilt_deg:6.1f}{p.azimuth_deg:7.1f}{p.area_m2:8.1f}"
              f"{p.confidence:6.2f}  {flags:<19}{p.boundary_method:<11}{len(p.face_indices):>6}")

    V, F = g.mesh.vertices, g.mesh.faces
    fig, ax = plt.subplots(figsize=(10, 10), dpi=130)
    ax.tripcolor(V[:, 0], V[:, 1], F, facecolors=np.full(len(F), 0.85),
                 cmap="Greys", shading="flat", linewidth=0, vmin=0, vmax=1)

    cmap = cm.get_cmap("tab20", max(20, top_n))
    for i, p in enumerate(planes[:top_n]):
        poly = np.array(p.polygon_3d)[:, :2]
        ax.add_patch(Polygon(poly, closed=True, fill=True,
                             facecolor=cmap(i % 20), edgecolor="black",
                             linewidth=0.6, alpha=0.65))
        cx, cy = poly[:, 0].mean(), poly[:, 1].mean()
        ax.text(cx, cy, p.plane_id.replace("roof_", "r"),
                ha="center", va="center", fontsize=7, color="black")

    ax.set_aspect("equal")
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_title(f"M5 detected planes — {glb_path.name}  "
                 f"({len(planes)} total, top {min(top_n, len(planes))} shown)")
    fig.tight_layout()
    out_path = OUT / f"{glb_path.stem}.planes.png"
    fig.savefig(out_path)
    plt.close(fig)
    print(f"  wrote {out_path}")


def main() -> None:
    args = sys.argv[1:]
    top_n = 20
    paths: list[Path] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--top":
            top_n = int(args[i + 1]); i += 2
        else:
            paths.append(Path(a)); i += 1
    if not paths:
        paths = sorted(ROOT.glob("*.glb"))

    for p in paths:
        render_one(p, top_n)


if __name__ == "__main__":
    main()
