"""Find dense, one-sided serrations on an end surface of an aligned part."""

from __future__ import annotations

import json

import numpy as np
import trimesh


def localized_end_serrations(mesh: trimesh.Trimesh) -> dict | None:
    """Count internal sharp edges that slope out of only one end surface."""
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) < 40:
        return None
    mesh = mesh.copy()
    mesh.merge_vertices()
    edges = mesh.face_adjacency_edges
    if len(edges) == 0:
        return None
    endpoints = mesh.vertices[edges]
    middle = endpoints.mean(axis=1)
    lengths = np.linalg.norm(endpoints[:, 0] - endpoints[:, 1], axis=1)
    extents = np.asarray(mesh.extents, dtype=float)
    if np.min(extents) <= 0:
        return None
    sharp = ((mesh.face_adjacency_angles >= .8)
             & (lengths >= max(.12, .015 * sorted(extents)[1])))
    if int(sharp.sum()) < 40:
        return None
    for axis in range(3):
        transverse = [index for index in range(3) if index != axis]
        inset = .12 * extents[transverse]
        inside = np.all(
            (middle[:, transverse] > mesh.bounds[0, transverse] + inset)
            & (middle[:, transverse] < mesh.bounds[1, transverse] - inset),
            axis=1,
        )
        axial_span = (np.abs(endpoints[:, 0, axis] - endpoints[:, 1, axis])
                      >= max(.12, .012 * extents[axis]))
        eligible = sharp & inside & axial_span
        width = max(.7, .18 * extents[axis])
        counts = [
            int(np.count_nonzero(
                eligible & (np.abs(middle[:, axis] - mesh.bounds[side, axis])
                            < width)))
            for side in (0, 1)
        ]
        for side in (0, 1):
            count, opposite = counts[side], counts[1 - side]
            if count >= 40 and opposite <= .35 * count:
                return {
                    "axis": axis,
                    "end": "min" if side == 0 else "max",
                    "sharp_interior_edges": count,
                    "opposite_end_edges": opposite,
                }
    return None


def main() -> None:
    from config import WORK
    from run_complexity_screen import current_case_source

    cases = json.loads((WORK / "selected.json").read_text())
    rows = []
    for case in cases:
        root, spec = current_case_source(case)
        for family in spec["families"]:
            path = root / "aligned" / f"{family['id']}.stl"
            metrics = localized_end_serrations(trimesh.load_mesh(path, process=False))
            if metrics:
                rows.append({"case": case["name"], "family": family["id"],
                             **metrics})
        print(case["name"], "localized serration scan done", flush=True)
    (WORK / "localized_serration_scan.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2))
    print("localized serration hits", len(rows), flush=True)


if __name__ == "__main__":
    main()
