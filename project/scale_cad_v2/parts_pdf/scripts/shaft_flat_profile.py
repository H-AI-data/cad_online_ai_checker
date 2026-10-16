"""Recognize a local milled flat on the outside of a cylindrical shaft."""

from __future__ import annotations

from collections import defaultdict

import numpy as np


def milled_shaft_flats(mesh, circles, dimensions):
    """Return measured shaft flats tied to STEP cylinder centerlines.

    A real flat is a broad coplanar patch recessed below the cylinder radius
    for only part of that cylinder's axial span. Narrow tessellation facets on
    the round wall fail the chord-width gate.
    """
    triangles = np.asarray(mesh.triangles, dtype=float)
    normals = np.asarray(mesh.face_normals, dtype=float)
    areas = np.asarray(mesh.area_faces, dtype=float)
    dims = np.asarray(dimensions, dtype=float)
    patches = defaultdict(list)
    for index, normal in enumerate(normals):
        if areas[index] < .05:
            continue
        axis = int(np.argmin(np.abs(normal)))
        if abs(normal[axis]) > .02:
            continue
        offset = float(np.dot(normal, triangles[index].mean(axis=0)))
        key = (axis, *np.round(normal, 3), round(offset, 2))
        patches[key].append(index)
    found = []
    for indices in patches.values():
        normal = np.mean(normals[indices], axis=0)
        normal /= np.linalg.norm(normal)
        axis = int(np.argmin(np.abs(normal)))
        transverse = [value for value in range(3) if value != axis]
        points = triangles[indices].reshape(-1, 3)
        start, end = float(points[:, axis].min()), float(points[:, axis].max())
        length = end - start
        if length < max(.5, .03 * dims[axis]):
            continue
        tangent = np.array([-normal[transverse[1]], normal[transverse[0]]])
        chord = float(np.ptp(points[:, transverse] @ tangent))
        if chord <= 0:
            continue
        patch_area = float(areas[indices].sum())
        if patch_area < .8 * length * chord:
            continue
        for circle in circles:
            direction = np.asarray(circle["axis"], dtype=float)
            if abs(direction[axis]) < .985:
                continue
            levels = sorted(float(value) for value in circle["axial_levels"])
            if len(levels) < 2 or start < levels[0] - .1 or end > levels[-1] + .1:
                continue
            stage_length = levels[-1] - levels[0]
            if length > .8 * stage_length:
                continue
            diameter = float(circle["diameter"])
            if chord < max(.5, .4 * diameter):
                continue
            center = np.asarray(circle["center"], dtype=float)
            radius = diameter / 2
            distance = float(np.dot(normal, points.mean(axis=0) - center))
            depth = radius - distance
            if not max(.08, .02 * diameter) <= depth <= .8 * radius:
                continue
            found.append({
                "axis": axis,
                "start": round(start, 3),
                "end": round(end, 3),
                "length": round(length, 3),
                "width": round(chord, 3),
                "depth": round(depth, 3),
                "diameter": round(diameter, 3),
                "normal": np.round(normal, 5).tolist(),
                "area": round(patch_area, 3),
                "bounds_min": np.round(points.min(axis=0), 3).tolist(),
                "bounds_max": np.round(points.max(axis=0), 3).tolist(),
            })
    unique = {}
    for flat in found:
        key = (flat["axis"], flat["start"], flat["end"],
               flat["width"], flat["depth"])
        if key not in unique or flat["area"] > unique[key]["area"]:
            unique[key] = flat
    return sorted(unique.values(), key=lambda item: (item["axis"], item["start"]))
