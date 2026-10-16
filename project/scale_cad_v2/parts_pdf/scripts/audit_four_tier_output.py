#!/usr/bin/env python3
"""Check objective page, layout, and STEP invariants before publishing parts.pdf.

The report stays in the author work directory.  This is a structural gate;
it does not certify that every geometric feature has a manufacturing dimension.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import pymupdf


FAMILY = re.compile(r"^FAMILY_\d+\b", re.IGNORECASE)
STEP_NOTICE = re.compile(r"STEP PROVIDED:\s*provided_part_files/(family_\d+)\.step",
                         re.IGNORECASE)


def audit(pdf_path: Path, manifest_path: Path, step_dir: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    ranges = manifest["page_ranges"]
    tiers = manifest["tiers"]
    families = [family for group in tiers.values() for family in group]
    problems: list[str] = []
    advisories: list[str] = []
    if len(families) != len(set(families)) or set(families) != set(ranges):
        problems.append("tier families and page ranges differ or contain duplicates")
    step_families = set(tiers.get("step", []))
    if step_families != set(manifest.get("provided_part_files", [])):
        problems.append("STEP tier and provided file manifest differ")

    with pymupdf.open(pdf_path) as doc:
        if len(doc) != manifest["pages"]:
            problems.append(f"page count {len(doc)} differs from manifest {manifest['pages']}")
        intervals = []
        for family, span in ranges.items():
            if (not isinstance(span, (list, tuple)) or len(span) != 2
                    or not all(isinstance(n, int) for n in span)):
                problems.append(f"{family}: invalid page range")
                continue
            start, end = span
            if start < 2 or end < start or end > len(doc):
                problems.append(f"{family}: page range outside document")
                continue
            intervals.extend((number, family) for number in range(start, end + 1))
            if family in step_families and start != end:
                problems.append(f"{family}: STEP tier needs exactly one page")
        numbers = [number for number, _ in intervals]
        if len(numbers) != len(set(numbers)):
            problems.append("family page ranges overlap")
        cover_count = min(numbers, default=len(doc) + 1) - 1
        if set(numbers) != set(range(cover_count + 1, len(doc) + 1)):
            problems.append("family page ranges have gaps")
        if cover_count < 1:
            problems.append("missing cover/index page")

        page_family = dict(intervals)
        for number, page in enumerate(doc, 1):
            page_text = page.get_text()
            text_upper = page_text.upper()
            if not page_text.strip():
                problems.append(f"page {number}: no selectable text")
            for forbidden in ("BLENDER", "DIFFICULT PART | STEP +", "DETAIL A", "DETAIL B"):
                if forbidden in text_upper:
                    problems.append(f"page {number}: forbidden public wording {forbidden}")
            for block in page.get_text("blocks"):
                if len(block) < 5 or not isinstance(block[4], str) or not block[4].strip():
                    continue
                x0, y0, x1, y1 = block[:4]
                if (x0 < -2 or y0 < -2 or x1 > page.rect.width + 2
                        or y1 > page.rect.height + 2):
                    problems.append(f"page {number}: text outside page")
                    break
            if number <= cover_count:
                if "FAMILY" not in text_upper:
                    advisories.append(f"page {number}: cover has no family index text")
                continue
            family = page_family.get(number)
            headings = {match.group(0).lower()
                        for line in page_text.splitlines()
                        if (match := FAMILY.match(line.strip()))}
            if family is None or headings != {family}:
                problems.append(f"page {number}: heading does not match indexed family {family}")
                continue
            notices = {match.lower() for match in STEP_NOTICE.findall(page_text)}
            if family in step_families:
                if notices != {family} or "3 VIEWS PROVIDED" not in text_upper:
                    problems.append(f"page {number}: STEP notice/view count differs from {family}")
                if len(page.get_images(full=True)) < 3:
                    problems.append(f"page {number}: fewer than three model images")
            elif notices:
                # Medium pages may announce a supplied STEP only when the
                # matching single-part file actually exists.
                for notice in notices:
                    if not (step_dir / f"{notice}.step").is_file():
                        problems.append(f"page {number}: STEP notice has no file: {notice}")

        cover_text = "\n".join(doc[i].get_text().upper() for i in range(cover_count))
        for family in families:
            if family.upper() not in cover_text:
                problems.append(f"cover index omits {family}")
        for family in step_families:
            if not (step_dir / f"{family}.step").is_file():
                problems.append(f"STEP file missing for {family}")

    return {"pdf": str(pdf_path), "pages": manifest["pages"],
            "families": len(families), "step_families": len(step_families),
            "problems": problems, "advisories": advisories}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--step-files-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report = audit(args.pdf, args.manifest, args.step_files_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in report.items()
                      if key != "pdf"}, ensure_ascii=False))
    if report["problems"]:
        raise SystemExit("Four-tier PDF audit failed")


if __name__ == "__main__":
    main()
