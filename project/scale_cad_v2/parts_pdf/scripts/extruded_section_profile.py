#!/usr/bin/env python3
"""Recover a dimensionable constant section from an aligned triangle mesh.

The section is accepted only when three interior cuts agree, its straight
stations are axis aligned, and every curved interval fits the same radius.
Small end-face conical recesses are measured separately rather than treated
as through holes.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import LineString


def _section(mesh, axis, station):
    origin = np.zeros(3)
    origin[axis] = station
    normal = np.eye(3)[axis]
    cut = mesh.section(plane_origin=origin, plane_normal=normal)
    if cut is None:
        return []
    other = [index for index in range(3) if index != axis]
    return [np.asarray(loop)[:, other] for loop in cut.discrete]


def _area(points):
    return abs(np.dot(points[:-1, 0], points[1:, 1]) -
               np.dot(points[:-1, 1], points[1:, 0])) / 2


def _circle_fit(points):
    center = np.mean(points, axis=0)
    shifted = points - center
    matrix = np.column_stack((2 * shifted, np.ones(len(points))))
    parameters = np.linalg.lstsq(matrix, np.sum(shifted**2, axis=1), rcond=None)[0]
    radius = np.sqrt(parameters[2] + np.sum(parameters[:2]**2))
    fitted_center = center + parameters[:2]
    residual = float(np.max(np.abs(np.linalg.norm(points-fitted_center, axis=1)-radius)))
    return fitted_center, float(radius), residual


def _flat_and_radius_stations(points):
    """Use long exact lines to delimit fitted curved intervals."""
    edges = np.diff(points, axis=0)
    lengths = np.linalg.norm(edges, axis=1)
    ordinary = float(np.median(lengths))
    long = lengths > max(.25, ordinary * 5)
    indices = np.flatnonzero(long)
    if not 4 <= len(indices) <= 100:
        return None
    spans = [float(np.ptp(points[:, i])) for i in range(2)]
    levels = [[], []]
    for index in indices:
        edge = edges[index]
        if abs(edge[0]) < max(.015, ordinary / 2):
            levels[0].append(float(points[index, 0]))
        elif abs(edge[1]) < max(.015, ordinary / 2):
            levels[1].append(float(points[index, 1]))
        else:
            return None
    stations = []
    for axis in range(2):
        values = sorted(levels[axis])
        grouped = []
        for value in values:
            if not grouped or value-grouped[-1][-1] > .08:
                grouped.append([value])
            else:
                grouped[-1].append(value)
        stations.append([float(np.mean(group)) for group in grouped])
        if (len(stations[-1]) < 3 or
                abs(stations[-1][0]-points[:, axis].min()) > .1 or
                abs(stations[-1][-1]-points[:, axis].max()) > .1):
            return None
    # Each run between straight edges should be a substantial, well-fitted
    # circular arc.  Curvy/freeform profiles do not pass this gate.
    circular = []
    for previous, following in zip(indices, np.r_[indices[1:], indices[0]+len(lengths)]):
        sample = points[np.arange(previous+1, following+1) % len(lengths)]
        if len(sample) < 8:
            continue
        _, radius, residual = _circle_fit(sample)
        if radius <= .2 or residual > max(.025, .006*radius):
            return None
        circular.append(radius)
    if len(circular) < 4 or np.ptp(circular) > max(.08, .025*np.median(circular)):
        return None
    return stations, float(np.median(circular)), len(circular), spans


def _end_recesses(mesh, axis, dimensions):
    """Find equal round recesses that occur only near one extrusion end."""
    found = []
    length = float(dimensions[axis])
    for end in (0., length):
        sign = 1 if end == 0 else -1
        near = _section(mesh, axis, end + sign*.005)
        farther = _section(mesh, axis, end + sign*.08)
        if len(near) < 2 or len(farther) != len(near):
            continue
        loops = []
        for points in near[1:]:
            center, radius, residual = _circle_fit(points)
            if not .1 < radius < 2 or residual > .02:
                break
            loops.append((center, radius))
        if len(loops) != len(near)-1:
            continue
        if np.ptp([radius for _, radius in loops]) > .025:
            continue
        # For a conical recess the diameter shrinks linearly along the axis.
        far_radii = []
        for center, _ in loops:
            matches = []
            for ring in farther[1:]:
                other_center, other_radius, residual = _circle_fit(ring)
                if np.linalg.norm(other_center-center) < .05 and residual < .02:
                    matches.append(other_radius)
            if len(matches) != 1:
                break
            far_radii.append(matches[0])
        if len(far_radii) != len(loops):
            continue
        near_radius = float(np.median([radius for _, radius in loops]))
        far_radius = float(np.median(far_radii))
        slope = (near_radius-far_radius)/.075
        if slope < .2 or np.ptp(far_radii) > .025:
            continue
        mouth_radius = near_radius + slope*.005
        depth = mouth_radius/slope
        if not .05 < depth < 2:
            continue
        found.append(dict(end=float(end), centers=[center.tolist() for center, _ in loops],
                          diameter=2*mouth_radius, depth=depth))
    return found


def analyze_extruded_section(mesh, dimensions, primary_profile=None):
    """Return a fully measured section, or None if evidence is incomplete."""
    dimensions = np.asarray(dimensions, float)
    if primary_profile:
        outline = np.asarray(primary_profile["outline"], float)
        if _area(np.vstack((outline, outline[0]))) / np.prod(dimensions[:2]) > .15:
            return None
    for axis in np.argsort(-dimensions):
        length = float(dimensions[axis])
        sections = [_section(mesh, int(axis), length*fraction)
                    for fraction in (.25, .5, .75)]
        if any(len(cut) != 1 for cut in sections):
            continue
        profiles = [cut[0] for cut in sections]
        other = [index for index in range(3) if index != axis]
        envelope = dimensions[other]
        areas = [_area(points) for points in profiles]
        if (min(areas)/np.prod(envelope) < .6 or
                np.ptp(areas) > max(.2, .001*np.mean(areas)) or
                any(LineString(points).hausdorff_distance(LineString(profiles[0])) > .05
                    for points in profiles[1:])):
            continue
        fitted = _flat_and_radius_stations(profiles[1])
        if fitted is None:
            continue
        stations, radius, count, spans = fitted
        return dict(axis=int(axis), other=other, length=length,
                    outline=profiles[1].tolist(), stations=stations,
                    radius=radius, arc_count=count, spans=spans,
                    end_recesses=_end_recesses(mesh, int(axis), dimensions))
    return None
