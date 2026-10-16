#!/usr/bin/env python3
"""Export one locally aligned display mesh per consolidated part family."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import trimesh

from generate_parts_pdf import (
    align_to_local_axes,
    canonicalize,
    dimension_variants,
    match_parts,
    drawing_families,
)
from render_three_views import load_stl_parts
from depth_view_rules import depth_view_features


def display_mesh(
    triangles: np.ndarray,
    face_limit: int,
) -> tuple[trimesh.Trimesh, dict]:
    mesh = trimesh.Trimesh(
        vertices=triangles.reshape(-1, 3),
        faces=np.arange(len(triangles) * 3, dtype=int).reshape(-1, 3),
        process=True,
    )
    features = depth_view_features(mesh)
    if len(mesh.faces) > face_limit:
        mesh = mesh.simplify_quadric_decimation(face_count=face_limit)
    return mesh, features


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--parts-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--face-limit", type=int, default=120_000)
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    raw_parts = load_stl_parts(args.parts_dir)
    parts, dimensions = canonicalize(raw_parts)
    if not np.allclose(dimensions, np.asarray(spec["envelope_wdh_mm"], float), atol=0.2):
        raise ValueError(f"GT mesh envelope {dimensions} does not match specification")

    part_by_occurrence = match_parts(parts, spec)
    families = drawing_families(spec)
    occurrence_specs = {item["id"]: item for item in spec["occurrences"]}
    aligned_by_occurrence: dict[str, np.ndarray] = {}
    dimensions_by_id: dict[str, np.ndarray] = {}
    for family in families:
        # Repeated placements of one part share its local dimensions.  Aligning
        # every placement independently is very costly for large fixtures and
        # can create spurious dimension variants on rotated symmetric parts.
        # Retain separate dimensions when the source metrics show a real change.
        representatives: list[str] = []
        for occurrence_id in family["occurrence_ids"]:
            item = occurrence_specs[occurrence_id]
            matched = None
            for representative in representatives:
                reference = occurrence_specs[representative]
                volume_close = abs(item["volume_mm3"] - reference["volume_mm3"]) <= max(
                    0.005 * reference["volume_mm3"], 1e-3
                )
                area_close = abs(item["surface_area_mm2"] - reference["surface_area_mm2"]) <= max(
                    0.005 * reference["surface_area_mm2"], 1e-3
                )
                if volume_close and area_close:
                    matched = representative
                    break
            if matched is None:
                representatives.append(occurrence_id)
                aligned, local_dimensions = align_to_local_axes(part_by_occurrence[occurrence_id])
                aligned_by_occurrence[occurrence_id] = aligned
                dimensions_by_id[occurrence_id] = local_dimensions
            else:
                dimensions_by_id[occurrence_id] = dimensions_by_id[matched]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for family in families:
        occurrence_id = family["occurrence_ids"][0]
        mesh, features = display_mesh(
            aligned_by_occurrence[occurrence_id], args.face_limit
        )
        mesh_path = args.output_dir / f"{family['id']}.stl"
        mesh.export(mesh_path)
        variants = dimension_variants(family, dimensions_by_id)
        manifest.append(
            {
                "id": family["id"],
                "quantity": family["quantity"],
                "occurrence_ids": family["occurrence_ids"],
                "source_family_ids": family["source_family_ids"],
                "dimensions_xyz_mm": dimensions_by_id[occurrence_id].tolist(),
                "dimension_variants_xyz_mm": [item.tolist() for item in variants],
                **features,
                "mesh": mesh_path.name,
            }
        )

    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"families": manifest}, indent=2), encoding="utf-8")
    print(json.dumps({"families": len(manifest), "manifest": str(manifest_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
