#!/usr/bin/env python3
"""Extract the private geometry specification for an assembly verifier.

The public ``parts.pdf`` can contain views and dimensions. Volume, area, center,
and source metadata remain private matching/scoring fingerprints.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import trimesh

from mechanical_families import MERGE_POLICY, mechanical_metrics, group_mechanical_metrics


def load_occurrence_meshes(path: Path) -> list[trimesh.Trimesh]:
    if path.suffix == '.gz':
        import gzip
        with gzip.open(path, 'rt') as stream:
            records = json.load(stream)
        return [trimesh.Trimesh(vertices=r['vertices'], faces=r['faces'], process=True) for r in records]
    loaded = trimesh.load(path, force="scene")
    if isinstance(loaded, trimesh.Trimesh):
        meshes = [loaded]
    elif isinstance(loaded, trimesh.Scene):
        meshes = list(loaded.dump(concatenate=False))
    else:
        raise ValueError("STEP did not produce a mesh scene")
    result = []
    for mesh in meshes:
        if not isinstance(mesh, trimesh.Trimesh) or len(mesh.vertices) < 4:
            continue
        mesh = mesh.copy()
        if str(mesh.metadata.get("units", "")).lower() in {"m", "meter", "meters"}:
            mesh.apply_scale(1000.0)
        result.append(mesh)
    if not result:
        raise ValueError("STEP contained no usable occurrence meshes")
    return result


def primitive_hint(size: np.ndarray, volume: float, face_count: int) -> str:
    bbox_volume = float(np.prod(size))
    fill = volume / max(bbox_volume, 1e-12)
    ordered = np.sort(size)
    equal_pair = min(
        abs(ordered[0] - ordered[1]) / max(ordered[1], 1e-12),
        abs(ordered[1] - ordered[2]) / max(ordered[2], 1e-12),
    )
    if fill >= 0.97 and face_count <= 24:
        return "rectangular_prism"
    if equal_pair <= 0.015 and 0.55 <= fill <= 0.82:
        return "cylindrical_or_ring"
    if ordered[0] <= 40 and ordered[2] >= 150:
        return "panel_or_trim"
    return "drawing_defined_complex"


def rounded(values, places=3):
    return [round(float(value), places) for value in values]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    meshes = load_occurrence_meshes(args.step)
    all_vertices = np.vstack([mesh.vertices for mesh in meshes])
    raw_min, raw_max = all_vertices.min(axis=0), all_vertices.max(axis=0)
    raw_dims = raw_max - raw_min
    # The source assembly and engineering drawing use X/Y/Z as longitudinal,
    # transverse, and vertical. Preserve that engineering frame and persist the
    # source mesh index so evaluation need not rematch the fixed GT STEP to this
    # private specification.
    axis_order = np.asarray([0, 1, 2], int)
    origin = raw_min[axis_order]
    envelope = raw_dims[axis_order]

    records = []
    for reference_index, mesh in enumerate(meshes):
        vertices = mesh.vertices[:, axis_order] - origin
        low, high = vertices.min(axis=0), vertices.max(axis=0)
        size = high - low
        volume = abs(float(mesh.volume))
        area = float(mesh.area)
        record = {
            "reference_index": reference_index,
            "source_name": str(mesh.metadata.get("name", "")),
            "source_node": str(mesh.metadata.get("node", "")),
            "size_wdh_mm": rounded(size),
            "center_wdh_mm": rounded((low + high) / 2),
            "volume_mm3": round(volume, 3),
            "surface_area_mm2": round(area, 3),
            "bbox_fill_ratio": round(volume / max(float(np.prod(size)), 1e-12), 6),
            "primitive_hint": primitive_hint(size, volume, len(mesh.faces)),
        }
        record["mechanical_metrics"] = mechanical_metrics(mesh)
        records.append(record)

    groups = group_mechanical_metrics([r["mechanical_metrics"] for r in records])
    for index, members in enumerate(groups, 1):
        for member in members:
            records[member]["family_id"] = f"family_{index:03d}"
    # Keep occurrence ordering positional for existing assembly consumers;
    # neither this order nor box dimensions participate in family comparison.
    records.sort(key=lambda item: (
        item["center_wdh_mm"][2], item["center_wdh_mm"][0],
        item["center_wdh_mm"][1], item["reference_index"],
    ))
    family_members: dict[str, list[dict]] = defaultdict(list)
    occurrences = []
    for index, record in enumerate(records, 1):
        family_id = record["family_id"]
        record["id"] = f"occurrence_{index:03d}"
        record["family_id"] = family_id
        occurrences.append(record)
        family_members[family_id].append(record)

    families = []
    for family_id in sorted(family_members):
        members = family_members[family_id]
        sample = members[0]
        families.append({
            "id": family_id,
            "quantity": len(members),
            "sorted_size_mm": sorted(sample["size_wdh_mm"]),
            "volume_mm3": sample["volume_mm3"],
            "surface_area_mm2": sample["surface_area_mm2"],
            "bbox_fill_ratio": sample["bbox_fill_ratio"],
            "primitive_hint": sample["primitive_hint"],
            "occurrence_ids": [member["id"] for member in members],
        })

    payload = {
        "schema_version": "cad-part-spec-2.0",
        "family_merge_policy": MERGE_POLICY,
        "units": "mm",
        "coordinate_frame": {
            "origin": "minimum X/Y/Z corner of the complete assembly envelope",
            "axes": {
                "W": "drawing longitudinal / STEP X axis",
                "D": "drawing transverse / STEP Y axis",
                "H": "drawing vertical / STEP Z axis",
            },
            "global_translation": "free",
            "axis_alignment": "assembly W/D/H must remain parallel to three STEP coordinate axes; axis permutation and reflection are accepted",
            "geometric_scaling": "forbidden",
        },
        "envelope_wdh_mm": rounded(envelope),
        "occurrence_count": len(occurrences),
        "family_count": len(families),
        "submission_contract": {
            "separate_occurrences_required": True,
            "accepted_step_schemas": ["AP203", "AP214", "AP242"],
            "labels_are_advisory": True,
            "equivalent_repeated_occurrences_may_be_permuted": True,
        },
        "full_credit_tolerances": {
            "envelope_mm": 1.0,
            "major_dimension_mm": 1.0,
            "thin_dimension_mm": 0.5,
            "component_volume_relative": 0.02,
            "occurrence_center_mm": 2.0,
        },
        "analytic_cylinder_families": "extracted directly from reference STEP at evaluation time",
        "families": families,
        "occurrences": occurrences,
        "notes": [
            "This full machine-readable specification is private evaluator data.",
            "Public parts.pdf contains family quantities, part views, and dimensional annotations.",
            "The engineering drawings remain authoritative for detailed profiles, bores, teeth, holes and assembly placement.",
            "source_name may contain legacy encoding artifacts and is informational only.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "occurrences": len(occurrences),
        "families": len(families),
        "envelope": rounded(envelope),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
