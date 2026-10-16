#!/usr/bin/env python3
"""Export selected consolidated-family representative solids as individual STEP files.

Run under FreeCAD Python. Family identities come from the exact part-spec.json
used for the drawing; reference_index points into Part.read(...).Solids.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile

from inertia_frame import canonicalize_shape
from step_export_status import (INVALID_EXPORTED_STEP_EVENT,
                                INVALID_EXPORTED_STEP_EXIT_CODE)


class InvalidExportedPartStep(Exception):
    def __init__(self, family_id, reference_index, phase, detail):
        self.family_id = family_id
        self.reference_index = reference_index
        self.phase = phase
        self.detail = detail
        super().__init__(f'{family_id}: {phase}: {detail}')


def read_valid_exported_solids(path, Part, family_id, reference_index, phase):
    """Reject an exported STEP as soon as its imported B-rep is not a solid."""
    try:
        solids = Part.read(str(path)).Solids
    except Exception as exc:
        raise InvalidExportedPartStep(
            family_id, reference_index, phase,
            f'cannot reimport STEP: {type(exc).__name__}: {exc}') from exc
    if not solids:
        raise InvalidExportedPartStep(
            family_id, reference_index, phase, 'no solid after STEP reimport')
    for item in solids:
        if not item.isValid() or not item.isClosed() or item.Volume <= 0:
            raise InvalidExportedPartStep(
                family_id, reference_index, phase,
                'invalid, open, or nonpositive-volume solid after STEP reimport')
    return solids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path,
                        help="Mesh catalog with the exact source STEP SHA-256")
    parser.add_argument("--geometry", required=True, type=Path,
                        help="Matching FreeCAD edge-sampled geometry cache")
    parser.add_argument("--features", required=True, type=Path,
                        help="Matching per-family world_to_local transforms used by parts.pdf")
    parser.add_argument("--families-json", required=True, type=Path,
                        help="JSON array of family IDs selected for STEP provision")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--freecad-lib", type=Path)
    args = parser.parse_args()
    if args.freecad_lib:
        sys.path.insert(0, str(args.freecad_lib))
    import FreeCAD  # noqa: F401 - import before Part for bundled FreeCAD
    import Part

    spec = json.loads(args.spec.read_text())
    catalog = json.loads(args.catalog.read_text())
    source_sha256 = hashlib.sha256(args.step.read_bytes()).hexdigest()
    if source_sha256 != catalog.get("source_sha256"):
        raise ValueError("Source STEP does not match the part-spec mesh catalog")
    geometry = json.loads(args.geometry.read_text())
    feature_plans = {item["family"]: item
                     for item in json.loads(args.features.read_text())}
    selected = json.loads(args.families_json.read_text())
    if not isinstance(selected, list) or len(selected) != len(set(selected)):
        raise ValueError("families-json must be an array of unique IDs")
    families = {item["id"]: item for item in spec["families"]}
    unknown = set(selected) - set(families)
    if unknown:
        raise ValueError(f"Unknown family IDs: {sorted(unknown)}")
    missing_plans = set(selected) - set(feature_plans)
    if missing_plans:
        raise ValueError(f"Missing local-coordinate transforms: {sorted(missing_plans)}")
    occurrences = {item["id"]: item for item in spec["occurrences"]}
    assembly = Part.read(str(args.step.resolve()))
    solids = assembly.Solids
    if len(solids) != len(occurrences) or len(geometry["parts"]) != len(solids):
        raise ValueError(f"STEP has {len(solids)} solids, spec has {len(occurrences)} occurrences")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for family_id in selected:
        family = families[family_id]
        representative = occurrences[family["occurrence_ids"][0]]
        index = representative["reference_index"]
        solid = solids[index]
        # A valid OCC solid can export as a shell even when it has only one
        # shell in memory. Refine redundant face splits only when a direct
        # STEP round-trip fails, and require the refined BRep to preserve the
        # source geometry before using it for the local export.
        if solid.Shells:
            with tempfile.TemporaryDirectory() as trial_dir:
                trial = Path(trial_dir) / "source.step"
                solid.exportStep(str(trial))
                recovered = read_valid_exported_solids(
                    trial, Part, family_id, index, 'source_roundtrip')
                direct_ok = (len(recovered) == 1 and
                             math.isclose(recovered[0].Volume, solid.Volume,
                                          rel_tol=0.005, abs_tol=0.01) and
                             math.isclose(recovered[0].Area, solid.Area,
                                          rel_tol=0.01, abs_tol=0.1))
                if not direct_ok:
                    refined = solid.removeSplitter()
                    if len(refined.Solids) == 1:
                        refined = refined.Solids[0]
                    before, after = solid.BoundBox, refined.BoundBox
                    stable_bounds = max(abs(getattr(before, key) - getattr(after, key))
                                        for key in ("XMin", "YMin", "ZMin",
                                                    "XMax", "YMax", "ZMax")) <= 0.05
                    stable = (len(refined.Solids) == 1 and refined.isClosed()
                              and refined.isValid() and stable_bounds
                              and (refined.CenterOfMass - solid.CenterOfMass).Length <= 0.05
                              and math.isclose(refined.Volume, solid.Volume,
                                               rel_tol=1e-4, abs_tol=0.01)
                              and math.isclose(refined.Area, solid.Area,
                                               rel_tol=1e-4, abs_tol=0.1))
                    if not stable:
                        raise ValueError(f"{family_id}: STEP export splits the source solid "
                                         "and splitter refinement changes its geometry")
                    refined.exportStep(str(trial))
                    recovered = read_valid_exported_solids(
                        trial, Part, family_id, index, 'refined_source_roundtrip')
                    if (len(recovered) != 1 or
                            not math.isclose(recovered[0].Volume, solid.Volume,
                                             rel_tol=0.005, abs_tol=0.01) or
                            not math.isclose(recovered[0].Area, solid.Area,
                                             rel_tol=0.01, abs_tol=0.1)):
                        raise ValueError(f"{family_id}: refined source STEP still does not "
                                         "round-trip as one solid")
                    solid = refined
                    print(json.dumps({"family": family_id, "reference_index": index,
                                      "source_refined_for_export": True}), flush=True)
        cached = geometry["parts"][index]
        # The cache uses sampled analytic geometry while the spec uses an
        # independently triangulated mesh.  Some curved parts have a roughly
        # 1 mm difference in their mesh envelope.  Cross-check the fresh STEP
        # tessellation against the analytic cache below; both must identify
        # the same source SHA/index, and volume/area are checked separately.
        # Edge-sampled analytic bounds may miss the farthest point of a curved
        # face.  The exact source SHA and solid index establish identity; the
        # fresh tessellated extent below is the geometry cross-check.
        expected_volume = representative["volume_mm3"]
        if not math.isclose(solid.Volume, expected_volume, rel_tol=0.02, abs_tol=0.05):
            # Mesh-derived volume and area can both deviate on dense curved
            # faces even when a finer tessellation gives the same result.
            # The source SHA/index and the fresh extent checks below still
            # bind this export to the original BRep; never use the relaxed
            # mesh metrics alone to identify a solid.
            # Dense curved support faces can lose several percent of surface
            # area in the 0.15 mm source mesh, even when their volume, bounds,
            # source checksum and occurrence index agree with the BRep.
            area_matches = math.isclose(
                solid.Area, representative["surface_area_mm2"], rel_tol=0.10, abs_tol=0.1)
            volume_near = math.isclose(
                solid.Volume, expected_volume, rel_tol=0.05, abs_tol=0.05)
            # A sub-10 mm^3 mesh can have a large relative volume error from
            # tessellation while differing by less than one cubic millimetre.
            tiny_volume_near = (expected_volume < 10.0 and
                                abs(solid.Volume - expected_volume) <= 1.5)
            if not (area_matches and (volume_near or tiny_volume_near)):
                # Signed volume is undefined for an open triangulation.  The
                # source SHA, solid index, analytic catalog volume and fresh
                # extent still identify the original closed B-rep.  In this
                # case the exact single-part STEP is the authoritative output.
                catalog_volume = catalog["parts"][index]["volume_mm3"]
                bounded_closed_mesh_deviation = (
                    representative["mechanical_metrics"]["watertight"]
                    and area_matches
                    and abs(solid.Volume - expected_volume)
                    <= max(0.05, abs(solid.Volume) * 0.10)
                )
                unreliable_mesh = (not representative["mechanical_metrics"]["watertight"]
                                   or bounded_closed_mesh_deviation)
                source_matches = (solid.isValid() and solid.isClosed()
                                  and math.isclose(solid.Volume, catalog_volume,
                                                   rel_tol=0.005, abs_tol=0.05))
                if not (unreliable_mesh and source_matches):
                    raise ValueError(
                        f"{family_id}: STEP/spec volume mismatch at solid {index} "
                        f"(STEP={solid.Volume:.4f}, spec={expected_volume:.4f}, "
                        f"STEP area={solid.Area:.4f}, spec area={representative['surface_area_mm2']:.4f})")
                print(json.dumps({"family": family_id, "reference_index": index,
                                  "source_mesh_volume_unreliable": True,
                                  "brep_volume_mm3": round(solid.Volume, 4),
                                  "mesh_volume_mm3": expected_volume}), flush=True)
            print(json.dumps({"family": family_id, "reference_index": index,
                              "mesh_volume_deviation_pct": round(
                                  100 * (solid.Volume / expected_volume - 1), 3),
                              "surface_area_deviation_pct": round(
                                  100 * (solid.Area / representative["surface_area_mm2"] - 1), 3)}),
                  flush=True)
        # OCC's analytic BoundBox can include remote B-spline control points.
        # Compare a fresh tessellated extent to the earlier source-mesh extent.
        vertices, _ = solid.tessellate(0.1)
        actual_size = sorted(max(getattr(p, axis) for p in vertices)
                             - min(getattr(p, axis) for p in vertices)
                             for axis in "xyz")
        expected_size = sorted(representative["size_wdh_mm"])
        cached_size = sorted(cached["size"])
        cache_matches = all(abs(a - b) <= max(0.5, 0.003 * b)
                            for a, b in zip(actual_size, cached_size))
        spec_matches = all(abs(a - b) <= max(0.5, 0.003 * b)
                           for a, b in zip(actual_size, expected_size))
        if not (cache_matches or spec_matches):
            raise ValueError(f"{family_id}: fresh STEP extent matches neither cache nor spec")
        if any(abs(a - b) > max(1.25, 0.025 * b)
               for a, b in zip(actual_size, expected_size)):
            raise ValueError(f"{family_id}: STEP tessellated size/spec mismatch")
        plan = feature_plans[family_id]
        transform = plan["world_to_local"]
        if len(transform) != 4 or any(len(row) != 3 for row in transform):
            raise ValueError(f"{family_id}: invalid world_to_local transform")
        matrix = FreeCAD.Matrix()
        for local_axis in range(3):
            for world_axis in range(3):
                setattr(matrix, f"A{local_axis + 1}{world_axis + 1}",
                        transform[world_axis][local_axis])
            setattr(matrix, f"A{local_axis + 1}4", transform[3][local_axis])
        # Transform the analytic shape in its BRep frame.  transformGeometry
        # converts surfaces to B-splines and can alter toroidal volume; rigid
        # transformShape preserves the source faces on these STEP inputs.
        try:
            local_solid = solid.transformShape(matrix, True, False)
        except Part.OCCError:
            local_solid = None
        if (local_solid is None or
                not math.isclose(local_solid.Volume,solid.Volume,rel_tol=0.005,abs_tol=0.01) or
                not math.isclose(local_solid.Area,solid.Area,rel_tol=0.005,abs_tol=0.1)):
            local_solid = solid.transformGeometry(matrix)
        local_vertices, _ = local_solid.tessellate(0.1)
        local_low = [min(getattr(p, axis) for p in local_vertices) for axis in "xyz"]
        local_high = [max(getattr(p, axis) for p in local_vertices) for axis in "xyz"]
        if (any(abs(value) > 0.5 for value in local_low)
                or any(abs(local_high[i] - plan["dimensions"][i]) > 0.5
                       for i in range(3))):
            raise ValueError(f"{family_id}: local STEP dimensions do not match parts.pdf")
        try:
            # FreeCAD may share the underlying OCC geometry between shapes.
            # Canonical rotation can make the local BRep round-trip incorrectly;
            # keep an independent drawing-local copy for the verified fallback.
            canonical_solid, frame = canonicalize_shape(local_solid.copy(), FreeCAD)
        except Part.OCCError:
            canonical_solid, frame = None, None
        destination = args.output_dir / f"{family_id}.step"
        with tempfile.TemporaryDirectory(dir=args.output_dir) as temporary:
            staged = Path(temporary) / destination.name
            candidates = ([('principal', canonical_solid)] if canonical_solid is not None
                          else []) + [('drawing_local', local_solid)]
            export_frame = None
            attempts = []
            for mode, candidate in candidates:
                candidate.exportStep(str(staged))
                exported = read_valid_exported_solids(
                    staged, Part, family_id, index, f'{mode}_roundtrip')
                if len(exported) > 1:
                    # Keep one substantial solid only when all others are
                    # unambiguous zero-volume import artifacts.
                    substantial = [item for item in exported
                                   if item.Volume > max(0.01, 1e-6 * solid.Volume)]
                    if len(substantial) == 1 and all(
                            item.Volume <= max(0.01, 1e-6 * solid.Volume)
                            for item in exported if item is not substantial[0]):
                        substantial[0].exportStep(str(staged))
                        exported = read_valid_exported_solids(
                            staged, Part, family_id, index,
                            f'{mode}_single_solid_roundtrip')
                valid = (len(exported) == 1
                         and math.isclose(exported[0].Volume, solid.Volume,
                                          rel_tol=0.005, abs_tol=0.01)
                         and math.isclose(exported[0].Area, solid.Area,
                                          rel_tol=0.03, abs_tol=0.1))
                attempts.append({"frame": mode, "solids": len(exported),
                                 "volumes": [round(item.Volume, 3) for item in exported],
                                 "areas": [round(item.Area, 3) for item in exported]})
                if not valid:
                    continue
                if mode == 'principal':
                    center = exported[0].CenterOfMass
                    inertia = exported[0].MatrixOfInertia
                    scale = max(abs(inertia.A11), abs(inertia.A22),
                                abs(inertia.A33), 1e-12)
                    attempts[-1]['center_mm'] = [center.x, center.y, center.z]
                    attempts[-1]['inertia_offdiag_relative'] = (
                        max(abs(inertia.A12), abs(inertia.A13), abs(inertia.A23))
                        / scale)
                    valid = (math.sqrt(center.x**2 + center.y**2 + center.z**2)
                             <= 1e-3 and
                             max(abs(inertia.A12), abs(inertia.A13),
                                 abs(inertia.A23)) <= 1e-6 * scale)
                else:
                    # The fallback remains in the verified parts.pdf local
                    # frame, with no assembly installation coordinates.
                    vertices, _ = exported[0].tessellate(0.1)
                    low = [min(getattr(p, axis) for p in vertices) for axis in 'xyz']
                    high = [max(getattr(p, axis) for p in vertices) for axis in 'xyz']
                    valid = (all(abs(value) <= 0.5 for value in low) and
                             all(abs(high[i] - plan['dimensions'][i]) <= 0.5
                                 for i in range(3)))
                    attempts[-1]['bounds'] = [low, high]
                if valid:
                    export_frame = mode
                    os.replace(staged, destination)
                    break
            if export_frame is None:
                raise ValueError(f"{family_id}: neither principal nor drawing-local "
                                 "STEP preserved one solid, volume, area and local dimensions; "
                                 f"source_volume={solid.Volume:.3f} "
                                 f"source_area={solid.Area:.3f} attempts={attempts}")
        print(json.dumps({"family": family_id, "reference_index": index,
                          "volume_mm3": round(exported[0].Volume, 4),
                          "bytes": destination.stat().st_size,
                          "export_frame": export_frame,
                          "principal_moment_gaps":
                          frame["minimum_relative_moment_gaps"] if frame else None}),
              flush=True)


if __name__ == "__main__":
    try:
        main()
    except InvalidExportedPartStep as exc:
        print(json.dumps({
            'event': INVALID_EXPORTED_STEP_EVENT,
            'family': exc.family_id,
            'reference_index': exc.reference_index,
            'phase': exc.phase,
            'detail': exc.detail,
        }), flush=True)
        raise SystemExit(INVALID_EXPORTED_STEP_EXIT_CODE) from None
