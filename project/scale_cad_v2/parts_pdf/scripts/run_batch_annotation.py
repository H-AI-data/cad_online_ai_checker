#!/usr/bin/env python3
"""Run the geometry-audited annotation pipeline over a case inventory."""

import argparse
import json
from pathlib import Path

import fitz

from run_complete_annotation import run


def source_pdf_matches(path, selected):
    if not path.is_file():
        return False
    with fitz.open(path) as pdf:
        return all(row["pdf_page"]<=len(pdf) and
                   pdf[row["pdf_page"]-1].get_text().startswith(
                       row["family"].upper()+"\n")
                   for row in selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path,
                        help="Same-run case inventory with parts_workdir and standalone_step")
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--harbor-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--input-fallback-root", type=Path,
                        help="Author-side same-run PDFs, used only if the Harbor PDF fails family/page matching")
    parser.add_argument("--cases", nargs="*", help="Omit to run every case in the candidate list")
    args = parser.parse_args()
    inventory = {row["name"]: row for row in json.loads(args.inventory.read_text())}
    candidates = json.loads(args.candidates.read_text())
    selected = set(args.cases or (row["case"] for row in candidates))
    missing = selected-inventory.keys()
    if missing:
        raise ValueError(f"Cases missing from inventory: {sorted(missing)}")
    for case in sorted(selected):
        row = inventory[case]
        parts = Path(row["parts_workdir"])
        step = Path(row["standalone_step"])
        selected_rows=[candidate for candidate in candidates if candidate["case"]==case]
        input_pdf=args.harbor_root/case/"environment/input/parts.pdf"
        if not source_pdf_matches(input_pdf,selected_rows):
            fallback=(args.input_fallback_root/case/"parts.pdf"
                      if args.input_fallback_root else None)
            if fallback is None or not source_pdf_matches(fallback,selected_rows):
                raise ValueError(f"No family/page-matched input PDF for {case}")
            input_pdf=fallback
            print(f"USING MATCHED INPUT {input_pdf}",flush=True)
        case_args = argparse.Namespace(
            case=case, step=step, spec=parts/"part-spec.json",
            catalog=parts/"geometry/catalog.json",
            geometry=parts/"analytic/manual_geometry.json",
            features=parts/"feature-annotations.json",
            meshes=parts/"aligned", renders=parts/"renders",
            candidates=args.candidates,
            input=input_pdf,
            work_dir=args.output_root/case,
        )
        print(f"PROCESSING {case}", flush=True)
        run(case_args)


if __name__ == "__main__":
    main()
