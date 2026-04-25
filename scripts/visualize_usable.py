"""M6 visual test — show raw / usable / bump polygons for top planes.

For each GLB run M4 -> M5 -> M6 on the top N largest planes and render:
  - mesh in light grey (top-down)
  - raw plane polygon in orange outline
  - usable polygon (after eaves erosion + bump subtraction) in gold fill
  - detected bumps in red

Also prints a per-plane table (raw area, usable area, n_bumps, bump area, %loss).

Usage:
    python scripts/visualize_usable.py
    python scripts/visualize_usable.py "3D_Modell Hamburg.glb" --top 8
    python scripts/visualize_usable.py --no-bumps          # eaves only
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roof3d.candidates import select_roof_candidates
from roof3d.loader import load_glb
from roof3d.planes import cluster_planes
from roof3d.usable import compute_usable

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)


def render_one(glb_path: Path, top_n: int, detect_bumps: bool) -> None:
    g = load_glb(glb_path)
    cand = select_roof_candidates(g.mesh)
    planes = cluster_planes(g.mesh, cand)
    print(f"\n=== {glb_path.name} ===")
    print(f"  total planes: {len(planes)}, processing top {min(top_n, len(planes))}")
    print(f"  {'id':<8}{'raw':>8}{'usable':>9}{'%loss':>7}{'bumps':>7}{'bump_a':>9}")

    fig, ax = plt.subplots(figsize=(11, 11), dpi=130)
    V, F = g.mesh.vertices, g.mesh.faces
    ax.tripcolor(V[:, 0], V[:, 1], F, facecolors=np.full(len(F), 0.88),
                 cmap="Greys", shading="flat", linewidth=0, vmin=0, vmax=1)

    raw_legend = used_legend = bump_legend = False
    for p in planes[:top_n]:
        u = compute_usable(g.mesh, p, detect_bumps=detect_bumps)
        raw_xy = np.array(u.raw_polygon_3d)[:, :2]
        ax.add_patch(Polygon(raw_xy, closed=True, fill=False,
                             edgecolor="orange", linewidth=1.4,
                             label=("raw" if not raw_legend else None)))
        raw_legend = True

        if u.usable_polygon_3d:
            usable_xy = np.array(u.usable_polygon_3d)[:, :2]
            ax.add_patch(Polygon(usable_xy, closed=True, fill=True,
                                 facecolor="gold", edgecolor="darkgoldenrod",
                                 linewidth=0.6, alpha=0.7,
                                 label=("usable" if not used_legend else None)))
            used_legend = True

        for b in u.bumps:
            bxy = np.array(b.polygon_3d)[:, :2]
            ax.add_patch(Polygon(bxy, closed=True, fill=True,
                                 facecolor="red", edgecolor="darkred",
                                 linewidth=0.4, alpha=0.85,
                                 label=("bump" if not bump_legend else None)))
            bump_legend = True

        loss_pct = 100.0 * (1.0 - (u.usable_area_m2 / u.raw_area_m2)) if u.raw_area_m2 > 0 else 0.0
        bump_area = sum(b.area_m2 for b in u.bumps)
        print(f"  {p.plane_id:<8}{u.raw_area_m2:8.1f}{u.usable_area_m2:9.1f}"
              f"{loss_pct:6.1f}%{len(u.bumps):>7}{bump_area:9.2f}")

    ax.set_aspect("equal")
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_title(f"M6 usable area — {glb_path.name}  "
                 f"(top {min(top_n, len(planes))} planes, "
                 f"bumps {'on' if detect_bumps else 'off'})")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()

    suffix = ".usable.png" if detect_bumps else ".usable_eavesonly.png"
    out_path = OUT / f"{glb_path.stem}{suffix}"
    fig.savefig(out_path)
    plt.close(fig)
    print(f"  wrote {out_path}")


def main() -> None:
    args = sys.argv[1:]
    top_n = 8
    detect_bumps = True
    paths: list[Path] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--top":
            top_n = int(args[i + 1]); i += 2
        elif a == "--no-bumps":
            detect_bumps = False; i += 1
        else:
            paths.append(Path(a)); i += 1
    if not paths:
        paths = sorted(ROOT.glob("*.glb"))
    for p in paths:
        render_one(p, top_n, detect_bumps)


if __name__ == "__main__":
    main()
