"""Rigidly place a FreeCAD solid at its center of mass in a principal frame."""

from __future__ import annotations

import itertools

import numpy as np

VERIFIER_AXIS_INERTIA_GAP_REL = 0.005


def canonicalize_shape(solid, freecad):
    """Return a rotated solid with its center of mass at XYZ=0.

    Principal moments are assigned in ascending order to X, Y, Z. Among the
    proper sign choices, retain the orientation closest to the incoming frame.
    An exactly degenerate eigenspace keeps the nearest incoming coordinate axes.
    """
    center = np.array([solid.CenterOfMass.x, solid.CenterOfMass.y,
                       solid.CenterOfMass.z], dtype=float)
    source = solid.MatrixOfInertia
    inertia = np.array([[getattr(source, f"A{i}{j}") for j in (1, 2, 3)]
                        for i in (1, 2, 3)], dtype=float)
    if not np.isfinite(center).all() or not np.isfinite(inertia).all():
        raise ValueError("Non-finite center of mass or inertia tensor")
    # Match the verifier's ascending-eigenvalue axis indices and proper-rotation
    # sign convention; use analytic BRep inertia to avoid tessellation noise.
    moments, axes = np.linalg.eigh(inertia)
    scale = max(float(np.max(np.abs(moments))), 1e-12)
    equal01 = abs(moments[1] - moments[0]) <= 1e-8 * scale
    equal12 = abs(moments[2] - moments[1]) <= 1e-8 * scale
    if equal01 and equal12:
        axes = np.eye(3)
    elif equal01 or equal12:
        unique = 2 if equal01 else 0
        direction = axes[:, unique]
        if direction[unique] < 0:
            direction = -direction
        preferred = np.eye(3)[:, 0 if equal01 else 1]
        in_plane = preferred - direction * float(preferred @ direction)
        if np.linalg.norm(in_plane) < 1e-8:
            preferred = np.eye(3)[:, 1 if equal01 else 2]
            in_plane = preferred - direction * float(preferred @ direction)
        in_plane /= np.linalg.norm(in_plane)
        if equal01:
            axes = np.column_stack((in_plane, np.cross(direction, in_plane), direction))
        else:
            axes = np.column_stack((direction, in_plane, np.cross(direction, in_plane)))
    else:
        proper = [axes @ np.diag(signs) for signs in itertools.product((-1.0, 1.0), repeat=3)
                  if np.linalg.det(axes @ np.diag(signs)) > 0]
        axes = max(proper, key=lambda basis: float(np.trace(basis)))
    rotation = axes.T
    translation = -rotation @ center
    matrix = freecad.Matrix()
    for row in range(3):
        for column in range(3):
            setattr(matrix, f"A{row + 1}{column + 1}", float(rotation[row, column]))
        setattr(matrix, f"A{row + 1}4", float(translation[row]))
    transformed = solid.transformShape(matrix, True, False)
    if not np.isclose(transformed.Volume, solid.Volume, rtol=1e-5, atol=1e-4):
        raise ValueError("Rigid principal-frame transform changed volume")
    if not np.isclose(transformed.Area, solid.Area, rtol=1e-5, atol=1e-4):
        raise ValueError("Rigid principal-frame transform changed area")
    new_center = np.array([transformed.CenterOfMass.x, transformed.CenterOfMass.y,
                           transformed.CenterOfMass.z])
    if np.linalg.norm(new_center) > 1e-4:
        raise ValueError(f"Center of mass was not moved to origin: {new_center}")
    tensor = transformed.MatrixOfInertia
    new_inertia = np.array([[getattr(tensor, f"A{i}{j}") for j in (1, 2, 3)]
                            for i in (1, 2, 3)], dtype=float)
    off_diagonal = new_inertia - np.diag(np.diag(new_inertia))
    if np.max(np.abs(off_diagonal)) > 1e-6 * scale:
        raise ValueError("Principal inertia axes are not aligned to XYZ")
    # The verifier divides adjacent gaps by the largest principal moment.
    gaps = np.abs(moments[:, None] - moments[None, :]) / scale
    np.fill_diagonal(gaps, np.inf)
    minimum_gaps = gaps.min(axis=1)
    return transformed, {
        "source_center_mm": center.tolist(),
        "principal_moments": moments.tolist(),
        "minimum_relative_moment_gaps": minimum_gaps.tolist(),
        "verifier_inertia_distinguishable": (
            minimum_gaps >= VERIFIER_AXIS_INERTIA_GAP_REL).tolist(),
        "rotation": rotation.tolist(),
        "translation_mm": translation.tolist(),
        "destination_center_mm": new_center.tolist(),
        "relative_inertia_off_diagonal": float(np.max(np.abs(off_diagonal)) / scale),
    }
