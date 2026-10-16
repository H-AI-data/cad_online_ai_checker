#!/usr/bin/env python3
"""Draw a dimension-complete plate pilot from a matching STEP feature cache and STL.

This deliberately accepts only an axis-aligned plate with rectangular through
notches, circular through holes, and two mirrored underside end bevels. Unknown
geometry fails the volume/profile audit instead of producing a misleading PDF.
"""

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import struct

from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfgen import canvas


BLUE = colors.HexColor("#214E78")
RED = colors.HexColor("#D9272E")
GREY = colors.HexColor("#64758A")
PALE = colors.HexColor("#EAF1F7")
PAGE_W, PAGE_H = landscape(A3)


def close(a, b, tolerance=0.015):
    return abs(a - b) <= tolerance


def fmt(value):
    return f"{value:.2f}".rstrip("0").rstrip(".")


def read_stl(path):
    raw = Path(path).read_bytes()
    if len(raw) < 84:
        raise ValueError("STL is too short")
    count = struct.unpack_from("<I", raw, 80)[0]
    if len(raw) != 84 + 50 * count:
        raise ValueError("Expected a binary STL with a complete triangle table")
    triangles = []
    for index in range(count):
        values = struct.unpack_from("<12f", raw, 84 + 50 * index)
        triangles.append((values[3:6], values[6:9], values[9:12]))
    return triangles


def signed_area(ring):
    return sum(
        p[0] * q[1] - q[0] * p[1]
        for p, q in zip(ring, ring[1:])
    ) / 2


def face_rings(triangles, z):
    edges = Counter()
    for tri in triangles:
        if not all(close(point[2], z) for point in tri):
            continue
        pts = [tuple(round(value, 3) for value in point[:2]) for point in tri]
        for i in range(3):
            if pts[i] != pts[(i + 1) % 3]:
                edges[tuple(sorted((pts[i], pts[(i + 1) % 3])))] += 1
    boundary = {edge for edge, count in edges.items() if count == 1}
    adj = defaultdict(list)
    for a, b in boundary:
        adj[a].append(b)
        adj[b].append(a)
    if not boundary or any(len(neighbors) != 2 for neighbors in adj.values()):
        raise ValueError(f"Could not trace closed planar contours at z={fmt(z)}")
    rings = []
    while boundary:
        start, second = next(iter(boundary))
        boundary.remove((start, second))
        ring = [start, second]
        while ring[-1] != start:
            previous, current = ring[-2:]
            candidate = next(
                (point for point in adj[current]
                 if point != previous and tuple(sorted((current, point))) in boundary),
                None,
            )
            if candidate is None:
                raise ValueError("Broken planar contour")
            boundary.remove(tuple(sorted((current, candidate))))
            ring.append(candidate)
        rings.append(ring)
    return sorted(rings, key=lambda ring: abs(signed_area(ring)), reverse=True)


def corners(ring):
    points = ring[:-1]
    result = []
    for i, point in enumerate(points):
        before = points[i - 1]
        after = points[(i + 1) % len(points)]
        v = (point[0] - before[0], point[1] - before[1])
        w = (after[0] - point[0], after[1] - point[1])
        if abs(v[0] * w[1] - v[1] * w[0]) > 0.001:
            result.append(point)
    return result


def top_notches(outer, width):
    result = []
    n = len(outer)
    for i, q in enumerate(outer):
        p, r, s = outer[i - 1], outer[(i + 1) % n], outer[(i + 2) % n]
        if (close(p[1], width) and close(s[1], width)
                and close(q[1], r[1]) and 0.05 < q[1] < width - 0.1
                and close(p[0], q[0]) and close(r[0], s[0])):
            left, right = sorted((q[0], r[0]))
            if right - left > 1:
                result.append((left, right, width - q[1]))
    return sorted(set(result))


