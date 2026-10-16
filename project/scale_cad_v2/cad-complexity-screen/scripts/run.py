"""Run the reusable CAD family complexity screen on a Harbor case inventory."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="JSON array of case records; each needs name, index and parts_workdir")
    parser.add_argument("--harbor-dir", type=Path, required=True,
                        help="Directory containing cad_*/environment/input/parts.pdf")
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="Directory for scan JSON, review crops, and reports")
    parser.add_argument("--output-dir", type=Path,
                        help="Directory for the preview PDF and PNG (default: work-dir)")
    parser.add_argument("--reuse-scans", action="store_true",
                        help="Rebuild selection from existing scan JSON without scanning again")
    parser.add_argument("--no-preview", action="store_true",
                        help="Skip preview PDF/PNG and its page verification")
    parser.add_argument("--skip-visual-watchlist", action="store_true",
                        help="Skip independent full-page OCR watchlist (not used for STEP selection)")
    parser.add_argument("--check-inputs", action="store_true",
                        help="Validate the inventory and current PDF/spec alignment, then exit")
    args = parser.parse_args()
    if args.skip_visual_watchlist and not args.no_preview and not args.check_inputs:
        parser.error("--skip-visual-watchlist requires --no-preview")

    manifest = args.manifest.expanduser().resolve()
    harbor = args.harbor_dir.expanduser().resolve()
    work = args.work_dir.expanduser().resolve()
    output = (args.output_dir or work).expanduser().resolve()
    cases = json.loads(manifest.read_text())
    if not isinstance(cases, list) or not cases:
        parser.error("manifest must be a nonempty JSON array")
    names = [case.get("name") for case in cases]
    if len(set(names)) != len(names) or any(not isinstance(name, str) for name in names):
        parser.error("case names must be unique strings")
    present = {path.name for path in harbor.glob("cad_*") if path.is_dir()}
    if set(names) != present:
        parser.error(f"case inventory mismatch: manifest={sorted(names)}, harbor={sorted(present)}")
    for case in cases:
        if "index" not in case or "parts_workdir" not in case:
            parser.error(f"{case['name']} needs index and parts_workdir")
        root = Path(case["parts_workdir"]).expanduser().resolve()
        for source in (root / "part-spec.json", root / "aligned/manifest.json",
                       root / "geometry/catalog.json",
                       root / "feature-annotations.json",
                       root / "analytic/manual_geometry.json",
                       harbor / case["name"] / "environment/input/parts.pdf"):
            if not source.is_file():
                parser.error(f"missing input: {source}")

    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    selected = work / "selected.json"
    if selected != manifest:
        shutil.copyfile(manifest, selected)
    env = os.environ.copy()
    env.update(CAD_COMPLEXITY_WORK=str(work), CAD_COMPLEXITY_HARBOR=str(harbor),
               CAD_COMPLEXITY_OUTPUT=str(output))
    if args.check_inputs:
        subprocess.run([sys.executable, str(SCRIPT_DIR / "check_inputs.py")],
                       env=env, check=True)
        return
    command = [sys.executable, str(SCRIPT_DIR / "run_complexity_screen.py")]
    if args.reuse_scans:
        command.append("--reuse-scans")
    if args.no_preview:
        command.append("--no-preview")
    if args.skip_visual_watchlist:
        command.append("--skip-visual-watchlist")
    subprocess.run(command, env=env, check=True)


if __name__ == "__main__":
    main()
