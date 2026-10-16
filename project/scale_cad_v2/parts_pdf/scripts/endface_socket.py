"""Find a recessed regular hexagon in an axial end face."""

from __future__ import annotations

import numpy as np
from scipy.spatial import ConvexHull


def recessed_hex_sockets(mesh, circles, dimensions):
    """Measure across-flats from a verified inner mesh section.

    The analytic outer cylinder supplies the axis and center.  A section near
    its end must contain a distinct six-sided inner loop; a round bore cannot
    pass the six-corner and equal-radius checks.
    """
    dims = np.asarray(dimensions, dtype=float)
    found = []
    for circle in circles:
        diameter = float(circle["diameter"])
        if diameter < 3 or len(circle["axial_levels"]) != 2:
            continue
        direction = np.asarray(circle["axis"], dtype=float)
        axis = int(np.argmax(np.abs(direction)))
        if abs(direction[axis]) < .985:
            continue
        lo, hi = sorted(float(value) for value in circle["axial_levels"])
        span = hi - lo
        if span < .3:
            continue
        at_high = dims[axis] - hi <= max(.3, .15 * diameter)
        at_low = lo <= max(.3, .15 * diameter)
        if not (at_high or at_low):
            continue
        station = lo + (.8 if at_high else .2) * span
        origin = np.asarray(circle["center"], dtype=float).copy()
        origin[axis] = station
        section = mesh.section(plane_origin=origin,
                               plane_normal=np.eye(3)[axis])
        if section is None:
            continue
        transverse = [value for value in range(3) if value != axis]
        center = origin[transverse]
        for loop in section.discrete:
            points = np.asarray(loop[:, transverse], dtype=float)
            if len(points) < 6:
                continue
            radial = np.linalg.norm(points-center, axis=1)
            if radial.max() > .78 * diameter / 2:
                continue
            hull = ConvexHull(points)
            vertices = points[hull.vertices]
            if len(vertices) != 6:
                continue
            radii = np.linalg.norm(vertices-center, axis=1)
            if (np.ptp(radii) > .035 * float(np.mean(radii)) or
                    np.linalg.norm(vertices.mean(axis=0)-center) >
                    .03 * diameter):
                continue
            edges = np.roll(vertices, -1, axis=0)-vertices
            lengths = np.linalg.norm(edges, axis=1)
            if lengths.min() < .85 * lengths.max():
                continue
            normals = np.stack((-edges[:, 1], edges[:, 0]), axis=1)
            normals /= np.linalg.norm(normals, axis=1)[:, None]
            widths = np.ptp(vertices @ normals.T, axis=0)
            width = float(widths.min())
            if not (.15 * diameter < width < .9 * diameter):
                continue
            equal = np.flatnonzero(widths <= width + max(.02, .01 * width))
            best = max(equal, key=lambda index: abs(normals[index, 1]))
            normal = normals[best]
            if normal[1] < 0:
                normal = -normal
            found.append({
                "axis": axis,
                "across_flats": round(width, 3),
                "center": np.round(center, 5).tolist(),
                "normal": np.round(normal, 5).tolist(),
                "outer_diameter": round(diameter, 3),
                "section_station": round(station, 3),
            })
            break
    unique = {}
    for item in found:
        key = (item["axis"], item["across_flats"])
        unique[key] = item
    return list(unique.values())
