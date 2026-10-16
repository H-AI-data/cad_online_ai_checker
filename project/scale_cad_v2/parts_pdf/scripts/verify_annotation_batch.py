#!/usr/bin/env python3
"""Render and audit every scripted annotation page in a batch output root."""

import argparse
import json
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw


def verify_case(case_dir, scale):
    manifest = json.loads((case_dir / "delivery-manifest.json").read_text())
    audit = json.loads((case_dir / "dimensioned_parts.audit.json").read_text())
    pdf = pymupdf.open(case_dir / "dimensioned_parts.pdf")
    families = manifest["families"]
    expected_pages = sum(1+row.get("supplement_pages",1)+
                         row["additional_projection_pages"] for row in families)
    assert len(pdf) == expected_pages, (case_dir.name, len(pdf), expected_pages)
    assert len(audit) == len(families)
    for row in families:
        if row["delivery"] == "dimensioned_pdf_plus_step":
            assert Path(row["step"]).is_file(), row["step"]
    page_dir = case_dir / "qa_pages"
    page_dir.mkdir(exist_ok=True)
    for stale in page_dir.glob("page-*.png"):
        stale.unlink()
    for stale in case_dir.glob("qa_contact_*.png"):
        stale.unlink()
    thumbnails = []
    issues = []
    for page_number, page in enumerate(pdf, 1):
        page_text = page.get_text()
        if "CYLINDRICAL DIMENSION REGISTER" in page_text:
            issues.append(f"page {page_number}: obsolete cylinder register")
        if not page_text.strip():
            issues.append(f"page {page_number}: no selectable text")
        for block in page.get_text("blocks"):
            x0, y0, x1, y1 = block[:4]
            if x0 < -2 or y0 < -2 or x1 > page.rect.width + 2 or y1 > page.rect.height + 2:
                issues.append(f"page {page_number}: text outside page")
                break
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        image.save(page_dir / f"page-{page_number:03}.png", optimize=True)
        thumb = image.copy()
        thumb.thumbnail((250, 250))
        thumbnails.append((page_number, thumb))
    for offset in range(0, len(thumbnails), 30):
        chunk = thumbnails[offset:offset + 30]
        sheet = Image.new("RGB", (5 * 270, 6 * 275), "white")
        draw = ImageDraw.Draw(sheet)
        for index, (number, thumb) in enumerate(chunk):
            x = (index % 5) * 270 + 10
            y = (index // 5) * 275 + 20
            sheet.paste(thumb, (x, y))
            draw.text((x, y - 16), f"{case_dir.name}  p{number}", fill="black")
        sheet.save(case_dir / f"qa_contact_{offset // 30 + 1:02}.png", optimize=True)
    return dict(case=case_dir.name, families=len(families), pages=len(pdf),
                projection_pages=sum(r["additional_projection_pages"] for r in families),
                secondary_groups=sum(r["secondary_cylindrical_groups"] for r in families),
                oblique_groups_in_step=sum(r["oblique_cylindrical_groups_in_step"]
                                           for r in families),
                step_parts=sum(r["delivery"] == "dimensioned_pdf_plus_step"
                               for r in families), issues=issues)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--scale", type=float, default=0.5)
    parser.add_argument("--expected-cases", type=int)
    args = parser.parse_args()
    cases = sorted(args.root.glob("cad_*/delivery-manifest.json"))
    report = [verify_case(path.parent, args.scale) for path in cases]
    (args.root / "render_qa.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2))
    for row in report:
        print(json.dumps(row, ensure_ascii=False), flush=True)
    if (not cases or
            (args.expected_cases is not None and len(cases) != args.expected_cases) or
            any(row["issues"] for row in report)):
        raise SystemExit("Annotation batch render QA failed")


if __name__ == "__main__":
    main()
