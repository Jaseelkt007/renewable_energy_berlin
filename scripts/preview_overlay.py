"""M2 visual test — overlay a roof JSON onto the GLB top-down render.

This is the offline analog of the frontend rendering panels on top of the GLB:
load the GLB, render it from the top in elevation colors, then plot the roof
polygon (orange), usable polygon (yellow), and panel rectangles (red) on top.
If panels appear inside the building footprint and at sane scale, the
coordinate frame contract is consistent.

Usage: python scripts/preview_overlay.py out/mock_hamburg.roof.json
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
from roof3d.contract import RoofDesign
from roof3d.loader import load_glb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"


def main(json_path: Path) -> None:
    design = RoofDesign.from_json(json_path.read_text())
    glb_path = ROOT / design.model_file
    g = load_glb(glb_path)
    V, F = g.mesh.vertices, g.mesh.faces

    fig, ax = plt.subplots(figsize=(8, 8), dpi=130)
    z_face = V[:, 2][F].mean(axis=1)
    ax.tripcolor(V[:, 0], V[:, 1], F, facecolors=z_face,
                 cmap="Greys_r", shading="flat", linewidth=0, alpha=0.85)

    for plane in design.roof_planes:
        poly = np.array(plane.polygon_3d)[:, :2]
        ax.add_patch(Polygon(poly, closed=True, fill=False,
                             edgecolor="orange", linewidth=2.0,
                             label=f"plane {plane.id}"))
        upoly = np.array(plane.usable_polygon_3d)[:, :2]
        ax.add_patch(Polygon(upoly, closed=True, fill=False,
                             edgecolor="gold", linewidth=1.2, linestyle="--",
                             label="usable"))

    for panel in design.panels:
        c = np.array(panel.corners_3d)[:, :2]
        ax.add_patch(Polygon(c, closed=True, facecolor="red",
                             edgecolor="darkred", linewidth=0.4, alpha=0.85))

    ax.set_aspect("equal")
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Y (m)")
    ax.set_title(
        f"{design.model_file}  |  {len(design.panels)} panels, "
        f"{design.summary.system_kwp} kWp  ({design.roof_planes[0].source})"
    )
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()

    out_path = OUT / f"{json_path.stem}.overlay.png"
    fig.savefig(out_path)
    plt.close(fig)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "mock_hamburg.roof.json"
    main(p)