def underside_reliefs(outer, length, width, triangles, thickness):
    # Bottom outline has two missing corners. The intermediate vertex height
    # identifies the bevel's raised, outer edge; volume checks its slope.
    left_y = min(y for x, y in outer if close(x, 0))
    right_y = min(y for x, y in outer if close(x, length))
    bottom_x = [x for x, y in outer if close(y, 0)]
    if not bottom_x or not close(left_y, right_y):
        raise ValueError("Expected mirrored underside corner reliefs")
    left_run = min(bottom_x)
    right_run = length - max(bottom_x)
    if not (0 < left_run < length / 4 and close(left_run, right_run)
            and 0 < left_y < width / 2):
        raise ValueError("Underside relief dimensions are outside the pilot geometry")
    heights = set()
    for tri in triangles:
        for x, y, z in tri:
            if ((close(x, 0) or close(x, length))
                    and (close(y, 0) or close(y, left_y))
                    and z > 0.02 and z < thickness - 0.02):
                heights.add(round(z, 3))
    if len(heights) != 1:
        raise ValueError("Expected one intermediate height for the end bevels")
    return left_run, left_y, heights.pop()


def step_holes(feature_path, family, thickness):
    data = json.loads(Path(feature_path).read_text())
    part = next((item for item in data if item.get("family") == family), None)
    if part is None:
        raise ValueError(f"No matching STEP-derived features for {family}")
    groups = defaultdict(set)
    for circle in part["circles"]:
        axis = circle["axis"]
        if abs(axis[2]) < 0.995 or 2 * circle["radius"] < 3:
            continue
        x, y, z = circle["center"]
        key = (round(x, 2), round(y, 2), round(circle["radius"], 3))
        if close(z, 0):
            groups[key].add("bottom")
        if close(z, thickness):
            groups[key].add("top")
    if not groups or any(sides != {"bottom", "top"} for sides in groups.values()):
        raise ValueError("Circular features are not confirmed on both plate faces")
    return sorted(groups)


def mesh_volume(triangles):
    total = 0.0
    for a, b, c in triangles:
        cross = (b[1] * c[2] - b[2] * c[1],
                 b[2] * c[0] - b[0] * c[2],
                 b[0] * c[1] - b[1] * c[0])
        total += sum(a[i] * cross[i] for i in range(3)) / 6
    return abs(total)


def extract_geometry(stl, features, family):
    triangles = read_stl(stl)
    vertices = [point for tri in triangles for point in tri]
    bounds = [(min(point[i] for point in vertices), max(point[i] for point in vertices))
              for i in range(3)]
    if any(not close(low, 0) for low, _ in bounds):
        raise ValueError("Aligned STL must begin at local XYZ origin")
    length, width, thickness = (high for _, high in bounds)
    if thickness > width / 2 or width > length / 2:
        raise ValueError("This pilot script only supports thin, long plates")
    top, bottom = (face_rings(triangles, z) for z in (thickness, 0))
    if len(top) != len(bottom):
        raise ValueError("Top and underside contour counts differ")
    top_outer, bottom_outer = corners(top[0]), corners(bottom[0])
    notches = top_notches(top_outer, width)
    if not notches:
        raise ValueError("No supported through notch found")
    top_area = length * width - sum((right - left) * depth
                                   for left, right, depth in notches)
    if not close(abs(signed_area(top[0])), top_area, 0.1):
        raise ValueError("Unexplained top outline geometry")
    run, reach, rise = underside_reliefs(
        bottom_outer, length, width, triangles, thickness)
    bottom_area = top_area - 2 * run * reach
    if not close(abs(signed_area(bottom[0])), bottom_area, 0.1):
        raise ValueError("Unexplained underside outline geometry")
    holes = step_holes(features, family, thickness)
    if len(top) - 1 != len(holes):
        raise ValueError("Number of STL inner loops does not match STEP holes")
    for face_name, face in (("top", top), ("bottom", bottom)):
        unmatched = list(holes)
        for ring in face[1:]:
            xs, ys = zip(*ring)
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            area = abs(signed_area(ring))
            matching = [hole for hole in unmatched
                        if close(cx, hole[0], 0.03)
                        and close(cy, hole[1], 0.03)
                        and abs(area - math.pi * hole[2] ** 2)
                        <= max(0.15, 0.01 * area)]
            if len(matching) != 1:
                raise ValueError(f"{face_name} STL loop does not match a STEP hole")
            unmatched.remove(matching[0])
        if unmatched:
            raise ValueError(f"Missing {face_name} STL hole loops")
    expected_volume = (top_area * thickness
                       - math.pi * sum(r * r for _, _, r in holes) * thickness
                       - run * reach * rise)
    actual_volume = mesh_volume(triangles)
    relative_error = abs(actual_volume - expected_volume) / expected_volume
    if relative_error > 0.0001:
        raise ValueError(f"Unexplained 3D geometry: volume error {relative_error:.2%}")
    return dict(length=length, width=width, thickness=thickness,
                top_outer=top_outer, bottom_outer=bottom_outer,
                notches=notches, holes=holes, relief=(run, reach, rise),
                expected_volume=expected_volume, mesh_volume=actual_volume,
                relative_error=relative_error)


