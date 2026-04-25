"""Smoke-test loader: print quick stats for one or more GLB files.

Usage:
    python scripts/inspect_glb.py "/path/to/model.glb" [more.glb ...]
    python scripts/inspect_glb.py            # auto-discovers *.glb in cwd
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import trimesh


def inspect(path: Path) -> None:
    print(f"\n=== {path.name} ===")
    print(f"file size: {path.stat().st_size / 1e6:.2f} MB")

    scene = trimesh.load(path, force="scene")
    if not isinstance(scene, trimesh.Scene):
        scene = trimesh.Scene(scene)

    print(f"scene nodes:    {len(scene.graph.nodes)}")
    print(f"geometries:     {len(scene.geometry)}")
    print(f"materials:      {sum(1 for g in scene.geometry.values() if getattr(g.visual, 'material', None) is not None)}")

    raw_bbox = scene.bounds  # already includes graph transforms
    raw_extent = raw_bbox[1] - raw_bbox[0]
    print(f"scene bbox min: {raw_bbox[0]}")
    print(f"scene bbox max: {raw_bbox[1]}")
    print(f"scene extent:   {raw_extent}  (max={raw_extent.max():.3f})")

    baked = scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)
    if isinstance(baked, list):
        baked = trimesh.util.concatenate(baked)
    print(f"baked vertices: {len(baked.vertices)}")
    print(f"baked faces:    {len(baked.faces)}")
    baked_extent = baked.extents
    print(f"baked extent:   {baked_extent}  (max={baked_extent.max():.3f})")


def main() -> None:
    args = [Path(p) for p in sys.argv[1:]]
    if not args:
        args = sorted(Path.cwd().glob("*.glb"))
    if not args:
        print("no GLB files given or found in cwd", file=sys.stderr)
        sys.exit(2)
    for p in args:
        try:
            inspect(p)
        except Exception as e:
            print(f"\n=== {p.name} === FAILED: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
