#!/usr/bin/env python3
"""Generate adaptive CAD part pages, or a single 3/4 view for STEP-provided families."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from generate_parts_pdf import (
    FEATURE_ANGLE_DEGREES,
    align_to_local_axes,
    canonicalize,
    dimension_variants,
    draw_family,
    draw_view,
    feature_edges,
    fmt,
    match_parts,
    drawing_families,
)
from render_three_views import load_stl_parts
from depth_view_rules import DEPTH_RULE_VERSION

INK = colors.HexColor("#303642")
MUTED = colors.HexColor("#687080")
LIGHT = colors.HexColor("#D6DAE1")
PANEL = colors.HexColor("#F7F8FA")
ACCENT = colors.HexColor("#376FAD")
STEP_VIEWS = (
    ("upper-oblique", "UPPER 3/4 VIEW"),
    ("rear-oblique", "OPPOSITE 3/4 VIEW"),
    ("top", "TOP VIEW | TRUE +Z"),
)


def step_view_boxes(page_width: float, page_height: float) -> tuple[tuple[float, ...], ...]:
    """Two large obliques above one centered top view, in PDF bottom-left coordinates."""
    side = 28.0
    gap = 12.0
    upper_width = (page_width - 2 * side - gap) / 2
    top_width = page_width - 2 * side
    return (
        (side, 236.0, upper_width, page_height - 354.0),
        (side + upper_width + gap, 236.0, upper_width, page_height - 354.0),
        ((page_width - top_width) / 2, 39.0, top_width, 185.0),
    )


def tight_view_image(image_path: Path) -> Image.Image:
    """Trim transparent camera margins without stretching the Blender view."""
    with Image.open(image_path) as source:
        image = source.convert("RGBA")
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        raise ValueError(f"Empty Blender view: {image_path}")
    pad = max(4, round(max(bounds[2] - bounds[0], bounds[3] - bounds[1]) * .025))
    return image.crop((max(0, bounds[0] - pad), max(0, bounds[1] - pad),
                       min(image.width, bounds[2] + pad),
                       min(image.height, bounds[3] + pad)))


def draw_image_fit(
    pdf: canvas.Canvas,
    image_path: Path,
    x: float,
    y: float,
    width: float,
    height: float,
    trim_alpha: bool = False,
) -> tuple[float, float, float, float]:
    if trim_alpha:
        source = tight_view_image(image_path)
        source_width, source_height = source.size
        pdf_image = ImageReader(source)
    else:
        with Image.open(image_path) as source:
            source_width, source_height = source.size
        pdf_image = str(image_path)
    scale = min(width / source_width, height / source_height)
    drawn_width = source_width * scale
    drawn_height = source_height * scale
    pdf.drawImage(
        pdf_image,
        x + (width - drawn_width) / 2,
        y + (height - drawn_height) / 2,
        drawn_width,
        drawn_height,
        mask="auto",
    )
    return (x + (width - drawn_width) / 2, y + (height - drawn_height) / 2,
            drawn_width, drawn_height)


def draw_depth_view(
    pdf: canvas.Canvas,
    image_path: Path,
    box: tuple[float, float, float, float],
    title: str,
    trim_alpha: bool = False,
) -> None:
    x, y, width, height = box
    pdf.setFillColor(PANEL)
    pdf.setStrokeColor(LIGHT)
    pdf.roundRect(x, y, width, height, 4, fill=1, stroke=1)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(x + 8, y + height - 14, title)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 6.5)
    pdf.drawRightString(x + width - 8, y + height - 14, "OPAQUE ORTHO")
    draw_image_fit(pdf, image_path, x + 8, y + 5, width - 16, height - 25,
                   trim_alpha=trim_alpha)


def footer(pdf: canvas.Canvas, page: int, total_pages: int, page_width: float) -> None:
    pdf.setStrokeColor(LIGHT)
    pdf.line(28, 25, page_width - 28, 25)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 6.5)
    pdf.drawString(28, 13, footer.label)
    pdf.drawRightString(page_width - 28, 13, f"PAGE {page} / {total_pages}")


def index_page_groups(families: list[dict], page_height: float) -> list[list[dict]]:
    table_y = page_height - 286
    max_rows = math.floor((table_y - 44) / 9) + 1
    page_count = max(1, math.ceil(len(families) / (3 * max_rows)))
    page_size = max(1, math.ceil(len(families) / page_count))
    return [families[index:index + page_size]
            for index in range(0, len(families), page_size)] or [[]]


def draw_cover(
    pdf: canvas.Canvas,
    families: list[dict],
    index_families: list[dict],
    page_width: float,
    page_height: float,
    product_title: str,
) -> None:
    complex_count = sum(bool(item["show_depth_views"]) for item in families)
    difficult_count = sum(bool(item.get("difficult_step")) for item in families)
    pdf.setFillColor(ACCENT)
    pdf.rect(0, page_height - 108, page_width, 108, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 24)
    pdf.drawString(34, page_height - 55, product_title)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(34, page_height - 80, "ADAPTIVE PART VIEWS AND LOCAL DETAILS")

    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(34, page_height - 137, "VIEW RULES")
    pdf.setFont("Helvetica", 8)
    rules = [
        f"{len(families)} consolidated geometry families; {complex_count} meet the depth-view complexity gate.",
        (f"{difficult_count} STEP-provided families show three model views each."
         if difficult_count else "All FRONT / SIDE / TOP panels are generated directly from the full aligned mesh."),
        f"A shared edge is drawn when adjacent face normals differ by more than {FEATURE_ANGLE_DEGREES:g} degrees; boundary edges remain.",
        "Regular line views retain overall dimensions and sparse STEP-derived dimensions (mm).",
        "Regular opaque views: upper, opposite upper, TOP (+Z), and BOTTOM (-Z).",
    ]
    for index, line in enumerate(rules):
        pdf.drawString(34, page_height - 157 - index * 13, line)

    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(34, page_height - 266, "FAMILY INDEX")
    draw_family_index(pdf, index_families, page_width, page_height - 286)


def draw_family_index(pdf: canvas.Canvas, families: list[dict], page_width: float,
                      table_y: float) -> None:
    columns = 3
    rows = math.ceil(len(families) / columns)
    table_x = 34
    column_width = (page_width - 68) / columns
    # Keep the final index row clear of the footer on larger assemblies.
    row_pitch = min(14, (table_y - 44) / max(1, rows - 1))
    if row_pitch < 9:
        raise ValueError("Family index exceeds a legible page")
    for column in range(columns):
        for row in range(rows):
            family_index = column * rows + row
            if family_index >= len(families):
                continue
            family = families[family_index]
            x = table_x + column * column_width
            y = table_y - row * row_pitch
            pdf.setStrokeColor(LIGHT)
            if row_pitch >= 12:
                pdf.line(x, y - 4, x + column_width - 8, y - 4)
            pdf.setFillColor(INK)
            pdf.setFont("Helvetica-Bold", 7)
            pdf.drawString(x, y + 2, family["id"].upper())
            pdf.setFont("Helvetica", 6.5)
            pdf.drawString(x + 66, y + 2, f"QTY {family['quantity']}")
            pdf.setFillColor(ACCENT if family["show_depth_views"] else MUTED)
            if family.get("provided_step"):
                status = "STEP PROVIDED"
            else:
                status = "7 VIEWS | 1 PAGE" if family["show_depth_views"] else "3 VIEWS | 2/PAGE"
            pdf.drawRightString(x + column_width - 12, y + 2, status)


def draw_index_continuation(pdf: canvas.Canvas, families: list[dict],
                            page_width: float, page_height: float,
                            first_number: int, last_number: int) -> None:
    pdf.setFillColor(ACCENT)
    pdf.rect(0, page_height - 70, page_width, 70, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(34, page_height - 43, "FAMILY INDEX (CONTINUED)")
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(34, page_height - 90, f"Families {first_number:03d}-{last_number:03d}")
    draw_family_index(pdf, families, page_width, page_height - 110)


def draw_complex_family_page(
    pdf: canvas.Canvas,
    family: dict,
    triangles: np.ndarray,
    dimensions_by_id: dict[str, np.ndarray],
    renders_dir: Path,
    page_width: float,
    page_height: float,
    step_note: str | None = None,
) -> None:
    source_ids = family["source_family_ids"]

    pdf.setFillColor(ACCENT)
    pdf.roundRect(28, page_height - 58, page_width - 56, 30, 7, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(40, page_height - 47, family["id"].upper())
    aliases = ""
    if len(source_ids) > 1:
        aliases = " | INCLUDES " + ", ".join(item.removeprefix("family_").upper() for item in source_ids[1:])
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(page_width - 40, page_height - 46, f"QTY {family['quantity']}{aliases}")
    if step_note:
        pdf.setFont("Helvetica-Bold", 7.5)
        pdf.drawCentredString(page_width / 2, page_height - 46, step_note)

    variants = dimension_variants(family, dimensions_by_id)
    variants_text = ";  ".join(" x ".join(fmt(value) for value in item) for item in variants)
    dimension_label = "LOCAL-AXIS X x Y x Z"
    if len(variants) > 1:
        dimension_label += " VARIANTS"
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(28, page_height - 76, f"{dimension_label}: {variants_text} mm")
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 6.5)
    pdf.drawRightString(page_width - 28, page_height - 76, "FULL-MESH 2-DEGREE FEATURE EDGES | NTS")

    boxes = grid_boxes(page_width, page_height)
    occurrence_id = family["occurrence_ids"][0]
    dimensions = dimensions_by_id[occurrence_id]
    edges = feature_edges(triangles)
    views = (
        ((0, 2), "FRONT", f"X {fmt(dimensions[0])}", f"Z {fmt(dimensions[2])}"),
        ((1, 2), "SIDE", f"Y {fmt(dimensions[1])}", f"Z {fmt(dimensions[2])}"),
        ((0, 1), "TOP", f"X {fmt(dimensions[0])}", f"Y {fmt(dimensions[1])}"),
    )
    for index, (axes, title, horizontal, vertical) in enumerate(views):
        draw_view(pdf, triangles, axes, boxes[index], title, horizontal, vertical, edges)
    family_dir = renders_dir / family["id"]
    rendered = json.loads((family_dir / "render_manifest.json").read_text())
    if rendered.get("view_version") != "upper-opposite-grid-linework-v3":
        raise ValueError("Outdated renders; rerun blender_render_part_depth.py")
    opaque = [
        ("upper-oblique", "UPPER OBLIQUE"),
        ("rear-oblique", "OPPOSITE UPPER OBLIQUE"),
        ("top", "TOP | TRUE +Z"),
        ("bottom", "BOTTOM | TRUE -Z"),
    ]
    for index, (name, title) in enumerate(opaque):
        draw_depth_view(pdf, family_dir / rendered["views"][name]["image"],
                        boxes[index + 3], title)


def draw_difficult_family_page(pdf: canvas.Canvas, family: dict, renders_dir: Path,
                               page_width: float, page_height: float) -> None:
    """A difficult family has a supplied STEP and three complementary views."""
    family_dir = renders_dir / family["id"]
    rendered = json.loads((family_dir / "render_manifest.json").read_text())
    if rendered.get("view_version") != "upper-opposite-grid-linework-v3":
        raise ValueError(f"Outdated Blender render for {family['id']}")
    views = rendered.get("views", {})
    for name, _ in STEP_VIEWS:
        view = views.get(name)
        if not view or not (family_dir / view["image"]).is_file():
            raise ValueError(f"Missing Blender {name} view for {family['id']}")
    pdf.setFillColor(ACCENT)
    pdf.roundRect(28, page_height - 58, page_width - 56, 30, 7, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(40, page_height - 47, family["id"].upper())
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawCentredString(page_width / 2, page_height - 46,
                         f"STEP PROVIDED: provided_part_files/{family['id']}.step")
    pdf.drawRightString(page_width - 40, page_height - 46, f"QTY {family['quantity']}")
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(28, page_height - 78, "3 VIEWS PROVIDED")
    for (name, title), box in zip(STEP_VIEWS, step_view_boxes(page_width, page_height)):
        draw_depth_view(pdf, family_dir / views[name]["image"], box, title,
                        trim_alpha=name == "top")


def grid_boxes(page_width: float, page_height: float) -> list[tuple]:
    gap = 8
    width = (page_width - 56 - gap * 2) / 3
    height = (page_height - 130 - gap * 2) / 3
    return [(28 + col * (width + gap), page_height - 92 - (row + 1) * height - row * gap,
             width, height) for row in range(3) for col in range(3)]


def load_geometry(
    spec_path: Path,
    parts_dir: Path,
) -> tuple[list[dict], dict[str, np.ndarray], dict[str, np.ndarray]]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    parts, envelope = canonicalize(load_stl_parts(parts_dir))
    if not np.allclose(envelope, np.asarray(spec["envelope_wdh_mm"], float), atol=0.2):
        raise ValueError(f"GT mesh envelope {envelope} does not match specification")
    part_by_occurrence = match_parts(parts, spec)
    aligned_by_occurrence: dict[str, np.ndarray] = {}
    dimensions_by_id: dict[str, np.ndarray] = {}
    for occurrence_id, triangles in part_by_occurrence.items():
        aligned, dimensions = align_to_local_axes(triangles)
        aligned_by_occurrence[occurrence_id] = aligned
        dimensions_by_id[occurrence_id] = dimensions
    families = drawing_families(spec)
    return families, aligned_by_occurrence, dimensions_by_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--parts-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--renders-dir", required=True, type=Path)
    parser.add_argument("--step-files-dir", type=Path,
                        help="Input directory containing family_XXX.step files; only existing files are announced")
    parser.add_argument("--difficult-families-json", type=Path,
                        help="Family-ID array rendered as three Blender views plus supplied STEP")
    parser.add_argument("--all-family-depth-views", action="store_true",
                        help="Draw all non-STEP families with three orthographic and four Blender views")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--family-ids", nargs="*", default=None)
    parser.add_argument("--product-title", default="CAD PARTS")
    parser.add_argument(
        "--footer-label",
        default=None,
    )
    parser.add_argument(
        "--detail-note",
        default="Orthographic views show the part shape; dimensions are in mm.",
    )
    args = parser.parse_args()
    if args.footer_label is None:
        args.footer_label = f"{args.product_title} | ADAPTIVE PART VIEW SET | UNITS: mm"

    geometry_families, aligned_by_occurrence, dimensions_by_id = load_geometry(
        args.spec,
        args.parts_dir,
    )
    manifest_by_id = {
        family["id"]: family
        for family in json.loads(args.manifest.read_text(encoding="utf-8"))["families"]
    }
    families = []
    for family in geometry_families:
        if family["id"] not in manifest_by_id:
            raise ValueError(f"Family missing from render manifest: {family['id']}")
        if manifest_by_id[family["id"]].get("depth_rule_version") != DEPTH_RULE_VERSION:
            raise ValueError("Outdated depth-view manifest; rerun export_aligned_family_meshes.py")
        families.append({**family, **manifest_by_id[family["id"]]})
    if args.family_ids:
        selected = set(args.family_ids)
        families = [family for family in families if family["id"] in selected]
        missing = selected - {family["id"] for family in families}
        if missing:
            raise ValueError(f"Unknown family IDs: {sorted(missing)}")
    if args.all_family_depth_views:
        for family in families:
            family["show_depth_views"] = True
    if args.step_files_dir:
        if not args.step_files_dir.is_dir():
            raise ValueError(f"STEP file directory not found: {args.step_files_dir}")
        for family in families:
            family["provided_step"] = (args.step_files_dir / (family["id"] + ".step")).is_file()
    if args.difficult_families_json:
        selected = json.loads(args.difficult_families_json.read_text())
        if (not isinstance(selected, list)
                or any(not isinstance(fid, str) for fid in selected)
                or len(selected) != len(set(selected))):
            raise ValueError("Difficult families must be a unique family-ID array")
        unknown = set(selected) - {family["id"] for family in families}
        if unknown:
            raise ValueError(f"Unknown difficult family IDs: {sorted(unknown)}")
        for family in families:
            family["difficult_step"] = family["id"] in selected
            if family["difficult_step"] and not family.get("provided_step"):
                raise ValueError(f"Difficult family has no supplied STEP: {family['id']}")
    page_width, page_height = landscape(A4)
    simple_families = [family for family in families if not family["show_depth_views"]
                       and not family.get("difficult_step")]
    complex_families = [family for family in families if family["show_depth_views"]
                        and not family.get("difficult_step")]
    difficult_families = [family for family in families if family.get("difficult_step")]
    simple_pages = [simple_families[index:index + 2] for index in range(0, len(simple_families), 2)]
    index_pages = index_page_groups(families, page_height)
    total_pages = len(index_pages) + len(simple_pages) + len(complex_families) + len(difficult_families)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # PNGs are already compressed; ASCII85 adds a second, slower encoding layer.
    rl_config.useA85 = 0
    pdf = canvas.Canvas(str(args.output), pagesize=(page_width, page_height), pageCompression=1)
    pdf.setTitle(f"{args.product_title.title()} - Adaptive part views")
    pdf.setSubject("Full-mesh feature-edge drawings with complexity-gated opaque depth views")

    footer.label = args.footer_label
    draw_cover(pdf, families, index_pages[0], page_width, page_height, args.product_title)
    footer(pdf, 1, total_pages, page_width)
    pdf.showPage()
    page_number = 2
    family_offset = len(index_pages[0])
    for index_families in index_pages[1:]:
        draw_index_continuation(pdf, index_families, page_width, page_height,
                                family_offset + 1, family_offset + len(index_families))
        footer(pdf, page_number, total_pages, page_width)
        pdf.showPage()
        page_number += 1
        family_offset += len(index_families)
    block_height = (page_height - 70) / 2
    for pair in simple_pages:
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(28, page_height - 28, "STANDARD PART FAMILY DETAILS | THREE VIEWS")
        for position, family in enumerate(pair):
            occurrence_id = family["occurrence_ids"][0]
            y = page_height - 45 - (position + 1) * block_height + 5
            step_note = (f"STEP PROVIDED: provided_part_files/{family['id']}.step"
                         if family.get("provided_step") else args.detail_note)
            draw_family(
                pdf,
                family,
                aligned_by_occurrence[occurrence_id],
                dimensions_by_id,
                (28, y, page_width - 56, block_height - 10),
                step_note,
            )
        footer(pdf, page_number, total_pages, page_width)
        pdf.showPage()
        page_number += 1

    for family in complex_families:
        occurrence_id = family["occurrence_ids"][0]
        draw_complex_family_page(
            pdf,
            family,
            aligned_by_occurrence[occurrence_id],
            dimensions_by_id,
            args.renders_dir,
            page_width,
            page_height,
            (f"STEP PROVIDED: provided_part_files/{family['id']}.step"
             if family.get("provided_step") else None),
        )
        footer(pdf, page_number, total_pages, page_width)
        pdf.showPage()
        page_number += 1
    for family in difficult_families:
        draw_difficult_family_page(pdf, family, args.renders_dir, page_width, page_height)
        footer(pdf, page_number, total_pages, page_width)
        pdf.showPage()
        page_number += 1
    pdf.save()
    print(json.dumps({"output": str(args.output), "pages": total_pages, "families": len(families)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
