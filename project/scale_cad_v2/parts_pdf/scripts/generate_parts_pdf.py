#!/usr/bin/env python3
"""Generate a human-readable 2D part drawing set from private GT meshes.

The resulting PDF intentionally publishes normal engineering inputs (family,
quantity, overall dimensions, and orthographic linework) rather than the
machine-oriented volume/area/placement fingerprints used by the verifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import trimesh
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from scipy.optimize import linear_sum_assignment
from render_three_views import load_stl_parts


INK = colors.HexColor("#303642")
MUTED = colors.HexColor("#687080")
LIGHT = colors.HexColor("#D6DAE1")
PANEL = colors.HexColor("#F7F8FA")
ACCENT = colors.HexColor("#376FAD")

FEATURE_ANGLE_DEGREES = 2.0

from mechanical_families import drawing_families


def canonicalize(parts: list[np.ndarray]) -> tuple[list[np.ndarray], np.ndarray]:
    """Put GT STL triangles in the private spec's X/Y/Z engineering frame."""
    vertices = np.concatenate(parts).reshape(-1, 3)
    origin = vertices.min(axis=0)
    dimensions = vertices.max(axis=0) - origin
    return [part - origin for part in parts], dimensions


def fmt(value: float) -> str:
    text = f"{float(value):.1f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def mesh_features(triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    vertices = triangles.reshape(-1, 3)
    low = vertices.min(axis=0)
    high = vertices.max(axis=0)
    return high - low, (high + low) / 2.0, float(np.prod(np.maximum(high - low, 1e-9)))


def _axis_sign(vertices: np.ndarray, axis: np.ndarray, scale: float) -> float:
    """Choose a deterministic positive direction for an otherwise unsigned axis."""
    coordinates = vertices @ axis
    third_moment = float(np.mean(coordinates**3))
    if abs(third_moment) > max(scale**3, 1.0) * 1e-10:
        return 1.0 if third_moment >= 0.0 else -1.0

    positive_reach = float(coordinates.max(initial=0.0))
    negative_reach = float(-coordinates.min(initial=0.0))
    if abs(positive_reach - negative_reach) > max(scale, 1.0) * 1e-5:
        return 1.0 if positive_reach >= negative_reach else -1.0

    dominant_world_axis = int(np.argmax(np.abs(axis)))
    return 1.0 if axis[dominant_world_axis] >= 0.0 else -1.0


