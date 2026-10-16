#!/usr/bin/env python3
"""Move supplied single-solid STEPs to their center-of-mass principal frame."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile

from inertia_frame import canonicalize_shape


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step-dir", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--freecad-lib", type=Path)
    args = parser.parse_args()
    if args.freecad_lib:
        sys.path.insert(0, str(args.freecad_lib))
    import FreeCAD
    import Part

    paths = sorted(args.step_dir.glob("family_*.step"))
    if not paths:
        raise ValueError(f"No family STEP files in {args.step_dir}")
    report = []
    for path in paths:
        solids = Part.read(str(path)).Solids
        if len(solids) != 1 or not solids[0].isValid():
            raise ValueError(f"{path.name}: expected one valid solid")
        source = solids[0]
        normalized, frame = canonicalize_shape(source, FreeCAD)
        with tempfile.TemporaryDirectory(dir=args.step_dir) as temporary:
            staged = Path(temporary) / path.name
            normalized.exportStep(str(staged))
            imported = Part.read(str(staged)).Solids
            if len(imported) != 1 or not imported[0].isValid():
                raise ValueError(f"{path.name}: invalid STEP after round trip")
            result = imported[0]
            if not (math.isclose(result.Volume, source.Volume, rel_tol=0.005, abs_tol=0.01)
                    and math.isclose(result.Area, source.Area, rel_tol=0.005, abs_tol=0.1)):
                raise ValueError(f"{path.name}: volume or area changed after round trip")
            center = result.CenterOfMass
            if math.sqrt(center.x**2 + center.y**2 + center.z**2) > 1e-3:
                raise ValueError(f"{path.name}: STEP round trip moved the center of mass")
            inertia = result.MatrixOfInertia
            scale = max(abs(inertia.A11), abs(inertia.A22), abs(inertia.A33), 1e-12)
            if max(abs(inertia.A12), abs(inertia.A13), abs(inertia.A23)) > 1e-6 * scale:
                raise ValueError(f"{path.name}: STEP round trip lost principal-axis alignment")
            os.replace(staged, path)
        report.append({"family": path.stem, "volume_mm3": result.Volume,
                       "surface_area_mm2": result.Area, **frame})
        print(json.dumps({"family": path.stem,
                          "center_error_mm": frame["destination_center_mm"],
                          "relative_inertia_off_diagonal": frame["relative_inertia_off_diagonal"]}),
              flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