def text(c, x, y, label, size=10, color=BLUE, font="Helvetica"):
    c.setFillColor(color)
    c.setFont(font, size)
    c.drawString(x, y, label)


def line(c, x1, y1, x2, y2, color=BLUE, width=0.9):
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(x1, y1, x2, y2)


def dim_horizontal(c, x1, x2, y, base_y, label):
    for x in (x1, x2):
        line(c, x, base_y, x, y - 7, RED, 0.65)
    line(c, x1, y, x2, y, RED)
    for x, direction in ((x1, 1), (x2, -1)):
        path = c.beginPath()
        path.moveTo(x, y)
        path.lineTo(x + 6 * direction, y + 2.3)
        path.lineTo(x + 6 * direction, y - 2.3)
        path.close()
        c.setFillColor(RED)
        c.drawPath(path, stroke=0, fill=1)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(RED)
    c.drawCentredString((x1 + x2) / 2, y + 5, label)


def dim_vertical(c, x, y1, y2, base_x, label):
    for y in (y1, y2):
        line(c, base_x, y, x + 8, y, RED, 0.65)
    line(c, x, y1, x, y2, RED)
    for y, direction in ((y1, 1), (y2, -1)):
        path = c.beginPath()
        path.moveTo(x, y)
        path.lineTo(x - 2.3, y + 6 * direction)
        path.lineTo(x + 2.3, y + 6 * direction)
        path.close()
        c.setFillColor(RED)
        c.drawPath(path, stroke=0, fill=1)
    text(c, x - 27, (y1 + y2) / 2 - 3, label, 10, RED, "Helvetica-Bold")


def header(c, family, page, subtitle):
    c.setFillColor(BLUE)
    c.rect(0, PAGE_H - 78, PAGE_W, 78, fill=1, stroke=0)
    text(c, 58, PAGE_H - 45, f"{family.upper()}  |  DETAILED PART DIMENSIONS",
         19, colors.white, "Helvetica-Bold")
    text(c, 59, PAGE_H - 66, subtitle, 10, colors.white)
    line(c, 54, 42, PAGE_W - 54, 42, GREY, 0.5)
    text(c, 57, 27, "All values in mm  |  Local datum: left/lower corner of TOP view  |  Views NTS", 8, GREY)
    text(c, PAGE_W - 112, 27, f"PAGE {page} / 2", 8, GREY)


def table(c, x, y, widths, headings, rows, row_height=24):
    total = sum(widths)
    c.setFillColor(PALE)
    c.roundRect(x, y - 6, total, 25, 4, fill=1, stroke=0)
    offset = x
    for width, title in zip(widths, headings):
        text(c, offset + 6, y + 3, title, 9, BLUE, "Helvetica-Bold")
        offset += width
    for index, row in enumerate(rows):
        row_y = y - (index + 1) * row_height
        if index % 2 == 1:
            c.setFillColor(colors.HexColor("#F7F9FC"))
            c.rect(x, row_y - 5, total, row_height, fill=1, stroke=0)
        offset = x
        for width, value in zip(widths, row):
            text(c, offset + 6, row_y + 3, str(value), 9, BLUE)
            offset += width
    return y - len(rows) * row_height