def align_to_local_axes(triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Reuse exact-input alignment between private build stages when enabled."""
    cache_dir = os.environ.get("CAD_PARTS_ALIGNMENT_CACHE")
    if not cache_dir:
        return _align_to_local_axes_uncached(triangles)
    values = np.ascontiguousarray(triangles)
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    digest.update(f"{np.__version__}:{trimesh.__version__}:{values.dtype}:{values.shape}".encode())
    digest.update(values.tobytes())
    cache = Path(cache_dir) / (digest.hexdigest() + ".npz")
    if cache.is_file():
        with np.load(cache, allow_pickle=False) as saved:
            return saved["aligned"], saved["dimensions"]
    aligned, dimensions = _align_to_local_axes_uncached(triangles)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_name(f"{cache.stem}.{os.getpid()}.tmp")
    with temporary.open("wb") as stream:
        np.savez_compressed(stream, aligned=aligned, dimensions=dimensions)
    temporary.replace(cache)
    return aligned, dimensions


def _align_to_local_axes_uncached(triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Center a part and align its longest/second/shortest exterior axes to X/Y/Z."""
    mesh = trimesh.Trimesh(
        vertices=triangles.reshape(-1, 3),
        faces=np.arange(len(triangles) * 3, dtype=int).reshape(-1, 3),
        process=True,
    )
    center = np.asarray(mesh.center_mass, float)
    if not np.all(np.isfinite(center)):
        center = np.asarray(mesh.centroid, float)

    centered_mesh_vertices = np.asarray(mesh.vertices, float) - center
    centered_triangles = np.asarray(triangles, float) - center
    # Keep an already axis-aligned part exactly on the assembly axes. Otherwise
    # use its minimum-volume exterior box. This prevents asymmetric ribs or
    # cut-outs from tilting a nominally rectangular housing.
    assembly_spans = np.ptp(centered_mesh_vertices, axis=0)
    to_origin, box_spans = trimesh.bounds.oriented_bounds(mesh, angle_digits=2, ordered=True)
    assembly_box_volume = float(np.prod(np.maximum(assembly_spans, 1e-9)))
    oriented_box_volume = float(np.prod(np.maximum(box_spans, 1e-9)))
    if assembly_box_volume <= oriented_box_volume * 1.005:
        exterior_axes = np.eye(3)
    else:
        exterior_axes = np.asarray(to_origin[:3, :3], float).T
    preliminary = centered_mesh_vertices @ exterior_axes
    spans = np.ptp(preliminary, axis=0)
    order = np.argsort(-spans, kind="stable")
    basis = exterior_axes[:, order].copy()
    scale = float(max(spans.max(initial=0.0), 1.0))

    # Fix X and Y signs deterministically, then derive Z to retain a right-handed
    # coordinate system. Symmetric parts remain visually equivalent under a flip.
    basis[:, 0] *= _axis_sign(centered_mesh_vertices, basis[:, 0], scale)
    basis[:, 1] *= _axis_sign(centered_mesh_vertices, basis[:, 1], scale)
    basis[:, 2] = np.cross(basis[:, 0], basis[:, 1])
    basis[:, 2] /= np.linalg.norm(basis[:, 2])

    aligned = centered_triangles @ basis
    low = aligned.reshape(-1, 3).min(axis=0)
    aligned -= low
    dimensions = np.ptp(aligned.reshape(-1, 3), axis=0)
    if dimensions[0] + 1e-6 < dimensions[1] or dimensions[1] + 1e-6 < dimensions[2]:
        raise ValueError(f"Local-axis dimensions are not ordered X >= Y >= Z: {dimensions}")
    return aligned, dimensions


def match_parts(parts: list[np.ndarray], spec: dict) -> dict[str, np.ndarray]:
    occurrences = spec["occurrences"]
    actual = [mesh_features(part) for part in parts]
    cost = np.zeros((len(occurrences), len(actual)), float)
    envelope = np.asarray(spec["envelope_wdh_mm"], float)
    for row, occurrence in enumerate(occurrences):
        target_size = np.asarray(occurrence["size_wdh_mm"], float)
        target_center = np.asarray(occurrence["center_wdh_mm"], float)
        for column, (size, center, _box_volume) in enumerate(actual):
            size_error = np.linalg.norm((size - target_size) / np.maximum(target_size, 1.0))
            center_error = np.linalg.norm((center - target_center) / envelope)
            cost[row, column] = 8.0 * size_error + center_error
    rows, columns = linear_sum_assignment(cost)
    assignments = [(int(row), int(column)) for row, column in zip(rows, columns)]
    if len(assignments) != len(occurrences):
        raise ValueError("Could not match every occurrence to a GT mesh")
    mapping = {occurrences[row]["id"]: parts[column] for row, column in assignments}
    worst = max(float(cost[row, column]) for row, column in assignments)
    if worst > 0.05:
        raise ValueError(f"GT mesh matching is ambiguous; worst normalized cost={worst:.6f}")
    return mapping


def dimension_variants(family: dict, dimensions_by_id: dict[str, np.ndarray]) -> list[np.ndarray]:
    """Return local X/Y/Z boxes unique at the PDF's published 0.1 mm precision."""
    variants: list[np.ndarray] = []
    seen: set[tuple[str, str, str]] = set()
    for occurrence_id in family["occurrence_ids"]:
        dims = dimensions_by_id[occurrence_id]
        key = tuple(fmt(value) for value in dims)
        if key not in seen:
            seen.add(key)
            variants.append(dims)
    return variants


def feature_edges(triangles: np.ndarray) -> list[tuple[np.ndarray, np.ndarray, bool]]:
    """Return boundary/sharp mesh edges; coplanar tessellation diagonals vanish."""
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1e-10
    normals[valid] /= lengths[valid, None]
    adjacency: dict[tuple[tuple[float, ...], tuple[float, ...]], list[np.ndarray]] = defaultdict(list)
    points: dict[tuple[tuple[float, ...], tuple[float, ...]], tuple[np.ndarray, np.ndarray]] = {}
    for index, triangle in enumerate(triangles):
        if not valid[index]:
            continue
        for first, second in ((0, 1), (1, 2), (2, 0)):
            a, b = triangle[first], triangle[second]
            ka, kb = tuple(np.round(a, 4)), tuple(np.round(b, 4))
            key = (ka, kb) if ka <= kb else (kb, ka)
            adjacency[key].append(normals[index])
            points[key] = (a.copy(), b.copy())
    result = []
    threshold = math.cos(math.radians(FEATURE_ANGLE_DEGREES))
    for key, adjacent in adjacency.items():
        boundary = len(adjacent) == 1
        sharp = boundary
        if len(adjacent) >= 2:
            sharp = min(
                float(np.dot(adjacent[0], normal))
                for normal in adjacent[1:]
            ) < threshold
        if sharp:
            result.append((*points[key], boundary))
    return result


def draw_arrow(c: canvas.Canvas, x1: float, y1: float, x2: float, y2: float) -> None:
    c.setStrokeColor(MUTED)
    c.setLineWidth(0.55)
    c.line(x1, y1, x2, y2)
    angle = math.atan2(y2 - y1, x2 - x1)
    for x, y, direction in ((x1, y1, angle), (x2, y2, angle + math.pi)):
        for delta in (-0.42, 0.42):
            c.line(x, y, x + 5 * math.cos(direction + delta), y + 5 * math.sin(direction + delta))


def draw_view(
    c: canvas.Canvas,
    triangles: np.ndarray,
    axes: tuple[int, int],
    box: tuple[float, float, float, float],
    title: str,
    horizontal_label: str,
    vertical_label: str,
    edges: list[tuple[np.ndarray, np.ndarray, bool]],
) -> None:
    x, y, width, height = box
    c.setFillColor(PANEL)
    c.setStrokeColor(LIGHT)
    c.roundRect(x, y, width, height, 4, fill=1, stroke=1)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(INK)
    c.drawString(x + 8, y + height - 14, title)
    c.setFont("Helvetica", 6.5)
    c.setFillColor(MUTED)
    c.drawRightString(x + width - 8, y + height - 14, "NTS")

    projected = triangles[:, :, list(axes)].copy()
    projected -= projected.reshape(-1, 2).min(axis=0)
    extent = projected.reshape(-1, 2).max(axis=0)
    drawing_left = x + 31
    drawing_bottom = y + 31
    drawing_width = width - 45
    drawing_height = height - 55
    scale = min(drawing_width / max(extent[0], 1e-6), drawing_height / max(extent[1], 1e-6))
    offset_x = drawing_left + (drawing_width - extent[0] * scale) / 2
    offset_y = drawing_bottom + (drawing_height - extent[1] * scale) / 2

    axis_minimum = triangles[:, :, list(axes)].reshape(-1, 2).min(axis=0)
    c.setLineCap(1)
    for first, second, boundary in edges:
        p1 = (offset_x + (first[axes[0]] - axis_minimum[0]) * scale,
              offset_y + (first[axes[1]] - axis_minimum[1]) * scale)
        p2 = (offset_x + (second[axes[0]] - axis_minimum[0]) * scale,
              offset_y + (second[axes[1]] - axis_minimum[1]) * scale)
        # Do not discard short projected segments.  Curved B-rep faces are
        # tessellated into many short feature edges; at whole-part scale a
        # complete small hole can otherwise vanish because every arc segment
        # falls below a fixed page-space cutoff.
        c.setStrokeColor(INK if boundary else MUTED)
        c.setLineWidth(0.65 if boundary else 0.35)
        c.line(p1[0], p1[1], p2[0], p2[1])

    c.setStrokeColor(LIGHT)
    c.setLineWidth(.4)
    right=offset_x+extent[0]*scale; top=offset_y+extent[1]*scale
    for xx in (offset_x,right): c.line(xx,offset_y,xx,y+17)
    for yy in (offset_y,top): c.line(x+17,yy,offset_x,yy)
    draw_arrow(c,offset_x,y+17,right,y+17)
    draw_arrow(c,x+17,offset_y,x+17,top)
    c.setFillColor(MUTED); c.setFont("Helvetica",6.5)
    c.drawCentredString((offset_x+right)/2,y+20,horizontal_label)
    c.saveState(); c.translate(x+14,(offset_y+top)/2); c.rotate(90)
    c.drawCentredString(0,0,vertical_label); c.restoreState()


def draw_family(
    c: canvas.Canvas,
    family: dict,
    triangles: np.ndarray,
    dimensions_by_id: dict[str, np.ndarray],
    box,
    detail_note: str = "Use the supplied detail/assembly drawings for teeth, bores, callouts and placement.",
) -> None:
    x, y, width, height = box
    c.setStrokeColor(LIGHT)
    c.setFillColor(colors.white)
    c.roundRect(x, y, width, height, 7, fill=1, stroke=1)
    c.setFillColor(ACCENT)
    c.roundRect(x, y + height - 29, width, 29, 7, fill=1, stroke=0)
    c.rect(x, y + height - 29, width, 8, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x + 12, y + height - 19, family["id"].upper())
    c.setFont("Helvetica", 8)
    source_ids = family["source_family_ids"]
    source_note = ""
    if len(source_ids) > 1:
        aliases = ", ".join(item.removeprefix("family_").upper() for item in source_ids[1:])
        source_note = f" | INCLUDES {aliases}"
    c.drawRightString(x + width - 12, y + height - 18, f"QTY {family['quantity']}{source_note}")

    variants = dimension_variants(family, dimensions_by_id)
    dims = dimensions_by_id[family["occurrence_ids"][0]]
    variants_text = ";  ".join(" x ".join(fmt(value) for value in variant) for variant in variants)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 7.2)
    label = "LOCAL-AXIS X x Y x Z"
    if len(variants) > 1:
        label += " VARIANTS"
    c.drawString(x + 12, y + height - 43, f"{label}: {variants_text} mm")

    view_y = y + 31
    view_height = height - 84
    gap = 8
    view_width = (width - 24 - gap * 2) / 3
    edges = feature_edges(triangles)
    draw_view(c, triangles, (0, 2), (x + 8, view_y, view_width, view_height),
              "FRONT", f"X {fmt(dims[0])}", f"Z {fmt(dims[2])}", edges)
    draw_view(c, triangles, (1, 2), (x + 8 + view_width + gap, view_y, view_width, view_height),
              "SIDE", f"Y {fmt(dims[1])}", f"Z {fmt(dims[2])}", edges)
    draw_view(c, triangles, (0, 1), (x + 8 + (view_width + gap) * 2, view_y, view_width, view_height),
              "TOP", f"X {fmt(dims[0])}", f"Y {fmt(dims[1])}", edges)

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.5)
    note = detail_note
    if len(source_ids) > 1:
        note += " Rotation-equivalent installed occurrences are consolidated."
    c.drawString(x + 12, y + 12, note)


