"""GLB loader with Draco decompression and CESIUM_RTC awareness.

The hackathon GLBs are Draco-compressed photogrammetry tiles (KHR_draco_mesh_compression)
with a CESIUM_RTC center offset. Plain `trimesh.load` returns zero-extent geometry because
it cannot decode Draco. This module decodes the buffers, applies node transforms and the
RTC offset (optional), and returns a single concatenated trimesh.Trimesh in *local* model
coordinates (RTC offset NOT applied by default, so coordinates stay small and frontend-
friendly).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import DracoPy
import numpy as np
import pygltflib
import trimesh


@dataclass
class LoadedGLB:
    mesh: trimesh.Trimesh
    rtc_center: tuple[float, float, float] | None
    extensions_required: list[str] = field(default_factory=list)
    primitive_count: int = 0
    raw_min: np.ndarray | None = None
    raw_max: np.ndarray | None = None


def _node_transform(gltf: pygltflib.GLTF2, node_idx: int, parent: np.ndarray) -> np.ndarray:
    node = gltf.nodes[node_idx]
    M = np.array(node.matrix, dtype=np.float64).reshape(4, 4).T if node.matrix else np.eye(4)
    if not node.matrix:
        T = np.eye(4)
        if node.translation:
            T[:3, 3] = node.translation
        R = np.eye(4)
        if node.rotation:
            x, y, z, w = node.rotation
            R[:3, :3] = trimesh.transformations.quaternion_matrix([w, x, y, z])[:3, :3]
        S = np.eye(4)
        if node.scale:
            S[:3, :3] = np.diag(node.scale)
        M = T @ R @ S
    return parent @ M


def _walk_scene(gltf: pygltflib.GLTF2, scene_idx: int = 0):
    """Yield (node, world_transform) for every node that references a mesh."""
    scene = gltf.scenes[scene_idx]
    stack = [(n, np.eye(4)) for n in scene.nodes]
    while stack:
        node_idx, parent = stack.pop()
        node = gltf.nodes[node_idx]
        world = _node_transform(gltf, node_idx, parent)
        if node.mesh is not None:
            yield node, world
        for child in node.children or []:
            stack.append((child, world))


def load_glb(path: str | Path, apply_rtc: bool = False) -> LoadedGLB:
    path = Path(path)
    gltf = pygltflib.GLTF2().load(str(path))
    blob = gltf.binary_blob() or b""

    rtc_center: tuple[float, float, float] | None = None
    if gltf.extensions and "CESIUM_RTC" in gltf.extensions:
        c = gltf.extensions["CESIUM_RTC"].get("center")
        if c is not None:
            rtc_center = (float(c[0]), float(c[1]), float(c[2]))

    all_verts: list[np.ndarray] = []
    all_faces: list[np.ndarray] = []
    all_normals: list[np.ndarray] = []
    vert_offset = 0
    prim_count = 0

    for node, world in _walk_scene(gltf):
        mesh = gltf.meshes[node.mesh]
        for prim in mesh.primitives:
            ext = (prim.extensions or {}).get("KHR_draco_mesh_compression")
            if ext is not None:
                bv = gltf.bufferViews[ext["bufferView"]]
                start = bv.byteOffset or 0
                data = blob[start : start + bv.byteLength]
                dm = DracoPy.decode(data)
                verts = np.asarray(dm.points, dtype=np.float64)
                faces = np.asarray(dm.faces, dtype=np.int64).reshape(-1, 3)
                normals = np.asarray(dm.normals, dtype=np.float64) if getattr(dm, "normals", None) is not None and len(dm.normals) else None
            else:
                # Fallback: read uncompressed POSITION accessor
                pos_idx = prim.attributes.POSITION
                acc = gltf.accessors[pos_idx]
                bv = gltf.bufferViews[acc.bufferView]
                start = (bv.byteOffset or 0) + (acc.byteOffset or 0)
                count = acc.count
                verts = np.frombuffer(blob, dtype=np.float32, count=count * 3, offset=start).reshape(-1, 3).astype(np.float64)
                # indices
                if prim.indices is not None:
                    iacc = gltf.accessors[prim.indices]
                    ibv = gltf.bufferViews[iacc.bufferView]
                    istart = (ibv.byteOffset or 0) + (iacc.byteOffset or 0)
                    dtype = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32}[iacc.componentType]
                    faces = np.frombuffer(blob, dtype=dtype, count=iacc.count, offset=istart).reshape(-1, 3).astype(np.int64)
                else:
                    faces = np.arange(count, dtype=np.int64).reshape(-1, 3)
                normals = None

            if len(verts) == 0 or len(faces) == 0:
                continue

            verts_world = trimesh.transformations.transform_points(verts, world)
            all_verts.append(verts_world)
            all_faces.append(faces + vert_offset)
            if normals is not None and len(normals) == len(verts):
                # rotate normals (use upper-3x3 of transform; ignore non-uniform scale for hackathon)
                R = world[:3, :3]
                n_world = normals @ R.T
                all_normals.append(n_world)
            else:
                all_normals.append(np.zeros((0, 3)))
            vert_offset += len(verts)
            prim_count += 1

    if not all_verts:
        raise RuntimeError(f"no decodable primitives in {path}")

    V = np.vstack(all_verts)
    F = np.vstack(all_faces)

    raw_min = V.min(axis=0).copy()
    raw_max = V.max(axis=0).copy()

    if apply_rtc and rtc_center is not None:
        V = V + np.array(rtc_center)

    mesh = trimesh.Trimesh(vertices=V, faces=F, process=False)

    return LoadedGLB(
        mesh=mesh,
        rtc_center=rtc_center,
        extensions_required=list(gltf.extensionsRequired or []),
        primitive_count=prim_count,
        raw_min=raw_min,
        raw_max=raw_max,
    )


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        r = load_glb(p)
        m = r.mesh
        print(f"{Path(p).name}: {len(m.vertices)} verts, {len(m.faces)} faces, "
              f"extent={m.extents}, rtc={r.rtc_center}")