def page_one(c, family, g):
    header(c, family, 1, "Top outline, every through hole, and the complete edge-notch schedule")
    L, W, T = g["length"], g["width"], g["thickness"]
    x0, y0, scale = 128, 566, min((PAGE_W - 250) / L, 2.35)
    text(c, 61, 724, "TOP  |  +Z", 12, BLUE, "Helvetica-Bold")
    path = c.beginPath()
    for i, (x, y) in enumerate(g["top_outer"]):
        (path.moveTo if i == 0 else path.lineTo)(x0 + x * scale, y0 + y * scale)
    path.close()
    c.setStrokeColor(BLUE)
    c.setLineWidth(1.4)
    c.drawPath(path, stroke=1, fill=0)
    for index, (x, y, radius) in enumerate(g["holes"], 1):
        cx, cy = x0 + x * scale, y0 + y * scale
        c.circle(cx, cy, radius * scale, stroke=1, fill=0)
        line(c, cx, cy - radius * scale - 2, cx, y0 - 10, GREY, 0.5)
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(BLUE)
        c.drawCentredString(cx, y0 - 23, f"H{index}")
    for index, (left, right, depth) in enumerate(g["notches"], 1):
        cx = x0 + (left + right) * scale / 2
        line(c, cx, y0 + (W - depth) * scale, cx, y0 + W * scale + 13, GREY, 0.5)
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(BLUE)
        c.drawCentredString(cx, y0 + W * scale + 17, f"N{index}")
    dim_horizontal(c, x0, x0 + L * scale, 505, y0, f"OVERALL  {fmt(L)}")
    dim_vertical(c, 91, y0, y0 + W * scale, x0, f"{fmt(W)}")
    text(c, x0 - 21, y0 - 6, "0,0", 8, RED, "Helvetica-Bold")

    text(c, 62, 445, "SIDE  |  PLATE THICKNESS", 11, BLUE, "Helvetica-Bold")
    # A short section is enlarged so the 3-mm thickness remains legible.
    c.setStrokeColor(BLUE)
    c.rect(270, 413, 135, 18, fill=0, stroke=1)
    dim_vertical(c, 431, 413, 431, 405, fmt(T))
    text(c, 455, 416, "Main plate; end-bevel sections on page 2", 9, GREY)

    text(c, 62, 373, "HOLE SCHEDULE  |  ALL THROUGH, AXES PARALLEL TO Z", 11, BLUE, "Helvetica-Bold")
    hole_rows = [
        (f"H{i}", fmt(x), fmt(y), f"Ø{fmt(2 * radius)}", "1")
        for i, (x, y, radius) in enumerate(g["holes"], 1)
    ]
    table(c, 62, 342, [65, 110, 110, 115, 65],
          ["ID", "X FROM DATUM", "Y FROM DATUM", "DIAMETER", "QTY"], hole_rows)

    text(c, 615, 373, "TOP-EDGE NOTCH SCHEDULE  |  THROUGH THICKNESS", 11,
         BLUE, "Helvetica-Bold")
    notch_rows = [
        (f"N{i}", fmt(left), fmt(right), fmt(right - left), fmt(depth), fmt(W - depth))
        for i, (left, right, depth) in enumerate(g["notches"], 1)
    ]
    table(c, 615, 342, [51, 90, 90, 80, 75, 100],
          ["ID", "X START", "X END", "WIDTH", "DEPTH", "FLOOR Y"], notch_rows)
    text(c, 617, 247, "The end bevels are visible only from below; see page 2.", 9, GREY)
    text(c, 62, 125, "GEOMETRY AUDIT", 10, BLUE, "Helvetica-Bold")
    text(c, 62, 106,
         f"{len(g['holes'])} STEP-confirmed through holes  |  {len(g['notches'])} top-edge notches  |  2 underside bevels",
         9, GREY)
    text(c, 62, 89,
         f"Analytic volume {g['expected_volume']:.2f} mm³  |  STL volume {g['mesh_volume']:.2f} mm³  |  gap {g['relative_error']:.4%}",
         9, GREY)
    c.showPage()


def notch_detail(c, x, y, label, item, width):
    left, right, depth = item
    scale = 7.0
    start = x + 35
    end = start + (right - left) * scale
    top = y + 105
    bottom = top - depth * scale
    text(c, x, y + 164, label, 12, BLUE, "Helvetica-Bold")
    line(c, start - 30, top, start, top, BLUE, 1.4)
    line(c, start, top, start, bottom, BLUE, 1.4)
    line(c, start, bottom, end, bottom, BLUE, 1.4)
    line(c, end, bottom, end, top, BLUE, 1.4)
    line(c, end, top, end + 30, top, BLUE, 1.4)
    dim_horizontal(c, start, end, top + 38, top, f"WIDTH {fmt(right - left)}")
    dim_vertical(c, end + 44, bottom, top, end, f"{fmt(depth)}")
    text(c, x, y + 20, f"X start {fmt(left)}    X end {fmt(right)}    Floor Y {fmt(width - depth)}", 9, GREY)


