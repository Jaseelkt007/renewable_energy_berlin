"""M4 visual test — render rejected vs kept faces side by side.

For each GLB, produce out/<stem>.candidates.png:
  Left  panel: rejected faces (light grey)
  Right panel: kept candidate faces (red), with rejected as faint background

Usage:
    python scripts/visualize_candidates.py                # all *.glb in cwd
    python scripts/visualize_candidates.py "3D_Modell Hamburg.glb"
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from roof3d.candidates import select_roof_candidates
from roof3d.loader import load_glb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)


def render_one(glb_path: Path) -> None:
    g = load_glb(glb_path)
    V, F = g.mesh.vertices, g.mesh.faces
    res = select_roof_candidates(g.mesh)
    print(f"\n{glb_path.name}: {res.rejection_reasons}")

    z_face = V[:, 2][F].mean(axis=1)
    kept_F = F[res.mask]
    rej_F = F[~res.mask]

    fig, axes = plt.subplots(1, 2, figsize=(13, 7), dpi=130)

    ax = axes[0]
    ax.tripcolor(V[:, 0], V[:, 1], rej_F, facecolors=z_face[~res.mask],
                 cmap="Greys_r", shading="flat", linewidth=0)
    ax.set_aspect("equal")
    ax.set_title(f"rejected ({(~res.mask).sum():,} faces)")
    ax.set_xlabel("X (m)"); ax.set_ylabel("Y (m)")

    ax = axes[1]
    # Faint background for context
    ax.tripcolor(V[:, 0], V[:, 1], rej_F, facecolors=np.full((~res.mask).sum(), 1.0),
                 cmap="Greys", shading="flat", linewidth=0, vmin=0, vmax=1, alpha=0.25)
    if len(kept_F):
        ax.tripcolor(V[:, 0], V[:, 1], kept_F,
                     facecolors=np.full(res.mask.sum(), 0.7),
                     cmap="Reds", shading="flat", linewidth=0, vmin=0, vmax=1)
    ax.set_aspect("equal")
    pct = 100.0 * res.mask.mean()
    ax.set_title(f"kept candidates ({res.mask.sum():,} = {pct:.1f}%)")
    ax.set_xlabel("X (m)"); ax.set_ylabel("Y (m)")

    fig.suptitle(f"M4 candidates — {glb_path.name}")
    fig.tight_layout()
    out_path = OUT / f"{glb_path.stem}.candidates.png"
    fig.savefig(out_path)
    plt.close(fig)
    print(f"  wrote {out_path}")


def main() -> None:
    if len(sys.argv) > 1:
        paths = [Path(p) for p in sys.argv[1:]]
    else:
        paths = sorted(ROOT.glob("*.glb"))
    for p in paths:
        render_one(p)


if __name__ == "__main__":
    main()
