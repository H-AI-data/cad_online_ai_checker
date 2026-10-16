"""Rotation-invariant part families using seven stable mechanical invariants."""
from __future__ import annotations

import numpy as np
import trimesh

RELATIVE_TOLERANCE = 0.01
CONTINUOUS_METRICS = (
    "volume", "surface_area", "principal_inertia", "convex_hull_volume",
    "convex_hull_area",
)
MERGE_POLICY = {
    "version": "mechanical-seven-v2",
    "relative_tolerance": RELATIVE_TOLERANCE,
    "exact_metrics": ["watertight", "euler_number"],
    "continuous_metrics": list(CONTINUOUS_METRICS),
    "comparison": "symmetric: abs(a-b) <= 0.01 * min(abs(a), abs(b)); zero must match exactly",
    "clustering": "complete-link, first compatible group, source reference-index order",
}


def stable_integral_mean_curvature(mesh):
    # Match verifier: exclude only seam slivers from this differential metric.
    faces_ok = trimesh.triangles.nondegenerate(mesh.triangles, height=1e-6)
    pairs = mesh.face_adjacency
    keep = faces_ok[pairs].all(axis=1)
    lengths = np.linalg.norm(np.subtract(*mesh.vertices[mesh.face_adjacency_edges.T]), axis=1)
    signs = np.where(mesh.face_adjacency_convex, 1.0, -1.0)
    return float((mesh.face_adjacency_angles[keep] * lengths[keep] * signs[keep]).sum() * 0.5)


def mechanical_metrics(mesh):
    # Translation does not change the invariants; centering avoids cancellation
    # for parts installed far from the origin. Do not alter topology or scale.
    mesh = mesh.copy()
    mesh.apply_translation(-mesh.center_mass)
    hull = mesh.convex_hull
    metrics = {
        "watertight": bool(mesh.is_watertight),
        "volume": float(mesh.volume),
        "surface_area": float(mesh.area),
        "principal_inertia": np.sort(np.asarray(mesh.principal_inertia_components, float)).tolist(),
        "convex_hull_volume": float(hull.volume),
        "convex_hull_area": float(hull.area),
        "euler_number": int(mesh.euler_number),
        "integral_mean_curvature": stable_integral_mean_curvature(mesh),
    }
    if any(not np.isfinite(np.asarray(metrics[key], float)).all() for key in CONTINUOUS_METRICS):
        raise ValueError("Cannot classify a part with non-finite mechanical metrics")
    return metrics


def same_mechanical_family(left, right):
    if any(left[key] != right[key] for key in ("watertight", "euler_number")):
        return False
    for key in CONTINUOUS_METRICS:
        a, b = np.asarray(left[key], float), np.asarray(right[key], float)
        if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
            return False
        # Require both reference directions to pass 1%; inertia is componentwise.
        if not np.all(np.abs(a - b) <= RELATIVE_TOLERANCE * np.minimum(np.abs(a), np.abs(b))):
            return False
    return True


def group_mechanical_metrics(metrics):
    """No transitive chaining: every pair within each family must pass 1%."""
    groups = []
    for index, item in enumerate(metrics):
        for group in groups:
            if all(same_mechanical_family(item, metrics[other]) for other in group):
                group.append(index)
                break
        else:
            groups.append([index])
    return groups


def drawing_families(spec):
    """All drawing entrypoints require the same already-merged scoring spec."""
    if spec.get("family_merge_policy") != MERGE_POLICY:
        raise ValueError("Regenerate part-spec.json with extract_part_spec.py: seven-metric family merging is missing or outdated")
    families = spec["families"]
    occurrences = {o["id"]: o for o in spec["occurrences"]}
    assigned = []
    for family in families:
        ids = family["occurrence_ids"]
        if len(ids) != family["quantity"] or not ids:
            raise ValueError("Invalid family quantity")
        if any(occurrences[i]["family_id"] != family["id"] for i in ids):
            raise ValueError("Drawing/scoring family mismatch")
        assigned.extend(ids)
    if len(assigned) != len(set(assigned)) or set(assigned) != set(occurrences):
        raise ValueError("Every occurrence must belong to exactly one family")
    return [{**f, "source_family_ids": [f["id"]]} for f in families]
