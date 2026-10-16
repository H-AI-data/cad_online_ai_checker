#!/usr/bin/env python3
"""Screen existing detailed drawings for likely annotation gaps.

This read-only pass uses already-generated feature metadata and PDF pages. It
does not export parts, regenerate drawings, or check existing STEP files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz

from annotation_feasibility import assess_drawing, screen


def run(args):
    candidates = [item for item in json.loads(args.candidates.read_text())
                  if item["case"] == args.case]
    features = {item["family"]: item
                for item in json.loads(args.features.read_text())}
    detail_dir = args.dimensioned_dir
    manifest = json.loads((detail_dir / "delivery-manifest.json").read_text())
    plans = json.loads((detail_dir / "dimension-plan.json").read_text())
    audits = json.loads((detail_dir / "dimensioned_parts.audit.json").read_text())
    families = [item["family"] for item in candidates]
    if (manifest["case"] != args.case or
            [item["family"] for item in manifest["families"]] != families or
            [item["family"] for item in plans] != families or
            [item["family"] for item in audits] != families or
            any(family not in features for family in families)):
        raise ValueError("Existing drawing and feature metadata have different families")

    decisions = []
    with fitz.open(detail_dir / "dimensioned_parts.pdf") as document:
        first_page = 0
        for index, candidate in enumerate(candidates):
            family = candidate["family"]
            count = 2 + manifest["families"][index]["additional_projection_pages"]
            pages = [document[page]
                     for page in range(first_page, first_page + count)]
            if not pages[0].get_text().startswith(family.upper() + "\n"):
                raise ValueError(f"Wrong PDF page for {family}")
            risk = screen(features[family], candidate)
            decision = assess_drawing(risk, plans[index], audits[index], pages)
            decisions.append({"family": family, **risk, **decision})
            first_page += count
        if first_page != len(document):
            raise ValueError("PDF page count differs from manifest")

    result = {
        "case": args.case,
        "screened_families": len(decisions),
        "step_priority_from_drawing_gaps": [
            item["family"] for item in decisions if item["step_priority"]],
        "decisions": decisions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps({"case": args.case,
                      "screened": len(decisions),
                      "step_priority": result["step_priority_from_drawing_gaps"],
                      "output": str(args.output)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--dimensioned-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
