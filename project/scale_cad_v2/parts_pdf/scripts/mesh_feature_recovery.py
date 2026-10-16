"""Recover circular projection edges omitted by the STEP cylinder cache.

Conical rims and short counterbores may be visible in TOP even when they do
not bound a cylindrical face.  The aligned mesh is the same geometry used to
draw the line views, so near-face section loops provide their actual sizes.
"""

from __future__ import annotations

import numpy as np
import trimesh


def top_circular_rims(mesh_path, existing):
    """Return well-closed, near-circular horizontal rims absent from *existing*."""
    mesh = trimesh.load_mesh(mesh_path, process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"Expected one aligned mesh: {mesh_path}")
    low, high = mesh.bounds[:, 2]
    span = high - low
    if span <= 0:
        return []
    inset = min(max(span * .0001, .00001), .001)
    candidates = []
    for level in (high - inset, low + inset):
        section = mesh.section(plane_origin=(0, 0, level),
                               plane_normal=(0, 0, 1))
        if section is None:
            continue
        for loop in section.discrete:
            points = np.asarray(loop, float)[:, :2]
            if len(points) < 24 or np.linalg.norm(points[0] - points[-1]) > .05:
                continue
            points = points[:-1]
            center = (points.min(axis=0) + points.max(axis=0)) / 2
            radii = np.linalg.norm(points - center, axis=1)
            radius = float(np.median(radii))
            diameter = 2 * radius
            if (diameter < 3 or diameter > 1.02 * min(mesh.extents[:2]) or
                    np.max(np.abs(radii - radius)) > max(.06, .012 * radius)):
                continue
            # Snap only a mesh-tessellation error near a tenth-mm nominal;
            # larger differences remain measured rather than invented.
            nominal = round(diameter, 1)
            if abs(diameter - nominal) <= .05:
                diameter = nominal
            if any(abs(2 * old["radius"] - diameter) < .12 and
                   np.linalg.norm(np.asarray(old["center"][:2]) - center) < .12
                   for old in [*existing, *candidates]):
                continue
            candidates.append({"radius": diameter / 2,
                               "axis": [0., 0., 1.],
                               "center": [float(center[0]), float(center[1]), float(level)],
                               "rim": [float(center[0] + radius), float(center[1]),
                                       float(level)]})
    return candidates
