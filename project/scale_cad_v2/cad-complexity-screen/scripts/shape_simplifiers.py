"""Conservative geometric checks for repeatable, easy-to-annotate shapes.

These checks remove a weak screening reason only when it is that family's sole
reason.  They do not override text, dense-hole, curved-profile or other cues.
"""

import numpy as np

from flex_probe import loop_metrics


def simple_annular_ring(mesh, euler):
    """Thin, nearly circular ring with only a few concentric radial levels."""
    if euler != 0:
        return False
    dims = mesh.extents
    axis = int(np.argmin(dims))
    u, v = [i for i in range(3) if i != axis]
    diameter = max(dims[u], dims[v])
    if diameter <= 0 or min(dims[u], dims[v]) / diameter < .98:
        return False
    if dims[axis] / diameter > .30:
        return False
    center = (mesh.bounds[0] + mesh.bounds[1]) / 2
    radius = np.linalg.norm(mesh.vertices[:, [u, v]] - center[[u, v]], axis=1)
    outer_radius = float(radius.max())
    if outer_radius <= 0:
        return False
    # Quantization is relative to the ring size, not a fixed millimeter step.
    radial_levels = len(np.unique(np.rint(radius / (.005 * outer_radius))))
    return radial_levels <= 16


def simple_turned_cylinder(mesh, euler):
    """Nearly axisymmetric solid with a small number of radial steps."""
    if euler != 2:
        return False
    dims = mesh.extents
    axis = int(np.argmin(dims))
    u, v = [i for i in range(3) if i != axis]
    diameter = max(dims[u], dims[v])
    if diameter <= 0 or min(dims[u], dims[v]) / diameter < .98:
        return False
    if dims[axis] / diameter > 1.2:
        return False
    center = (mesh.bounds[0] + mesh.bounds[1]) / 2
    radius = np.linalg.norm(mesh.vertices[:, [u, v]] - center[[u, v]], axis=1)
    outer_radius = float(radius.max())
    if outer_radius <= 0:
        return False
    radial_levels = len(np.unique(np.rint(radius / (.005 * outer_radius))))
    return radial_levels <= 40


def regular_two_row_perforation(mesh):
    """Thin plate whose small equal through-holes form two uniform rows."""
    dims = mesh.extents
    axis = int(np.argmin(dims))
    u, v = [i for i in range(3) if i != axis]
    short_side = min(dims[u], dims[v])
    if short_side <= 0 or dims[axis] / short_side > .15:
        return False
    origin = mesh.bounds[0].copy()
    origin[axis] += dims[axis] * .5
    section = mesh.section(plane_origin=origin, plane_normal=np.eye(3)[axis])
    if section is None:
        return False
    loops = [metric for path in section.discrete
             if (metric := loop_metrics(path, axis)) is not None]
    if len(loops) < 11:
        return False
    loops.sort(key=lambda item: item['area'], reverse=True)
    outer_area = loops[0]['area']
    holes = loops[1:]
    areas = np.asarray([hole['area'] for hole in holes])
    if outer_area <= 0 or areas.max() / outer_area > .002:
        return False
    if areas.std() / areas.mean() > .10:
        return False
    centers = np.asarray([hole['center'] for hole in holes])
    # Pick the shorter in-plane direction as the row-separation coordinate.
    row_axis = 0 if dims[u] < dims[v] else 1
    run_axis = 1 - row_axis
    row_step = .01 * short_side
    row_ids = np.rint(centers[:, row_axis] / row_step)
    levels, counts = np.unique(row_ids, return_counts=True)
    if len(levels) != 2 or counts.min() < 4 or counts.max() - counts.min() > 1:
        return False
    for level in levels:
        positions = np.sort(centers[row_ids == level, run_axis])
        spacing = np.diff(positions)
        if len(spacing) < 3 or spacing.mean() <= 0:
            return False
        if spacing.std() / spacing.mean() > .15:
            return False
    return True