def bevel_detail(c, x, y, label, run, reach, rise, thickness, right=False):
    scale = 15.0
    text(c, x, y + 196, label, 12, BLUE, "Helvetica-Bold")
    text(c, x, y + 176, "BOTTOM VIEW  |  cut-away corner", 9, GREY)
    px, py = x + 35, y + 32
    if right:
        # Mirror the left-hand L-shaped underside boundary.
        line(c, px, py + reach * scale, px + run * scale, py + reach * scale, BLUE, 1.4)
        line(c, px, py + reach * scale, px, py, BLUE, 1.4)
        edge_x = px + run * scale
    else:
        line(c, px, py + reach * scale, px + run * scale, py + reach * scale, BLUE, 1.4)
        line(c, px + run * scale, py + reach * scale, px + run * scale, py, BLUE, 1.4)
        edge_x = px
    c.setDash(3, 3)
    line(c, px, py, px + run * scale, py, GREY, 0.6)
    line(c, edge_x, py, edge_x, py + reach * scale, GREY, 0.6)
    c.setDash()
    dim_horizontal(c, px, px + run * scale, py - 19, py, fmt(run))
    dim_vertical(c, px + run * scale + 36, py, py + reach * scale,
                 px + run * scale, fmt(reach))
    text(c, x + 250, y + 176, "END SECTION  |  sloped relief", 9, GREY)
    sx, sy = x + 255, y + 52
    section_scale = 13
    # Cross-section in X-Z: top remains full; underside bevel rises at the end.
    if right:
        points = [(0, 0), (run, rise), (run, thickness), (0, thickness)]
    else:
        points = [(0, rise), (run, 0), (run, thickness), (0, thickness)]
    path = c.beginPath()
    for index, (dx, dz) in enumerate(points):
        (path.moveTo if index == 0 else path.lineTo)(sx + dx * section_scale,
                                                     sy + dz * section_scale)
    path.close()
    c.setStrokeColor(BLUE)
    c.setLineWidth(1.2)
    c.drawPath(path, stroke=1, fill=0)
    text(c, sx, sy - 24, f"Rise at outer end = {fmt(rise)}", 9, RED, "Helvetica-Bold")


def page_two(c, family, g):
    header(c, family, 2, "Enlarged edge notches and the sloped underside relief at each end")
    c.setFillColor(PALE)
    c.roundRect(57, 515, PAGE_W - 114, 225, 9, fill=1, stroke=0)
    text(c, 72, 715, "TOP-EDGE THROUGH NOTCHES", 11, BLUE, "Helvetica-Bold")
    for index, notch in enumerate(g["notches"]):
        notch_detail(c, 82 + 555 * index, 525, f"N{index + 1}", notch, g["width"])
    run, reach, rise = g["relief"]
    c.setFillColor(PALE)
    c.roundRect(57, 158, PAGE_W - 114, 335, 9, fill=1, stroke=0)
    text(c, 72, 469, "UNDERSIDE END BEVELS  |  MIRRORED LEFT / RIGHT", 11,
         BLUE, "Helvetica-Bold")
    bevel_detail(c, 82, 190, "B1  |  LEFT END", run, reach, rise, g["thickness"])
    bevel_detail(c, 635, 190, "B2  |  RIGHT END", run, reach, rise,
                  g["thickness"], right=True)
    text(c, 72, 112,
         "Both end bevels extend from Y=0 to Y=" + fmt(reach)
         + "; their sloped lower faces rise " + fmt(rise)
         + " from the original bottom plane at the outside edge.", 9, GREY)
    c.showPage()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stl", required=True, help="Aligned representative family STL")
    parser.add_argument("--features", required=True,
                        help="Matching STEP-derived feature-annotations.json")
    parser.add_argument("--family", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    geometry = extract_geometry(args.stl, args.features, args.family)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(output), pagesize=(PAGE_W, PAGE_H))
    pdf.setTitle(f"{args.family} - detailed automatic dimensions")
    page_one(pdf, args.family, geometry)
    page_two(pdf, args.family, geometry)
    pdf.save()
    print(json.dumps({key: value for key, value in geometry.items()
                      if key not in ("top_outer", "bottom_outer")}, indent=2))
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