def footer(c: canvas.Canvas, page_number: int, total_pages: int, page_width: float,
           label: str) -> None:
    c.setStrokeColor(LIGHT)
    c.line(28, 23, page_width - 28, 23)
    c.setFont("Helvetica", 6.5)
    c.setFillColor(MUTED)
    c.drawString(28, 12, label)
    c.drawRightString(page_width - 28, 12, f"PAGE {page_number} / {total_pages}")


def draw_cover(
    c: canvas.Canvas,
    families: list[dict],
    dimensions_by_id: dict[str, np.ndarray],
    page_width: float,
    page_height: float,
    product_title: str,
    index_families: list[dict],
) -> None:
    c.setFillColor(ACCENT)
    c.rect(0, page_height - 110, page_width, 110, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(34, page_height - 58, product_title)
    c.setFont("Helvetica", 13)
    c.drawString(34, page_height - 83, "PART DRAWING SET")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(34, page_height - 139, "DOCUMENT SCOPE")
    c.setFont("Helvetica", 8)
    lines = [
        f"{len(families)} geometry families, {sum(item['quantity'] for item in families)} total occurrences.",
        "Rotation-equivalent source families are consolidated; quantities are summed.",
        "Every part is centered and exterior-axis aligned to local +X/+Y/+Z before projection.",
        "The longest, second-longest, and shortest oriented-box spans map to local X, Y, and Z.",
        "Local X/Y/Z dimensions are measured after alignment; assembly placement comes from the assembly/DXF views.",
        "Each detail sheet contains front, side, and top orthographic linework with overall dimensions.",
        "Views are independently fitted and marked NTS; written dimensions are authoritative.",
        "Detailed teeth, bores, callouts, and assembly locations remain in the supplied engineering drawings.",
    ]
    for index, line in enumerate(lines):
        c.drawString(34, page_height - 158 - index * 13, line)

    c.setFont("Helvetica-Bold", 9)
    c.drawString(34, page_height - 266, "FAMILY INDEX")
    table_x, table_y = 34, page_height - 287
    columns = 3
    column_width = (page_width - 68) / columns
    row_height = 14
    rows = math.ceil(len(index_families) / columns)
    for column in range(columns):
        for row in range(rows):
            index = column * rows + row
            if index >= len(index_families):
                continue
            family = index_families[index]
            dims = dimensions_by_id[family["occurrence_ids"][0]]
            x = table_x + column * column_width
            y = table_y - row * row_height
            c.setStrokeColor(LIGHT)
            c.line(x, y - 4, x + column_width - 8, y - 4)
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 7)
            c.drawString(x, y + 2, family["id"].upper())
            c.setFont("Helvetica", 6.5)
            c.drawString(x + 63, y + 2, f"QTY {family['quantity']}")
            c.setFillColor(MUTED)
            variants = dimension_variants(family, dimensions_by_id)
            if len(variants) == 1:
                dimension_note = f"{fmt(dims[0])} x {fmt(dims[1])} x {fmt(dims[2])}"
            else:
                dimension_note = f"{len(variants)} X/Y/Z variants"
            c.drawRightString(x + column_width - 12, y + 2, dimension_note)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--parts-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--product-title", default="CAD PARTS")
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    raw_parts = load_stl_parts(args.parts_dir)
    parts, dimensions = canonicalize(raw_parts)
    if not np.allclose(dimensions, np.asarray(spec["envelope_wdh_mm"], float), atol=0.2):
        raise ValueError(f"GT mesh envelope {dimensions} does not match specification")
    occurrences_by_id = {item["id"]: item for item in spec["occurrences"]}
    part_by_occurrence = match_parts(parts, spec)
    aligned_by_occurrence: dict[str, np.ndarray] = {}
    dimensions_by_id: dict[str, np.ndarray] = {}
    for occurrence_id, triangles in part_by_occurrence.items():
        aligned, local_dimensions = align_to_local_axes(triangles)
        aligned_by_occurrence[occurrence_id] = aligned
        dimensions_by_id[occurrence_id] = local_dimensions
    families = drawing_families(spec)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    page_width, page_height = landscape(A4)
    table_y = page_height - 287
    max_rows = max(1, math.floor((table_y - 35) / 14) + 1)
    index_page_size = 3 * max_rows
    index_pages = [families[index:index + index_page_size]
                   for index in range(0, len(families), index_page_size)] or [[]]
    total_pages = len(index_pages) + math.ceil(len(families) / 2)
    c = canvas.Canvas(str(args.output), pagesize=(page_width, page_height), pageCompression=1)
    c.setTitle(f"{args.product_title} - Part Drawing Set")
    c.setSubject(f"Dimensioned orthographic drawings for {len(families)} part families")

    footer_label = f"{args.product_title} | PART DRAWING SET | UNITS: mm"
    for page_number, index_families in enumerate(index_pages, 1):
        draw_cover(c, families, dimensions_by_id, page_width, page_height,
                   args.product_title, index_families)
        footer(c, page_number, total_pages, page_width, footer_label)
        c.showPage()

    block_height = (page_height - 70) / 2
    for index, family in enumerate(families):
        position = index % 2
        page_number = len(index_pages) + 1 + index // 2
        if position == 0:
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold", 12)
            c.drawString(28, page_height - 28, "PART FAMILY DETAILS")
        occurrence = occurrences_by_id[family["occurrence_ids"][0]]
        triangles = aligned_by_occurrence[occurrence["id"]]
        y = page_height - 45 - (position + 1) * block_height + 5
        draw_family(
            c,
            family,
            triangles,
            dimensions_by_id,
            (28, y, page_width - 56, block_height - 10),
        )
        if position == 1 or index == len(families) - 1:
            footer(c, page_number, total_pages, page_width, footer_label)
            c.showPage()

    c.save()
    print(json.dumps({"output": str(args.output), "pages": total_pages, "families": len(families)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
