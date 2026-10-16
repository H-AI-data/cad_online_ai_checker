"""Cross-section helpers shared by longitudinal part screens."""

import numpy as np

from flex_probe import loop_metrics


def point_inside(point, polygon):
    vertices = np.asarray(polygon)[:, :2]
    x, y = point
    first, second = vertices[:-1], vertices[1:]
    crossings = ((first[:, 1] > y) != (second[:, 1] > y)) & (
        x < (second[:, 0] - first[:, 0]) * (y - first[:, 1]) /
        (second[:, 1] - first[:, 1] + 1e-12) + first[:, 0])
    return bool(np.count_nonzero(crossings) % 2)


def solid_sections(mesh, axis, fraction):
    """Return exterior solid loops, excluding loops nested as holes."""
    origin = mesh.bounds[0].copy()
    origin[axis] += mesh.extents[axis] * fraction
    section = mesh.section(plane_origin=origin, plane_normal=np.eye(3)[axis])
    if section is None:
        return []
    loops = []
    for path in section.discrete:
        metric = loop_metrics(path, axis)
        if metric and metric['area'] > 1e-4:
            polygon = np.asarray(path)[:, [i for i in range(3) if i != axis]]
            loops.append((metric, polygon))
    solids = []
    for index, (metric, _) in enumerate(loops):
        nested = any(
            other_index != index and other['area'] > metric['area']
            and point_inside(metric['center'], other_polygon)
            for other_index, (other, other_polygon) in enumerate(loops)
        )
        if not nested:
            solids.append(metric)
    return sorted(solids, key=lambda item: item['area'], reverse=True)
