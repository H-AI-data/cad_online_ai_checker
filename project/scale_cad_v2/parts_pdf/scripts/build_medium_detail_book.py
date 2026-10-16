#!/usr/bin/env python3
"""Build scripted dimensioned pages from model-space geometric primitives.

The seven original views remain. A second page shows direct dimensions for a
certified shape class or a partial geometric preview with an explicit STEP
handoff. No family ID selects a drawing template.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import io
import json
from pathlib import Path
import re

import fitz
import numpy as np
import trimesh
from reportlab import rl_config
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from annotate_complete_plate import extract_geometry
from generate_parts_pdf_six_views import (draw_complex_family_page,
                                          draw_difficult_family_page,
                                          grid_boxes)
from generate_parts_pdf import draw_view, feature_edges
from general_dimension_engine import (analyze_family, grouped_circles,
                                      mesh_envelope_tolerance,
                                      _local_analytic_edges,
                                      projected_planar_stations)
from extruded_section_profile import (analyze_extruded_section, _section,
                                      _circle_fit, _area)
from keyed_bore_profile import detect_keyed_bore
from shaft_flat_profile import milled_shaft_flats
from dimension_density import MIN_DRAWN_DIMENSION_MM, simplify_dimension_stations


BLUE = (0.13, 0.30, 0.46)
RED = (0.78, 0.13, 0.18)
INK = (0.13, 0.17, 0.22)
MUTED = (0.36, 0.43, 0.49)
PALE = (0.94, 0.97, 0.99)
LINE = (0.77, 0.82, 0.86)
WHITE = (1, 1, 1)


def num(x: float) -> str:
    displayed = round(float(x), 2)
    # STL tessellation and STEP-to-local transforms can leave nominal whole
    # millimetres at N.99 or N.01.  Normalize only those displayed endings;
    # the measured geometry and dimension endpoints remain untouched.
    if int(round(abs(displayed) * 100)) % 100 in (1, 99):
        displayed = round(displayed)
    return f"{displayed:.2f}".rstrip("0").rstrip(".")


def uninformative_profile_section(feature, plan):
    """Identify a sparse interior slice that does not warrant a detail page.

    The ordinary base page already shows the 3D form. A tiny interior polygon
    without openings adds no useful dimensions when several axes describe the
    part; the measured orthographic projection pages carry those dimensions.
    """
    profile = plan.get("profile")
    if not profile or profile.get("circular_features"):
        return False
    dims = np.asarray(feature["dimensions"], float)
    z = float(profile["plane_z"])
    if min(z, dims[2] - z) <= max(.1, .005 * dims[2]):
        return False
    outline = np.asarray(profile["outline"], float)
    if len(outline) < 3:
        return False
    area = abs(np.dot(outline[:, 0], np.roll(outline[:, 1], -1)) -
               np.dot(outline[:, 1], np.roll(outline[:, 0], -1))) / 2
    coverage = area / max(dims[0] * dims[1], 1e-9)
    if coverage >= .18:
        return False
    cylinders = grouped_circles(feature["circles"])
    axes = {int(np.argmax(np.abs(circle["axis"]))) for circle in cylinders
            if max(np.abs(circle["axis"])) >= .995}
    return len(axes) >= 2


def text(page, x, y, value, size=10, color=BLUE, bold=False):
    if color == RED:
        # Final guard for bespoke dimension renderers. Chain renderers also
        # remove the corresponding short rails and ticks before they are drawn.
        label = re.fullmatch(r"(?:(?:\d+)x\s*)?(?:R|Ø|DEPTH\s+)?(\d+(?:\.\d+)?)",
                             str(value).strip())
        if label and float(label.group(1)) <= MIN_DRAWN_DIMENSION_MM:
            return
    page.insert_text((x, y), str(value), fontname="hebo" if bold else "helv",
                     fontsize=size, color=color)


def line(page, a, b, color=LINE, width=0.6):
    page.draw_line(fitz.Point(*a), fitz.Point(*b), color=color, width=width)


def group_circles(circles):
    """Merge cylindrical edges on opposite faces of the same axis."""
    groups = defaultdict(list)
    for circle in circles:
        center = np.asarray(circle["center"], float)
        axis = np.asarray(circle["axis"], float)
        axis /= np.linalg.norm(axis)
        if axis[np.argmax(np.abs(axis))] < 0:
            axis = -axis
        foot = center - axis * np.dot(center, axis)
        key = (round(2 * circle["radius"], 2),
               *np.round(foot, 1), *np.round(axis, 1))
        groups[key].append((center, axis))
    result = []
    for key, edges in groups.items():
        centers = np.array([item[0] for item in edges])
        result.append({"diameter": key[0], "center": np.mean(centers, axis=0),
                       "z_levels": sorted(set(round(p[2], 2) for p in centers)),
                       "axis": edges[0][1], "edge_count": len(edges)})
    return sorted(result, key=lambda c: (round(c["center"][0], 1),
                                         round(c["center"][1], 1), c["diameter"]))


def spread_dimension_labels(labels, left, right, gap=8):
    """Place leader labels without overlapping while keeping their axial order.

    Each label has an ideal x coordinate and a measured text width.  Return
    one or more rows of (label, x) pairs, allowing dense shafts to use the
    clear space above the drawing instead of stacking text at one station.
    """
    available = right - left
    rows = []
    for label in sorted(labels, key=lambda item: (item["ideal"], -item["diameter"])):
        needed = label["width"] + (gap if rows and rows[-1] else 0)
        occupied = (sum(item["width"] for item in rows[-1]) +
                    gap * max(0, len(rows[-1]) - 1)) if rows else 0
        if not rows or occupied + needed > available:
            rows.append([])
        rows[-1].append(label)
    placed = []
    for row in rows:
        centers = [min(max(item["ideal"], left + item["width"] / 2),
                       right - item["width"] / 2) for item in row]
        for i in range(1, len(row)):
            separation = (row[i - 1]["width"] + row[i]["width"]) / 2 + gap
            centers[i] = max(centers[i], centers[i - 1] + separation)
        centers[-1] = min(centers[-1], right - row[-1]["width"] / 2)
        for i in range(len(row) - 2, -1, -1):
            separation = (row[i]["width"] + row[i + 1]["width"]) / 2 + gap
            centers[i] = min(centers[i], centers[i + 1] - separation)
        if centers[0] - row[0]["width"] / 2 < left:
            shift = left - (centers[0] - row[0]["width"] / 2)
            centers = [center + shift for center in centers]
        placed.append(list(zip(row, centers)))
    return placed


def exposed_axial_station(circle):
    """Anchor a diameter on one of its actual cylindrical spans.

    A grouped cylinder may have two disconnected end spans.  Averaging all
    rim stations then places its leader in the middle of a wider, enclosing
    cylinder.  Paired rim stations bound each exposed span; use the longest
    pair, with the rightmost pair breaking ties to keep leaders predictable.
    """
    levels = sorted(float(value) for value in circle["axial_levels"])
    if len(levels) >= 2 and len(levels) % 2 == 0:
        spans = [(b - a, b, (a + b) / 2)
                 for a, b in zip(levels[::2], levels[1::2]) if b > a]
        if spans:
            return max(spans)[2]
    return float(circle["center"][int(np.argmax(np.abs(circle["axis"])))])


def compound_head_profile(mesh, circles, major):
    """Measure opposite crest and waist spans on a common-axis head rim."""
    transverse = [axis for axis in range(3) if axis != major]
    centers = np.asarray([circle["center"] for circle in circles], float)
    center = np.median(centers[:, transverse], axis=0)
    first = min(float(level) for circle in circles
                for level in circle["axial_levels"])
    vertices = np.asarray(mesh.vertices)
    rim = vertices[np.abs(vertices[:, major] - first) < .06][:, transverse]
    if len(rim) < 80:
        return None
    relative = rim - center
    radii = np.linalg.norm(relative, axis=1)
    outer = radii > 1.2 * max(circle["diameter"] / 2 for circle in circles)
    relative, radii = relative[outer], radii[outer]
    if len(radii) < 80:
        return None
    angles = np.mod(np.arctan2(relative[:, 1], relative[:, 0]), 2 * np.pi)
    sampled = []
    for angle in np.linspace(0, 2 * np.pi, 72, endpoint=False):
        separation = np.abs((angles - angle + np.pi) % (2 * np.pi) - np.pi)
        nearby = radii[separation < np.deg2rad(4)]
        if len(nearby) < 3:
            return None
        sampled.append(float(np.quantile(nearby, .97)))
    sampled = np.asarray(sampled)
    if sampled.max() / sampled.min() < 1.15:
        return None
    peak = int(np.argmax(sampled[:36] + sampled[36:]))
    valley = int(np.argmin(sampled[:36] + sampled[36:]))
    if (abs(sampled[peak] - sampled[peak + 36]) > .5 or
            abs(sampled[valley] - sampled[valley + 36]) > .5):
        return None
    spans = [(np.deg2rad(5 * index),
              float((sampled[index] + sampled[index + 36]) / 2))
             for index in (peak, valley)]
    return center, spans


def add_recessed_head_section(pdf, mesh, row, feature, major):
    """Show the measured non-round drive loop at a documented axial station."""
    section_data = row.get("drive_section")
    if not section_data:
        return
    station = float(section_data["section_station"])
    transverse = [axis for axis in range(3) if axis != major]
    origin = np.median(mesh.vertices, axis=0)
    origin[major] = station
    section = mesh.section(plane_origin=origin, plane_normal=np.eye(3)[major])
    if section is None:
        return
    center = np.median([circle["center"] for circle in feature["circles"]],
                       axis=0)[transverse]
    target = float(section_data["drive_outer_span"]) / 2
    loops = []
    for loop in section.discrete:
        points = np.asarray(loop)[:, transverse]
        radii = np.linalg.norm(points - center, axis=1)
        if len(points) >= 20 and abs(float(radii.max()) - target) < .12:
            loops.append((float(radii.max() / max(radii.min(), .01)), points))
    if not loops:
        return
    points = max(loops, key=lambda item: item[0])[1]
    relative = points - center
    radii = np.linalg.norm(relative, axis=1)
    box = (570., 38., 226., 184.)
    bx, by, bw, bh = box
    pdf.setFillColorRGB(.97, .98, .99)
    pdf.setStrokeColorRGB(.84, .87, .90)
    pdf.roundRect(*box, 4, fill=1, stroke=1)
    pdf.setFillColorRGB(*BLUE)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(bx + 9, by + bh - 15, "DRIVE RECESS SECTION")
    pdf.setFont("Helvetica", 6)
    pdf.drawRightString(bx + bw - 8, by + bh - 15,
                        f"X {num(station)}")
    scale = min((bw - 75) / (2 * radii.max()),
                (bh - 55) / (2 * radii.max()))
    page_center = np.asarray([bx + bw / 2, by + bh / 2 - 2])
    projected = page_center + relative * scale
    pdf.setStrokeColorRGB(.38, .43, .50)
    pdf.setLineWidth(.65)
    path = pdf.beginPath()
    path.moveTo(*projected[0])
    for point in projected[1:]:
        path.lineTo(*point)
    path.close()
    pdf.drawPath(path, stroke=1, fill=0)
    pdf.setStrokeColorRGB(*RED)
    pdf.setFillColorRGB(*RED)
    pdf.setLineWidth(.75)
    # Overall cross-arm span, followed by the width of the upper arm.  These
    # are directly visible chords of the sampled recess, not a fictitious Ø.
    left, right = float(projected[:, 0].min()), float(projected[:, 0].max())
    lower_rail = max(by + 16, float(projected[:, 1].min()) - 10)
    pdf.line(left, lower_rail, right, lower_rail)
    for xpos in (left, right):
        pdf.line(xpos, lower_rail - 3, xpos, lower_rail + 3)
    pdf.setFont("Helvetica-Bold", 7)
    pdf.drawCentredString((left + right) / 2, lower_rail - 11,
                           f"~{num(round(float(np.ptp(points[:, 0])), 1))}")
    top_band = points[:, 1] >= float(points[:, 1].max()) - .12 * float(
        np.ptp(points[:, 1]))
    if np.count_nonzero(top_band) >= 2:
        arm_left = float(projected[top_band, 0].min())
        arm_right = float(projected[top_band, 0].max())
        upper_rail = float(np.mean(projected[top_band, 1])) - 8
        pdf.line(arm_left, upper_rail, arm_right, upper_rail)
        for xpos in (arm_left, arm_right):
            pdf.line(xpos, upper_rail - 3, xpos, upper_rail + 3)
        tx, ty = bx + bw - 9, upper_rail + 4
        pdf.line(arm_right, upper_rail, tx - 26, ty - 2)
        pdf.drawRightString(tx, ty, f"~{num(round(float(np.ptp(points[top_band, 0])), 1))}")


def add_coaxial_stepped_page(out, row, feature, mesh,
                            min_axial_interval=0., min_paper_interval=13.):
    """Dimension a stepped common-axis body in its longitudinal projection."""
    from general_dimension_engine import grouped_circles

    circles = grouped_circles(feature["circles"])
    reference = np.asarray(circles[0]["axis"], float)
    major = int(np.argmax(np.abs(reference)))
    axes = (major, 2) if major != 2 else (0, 2)
    horizontal = major != 2
    longitudinal = major if horizontal else 2
    envelope = np.asarray(feature["dimensions"], float)
    stations = [0., float(envelope[longitudinal])]
    for circle in circles:
        if abs(np.dot(reference, circle["axis"])) >= .985:
            stations.extend(circle["axial_levels"])
    stations = sorted(set(round(float(value), 3) for value in stations
                          if -.05 <= value <= envelope[longitudinal] + .05))
    if min_axial_interval:
        # Tiny adjacent STEP rim levels often describe edge breaks.  On an
        # enlarged longitudinal view they leave no room for a legible chain;
        # retain measured stations only when the gap is large enough to label.
        kept = [stations[0]]
        for station in stations[1:-1]:
            if station-kept[-1] >= min_axial_interval and \
                    stations[-1]-station >= min_axial_interval:
                kept.append(station)
        stations = kept + [stations[-1]]
    required_stations=(4 if min_axial_interval or row.get("recessed_fastener_head")
                       else 5)
    if len(stations) < required_stations:
        # A provisional stepped-body classification can come from transverse
        # bores.  Without enough verified axial levels there is no useful
        # chain to draw; let the general projection annotator handle it.
        return []

    buffer = io.BytesIO()
    page_width, page_height = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=(page_width, page_height))
    pdf.setFillColorRGB(*BLUE)
    pdf.rect(0, page_height - 72, page_width, 72, stroke=0, fill=1)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.setFont("Helvetica-Bold", 17)
    pdf.drawString(36, page_height - 44,
                   f"{row['family'].upper()}  |  AXIAL DIMENSIONS")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(37, page_height - 91, "LONGITUDINAL VIEW  |  mm")

    # The generic fallback gets a larger projection so shallow axial stages
    # remain readable; the established shaft template keeps its existing rail.
    box = ((45., 80., page_width - 90., 430.) if min_axial_interval else
           (45., 230., page_width - 90., 255.))
    triangles = np.asarray(mesh.triangles)
    draw_view(pdf, triangles, axes, box,
              "FRONT" if axes == (0, 2) else "SIDE",
              f"{('X','Y','Z')[axes[0]]} {num(envelope[axes[0]])}",
              f"Z {num(envelope[axes[1]])}", feature_edges(triangles))
    x, y, width, height = box
    scale = min((width - 45) / envelope[axes[0]],
                (height - 55) / envelope[axes[1]])
    origin_x = x + 31 + (width - 45 - envelope[axes[0]] * scale) / 2
    origin_y = y + 31 + (height - 55 - envelope[axes[1]] * scale) / 2
    pdf.setStrokeColorRGB(*RED)
    pdf.setFillColorRGB(*RED)
    pdf.setLineWidth(.8)
    # One diameter callout per nominal size.  The same cylindrical rim can
    # occur at several axial stations; repeating its value adds clutter.
    by_diameter = {}
    for circle in circles:
        if abs(np.dot(reference, circle["axis"])) < .985:
            continue
        key = round(float(circle["diameter"]), 2)
        span = max(circle["axial_levels"]) - min(circle["axial_levels"])
        if key not in by_diameter or span > by_diameter[key][0]:
            by_diameter[key] = (span, circle)
    if horizontal:
        labels = []
        for diameter, (_, circle) in by_diameter.items():
            px = origin_x + exposed_axial_station(circle) * scale
            py = origin_y + float(circle["center"][2] + diameter / 2) * scale
            caption = f"Ø{num(diameter)}"
            labels.append({"diameter": diameter, "ideal": px,
                           "width": pdf.stringWidth(caption, "Helvetica-Bold", 8),
                           "caption": caption, "anchor": (px, py)})
        rows = spread_dimension_labels(labels, x + 45, x + width - 30)
        if len(rows) > 4:
            raise ValueError(f"Too many crowded diameter labels: {row['family']}")
        for row_index, placed in enumerate(rows):
            ty = y + height - 47 - 18 * row_index
            for label, tx in placed:
                px, py = label["anchor"]
                pdf.line(px, py, tx, ty - 4)
                pdf.circle(px, py, 1.5, stroke=0, fill=1)
                pdf.setFont("Helvetica-Bold", 8)
                pdf.drawCentredString(tx, ty, label["caption"])
    else:
        for diameter, (_, circle) in by_diameter.items():
            px = origin_x + float(circle["center"][0] + diameter / 2) * scale
            py = origin_y + exposed_axial_station(circle) * scale
            tx, ty = px + 14, py + 13
            pdf.line(px, py, tx, ty - 3)
            pdf.circle(px, py, 1.5, stroke=0, fill=1)
            pdf.setFont("Helvetica-Bold", 8)
            pdf.drawCentredString(tx, ty, f"Ø{num(diameter)}")

    # Adjacent distances carry the axial shape; no absolute XYZ table.
    rail = 45. if min_axial_interval else 154.
    if horizontal:
        for index, (a, b) in enumerate(zip(stations, stations[1:])):
            ax, bx = origin_x + a * scale, origin_x + b * scale
            if bx - ax < min_paper_interval:
                continue
            label = num(b - a)
            pdf.setFont("Helvetica-Bold", 7.5)
            rail_y = (rail - 27 * (index % 2)
                      if bx - ax < pdf.stringWidth(label, "Helvetica-Bold", 7.5) + 14
                      else rail)
            pdf.line(ax, rail_y, bx, rail_y)
            pdf.line(ax, rail_y - 4, ax, rail_y + 4)
            pdf.line(bx, rail_y - 4, bx, rail_y + 4)
            pdf.drawCentredString((ax + bx) / 2, rail_y + 7, label)
    else:
        rail_x = x + width + 15
        for a, b in zip(stations, stations[1:]):
            ay, by = origin_y + a * scale, origin_y + b * scale
            if by - ay < min_paper_interval:
                continue
            pdf.line(rail_x, ay, rail_x, by)
            pdf.line(rail_x - 4, ay, rail_x + 4, ay)
            pdf.line(rail_x - 4, by, rail_x + 4, by)
            pdf.setFont("Helvetica-Bold", 7.5)
            pdf.drawString(rail_x + 8, (ay + by) / 2, num(b - a))
    if row.get("compound_axial_head"):
        profile = compound_head_profile(mesh, circles, major)
        if profile is not None:
            end_axes = tuple(index for index in range(3) if index != major)
            side_box = (612., 35., 188., 188.)
            draw_view(pdf, triangles, end_axes, side_box, "SIDE",
                      f"{('X','Y','Z')[end_axes[0]]} {num(envelope[end_axes[0]])}",
                      f"{('X','Y','Z')[end_axes[1]]} {num(envelope[end_axes[1]])}",
                      feature_edges(triangles))
            bx, by, bw, bh = side_box
            transverse = envelope[list(end_axes)]
            s = min((bw - 45) / transverse[0], (bh - 55) / transverse[1])
            ox = bx + 31 + (bw - 45 - transverse[0] * s) / 2
            oy = by + 31 + (bh - 55 - transverse[1] * s) / 2
            center, spans = profile
            pdf.setStrokeColorRGB(*RED)
            pdf.setFillColorRGB(*RED)
            pdf.setLineWidth(.8)
            for index, (angle, radius) in enumerate(spans):
                direction = np.asarray([np.cos(angle), np.sin(angle)])
                normal = np.asarray([-direction[1], direction[0]])
                offset = normal * (11 if index == 0 else -13)
                a = np.asarray([ox, oy]) + (center - radius * direction) * s
                b = np.asarray([ox, oy]) + (center + radius * direction) * s
                a2, b2 = a + offset, b + offset
                pdf.line(*a, *a2)
                pdf.line(*b, *b2)
                pdf.line(*a2, *b2)
                for point in (a2, b2):
                    tick = normal * 3
                    pdf.line(*(point - tick), *(point + tick))
                pdf.setFont("Helvetica-Bold", 7)
                if index == 0:
                    tx, ty = bx + bw + 25, by + bh - 20
                    pdf.line(*(b2 + direction * 3), tx - 22, ty - 2)
                    pdf.drawRightString(tx, ty, f"~{num(round(2 * radius, 1))}")
                else:
                    tx, ty = bx + bw + 25, by + bh / 2 - 7
                    pdf.line(*(b2 + direction * 3), tx - 22, ty - 2)
                    pdf.drawRightString(tx, ty, f"~{num(round(2 * radius, 1))}")
    if row.get("recessed_fastener_head"):
        add_recessed_head_section(pdf, mesh, row, feature, major)
    pdf.showPage()
    pdf.save()
    supplement = fitz.open(stream=buffer.getvalue(), filetype="pdf")
    out.insert_pdf(supplement)
    supplement.close()
    return stations


def symmetric_chamfer_panel(mesh, dims, circles):
    """Recognize a mirrored two-corner panel and its paired circular openings."""
    if len(circles) != 2 or any(abs(c["axis"][2]) < .995 for c in circles):
        return None
    left, right = sorted(circles, key=lambda c: c["center"][0])
    length, width, height = map(float, dims)
    if (abs(left["diameter"]-right["diameter"]) > .02 or
            abs(left["center"][1]-right["center"][1]) > .02 or
            abs(left["center"][0] + right["center"][0] - length) > .03):
        return None
    vertices = np.asarray(mesh.vertices)
    top = vertices[np.abs(vertices[:, 2]-height) < .02]
    at_bottom = top[np.abs(top[:, 1]) < .02]
    at_left = top[np.abs(top[:, 0]) < .02]
    at_right = top[np.abs(top[:, 0]-length) < .02]
    if min(map(len, (at_bottom, at_left, at_right))) < 2:
        return None
    run_left = float(np.min(at_bottom[:, 0]))
    run_right = length-float(np.max(at_bottom[:, 0]))
    rise_left = float(np.min(at_left[:, 1]))
    rise_right = float(np.min(at_right[:, 1]))
    if not (.03*length < run_left < .4*length and
            .05*width < rise_left < .6*width and
            abs(run_left-run_right) < .03 and
            abs(rise_left-rise_right) < .03):
        return None
    # Verify that the four candidate chamfer endpoints also occur on an
    # interior cut, so a stray STL vertex cannot trigger this classifier.
    section = mesh.section(plane_origin=(0, 0, height-.5),
                           plane_normal=(0, 0, 1))
    if section is None:
        return None
    samples = np.vstack(section.discrete)[:, :2]
    corners = ((0, rise_left), (run_left, 0),
               (length-run_right, 0), (length, rise_right))
    if any(np.min(np.linalg.norm(samples-np.asarray(corner), axis=1)) > .3
           for corner in corners):
        return None
    return {"left_run": run_left, "right_run": run_right,
            "left_rise": rise_left, "right_rise": rise_right,
            "hole_diameter": left["diameter"],
            "hole_centers": [left["center"], right["center"]]}


def plate_top_dimension_view(page, feature, panel, plate):
    """Draw a clean dimension-first plate view instead of reusing sparse labels."""
    page.draw_rect(panel, fill=PALE, color=LINE, width=.8)
    text(page, panel.x0 + 22, panel.y0 + 32, "TOP  |  DIMENSIONED OUTLINE", 16, INK, True)
    text(page, panel.x1 - 43, panel.y0 + 32, "NTS", 10, MUTED)
    length, width = plate["length"], plate["width"]
    x0, y0 = panel.x0 + 81, panel.y0 + 242
    scale = (panel.width - 165) / length

    def xy(x, y):
        return (x0 + x * scale, y0 - y * scale)

    outline = plate["top_outer"]
    for a, b in zip(outline, outline[1:] + outline[:1]):
        line(page, xy(*a), xy(*b), INK, 1.3)
    for x, y, radius in plate["holes"]:
        page.draw_circle(fitz.Point(*xy(x, y)), radius * scale,
                         color=INK, width=1)

    # Top-edge chain: adjacent edge-to-edge dimensions, no absolute X list.
    cuts = [0]
    for left, right, _ in plate["notches"]:
        cuts.extend((left, right))
    cuts.append(length)
    chain_y = y0 - width * scale - 39
    line(page, (x0, chain_y), (x0 + length*scale, chain_y), RED, .95)
    for cut in cuts:
        xx = xy(cut, width)[0]
        line(page, (xx, chain_y - 4), (xx, y0 - width*scale - 3), RED, .65)
    for a, b in zip(cuts, cuts[1:]):
        middle = (xy(a, 0)[0] + xy(b, 0)[0]) / 2
        value = num(b-a)
        length_text = fitz.get_text_length(value, fontname="hebo", fontsize=10)
        text(page, middle - length_text/2, chain_y - 8, value, 10, RED, True)
    depth = plate["notches"][0][2]
    notch_end = plate["notches"][0][1]
    dim_x = xy(notch_end, width)[0] + 13
    upper_y = xy(notch_end, width)[1]
    lower_y = xy(notch_end, width-depth)[1]
    line(page, (dim_x, upper_y), (dim_x, lower_y), RED, .9)
    for yy in (upper_y, lower_y):
        line(page, (dim_x-4, yy), (dim_x+4, yy), RED, .8)
    text(page, dim_x+7, lower_y+3, f"{num(depth)}  (2x)", 9, RED, True)

    # Diameter size is specified once per repeated group.
    diameter_counts = defaultdict(int)
    for _, _, radius in plate["holes"]:
        diameter_counts[round(2*radius, 3)] += 1
    diameter_line = "   ".join(f"{count}x Ø{num(diameter)}"
                                 for diameter, count in sorted(diameter_counts.items(), reverse=True))
    text(page, x0 + 4, panel.y0 + 74, diameter_line, 10, RED, True)

    # Hole pattern as center-to-center pitches, with repeated end offset.
    holes = sorted(plate["holes"])
    centers = [x for x, _, _ in holes]
    pitch_y = y0 + 76
    line(page, (xy(centers[0], 0)[0], pitch_y),
         (xy(centers[-1], 0)[0], pitch_y), RED, .95)
    for center in centers:
        xx = xy(center, 0)[0]
        line(page, (xx, xy(center, holes[0][1])[1] + 6),
             (xx, pitch_y + 5), RED, .62)
    for left, right in zip(centers, centers[1:]):
        value = num(right-left)
        xx = (xy(left, 0)[0] + xy(right, 0)[0])/2
        tw = fitz.get_text_length(value, fontname="hebo", fontsize=9.5)
        text(page, xx-tw/2, pitch_y-7, value, 9.5, RED, True)
    end_offset = centers[0]
    if abs(length-centers[-1]-end_offset) < .02:
        offset_y = pitch_y + 37
        for left, right in ((0, centers[0]), (centers[-1], length)):
            xa, xb = xy(left, 0)[0], xy(right, 0)[0]
            line(page, (xa, offset_y), (xb, offset_y), RED, .85)
            for xx in (xa, xb):
                line(page, (xx, offset_y-4), (xx, offset_y+4), RED, .75)
            tw = fitz.get_text_length(num(end_offset), fontname="hebo", fontsize=9)
            text(page, (xa+xb-tw)/2, offset_y-7, num(end_offset), 9, RED, True)
    # From the lower edge to the common hole-row centerline.
    row_y = xy(0, holes[0][1])[1]
    row_dim_x = x0 - 18
    line(page, (row_dim_x, row_y), (row_dim_x, y0), RED, .85)
    for yy in (row_y, y0):
        line(page, (row_dim_x-4, yy), (row_dim_x+4, yy), RED, .75)
    text(page, row_dim_x+5, y0-4, num(holes[0][1]), 8.5, RED, True)

    # Hidden bottom relief footprint is shown dashed in this top projection.
    run, reach, rise = plate["relief"]
    for i, side in enumerate((0, length-run), 1):
        p0 = xy(side, 0)
        p1 = xy(side+run, reach)
        page.draw_rect(fitz.Rect(min(p0[0],p1[0]), min(p0[1],p1[1]),
                                 max(p0[0],p1[0]), max(p0[1],p1[1])),
                       color=RED, width=.8, dashes="[2 2] 0")
        tx = x0 + (18 if i == 1 else length*scale-64)
        text(page, tx, y0 + 16, f"B{i}", 9, RED, True)

    # Overall and width dimensions.
    overall_y = panel.y1 - 13
    line(page, (x0, overall_y), (x0+length*scale, overall_y), MUTED, .8)
    for x in (x0, x0+length*scale):
        line(page, (x, overall_y-5), (x, overall_y+5), MUTED, .8)
    text(page, x0 + length*scale/2 - 22, overall_y-5,
         num(length), 9, MUTED, True)
    width_x = panel.x0 + 30
    line(page, (width_x, xy(0,width)[1]), (width_x, y0), MUTED, .8)
    for yy in (xy(0,width)[1], y0):
        line(page, (width_x-5, yy), (width_x+5, yy), MUTED, .8)
    text(page, width_x+7, y0-width*scale/2+3, num(width), 9, MUTED, True)


def draw_plate_bevel_views(page, plate):
    """Bottom-view footprint and X-Z slope, dimensioned without prose."""
    panel = fitz.Rect(43, 604, 799, 843)
    page.draw_rect(panel, fill=PALE, color=LINE, width=.65)
    text(page, 58, 629, "BOTTOM  |  B1", 10, INK, True)
    text(page, 303, 629, "BOTTOM  |  B2", 10, INK, True)
    text(page, 566, 629, "X-Z  |  2x", 10, INK, True)
    run, reach, rise = plate["relief"]
    s = 10
    top,bottom = 651,651+reach*s
    left_outer,left_inner = 120,120+run*s
    right_inner,right_outer = 363,363+run*s
    for a,b in (((left_outer,top),(left_inner,top)),
                ((left_inner,top),(left_inner,bottom)),
                ((right_inner,bottom),(right_inner,top)),
                ((right_inner,top),(right_outer,top))):
        line(page,a,b,INK,1.2)
    for a,b in (((left_outer,top),(left_outer,bottom)),
                ((left_outer,bottom),(left_inner,bottom)),
                ((right_outer,top),(right_outer,bottom)),
                ((right_inner,bottom),(right_outer,bottom))):
        page.draw_line(fitz.Point(*a),fitz.Point(*b),
                       color=MUTED,width=.7,dashes="[2 2] 0")
    # The two footprints are mirrored; one dimension pair applies to both.
    dim_y=bottom+23
    line(page,(left_outer,dim_y),(left_inner,dim_y),RED,.9)
    for xx in (left_outer,left_inner):line(page,(xx,dim_y-5),(xx,dim_y+5),RED,.8)
    text(page,(left_outer+left_inner)/2-5,dim_y-7,num(run),10,RED,True)
    dim_x=left_outer-23
    line(page,(dim_x,top),(dim_x,bottom),RED,.9)
    for yy in (top,bottom):line(page,(dim_x-5,yy),(dim_x+5,yy),RED,.8)
    text(page,dim_x-22,(top+bottom)/2+3,num(reach),10,RED,True)
    text(page,207,772,"2x",10,RED,True)

    # End section: outside bottom is raised; the full plate remains at Z=3.
    sx,sy,ss=597,755,22
    outer_bottom=sy-rise*ss
    top_z=sy-plate["thickness"]*ss
    inner_x=sx+run*ss
    for a,b in (((sx,top_z),(inner_x,top_z)),
                ((inner_x,top_z),(inner_x,sy)),
                ((inner_x,sy),(sx,outer_bottom)),
                ((sx,outer_bottom),(sx,top_z))):
        line(page,a,b,INK,1.1)
    rail=sy+27
    line(page,(sx,rail),(inner_x,rail),RED,.9)
    for xx in (sx,inner_x):line(page,(xx,rail-5),(xx,rail+5),RED,.8)
    text(page,(sx+inner_x)/2-5,rail-8,num(run),10,RED,True)
    line(page,(sx-15,outer_bottom),(sx-15,sy),RED,.9)
    for yy in (outer_bottom,sy):line(page,(sx-20,yy),(sx-10,yy),RED,.8)
    text(page,sx-43,(outer_bottom+sy)/2+4,num(rise),9.5,RED,True)
    line(page,(inner_x+14,top_z),(inner_x+14,sy),MUTED,.75)
    for yy in (top_z,sy):line(page,(inner_x+10,yy),(inner_x+18,yy),MUTED,.7)
    text(page,inner_x+20,(top_z+sy)/2+4,num(plate["thickness"]),9,MUTED,True)


def original_top_rect(src_page):
    w, h = src_page.rect.width, src_page.rect.height
    gap = 8
    pw = (w - 72) / 3
    ph = (h - 146) / 3
    return fitz.Rect(28 + 2 * (pw + gap), 92,
                     28 + 2 * (pw + gap) + pw, 92 + ph)


def top_project(feature, source_rect, dest_rect, center):
    d = feature["dimensions"]
    pw, ph = source_rect.width, source_rect.height
    scale = min((pw - 45) / d[0], (ph - 55) / d[1])
    ox = source_rect.x0 + 31 + (pw - 45 - d[0] * scale) / 2
    oy = source_rect.y0 + ph - 31 - (ph - 55 - d[1] * scale) / 2
    src_x = ox + center[0] * scale
    src_y = oy - center[1] * scale
    return (dest_rect.x0 + (src_x - source_rect.x0) * dest_rect.width / pw,
            dest_rect.y0 + (src_y - source_rect.y0) * dest_rect.height / ph)


def side_project(feature, source_rect, dest_rect, y, z):
    """Use the same orthographic transform as the original SIDE view."""
    width, height = feature["dimensions"][1:]
    pw, ph = source_rect.width, source_rect.height
    scale = min((pw-45)/width, (ph-55)/height)
    ox = source_rect.x0+31+(pw-45-width*scale)/2
    oy = source_rect.y0+ph-31-(ph-55-height*scale)/2
    return (dest_rect.x0+(ox+y*scale-source_rect.x0)*dest_rect.width/pw,
            dest_rect.y0+(oy-z*scale-source_rect.y0)*dest_rect.height/ph)


def projection_rect_and_axes(src_page, normal):
    """Select the original orthographic view perpendicular to a local axis."""
    w,h=src_page.rect.width,src_page.rect.height
    pw,ph=(w-72)/3,(h-146)/3
    view_index={1:0,0:1,2:2}[normal]
    rect=fitz.Rect(28+view_index*(pw+8),92,
                   28+view_index*(pw+8)+pw,92+ph)
    axes={1:(0,2),0:(1,2),2:(0,1)}[normal]
    return rect,axes


def horizontal_face_levels(mesh, dimensions):
    """Return substantial interior Z planes that need a height projection.

    Triangle centroids on a planar horizontal face share one Z coordinate.
    Group nearby levels to tolerate tessellation noise and reject tiny hole
    chamfers and sliver faces before requesting another drawing page.
    """
    footprint = float(dimensions[0] * dimensions[1])
    minimum_area = max(25.0, .01 * footprint)
    height = float(dimensions[2])
    raw = []
    for triangle, center, normal, area in zip(
            mesh.triangles, mesh.triangles_center, mesh.face_normals,
            mesh.area_faces):
        z = float(center[2])
        if (abs(float(normal[2])) > .999 and
                np.ptp(triangle[:, 2]) <= .02 and .5 < z < height - .5):
            raw.append((z, float(area)))
    raw.sort()
    groups = []
    for z, area in raw:
        if groups and z - groups[-1][-1][0] <= .15:
            groups[-1].append((z, area))
        else:
            groups.append([(z, area)])
    return [round(sum(z * area for z, area in group) /
                  sum(area for _, area in group), 2)
            for group in groups
            if sum(area for _, area in group) >= minimum_area]


def repeated_axial_profile(mesh, dimensions):
    """Find a repeated run of measured planar shoulders on a long axis.

    The repeat describes station intervals, not inferred thread geometry.
    It is used to request a longitudinal dimension page when the end view
    alone only gives diameters.
    """
    axis = int(np.argmax(dimensions))
    if axis not in (0, 1) or dimensions[axis] < 4 * max(
            dimensions[i] for i in range(3) if i != axis):
        return None
    transverse_area = float(np.prod([dimensions[i] for i in range(3)
                                     if i != axis]))
    areas = defaultdict(float)
    for triangle, center, normal, area in zip(
            mesh.triangles, mesh.triangles_center, mesh.face_normals,
            mesh.area_faces):
        if (abs(float(normal[axis])) > .999 and
                np.ptp(triangle[:, axis]) <= .02):
            areas[round(float(center[axis]), 2)] += float(area)
    threshold = max(40., .06 * transverse_area)
    stations = sorted(x for x, area in areas.items() if area >= threshold)
    if len(stations) < 9:
        return None
    gaps = np.diff(stations)
    best = None
    for start in range(len(gaps)):
        for width_count in range(2, 7):
            pattern = gaps[start:start + width_count]
            if len(pattern) != width_count or min(pattern) < .8:
                continue
            repeats = 1
            while (start + (repeats + 1) * width_count <= len(gaps) and
                   np.max(np.abs(gaps[start + repeats * width_count:
                                      start + (repeats + 1) * width_count] -
                                 pattern)) <= .12):
                repeats += 1
            if repeats < 3:
                continue
            end = start + repeats * width_count
            score = (repeats * width_count, stations[end] - stations[start])
            if best is None or score > best[0]:
                best = (score, start, end, pattern, repeats)
    if best is None:
        return None
    _, start, end, pattern, repeats = best
    period = float(sum(pattern))
    first = stations[start]
    last = stations[end]
    if period < 3. or last - first < .15 * dimensions[axis]:
        return None
    after = [x for x in stations[end + 1:]
             if x - stations[end + 1] >= .04 * dimensions[axis]]
    major = [0., first, stations[end + 1] if end + 1 < len(stations) else last]
    if after:
        major.append(after[0])
    major.append(float(dimensions[axis]))
    major = sorted(set(round(x, 2) for x in major))
    return dict(axis=axis, start=first, pitch=round(period, 2),
                intervals=[round(float(value), 2) for value in pattern],
                repeats=repeats, major_stations=major)


def upper_end_chamfers(mesh, dimensions):
    """Read the top-right straight bevel in FRONT and SIDE from sharp edges."""
    result = {}
    edges = feature_edges(np.asarray(mesh.triangles))
    height = float(dimensions[2])
    for axis, normal in ((0, 1), (1, 0)):
        end = float(dimensions[axis])
        candidates = defaultdict(int)
        for a, b, _ in edges:
            top, low = (a, b) if a[2] >= b[2] else (b, a)
            run = float(low[axis] - top[axis])
            drop = float(top[2] - low[2])
            if (abs(float(top[2]) - height) <= .08 and
                    abs(float(low[axis]) - end) <= .08 and
                    abs(float(top[1-axis] - low[1-axis])) <= .08 and
                    run >= max(1., .01 * end) and
                    drop >= max(1., .01 * height) and
                    abs(run - drop) <= .1):
                candidates[(round(float(top[axis]), 2),
                            round(run, 2), round(drop, 2))] += 1
        if candidates:
            (start, run, drop), count = max(candidates.items(),
                                            key=lambda item: (item[1], item[0][1]))
            if count >= 2:
                result[normal] = dict(start=start, run=run, drop=drop)
    return result


def two_stage_coaxial_plan(feature):
    """Find adjacent concentric stages and their actual rim stations."""
    circles=grouped_circles(feature["circles"])
    dims=feature["dimensions"]
    best=None
    for index,first in enumerate(circles):
        for second in circles[index+1:]:
            axis=np.asarray(first["axis"],float)
            major=int(np.argmax(np.abs(axis)))
            if (abs(axis[major])<.995 or
                    abs(float(second["axis"][major]))<.995 or
                    abs(first["diameter"]-second["diameter"])<
                    max(1.,.05*max(first["diameter"],second["diameter"]))):
                continue
            transverse=[k for k in range(3) if k!=major]
            if np.linalg.norm(np.asarray(first["center"])[transverse]-
                              np.asarray(second["center"])[transverse])>.15:
                continue
            a0,a1=min(first["axial_levels"]),max(first["axial_levels"])
            b0,b1=min(second["axial_levels"]),max(second["axial_levels"])
            if (min(a1-a0,b1-b0)<2. or
                    min(abs(a1-b0),abs(b1-a0))>.15):
                continue
            stations=sorted({0.,round(float(dims[major]),2),
                             *[round(float(value),2)
                               for value in (*first["axial_levels"],
                                             *second["axial_levels"])]})
            if stations[0]<-.1 or stations[-1]>dims[major]+.1:
                continue
            minimum_interval=.01*float(dims[major])
            stations=[value for index,value in enumerate(stations)
                      if index in (0,len(stations)-1) or
                      (value-stations[index-1]>=minimum_interval and
                       stations[-1]-value>=minimum_interval)]
            score=(a1-a0)+(b1-b0)
            transverse_size=min(float(dims[k]) for k in transverse)
            kind=("bore" if max(first["diameter"],second["diameter"])<
                  .9*transverse_size else "outer")
            candidate=dict(axis=major,stations=stations,
                           kind=kind,
                           stages=[dict(diameter=first["diameter"],length=a1-a0),
                                   dict(diameter=second["diameter"],length=b1-b0)])
            if best is None or score>best[0]:
                best=(score,candidate)
    return best[1] if best else None


def add_shaft_flat_pages(out, src, feature, flats):
    """Show a true shaft-flat section and its measured axial location."""
    dimensions = np.asarray(feature["dimensions"], dtype=float)
    for flat in flats:
        axis = flat["axis"]
        transverse = [index for index in range(3) if index != axis]
        normal = max(transverse, key=lambda index: abs(flat["normal"][index]))
        source_rect, axes = projection_rect_and_axes(src, normal)
        if axis not in axes:
            raise ValueError("Shaft axis absent from flat projection")
        name = {0: "SIDE", 1: "FRONT", 2: "TOP"}[normal]
        page = out.new_page(width=1190, height=900)
        page.draw_rect(fitz.Rect(0, 0, 1190, 72), fill=BLUE, color=None)
        text(page, 42, 40, f"{feature['family'].upper()}  |  SHAFT FLAT DIMENSIONS",
             17, WHITE, True)
        text(page, 46, 94, f"{name} VIEW | AXIAL LOCATION", 11, INK, True)
        dest = fitz.Rect(80, 115, 1050, 465)
        page.show_pdf_page(dest, src.parent, src.number, clip=source_rect,
                           keep_proportion=False)
        page.draw_rect(dest, color=LINE, width=.75)
        pw, ph = source_rect.width, source_rect.height
        u, v = axes
        scale = min((pw - 45) / dimensions[u],
                    (ph - 55) / dimensions[v])
        ox = source_rect.x0 + 31 + (pw - 45 - dimensions[u] * scale) / 2
        oy = source_rect.y0 + ph - 31 - (ph - 55 - dimensions[v] * scale) / 2

        def project(point):
            px = ox + point[u] * scale
            py = oy - point[v] * scale
            return (dest.x0 + (px - source_rect.x0) * dest.width / pw,
                    dest.y0 + (py - source_rect.y0) * dest.height / ph)

        other = next(index for index in axes if index != axis)
        anchor = (flat["bounds_max"][other] if flat["normal"][other] > 0
                  else flat["bounds_min"][other])
        center = np.asarray([(lo + hi) / 2 for lo, hi in zip(
            flat["bounds_min"], flat["bounds_max"])])

        def station(value):
            point = center.copy()
            point[axis] = value
            point[other] = anchor
            return project(point)

        length = dimensions[axis]
        if flat["start"] <= length - flat["end"]:
            offset_start, offset_end = 0., flat["start"]
            offset_label = "FROM LEFT END"
        else:
            offset_start, offset_end = flat["end"], length
            offset_label = "TO RIGHT END"
        first, last = station(flat["start"]), station(flat["end"])
        offset_a, offset_b = station(offset_start), station(offset_end)
        if axis == u:
            for a, b, rail, label in (
                (first, last, 505, f"FLAT LENGTH {num(flat['length'])}"),
                (offset_a, offset_b, 552,
                 f"{offset_label} {num(offset_end-offset_start)}"),
            ):
                x0, x1 = sorted((a[0], b[0]))
                line(page, (x0, rail), (x1, rail), RED, 1)
                for point in (a, b):
                    line(page, (point[0], point[1] + 5),
                         (point[0], rail - 7), RED, .5)
                    line(page, (point[0], rail - 5),
                         (point[0], rail + 5), RED, .9)
                label_width = fitz.get_text_length(label, fontname="hebo",
                                                   fontsize=11)
                if rail == 552:
                    tx = max(80, min(x0, 1100-label_width))
                    text(page, tx, rail + 24, label, 11, RED, True)
                else:
                    tx = max(80, min((x0+x1-label_width)/2, 1100-label_width))
                    text(page, tx, rail - 10, label, 11, RED, True)
        else:
            for a, b, rail, label in (
                (first, last, 1080, f"FLAT {num(flat['length'])}"),
                (offset_a, offset_b, 1130,
                 f"END {num(offset_end-offset_start)}"),
            ):
                y0, y1 = sorted((a[1], b[1]))
                line(page, (rail, y0), (rail, y1), RED, 1)
                for point in (a, b):
                    line(page, (point[0]+5, point[1]),
                         (rail-7, point[1]), RED, .5)
                    line(page, (rail-5, point[1]),
                         (rail+5, point[1]), RED, .9)
                text(page, rail-12, max(130, y0-12), label, 9, RED, True)

        text(page, 80, 615, "END-ON ORTHOGRAPHIC VIEW | FLAT WIDTH & DEPTH",
             11, INK, True)
        end_source,end_axes=projection_rect_and_axes(src,axis)
        end_dest=fitz.Rect(80,630,590,845)
        page.show_pdf_page(end_dest,src.parent,src.number,clip=end_source,
                           keep_proportion=False)
        page.draw_rect(end_dest,color=LINE,width=.75)
        eu,ev=end_axes
        end_scale=min((end_source.width-45)/dimensions[eu],
                      (end_source.height-55)/dimensions[ev])
        end_ox=(end_source.x0+31+
                (end_source.width-45-dimensions[eu]*end_scale)/2)
        end_oy=(end_source.y0+end_source.height-31-
                (end_source.height-55-dimensions[ev]*end_scale)/2)
        def end_project(point):
            return np.asarray((
                end_dest.x0+(end_ox+point[eu]*end_scale-end_source.x0)
                *end_dest.width/end_source.width,
                end_dest.y0+(end_oy-point[ev]*end_scale-end_source.y0)
                *end_dest.height/end_source.height))
        candidate_centers=[np.asarray(circle["center"],float)
                           for circle in grouped_circles(feature["circles"])
                           if abs(circle["axis"][axis])>.985 and
                           abs(circle["diameter"]-flat["diameter"])<.08]
        if not candidate_centers:
            raise ValueError("Verified shaft flat lost its cylinder center")
        center=candidate_centers[0]
        outward=np.asarray(flat["normal"],float)
        outward[axis]=0
        outward/=np.linalg.norm(outward)
        tangent=np.zeros(3)
        tangent[eu],tangent[ev]=-outward[ev],outward[eu]
        radius=flat["diameter"]/2
        chord_center=center+outward*(radius-flat["depth"])
        left=end_project(chord_center-tangent*flat["width"]/2)
        right=end_project(chord_center+tangent*flat["width"]/2)
        mid=end_project(chord_center)
        rim=end_project(center+outward*radius)
        page.draw_line(fitz.Point(*left),fitz.Point(*right),
                       color=MUTED,width=1.1,dashes="[3 2] 0")
        outward_page=rim-mid
        outward_page/=np.linalg.norm(outward_page)
        tangent_page=right-left
        tangent_page/=np.linalg.norm(tangent_page)
        width_offset=26*outward_page
        left_rail,right_rail=left+width_offset,right+width_offset
        line(page,left_rail,right_rail,RED,.9)
        for actual,rail in ((left,left_rail),(right,right_rail)):
            line(page,actual,rail+6*outward_page,RED,.55)
            line(page,rail-5*outward_page,rail+5*outward_page,RED,.8)
        width_label=f"FLAT WIDTH {num(flat['width'])}"
        label_mid=(left_rail+right_rail)/2+14*outward_page
        text(page,label_mid[0]-fitz.get_text_length(
            width_label,fontname="hebo",fontsize=9)/2,
             label_mid[1],width_label,9,RED,True)
        depth_offset=34*tangent_page
        near,far=mid+depth_offset,rim+depth_offset
        line(page,near,far,RED,.9)
        for actual,rail in ((mid,near),(rim,far)):
            line(page,actual,rail+5*tangent_page,RED,.55)
            line(page,rail-5*outward_page,rail+5*outward_page,RED,.8)
        depth_label=f"DEPTH {num(flat['depth'])}"
        depth_mid=(near+far)/2+10*tangent_page
        text(page,depth_mid[0],depth_mid[1],depth_label,9,RED,True)
        text(page, 620, 690, f"SHAFT Ø{num(flat['diameter'])}", 13, INK, True)
        text(page, 620, 726,
             f"FLAT AXIAL RANGE: {('XYZ')[axis]} "
             f"{num(flat['start'])}-{num(flat['end'])} mm",
             11, RED, True)
        text(page, 620, 759,
             "Dashed flat edge is projected from the measured local feature.",
             10, MUTED)
        text(page, 43, 864,
             "Flat plane and cylindrical radius measured from the same aligned model; units: mm.",
             9, MUTED)
    return len(flats)


def choose_axial_projection_normal(dimensions, bores, normals=(0, 1)):
    """Choose an axial projection by drawable span and bore separation."""
    height = float(dimensions[2])
    def score(normal):
        across = 1 - normal
        scale = min(980 / float(dimensions[across]), 360 / height)
        stations = [float(center[across]) for bore in bores
                    for center in bore["centers"]]
        spread = max(stations) - min(stations) if stations else 0.
        return (float(dimensions[across]) + .25 * spread) * scale
    return max(normals, key=lambda normal: (score(normal), -normal))


def projected_planar_face_stations(mesh, dimensions, across, normal):
    """Return substantial projected wall planes, excluding circular facets."""
    areas = defaultdict(float)
    for triangle, center, face_normal, area in zip(
            mesh.triangles, mesh.triangles_center, mesh.face_normals,
            mesh.area_faces):
        if (abs(float(face_normal[across])) > .999 and
                np.ptp(triangle[:, across]) < .02):
            areas[round(float(center[across]), 2)] += float(area)
    minimum = max(25., .07 * dimensions[normal] * dimensions[2])
    return sorted({0., round(float(dimensions[across]), 2),
                   *(station for station, area in areas.items()
                     if area >= minimum)})


def draw_projected_profile_chains(page, project, feature, mesh, normal,
                                  view_rect):
    """Dimension substantial wall stations and horizontal face heights."""
    dimensions = feature["dimensions"]
    across = 1 - normal
    x_stations = projected_planar_face_stations(
        mesh, dimensions, across, normal)
    z_stations = sorted({0., float(dimensions[2]),
                         *horizontal_face_levels(mesh, dimensions)})
    model_bottom = project(0, 0)[1]
    chain_y = min(view_rect.y1 - 64, model_bottom + 93)
    if chain_y - model_bottom >= 65 and len(x_stations) > 2:
        short_index = 0
        for a, b in zip(x_stations, x_stations[1:]):
            xa, xb = project(a, 0)[0], project(b, 0)[0]
            line(page, (xa, chain_y), (xb, chain_y), RED, .8)
            for xx in (xa, xb):
                line(page, (xx, chain_y - 5), (xx, chain_y + 5), RED, .75)
            caption = num(b - a)
            width = fitz.get_text_length(caption, fontname="hebo", fontsize=9)
            if xb - xa < width + 5:
                label_y = chain_y + 23 + 16 * (short_index % 2)
                short_index += 1
                line(page, ((xa + xb) / 2, chain_y + 3),
                     ((xa + xb) / 2, label_y - 11), RED, .55)
                text(page, max(view_rect.x0 + 5,
                               min((xa + xb - width) / 2,
                                   view_rect.x1 - width - 5)),
                     label_y, caption, 9, RED, True)
            else:
                text(page, (xa + xb - width) / 2, chain_y - 9,
                     caption, 9, RED, True)
    rail_x = project(0, 0)[0] - 37
    if rail_x - view_rect.x0 >= 26 and len(z_stations) > 2:
        for a, b in zip(z_stations, z_stations[1:]):
            ya, yb = project(0, a)[1], project(0, b)[1]
            upper, lower = sorted((ya, yb))
            line(page, (rail_x, upper), (rail_x, lower), RED, .8)
            for yy in (upper, lower):
                line(page, (rail_x - 5, yy), (rail_x + 5, yy), RED, .75)
            caption = num(b - a)
            width = fitz.get_text_length(caption, fontname="hebo", fontsize=9)
            text(page, rail_x - width - 8, (upper + lower) / 2 + 3,
                 caption, 9, RED, True)
    return x_stations, z_stations


def add_direct_axial_projection_page(out, feature, bores, mesh, normal):
    """Redraw the selected axial projection and dimension bore depths in it."""
    dims = feature["dimensions"]
    across = 1 - normal
    view_name = "FRONT" if normal == 1 else "SIDE"
    page = out.new_page(width=1190, height=850)
    page.draw_rect(fitz.Rect(0, 0, 1190, 72), fill=BLUE, color=None)
    text(page, 42, 40,
         f"{feature['family'].upper()}  |  {view_name} BORE AND PROFILE DIMENSIONS",
         17, WHITE, True)
    text(page, 43, 96,
         f"{view_name} VIEW | measured bore spans, wall stations and face heights",
         11, INK, True)
    panel = fitz.Rect(52, 122, 1138, 780)
    page.draw_rect(panel, fill=(.97, .98, .99), color=LINE, width=.8)
    text(page, 72, 153, view_name, 16, INK, True)
    text(page, 1070, 153, "NTS", 9, MUTED)
    triangles = np.asarray(mesh.triangles)
    edges = feature_edges(triangles)
    minimum = triangles[:, :, [across, 2]].reshape(-1, 2).min(axis=0)
    span = np.ptp(triangles[:, :, [across, 2]].reshape(-1, 2), axis=0)
    scale = min(900 / span[0], 360 / span[1])
    x0 = (1190 - span[0] * scale) / 2
    y0 = 305 + (360 - span[1] * scale) / 2

    def project(station, z):
        return (x0 + (station - minimum[0]) * scale,
                y0 + (span[1] - (z - minimum[1])) * scale)

    for boundary, color, width in ((False, MUTED, .4), (True, INK, .7)):
        shape = page.new_shape()
        for first, second, edge_boundary in edges:
            if edge_boundary == boundary:
                shape.draw_line(project(first[across], first[2]),
                                project(second[across], second[2]))
        shape.finish(color=color, width=width)
        shape.commit()

    layout = plan_grouped_axial_bores_on_projection(
        page, project, feature, bores, across, panel)
    if layout is None:
        out.delete_page(len(out) - 1)
        return 0
    draw_grouped_axial_bores_on_projection(page, layout)
    draw_projected_profile_chains(page, project, feature, mesh, normal, panel)
    text(page, 43, 823,
         "Depth endpoints are verified inner circular rims in the aligned model; units: mm.",
         9, MUTED)
    return 1


def add_vertical_axial_bore_page(out, src, feature, bores, normal):
    """Add measured depth spans on an enlarged projection when inline space fails."""
    if not bores:
        return 0
    view_name = "FRONT" if normal == 1 else "SIDE"
    source_rect, _ = projection_rect_and_axes(src, normal)
    across = 1 - normal
    dims = feature["dimensions"]
    dest = fitz.Rect(65, 250, 1125, 735)
    label_bands = {
        "top": fitz.Rect(65, 117, 1125, 238),
        "bottom": fitz.Rect(65, 750, 1125, 875),
    }
    pw, ph = source_rect.width, source_rect.height
    scale = min((pw - 45) / dims[across], (ph - 55) / dims[2])
    ox = source_rect.x0 + 31 + (pw - 45 - dims[across] * scale) / 2
    oy = source_rect.y0 + ph - 31 - (ph - 55 - dims[2] * scale) / 2

    def project(station, z):
        px = ox + station * scale
        py = oy - z * scale
        return (dest.x0 + (px - source_rect.x0) * dest.width / pw,
                dest.y0 + (py - source_rect.y0) * dest.height / ph)

    pending = [list(bores)]
    page_count = 0
    while pending:
        batch = pending.pop(0)
        page = out.new_page(width=1190, height=930)
        page.draw_rect(fitz.Rect(0, 0, 1190, 72), fill=BLUE, color=None)
        text(page, 42, 40,
             f"{feature['family'].upper()}  |  {view_name} BORE DEPTHS",
             17, WHITE, True)
        text(page, 43, 96,
             "Verified bore spans on the enlarged axial projection | NTS",
             10, INK, True)
        page.show_pdf_page(dest, src.parent, src.number, clip=source_rect,
                           keep_proportion=False)
        page.draw_rect(dest, color=LINE, width=.75)
        layout = plan_grouped_axial_bores_on_projection(
            page, project, feature, batch, across, dest,
            label_bands=label_bands)
        if layout is None:
            out.delete_page(len(out) - 1)
            if len(batch) == 1:
                raise ValueError(f"{feature['family']}: no valid bore-depth layout")
            middle = len(batch) // 2
            pending[0:0] = [batch[:middle], batch[middle:]]
            continue
        draw_grouped_axial_bores_on_projection(page, layout)
        text(page, 43, 910,
             "Depth endpoints are verified inner STEP rims; units: mm.",
             9, MUTED)
        page_count += 1
    return page_count


def plan_grouped_axial_bores_on_projection(page, project, feature, bores,
                                           across, view_rect, label_bands=None):
    """Plan labels from measured text, free projection space and real bore rims.

    Return None when the existing projection cannot carry every group.  The
    caller then uses the separate dimensioned page instead of dropping labels.
    """
    if not bores:
        return []
    height = float(feature["dimensions"][2])
    margin, gap, card_height = 12., 12., 51.
    ordered = sorted(bores, key=lambda bore:(
        min(point[across] for point in bore["centers"]),
        bore["depth"], bore["diameter"]))
    labels = []
    for bore in ordered:
        low, high = float(bore["low"]), float(bore["high"])
        if (bore["side"] not in ("top", "bottom") or not bore["centers"]
                or low < -.1 or high > height + .1 or low >= high
                or abs(high - low - float(bore["depth"])) > .1):
            return None
        entry = float(bore.get("entry_z", high if bore["side"] == "top" else low))
        if abs(entry - (high if bore["side"] == "top" else low)) > .1:
            return None
        end = low if bore["side"] == "top" else high
        entry_side = (bore["side"].upper() + " ENTRY" if
                      abs(entry - (height if bore["side"] == "top" else 0.)) < .1
                      else "RECESSED " + bore["side"].upper() + " ENTRY")
        captions = (
            f"{bore['count']}x Ø{num(bore['diameter'])}  {entry_side}",
            f"CYL DEPTH {num(bore['depth'])}  |  Z {num(entry)} TO {num(end)}")
        labels.append(captions)
    model_top = min(project(0, 0)[1], project(0, height)[1])
    model_bottom = max(project(0, 0)[1], project(0, height)[1])
    text_rects = [fitz.Rect(block[0] - 3, block[1] - 3,
                            block[2] + 3, block[3] + 3)
                  for block in page.get_text("blocks")
                  if isinstance(block[4], str) and block[4].strip()]
    placed = []
    for side in ("top", "bottom"):
        entries = [(bore, captions) for bore, captions in zip(ordered, labels)
                   if bore["side"] == side]
        if not entries:
            continue
        area = (label_bands[side] if label_bands else
                fitz.Rect(view_rect.x0,
                          view_rect.y0 if side == "top" else model_bottom,
                          view_rect.x1,
                          model_top if side == "top" else view_rect.y1))
        widths = [max(
            fitz.get_text_length(caption, fontname="hebo", fontsize=size) + 14
            for caption, size in zip(captions, (8.4, 8.2)))
            for _, captions in entries]
        usable_width = area.width - 2 * margin
        if sum(widths) + gap * (len(entries) - 1) > usable_width:
            return None
        if len(entries) == 1:
            bore = entries[0][0]
            station_x = min(project(point[across], 0)[0]
                            for point in bore["centers"])
            lefts = [max(area.x0 + margin,
                         min(station_x - widths[0] / 2,
                             area.x1 - margin - widths[0]))]
        else:
            lefts = []
            for (bore, _), width in zip(entries, widths):
                station_x = min(project(point[across], 0)[0]
                                for point in bore["centers"])
                preferred = max(area.x0 + margin,
                                min(station_x - width / 2,
                                    area.x1 - margin - width))
                left = max(preferred,
                           lefts[-1] + widths[len(lefts) - 1] + gap
                           if lefts else area.x0 + margin)
                lefts.append(left)
            overflow = lefts[-1] + widths[-1] - (area.x1 - margin)
            if overflow > 0:
                lefts = [left - overflow for left in lefts]
            if lefts[0] < area.x0 + margin - .1:
                return None
        first_top = area.y0 + margin
        last_top = area.y1 - card_height - margin
        if last_top < first_top:
            return None
        cards = None
        for candidate in np.arange(first_top, last_top + 1, 4):
            trial = [fitz.Rect(left, candidate, left + width,
                               candidate + card_height)
                     for left, width in zip(lefts, widths)]
            if all(not any(box.intersects(existing) for existing in text_rects)
                   for box in trial):
                cards = trial
                break
        if cards is None:
            return None
        side_placed = []
        for (bore, captions), box in zip(entries, cards):
            target_x = (box.x0 + box.x1) / 2
            candidates = sorted(bore["centers"], key=lambda point:(
                abs(project(point[across], 0)[0] - target_x),
                point[across]))
            center = next((point for point in candidates
                           if all(abs(project(point[across], 0)[0] - old["x"]) > 18
                                  for old in side_placed)), None)
            if center is None:
                return None
            station = float(center[across])
            x = project(station, 0)[0]
            upper, lower = sorted((project(station, bore["high"])[1],
                                   project(station, bore["low"])[1]))
            if not view_rect.x0 + 5 < x < view_rect.x1 - 5:
                return None
            side_placed.append(dict(box=box, x=x, upper=upper, lower=lower,
                                    anchor_y=upper if side == "top" else lower,
                                    side=side, captions=captions))
        placed.extend(side_placed)
    return placed


def draw_grouped_axial_bores_on_projection(page, layout):
    """Draw a previously validated layout on its real projected bore stations."""
    for item in layout:
        box = item["box"]
        x, upper, lower = item["x"], item["upper"], item["lower"]
        line(page, (x, upper), (x, lower), RED, 1.1)
        for y in (upper, lower):
            line(page, (x - 5, y), (x + 5, y), RED, .9)
        leader_x = max(box.x0 + 8, min(x, box.x1 - 8))
        if item["side"] == "top":
            line(page, (leader_x, box.y1),
                 (x, item["anchor_y"] - 5), RED, .65)
        else:
            line(page, (leader_x, box.y0),
                 (x, item["anchor_y"] + 5), RED, .65)
        page.draw_circle(fitz.Point(x, item["anchor_y"]), 2.1,
                         color=RED, fill=RED)
        page.draw_rect(box, fill=WHITE, color=RED, width=.55)
        for row, caption in enumerate(item["captions"]):
            text(page, box.x0 + 7, box.y0 + 18 + row * 18,
                 caption, 8.4 if row == 0 else 8.2,
                 RED if row else INK, True)


def secondary_projection_batches(plan, unified_top=False):
    """Use one dimensioned page per orthographic direction."""
    buckets=defaultdict(lambda: defaultdict(list))
    oblique=[]
    secondary=plan["circular_coverage"]["secondary_features"]
    drawing=list(secondary)
    if unified_top:
        # An enlarged TOP projection must exist even when the selected
        # horizontal face has no Z-axis circles.  It is the full orthographic
        # linework, not a sparse interior slice.
        _=buckets[2]
        drawing.extend(circle for circle in plan["circular_coverage"]["all_features"]
                       if circle not in secondary and abs(circle["axis"][2])>.995)
    for circle in drawing:
        axis=np.asarray(circle["axis"],float)
        normal=int(np.argmax(np.abs(axis)))
        if abs(axis[normal])<.995:
            if circle in secondary:
                oblique.append(circle)
            continue
        buckets[normal][round(circle["diameter"],3)].append(circle)
    if plan.get("projected_stations"):
        # A nonplanar shell still has measured straight edges in TOP.  Keep
        # that view on the same page as any Z-axis circular features.
        _=buckets[2]
    if (unified_top and len(plan.get("horizontal_face_levels", [])) >= 2
            and plan.get("direct_axial_projection_normal") is None):
        # A TOP-only detail sheet cannot define the heights of substantial
        # pockets, ledges, or other horizontal faces.
        _=buckets[plan.get("preferred_axial_projection_normal", 1)]
    repeat = plan.get("repeated_axial_profile")
    if repeat:
        _=buckets[1 if repeat["axis"] == 0 else 0]
    for normal in plan.get("upper_end_chamfers", {}):
        _=buckets[normal]
    bore=plan.get("two_stage_coaxial_profile")
    if bore:
        _=buckets[{0:1,1:0,2:plan.get("preferred_axial_projection_normal",1)}[bore["axis"]]]
    shaft=plan.get("longitudinal_shaft_stations")
    if shaft:
        # The end-on circle view cannot show the axial shoulder positions.
        # Force an enlarged longitudinal view even without face-on circles.
        longitudinal_view={0:1,1:0,2:plan.get("preferred_axial_projection_normal",1)}[shaft["axis"]]
        _=buckets[longitudinal_view]
    batches=[]
    for normal,diameters in sorted(buckets.items()):
        u,v={1:(0,2),0:(1,2),2:(0,1)}[normal]
        groups=[]
        for diameter,group in sorted(diameters.items(),
                                     key=lambda item:(-len(item[1]),item[0])):
            group.sort(key=lambda c:(c["center"][v],c["center"][u]))
            groups.append((diameter,group))
        batches.append((normal,groups))
    return batches,oblique


def draw_stepped_bore_top_chain(page,feature,plan,project):
    """Put verified stepped-bore runs beside the enlarged TOP projection."""
    eligible=[item for item in plan.get("stepped_bore_axial_chains",[])
              if item["axis"]==1 and item["count"]>=2]
    if not eligible:
        return
    item=max(eligible,key=lambda entry:(entry["count"],sum(entry["stages"])))
    stations=[0.,item["stages"][0],sum(item["stages"])]
    rail_x=1124
    for station in stations:
        y=project(0,station)[1]
        line(page,(1099,y),(rail_x+5,y),RED,.55)
    for low,high in zip(stations,stations[1:]):
        y0,y1=project(0,low)[1],project(0,high)[1]
        upper,lower=min(y0,y1),max(y0,y1)
        line(page,(rail_x,upper),(rail_x,lower),RED,.9)
        for y in (upper,lower):
            line(page,(rail_x-5,y),(rail_x+5,y),RED,.8)
        text(page,rail_x+9,(upper+lower)/2+3,num(high-low),10,RED,True)


def draw_broad_interior_top_span(page,feature,plan,project):
    """Dimension one independently verified opening between broad inner walls."""
    wall=plan.get("broad_interior_wall_span")
    if not wall or wall["axis"]!=1:
        return
    x=min(.08*feature["dimensions"][0],12.)
    top=project(x,wall["high"])
    bottom=project(x,wall["low"])
    line(page,top,bottom,RED,1)
    for anchor in (top,bottom):
        line(page,(anchor[0]-5,anchor[1]),
             (anchor[0]+5,anchor[1]),RED,.9)
    value=num(wall["span"])
    y=(top[1]+bottom[1])/2
    tw=fitz.get_text_length(value,fontname="hebo",fontsize=11)
    page.draw_rect(fitz.Rect(top[0]+6,y-13,top[0]+tw+16,y+4),
                   fill=WHITE,color=None)
    text(page,top[0]+10,y,value,11,RED,True)


def draw_top_circular_rim(page,plan,project):
    """Call out a real circular top edge missed by analytic STEP extraction."""
    rims=plan.get("top_circular_rims",[])
    if not rims:
        return
    rim=rims[0]
    center=np.asarray(rim["center"],float)
    radius=rim["diameter"]/2
    anchor=np.asarray(project(center[0]+radius*.707,
                              center[1]+radius*.707),float)
    label=f"Ø{num(rim['diameter'])}"
    tx,ty=anchor+np.array((34,44))
    width=fitz.get_text_length(label,fontname="hebo",fontsize=10)
    tx=min(tx,1080-width)
    page.draw_rect(fitz.Rect(tx-4,ty-14,tx+width+5,ty+4),
                   fill=WHITE,color=RED,width=.4)
    line(page,anchor,(tx-4,ty-5),RED,.8)
    page.draw_circle(fitz.Point(*anchor),2,color=RED,fill=RED)
    text(page,tx,ty,label,10,RED,True)


def draw_tapered_mouth_top(page,feature,plan,project):
    """Label the true entrance rim of a STEP/mesh-verified conical bore."""
    groups=[item for item in plan.get("tapered_bores",[])
            if item["mouth_at"]=="top" and item["mouth_diameter"]>=3]
    if not groups:
        return
    length=feature["dimensions"][0]
    for index,group in enumerate(groups[:2]):
        center=min(group["centers"],
                   key=lambda xy:abs(xy[0]-.65*length))
        cx,cy=project(*center)
        radius=abs(project(center[0]+group["mouth_diameter"]/2,
                           center[1])[0]-cx)
        page.draw_circle(fitz.Point(cx,cy),radius,color=MUTED,width=.65)
        label=(f"{group['count']}x " if group["count"]>1 else "")+\
              f"Ø{num(group['mouth_diameter'])}"
        width=fitz.get_text_length(label,fontname="hebo",fontsize=10)
        tx=min(cx+radius+18,1080-width)
        ty=cy+radius+25+24*index
        rim=np.array((cx+radius*.707,cy+radius*.707))
        page.draw_rect(fitz.Rect(tx-4,ty-14,tx+width+5,ty+4),
                       fill=WHITE,color=RED,width=.4)
        line(page,rim,(tx-4,ty-5),RED,.8)
        page.draw_circle(fitz.Point(*rim),2,color=RED,fill=RED)
        text(page,tx,ty,label,10,RED,True)


def add_step_first_page(out, row, renders):
    """Use the standard three-view difficult-part template for a verified STEP."""
    buffer=io.BytesIO()
    page_width,page_height=landscape(A4)
    pdf=canvas.Canvas(buffer,pagesize=(page_width,page_height))
    family={"id":row["family"],"quantity":row["quantity"]}
    draw_difficult_family_page(pdf,family,renders,page_width,page_height)
    pdf.showPage()
    pdf.save()
    page=fitz.open(stream=buffer.getvalue(),filetype="pdf")
    out.insert_pdf(page)
    page.close()


def draw_coaxial_step_front(page, src, feature, steps, top_rails):
    """Put a verified two-step axial chain under the TOP projection."""
    top=658+62*top_rails+58
    source_rect,_=projection_rect_and_axes(src,1)
    dest=fitz.Rect(195,top,935,top+278)
    page.show_pdf_page(dest,src.parent,src.number,clip=source_rect,
                       keep_proportion=False)
    page.draw_rect(dest,color=LINE,width=.75)
    text(page,dest.x0,top-14,"FRONT AXIAL LENGTHS",11,INK,True)
    dims=feature["dimensions"]
    width,height=dims[0],dims[2]
    pw,ph=source_rect.width,source_rect.height
    scale=min((pw-45)/width,(ph-55)/height)
    ox=source_rect.x0+31+(pw-45-width*scale)/2
    oy=source_rect.y0+ph-31-(ph-55-height*scale)/2
    def project(x,z):
        return (dest.x0+(ox+x*scale-source_rect.x0)*dest.width/pw,
                dest.y0+(oy-z*scale-source_rect.y0)*dest.height/ph)
    center=width/2
    shoulder=steps["boss_height"]
    stations=((0,steps["boss_diameter"]),
              (shoulder,steps["outer_diameter"]),
              (height,steps["outer_diameter"]))
    chain_x=987
    for z,diameter in stations:
        rim_x,y=project(center+diameter/2,z)
        line(page,(rim_x+4,y),(chain_x+8,y),RED,.6)
        line(page,(chain_x-6,y),(chain_x+6,y),RED,.9)
    for (z0,_),(z1,_) in zip(stations,stations[1:]):
        y0,y1=project(center,z0)[1],project(center,z1)[1]
        line(page,(chain_x,y0),(chain_x,y1),RED,1)
        value=num(z1-z0)
        text(page,chain_x+15,(y0+y1)/2+4,value,12,RED,True)


def draw_radial_step_top(page, detail, project):
    """Dimension a STEP/section-verified two-diameter radial bore in TOP."""
    center=np.asarray(detail["center"],float)
    axis=np.asarray(detail["axis"],float)
    sideways=np.array((-axis[1],axis[0]))
    radii=(detail["inner_wall_diameter"]/2,
           detail["inner_wall_diameter"]/2+detail["inner_run"],
           detail["outer_wall_diameter"]/2)
    def point(radius,lateral=0):
        x,y=center+radius*axis+lateral*sideways
        return np.asarray(project(x,y),float)
    actual=[point(r) for r in radii]
    rail=[point(r,4.2) for r in radii]
    direction=rail[-1]-rail[0]
    direction/=np.linalg.norm(direction)
    normal=np.array((-direction[1],direction[0]))
    for source,mark in zip(actual,rail):
        line(page,source,mark,RED,.65)
        line(page,mark-5*normal,mark+5*normal,RED,.9)
    for index,(start,end) in enumerate(zip(rail,rail[1:])):
        line(page,start,end,RED,1)
        value=num(detail["inner_run"] if index==0 else detail["outer_run"])
        diameter=num(detail["inner_diameter"] if index==0 else detail["outer_diameter"])
        midpoint=(start+end)/2
        for label,offset in ((value,-12),(f"Ø{diameter}",12)):
            anchor=midpoint+offset*normal
            tw=fitz.get_text_length(label,fontname="hebo",fontsize=10)
            page.draw_rect(fitz.Rect(anchor[0]-tw/2-2,anchor[1]-10,
                                     anchor[0]+tw/2+2,anchor[1]+3),
                           fill=WHITE,color=None)
            text(page,anchor[0]-tw/2,anchor[1],label,10,RED,True)
    angle=f"{num(detail['angle_deg'])}° FROM +X"
    anchor=rail[-1]+np.array((20,-15))
    line(page,rail[-1],anchor+np.array((-4,3)),RED,.65)
    text(page,anchor[0],anchor[1],angle,10,RED,True)


def add_secondary_projection_pages(out, src, feature, plan, step_available,
                                   unified_top=False, inline_axial_normal=None):
    """Dimension missed circles on enlarged TOP/FRONT/SIDE, without a list page."""
    batches,oblique=secondary_projection_batches(plan,unified_top)
    inline_drawn = False
    axis_names="XYZ"
    for normal,groups in batches:
        circles=[circle for _,group in groups for circle in group]
        # Outline stations from an interior or bottom face are not necessarily
        # visible edges in the source TOP projection.  Never place their
        # dimensions on that view as if they were top-view boundaries.
        outline=(plan["profile"] if unified_top and normal==2 and
                 plan.get("profile_is_top") else None)
        side_bores=(top_projected_side_bores(plan,feature["dimensions"])
                    if outline else [])
        u,v={1:(0,2),0:(1,2),2:(0,1)}[normal]
        dims=feature["dimensions"]
        shaft=plan.get("longitudinal_shaft_stations")
        station_chains=[]
        seen_stations=set()
        if shaft and shaft["axis"]==u:
            stations=tuple(shaft["stations"])
            station_chains=[stations]
            seen_stations={stations}
        elif not shaft:
            for _,group in groups:
                stations=tuple(sorted({0.,round(float(dims[u]),2),
                                       *(round(float(c["center"][u]),2)
                                         for c in group)}))
                if stations not in seen_stations:
                    station_chains.append(stations)
                    seen_stations.add(stations)
        structural=(plan.get("projected_stations") if normal==2 else None)
        if structural:
            stations=tuple(sorted({0.,round(float(dims[u]),3),
                                   *(round(float(value),3)
                                     for value in structural["x_stations"]),
                                   *(round(float(circle["center"][u]),3)
                                     for circle in circles)}))
            station_chains=[stations]
            seen_stations={stations}
        if side_bores:
            for side_axis in (0,1):
                aligned=[bore for bore in side_bores if bore["axis"]==side_axis]
                if not aligned:
                    continue
                transverse=1-side_axis
                if transverse==u:
                    stations=tuple(sorted({0.,round(float(dims[u]),3),
                                           *(round(bore["center"],3)
                                             for bore in aligned)}))
                    if stations not in seen_stations:
                        station_chains.append(stations)
                        seen_stations.add(stations)
        repeat=plan.get("repeated_axial_profile")
        if repeat and repeat["axis"]==u and v==2:
            station_chains=[tuple(repeat["major_stations"])]
        else:
            repeat=None
        bore=plan.get("two_stage_coaxial_profile")
        if bore and bore["axis"]==u and v==2:
            station_chains=[tuple(bore["stations"])]
        else:
            bore=None
        extra_rails=len(station_chains)+(1 if outline else 0)+(2 if repeat else 0)
        page_height=max(850,850+62*(extra_rails-2))
        axial_inset=(plan.get("coaxial_steps") if normal==2 else None)
        if axial_inset:
            page_height=max(page_height,658+62*len(station_chains)+58+278+55)
        side_detail=(plan.get("orthogonal_bores") if normal==0 else None)
        side_rail=658+62*len(station_chains)+20
        if side_detail:
            page_height=max(page_height,
                            side_rail+49+54*(len(side_detail["side_blind"])-1)+90)
        p=out.new_page(width=1190,height=page_height)
        p.draw_rect(fitz.Rect(0,0,1190,72),fill=BLUE,color=None)
        view_name={2:"TOP",1:"FRONT",0:"SIDE"}[normal]
        title=(f"{view_name} BORE AND HEIGHT DIMENSIONS"
               if normal == inline_axial_normal and
               len(plan["vertical_axial_bores"]) > 2 and
               len(plan.get("horizontal_face_levels", [])) >= 2
               else f"{view_name} BORE DEPTHS"
               if normal == inline_axial_normal and
               len(plan["vertical_axial_bores"]) > 2
               else f"{view_name} AXIAL DIMENSIONS" if shaft and shaft["axis"]==u
               else f"{view_name} AXIAL STATIONS" if repeat
               else f"{view_name} BORE DEPTHS" if bore and bore["kind"]=="bore"
               else f"{view_name} AXIAL STAGE LENGTHS" if bore
               else f"{view_name} HEIGHT DIMENSIONS" if normal in (0,1) and
                    len(plan.get("horizontal_face_levels", []))>=2
               else f"{view_name} DIMENSIONS" if outline or structural
               else f"{view_name} ADDITIONAL DIMENSIONS")
        text(p,42,40,f"{feature['family'].upper()}  |  {title}",
             17,WHITE,True)
        if groups:
            text(p,43,91,"Dashed rims are hidden; diameter leaders end on the rim.",
                 10,MUTED)
        source_rect,axes=projection_rect_and_axes(src,normal)
        dest=fitz.Rect(135,117,1095,632)
        p.show_pdf_page(dest,src.parent,src.number,clip=source_rect,
                        keep_proportion=False)
        p.draw_rect(dest,color=LINE,width=.75)
        pw,ph=source_rect.width,source_rect.height
        assert axes==(u,v)
        scale=min((pw-45)/dims[u],(ph-55)/dims[v])
        ox=source_rect.x0+31+(pw-45-dims[u]*scale)/2
        oy=source_rect.y0+ph-31-(ph-55-dims[v]*scale)/2
        def project(u_value,v_value):
            px=ox+u_value*scale
            py=oy-v_value*scale
            return (dest.x0+(px-source_rect.x0)*dest.width/pw,
                    dest.y0+(py-source_rect.y0)*dest.height/ph)
        all_circles=plan["circular_coverage"]["all_features"]
        for side_bore in side_bores:
            left,bottom,right,top=side_bore["box"]
            x0,y0=project(left,bottom)
            x1,y1=project(right,top)
            p.draw_rect(fitz.Rect(min(x0,x1),min(y0,y1),
                                  max(x0,x1),max(y0,y1)),
                        color=RED,width=.9,dashes="[3 2] 0")
        occupied=[]
        for diameter,group in groups:
            for circle in group:
                cx,cy=project(circle["center"][u],circle["center"][v])
                rx=abs(project(circle["center"][u]+diameter/2,
                               circle["center"][v])[0]-cx)
                ry=abs(project(circle["center"][u],
                               circle["center"][v]+diameter/2)[1]-cy)
                hidden=any(
                    other["diameter"]>diameter+.05 and
                    abs(other["center"][u]-circle["center"][u])<.08 and
                    abs(other["center"][v]-circle["center"][v])<.08 and
                    max(other.get("axial_levels",[other["center"][normal]]))>
                    max(circle.get("axial_levels",[circle["center"][normal]]))+.05
                    for other in all_circles)
                p.draw_oval(fitz.Rect(cx-rx,cy-ry,cx+rx,cy+ry),
                            color=MUTED,width=.65,
                            dashes="[2 2] 0" if hidden else None)
                p.draw_circle(fitz.Point(cx,cy),3.2,color=RED,width=.9)
                line(p,(cx-7,cy),(cx+7,cy),RED,.65)
                line(p,(cx,cy-7),(cx,cy+7),RED,.65)
            target=max(group,key=lambda c:(c["center"][v],c["center"][u]))
            cx,cy=project(target["center"][u],target["center"][v])
            label=f"{len(group)}x Ø{num(diameter)}" if len(group)>1 else f"Ø{num(diameter)}"
            tw=fitz.get_text_length(label,fontname="hebo",fontsize=11)
            preferred=cx+22 if cx<dest.x1-tw-38 else cx-tw-22
            tx=max(dest.x0+8,min(preferred,dest.x1-tw-8))
            ty=next((y for y in (cy-24,cy-45,cy+26,cy+46,cy-65)
                     if dest.y0+22<y<dest.y1-10 and
                     all(not (tx<ox+ow+8 and tx+tw+8>ox and
                              y-13<oy+5 and y+5>oy-13)
                         for ox,oy,ow in occupied)),
                    max(dest.y0+27,min(cy-24,dest.y1-15)))
            occupied.append((tx,ty,tw))
            p.draw_rect(fitz.Rect(tx-3,ty-13,tx+tw+4,ty+4),
                        fill=WHITE,color=RED,width=.4)
            text(p,tx,ty,label,11,RED,True)
            anchor=np.asarray((tx-3 if tx>cx else tx+tw+4,ty-5),float)
            delta=anchor-np.asarray((cx,cy))
            rx=abs(project(target["center"][u]+diameter/2,
                           target["center"][v])[0]-cx)
            ry=abs(project(target["center"][u],
                           target["center"][v]+diameter/2)[1]-cy)
            factor=1/np.sqrt((delta[0]/rx)**2+(delta[1]/ry)**2)
            rim=np.asarray((cx,cy))+factor*delta
            line(p,rim,anchor,RED,.8)
            p.draw_circle(fitz.Point(*rim),1.6,color=RED,fill=RED)
        if normal==2:
            for radial in plan.get("radial_stepped_bores",[]):
                draw_radial_step_top(p,radial,project)
            draw_stepped_bore_top_chain(p,feature,plan,project)
            draw_broad_interior_top_span(p,feature,plan,project)
            draw_top_circular_rim(p,plan,project)
            draw_tapered_mouth_top(p,feature,plan,project)
        # Adjacent edge/center intervals are dimensioned even if too short
        # to hold text inside the segment: short values use staggered leaders.
        v_stations=([] if shaft or repeat or bore else
                    sorted({0.,round(float(dims[v]),3),
                            *(round(float(c["center"][v]),3) for c in circles),
                            *(round(float(value),3)
                              for value in (structural or {}).get("y_stations",[])),
                            *(round(float(value),3)
                              for value in (plan.get("horizontal_face_levels", [])
                                            if normal in (0,1) else []))}))
        for group_index,u_stations in enumerate(station_chains):
            # Put the center chain in the clear strip immediately below the
            # projected part.  The source view's overall X arrow sits farther
            # down; long extensions to an outside rail would cross its label.
            if plan.get("orthographic_top_for_profile") and normal==2:
                body_bottom=project(0,0)[1]
                inner_rail=body_bottom+38+40*group_index
                rail_y=(inner_rail if inner_rail<dest.y1-68 else
                        658+62*group_index)
            else:
                rail_y=658+62*group_index
            short_index=0
            stations_x=[project(value,0)[0] for value in u_stations]
            line(p,(stations_x[0],rail_y),(stations_x[-1],rail_y),RED,.8)
            for xx in stations_x:
                line(p,(xx,rail_y-5),(xx,rail_y+5),RED,.75)
            # Tie each measured station to a projected hole center.  Without
            # these extension lines the red ticks can appear unrelated to the
            # complete orthographic linework underneath them.
            for station in (u_stations[1:-1]
                            if plan.get("orthographic_top_for_profile") and
                            normal==2 and rail_y<dest.y1-68 else []):
                matching=[circle for circle in circles
                          if abs(circle["center"][u]-station)<.06]
                if not matching:
                    continue
                anchor=max((project(circle["center"][u],
                                    circle["center"][v])
                            for circle in matching),key=lambda point:point[1])
                line(p,(anchor[0],anchor[1]+7),
                     (anchor[0],rail_y-7),RED,.3)
            interval_lengths=[b-a for a,b in zip(u_stations,u_stations[1:])]
            index=0
            while index<len(interval_lengths):
                end=index+1
                while (end<len(interval_lengths) and
                       abs(interval_lengths[end]-interval_lengths[index])<.02):
                    end+=1
                repeats=end-index
                xa,xb=stations_x[index],stations_x[end]
                value=(f"{repeats}x {num(interval_lengths[index])}"
                       if repeats>1 else num(interval_lengths[index]))
                tw=fitz.get_text_length(value,fontname="hebo",fontsize=10)
                if xb-xa>=tw+8:
                    text(p,(xa+xb-tw)/2,rail_y-9,value,10,RED,True)
                else:
                    yy=rail_y+24+16*(short_index%2)
                    short_index+=1
                    line(p,((xa+xb)/2,rail_y+3),((xa+xb)/2,yy-11),RED,.55)
                    text(p,max(135,min((xa+xb-tw)/2,1095-tw)),yy,value,10,RED,True)
                index=end
        if repeat:
            first=float(repeat["start"])
            pitch=float(repeat["pitch"])
            count=int(repeat["repeats"])
            rail_y=658+62*len(station_chains)
            marks=[project(first+i*pitch,0)[0] for i in range(count+1)]
            line(p,(marks[0],rail_y),(marks[-1],rail_y),RED,.9)
            for xx in marks:
                line(p,(xx,rail_y-6),(xx,rail_y+6),RED,.8)
            label=f"{count}x PITCH {num(pitch)}"
            tw=fitz.get_text_length(label,fontname="hebo",fontsize=10)
            text(p,(marks[0]+marks[-1]-tw)/2,rail_y-11,label,10,RED,True)
            pieces=" + ".join(num(value) for value in repeat["intervals"])
            text(p,145,rail_y+50,
                 f"ONE REPEAT: {pieces} = {num(pitch)} mm",10,RED,True)
        if bore:
            labels="  |  ".join(
                f"Ø{num(stage['diameter'])} x {num(stage['length'])}"
                for stage in sorted(bore["stages"],
                                    key=lambda item:-item["diameter"]))
            kind="BORE" if bore["kind"]=="bore" else "OUTER"
            text(p,145,658+62*len(station_chains)+14,
                 f"COAXIAL {kind} STAGES: "+labels,10,RED,True)
        rail_x=(max(dest.x0+30,project(0,0)[0]-16)
                if plan.get("orthographic_top_for_profile") and normal==2
                else 103)
        short_index=0
        for a,b in zip(v_stations,v_stations[1:]):
            ya,yb=project(0,a)[1],project(0,b)[1]
            upper,lower=min(ya,yb),max(ya,yb)
            value=num(b-a)
            tw=fitz.get_text_length(value,fontname="hebo",fontsize=10)
            line(p,(rail_x,upper),(rail_x,lower),RED,.8)
            for yy in (upper,lower):line(p,(rail_x-5,yy),(rail_x+5,yy),RED,.75)
            label_y=(upper+lower)/2+3
            if lower-upper<13:
                label_y+=18*(short_index%3-1)
                short_index+=1
            text(p,rail_x-10-tw,label_y,value,10,RED,True)
        for station in (v_stations[1:-1]
                        if plan.get("orthographic_top_for_profile") and normal==2
                        else []):
            matching=[circle for circle in circles
                      if abs(circle["center"][v]-station)<.06]
            if not matching:
                continue
            anchor=min((project(circle["center"][u],
                                circle["center"][v])
                        for circle in matching),key=lambda point:point[0])
            line(p,(rail_x+7,anchor[1]),
                 (anchor[0]-7,anchor[1]),RED,.3)
        if side_bores:
            # An X-axis edge bore is a horizontal footprint in TOP.  Mark
            # its actual entry-to-bottom distance beside one representative
            # footprint; identical depths share one value and a count.
            x_depth_groups = defaultdict(list)
            for bore in side_bores:
                if bore["axis"] == 0:
                    entry_side = round(bore["entry"] / max(dims[0], .001))
                    x_depth_groups[(round(bore["depth"], 3), entry_side)].append(bore)
            used_depth_rails = []
            for (depth, _), matching in sorted(x_depth_groups.items()):
                bore = min(matching, key=lambda item: project(0, item["box"][3])[1])
                lo, _, hi, top = bore["box"]
                xa, xb = sorted((project(lo, 0)[0], project(hi, 0)[0]))
                footprint_top = project(0, top)[1]
                rail = max(dest.y0 + 38, footprint_top - 17)
                while any(abs(rail - previous) < 21 for previous in used_depth_rails):
                    rail += 22
                used_depth_rails.append(rail)
                line(p, (xa, rail), (xb, rail), RED, .8)
                for xx in (xa, xb):
                    line(p, (xx, rail - 5), (xx, footprint_top - 2), RED, .6)
                label = (f"{len(matching)}x {num(depth)}"
                         if len(matching) > 1 else num(depth))
                tw = fitz.get_text_length(label, fontname="hebo", fontsize=9)
                text(p, (xa + xb - tw) / 2, rail - 7, label, 9, RED, True)
            depth_groups={round(bore["depth"],3):bore
                          for bore in side_bores if bore["axis"]==1}
            for index,(depth,bore) in enumerate(sorted(depth_groups.items())):
                _,lo,_,hi=bore["box"]
                ya,yb=project(0,lo)[1],project(0,hi)[1]
                upper,lower=min(ya,yb),max(ya,yb)
                rail=dest.x1+20+25*index
                line(p,(rail,upper),(rail,lower),RED,.8)
                for yy in (upper,lower):line(p,(rail-5,yy),(rail+5,yy),RED,.7)
                text(p,rail+7,(upper+lower)/2+3,num(depth),9,RED,True)
        if normal==1 and plan.get("orthogonal_bores"):
            # STEP Z-axis rims give the drilling depth.  The dense vertical
            # strokes in this projection are facets of the through bore, not
            # a set of separate ribs needing a pitch dimension.
            for index,drill in enumerate(plan["orthogonal_bores"]["top_blind"]):
                center=max(drill["centers"],key=lambda item:item[0])
                top=project(center[0],dims[2])[1]
                bottom=project(center[0],drill["bottom_z"])[1]
                dim_x=dest.x1-53-55*index
                line(p,(dim_x,top),(dim_x,bottom),RED,.95)
                for yy in (top,bottom):
                    line(p,(dim_x-5,yy),(dim_x+5,yy),RED,.8)
                source_x=project(center[0],0)[0]
                for yy in (top,bottom):
                    line(p,(source_x+5,yy),(dim_x-7,yy),RED,.35)
                text(p,dim_x+8,(top+bottom)/2+4,
                     f"{drill['count']}x CYL DEPTH {num(drill['depth'])}",
                     10,RED,True)
        if normal == inline_axial_normal:
            bore_groups = plan["vertical_axial_bores"]
            layout = plan_grouped_axial_bores_on_projection(
                p, project, feature, bore_groups, u, dest)
            if layout is not None:
                draw_grouped_axial_bores_on_projection(p, layout)
                inline_drawn = True
        bevel=plan.get("upper_end_chamfers",{}).get(normal)
        if bevel:
            start=float(bevel["start"])
            run=float(bevel["run"])
            drop=float(bevel["drop"])
            top=np.asarray(project(start,dims[2]))
            corner=np.asarray(project(start+run,dims[2]))
            end=np.asarray(project(start+run,dims[2]-drop))
            upper_rail=top[1]-35
            line(p,(top[0],upper_rail),(corner[0],upper_rail),RED,.9)
            for xx in (top[0],corner[0]):
                line(p,(xx,upper_rail-5),(xx,top[1]-4),RED,.7)
            label=num(run)
            tw=fitz.get_text_length(label,fontname="hebo",fontsize=10)
            text(p,(top[0]+corner[0]-tw)/2,upper_rail-9,label,10,RED,True)
            side_rail=corner[0]+31
            line(p,(side_rail,corner[1]),(side_rail,end[1]),RED,.9)
            for yy in (corner[1],end[1]):
                line(p,(corner[0]+5,yy),(side_rail+5,yy),RED,.7)
            text(p,side_rail+8,(corner[1]+end[1])/2+4,
                 num(drop),10,RED,True)
        if side_detail:
            lo,hi=side_detail["through_bore"]["y_tangents"]
            tangent_stations=tuple(round(value,3)
                                   for value in (0.,lo,hi,dims[1]))
            draw_orthogonal_side_annotations(
                p,feature,side_detail,project,side_rail,
                tangent_stations not in seen_stations)
        if outline:
            stations=outline["x_outline_stations"]
            rail_y=658+62*len(station_chains)
            for a,b in zip(stations,stations[1:]):
                xa,xb=project(a,0)[0],project(b,0)[0]
                value=num(b-a)
                tw=fitz.get_text_length(value,fontname="hebo",fontsize=10)
                line(p,(xa,rail_y),(xb,rail_y),RED,.8)
                for xx in (xa,xb):line(p,(xx,rail_y-5),(xx,rail_y+5),RED,.75)
                if xb-xa>=tw+8:
                    text(p,(xa+xb-tw)/2,rail_y-9,value,10,RED,True)
                else:
                    tx=max(135,min((xa+xb-tw)/2,1095-tw))
                    text(p,tx,rail_y+25,value,10,RED,True)
            outline_y=sorted(set(outline["y_outline_stations"]))
            for a,b in zip(outline_y,outline_y[1:]):
                ya,yb=project(0,a)[1],project(0,b)[1]
                upper,lower=min(ya,yb),max(ya,yb)
                line(p,(35,upper),(35,lower),RED,.8)
                for yy in (upper,lower):line(p,(30,yy),(40,yy),RED,.75)
                text(p,42,(upper+lower)/2+3,num(b-a),9,RED,True)
        if axial_inset:
            draw_coaxial_step_front(p,src,feature,axial_inset,len(station_chains))
        footer=("Axial stations measured from planar shoulder faces; units: mm."
                if repeat else
                "Axial lengths measured between concentric STEP rim planes; units: mm."
                if bore else
                "Z levels measured from substantial horizontal faces; units: mm."
                if normal in (0,1) and len(plan.get("horizontal_face_levels", []))>=2
                else f"{axis_names[u]}/{axis_names[v]} edge-to-axis dimension chains; units: mm.")
        text(p,43,page_height-45,footer,9,MUTED)
        if step_available:
            text(p,43,page_height-18,
                 f"STEP PROVIDED: provided_part_files/{feature['family']}.step",
                 9,RED,True)
    secondary=plan["circular_coverage"]["secondary_features"]
    return len(batches),len(secondary)-len(oblique),len(oblique),inline_drawn


def draw_orthogonal_side_annotations(p,feature,detail,project,first_rail,
                                     draw_tangent_chain=True):
    """Dimension STEP-backed bore tangents and straight cylindrical depths."""
    width=feature["dimensions"][1]
    lo,hi=detail["through_bore"]["y_tangents"]
    stations=(0.,lo,hi,width)
    if draw_tangent_chain:
        for a,b in zip(stations,stations[1:]):
            xa,xb=project(a,0)[0],project(b,0)[0]
            line(p,(xa,first_rail),(xb,first_rail),RED,.9)
            for xx in (xa,xb):line(p,(xx,first_rail-5),(xx,first_rail+5),RED,.75)
            label=num(b-a)
            tw=fitz.get_text_length(label,fontname="hebo",fontsize=10)
            text(p,(xa+xb-tw)/2,first_rail-9,label,10,RED,True)
    for index,drill in enumerate(detail["side_blind"]):
        rail=first_rail+49+54*index
        x0=project(drill["start_y"],0)[0]
        x1=project(drill["end_y"],0)[0]
        line(p,(x0,rail),(x1,rail),RED,.9)
        for x in (x0,x1):line(p,(x,rail-5),(x,rail+5),RED,.8)
        representative=max(drill["centers"],key=lambda point:point[2])
        bore_y=project(representative[1],representative[2])[1]
        for x in (x0,x1):
            line(p,(x,bore_y+5),(x,rail-7),RED,.35)
        text(p,x1+10,rail+4,
             f"{drill['count']}x CYL DEPTH {num(drill['depth'])}",10,RED,True)


def add_orthogonal_bore_side_page(out,src,feature,plan,step_available):
    """Add SIDE only when the generic cylinder batch has no SIDE projection."""
    detail=plan.get("orthogonal_bores")
    if not detail:
        return 0
    batches,_=secondary_projection_batches(plan)
    if any(normal==0 for normal,_ in batches):
        return 0
    page_height=max(850,646+49+54*(len(detail["side_blind"])-1)+90)
    p=out.new_page(width=1190,height=page_height)
    p.draw_rect(fitz.Rect(0,0,1190,72),fill=BLUE,color=None)
    text(p,42,40,f"{feature['family'].upper()}  |  SIDE DIMENSIONS",17,WHITE,True)
    text(p,43,91,"Main bore position and STEP-measured side drilling depths",10,MUTED)
    source_rect,_=projection_rect_and_axes(src,0)
    dest=fitz.Rect(195,115,995,610)
    p.show_pdf_page(dest,src.parent,src.number,clip=source_rect,
                    keep_proportion=False)
    p.draw_rect(dest,color=LINE,width=.75)
    project=lambda y,z:side_project(feature,source_rect,dest,y,z)
    draw_orthogonal_side_annotations(p,feature,detail,project,646)
    if step_available:
        text(p,43,page_height-18,f"STEP PROVIDED: provided_part_files/{feature['family']}.step",
             9,RED,True)
    return 1


def draw_multi_surface_bend(page, src, feature, plan):
    """Dimension every analytic top-projected opening and a verified bend.

    This page uses no family-specific coordinates.  It groups equal diameters,
    shows projected center-spacing chains, and uses the original side view for
    bend and material-gauge callouts.  The STEP still covers unresolved form.
    """
    detail = plan["multi_surface_bend"]
    circles = detail["circular_features"]
    page.draw_rect(fitz.Rect(0,0,page.rect.width,72),fill=BLUE,color=None)
    text(page,42,40,f"{feature['family'].upper()}  |  ALL-FACE DIMENSIONS",17,WHITE,True)
    text(page,43,88,"TOP projection: analytic STEP openings on all levels and slopes",10,MUTED)
    top_src=original_top_rect(src)
    top_dst=fitz.Rect(42,100,1148,645)
    page.show_pdf_page(top_dst,src.parent,src.number,clip=top_src,keep_proportion=False)
    page.draw_rect(top_dst,color=LINE,width=.7)
    # The source view includes its own overall X/Y arrows.  Clear only their
    # margins, then place the denser hole-center chains there without overlap.
    page.draw_rect(fitz.Rect(43,207,168,515),fill=(.966,.970,.977),color=None)
    page.draw_rect(fitz.Rect(170,518,1117,642),fill=(.966,.970,.977),color=None)
    xy=lambda x,y:top_project(feature,top_src,top_dst,(x,y,0))
    length,width=feature["dimensions"][:2]
    # Every analytic opening is marked, even when its supporting face is
    # tilted or at a different Z from the chosen planar profile.
    for c in circles:
        cx,cy=xy(*c["center"][:2])
        page.draw_circle(fitz.Point(cx,cy),2.7,color=RED,width=.85)
        line(page,(cx-5,cy),(cx+5,cy),RED,.45)
        line(page,(cx,cy-5),(cx,cy+5),RED,.45)

    def chain(stations,rail,centers,above=False):
        stations=sorted({round(float(x),3) for x in stations})
        short_index=0
        for a,b in zip(stations,stations[1:]):
            x1,x2=xy(a,0)[0],xy(b,0)[0]
            label=num(b-a)
            tw=fitz.get_text_length(label,fontname="hebo",fontsize=8.8)
            line(page,(x1,rail),(x2,rail),RED,.75)
            for x in (x1,x2):line(page,(x,rail-4),(x,rail+4),RED,.65)
            if x2-x1>=tw+8:
                text(page,(x1+x2-tw)/2,rail-7,label,8.8,RED,True)
            else:
                label_y=rail+15+14*(short_index%3)
                short_index+=1
                line(page,((x1+x2)/2,rail),((x1+x2)/2,label_y-10),RED,.5)
                text(page,max(42,min((x1+x2-tw)/2,1148-tw)),
                     label_y,label,8.8,RED,True)
        for c in centers:
            px,py=xy(*c["center"][:2])
            stop=rail+5 if above else rail-5
            line(page,(px,py+(-4 if above else 4)),(px,stop),RED,.3)

    rows=defaultdict(list)
    for c in circles:
        rows[round(c["center"][1],2)].append(c)
    substantial=sorted((row for row in rows.values() if len(row)>=3),
                       key=lambda row:-np.mean([c["center"][1] for c in row]))
    if substantial:
        row=substantial[0]
        chain([0,length,*[c["center"][0] for c in row]],191,row,True)
    for i,row in enumerate(substantial[1:3]):
        chain([0,length,*[c["center"][0] for c in row]],548+i*30,row)
    pairs=sorted((row for row in rows.values() if len(row)==2),
                 key=lambda row:np.mean([c["center"][1] for c in row]))
    if pairs:
        row=pairs[0]
        chain([0,length,*[c["center"][0] for c in row]],604,row)
    x_left,x_right=xy(0,0)[0],xy(length,0)[0]
    line(page,(x_left,631),(x_right,631),MUTED,.9)
    for x in (x_left,x_right):line(page,(x,626),(x,636),MUTED,.9)
    overall=num(length)
    text(page,(x_left+x_right-fitz.get_text_length(overall,fontname="hebo",fontsize=10))/2,
         625,overall,10,MUTED,True)
    y_stations=sorted({0.,round(float(width),3),
                       *[round(float(c["center"][1]),3) for c in circles]})
    short_y_index=0
    for a,b in zip(y_stations,y_stations[1:]):
        ya,yb=xy(0,a)[1],xy(0,b)[1]
        line(page,(123,ya),(123,yb),RED,.75)
        for y in (ya,yb):line(page,(119,y),(127,y),RED,.65)
        label=num(b-a)
        tw=fitz.get_text_length(label,fontname="hebo",fontsize=8.6)
        mid=(ya+yb)/2+3
        if abs(yb-ya)<7:
            mid+=13*(short_y_index%3-1)
            short_y_index+=1
        text(page,115-tw,mid,label,8.6,RED,True)
    y_top,y_bottom=xy(0,width)[1],xy(0,0)[1]
    line(page,(65,y_top),(65,y_bottom),MUTED,.9)
    for y in (y_top,y_bottom):line(page,(60,y),(70,y),MUTED,.9)
    text(page,42,(y_top+y_bottom)/2+4,num(width),10,MUTED,True)
    # Tie each projected center row to the vertical chain without a coordinate
    # table.  Same-diameter tilted and horizontal groups remain distinct.
    for row in rows.values():
        left=min(row,key=lambda c:c["center"][0])
        cx,cy=xy(*left["center"][:2])
        line(page,(127,cy),(cx-6,cy),RED,.27)
    groups=defaultdict(list)
    for c in circles:
        axis=tuple(round(float(x),2) for x in c["axis"])
        groups[(round(c["diameter"],2),axis)].append(c)
    for taper in detail.get("local_tapered_bores",[]):
        for center in taper["centers"]:
            cx,cy=xy(*center)
            radius=taper["mouth_diameter"]/2
            rx=abs(xy(center[0]+radius,center[1])[0]-cx)
            ry=abs(xy(center[0],center[1]+radius)[1]-cy)
            page.draw_oval(fitz.Rect(cx-rx,cy-ry,cx+rx,cy+ry),
                           color=RED,width=.75,
                           dashes="[2 2] 0" if taper["mouth_at"]=="bottom" else None)
            groups[(taper["mouth_diameter"],(0.,0.,1.))].append(
                dict(diameter=taper["mouth_diameter"],
                     center=[center[0],center[1],0.],axis=[0.,0.,1.]))
    occupied=[]
    for (diameter,_axis),group in sorted(groups.items(),
                  key=lambda item:-np.mean([c["center"][1] for c in item[1]])):
        # Higher-Y examples avoid crossing the lower hole rows with leaders.
        target=max(group,key=lambda c:c["center"][1])
        cx,cy=xy(*target["center"][:2])
        label=(f"{len(group)}x " if len(group)>1 else "")+f"Ø{num(diameter)}"
        tw=fitz.get_text_length(label,fontname="hebo",fontsize=10.5)
        preferred=cx+19 if cx<950 else cx-tw-26
        tx=max(142,min(preferred,top_dst.x1-tw-12))
        alternatives=[cy-20,cy-38,cy+24,cy+42,cy-56,cy+60]
        ty=next((y for y in alternatives
                 if 214<y<525 and all(not (tx<ox+ow+10 and tx+tw+10>ox and
                                             y-13<oy+6 and y+5>oy-13)
                                         for ox,oy,ow in occupied)),
                max(218,min(cy-20,515)))
        occupied.append((tx,ty,tw))
        page.draw_rect(fitz.Rect(tx-3,ty-12,tx+tw+4,ty+4),fill=WHITE,color=RED,width=.3)
        text(page,tx,ty,label,10.5,RED,True)
        # A diameter leader terminates on the analytic projected rim.  The
        # red center crosses above locate patterns; they are never Ø targets.
        anchor=np.asarray((tx-3 if tx>cx else tx+tw+4,ty-5),float)
        delta=anchor-np.asarray((cx,cy))
        radius=diameter/2
        rx=abs(xy(target["center"][0]+radius,target["center"][1])[0]-cx)
        ry=(abs(xy(target["center"][0],target["center"][1]+radius)[1]-cy)
            *max(.1,abs(float(target["axis"][2]))))
        factor=1/np.sqrt((delta[0]/rx)**2+(delta[1]/ry)**2)
        rim=np.asarray((cx,cy))+factor*delta
        line(page,rim,anchor,RED,.75)
        page.draw_circle(fitz.Point(*rim),1.5,color=RED,fill=RED)
    text(page,43,665,"X/Y center spacing and true diameters; red crosses mark all analytic openings",9.5,MUTED)

    # Original orthographic SIDE, enlarged.  The matched concentric STEP arcs
    # establish the exact bend radii; flat opposite-surface pairs establish
    # each shown thickness independently.
    pw=(src.rect.width-72)/3
    ph=(src.rect.height-146)/3
    side_src=fitz.Rect(28+pw+8,92,28+2*pw+8,92+ph)
    side_dst=fitz.Rect(42,715,785,1040)
    page.show_pdf_page(side_dst,src.parent,src.number,clip=side_src,keep_proportion=False)
    page.draw_rect(side_dst,color=LINE,width=.7)
    text(page,43,705,"SIDE  |  VERIFIED BEND AND LOCAL GAUGES",10,INK,True)
    yz=lambda y,z:side_project(feature,side_src,side_dst,y,z)
    bend=detail["bend"]
    cy,cz=bend["center_y"],bend["center_z"]
    # The original SIDE is too small to separate radii differing by one sheet
    # gauge.  Show one locator there and an exact, scaled two-arc inset.
    direction=bend["z_direction"]
    theta=np.pi-direction*.72
    inner=(cy+bend["inner_radius"]*np.cos(theta),
           cz+bend["inner_radius"]*np.sin(theta))
    px,py=yz(*inner)
    page.draw_circle(fitz.Point(px,py),2,color=RED,fill=RED)
    line(page,(px,py),(330,812),RED,.9)
    text(page,337,816,"R"+num(bend["inner_radius"]),11,RED,True)
    tangents=bend.get("tangent_lines")
    if tangents:
        before=tangents["before"]
        arc_start=yz(*before["tangent"])
        straight_end=yz(*before["far"])
        # The vertical straight length terminates exactly at the STEP arc's
        # first tangent, so the radius is now located along the side wall.
        rail=min(arc_start[0],straight_end[0])-22
        line(page,(rail,arc_start[1]),(rail,straight_end[1]),RED,.85)
        for point in (arc_start,straight_end):
            line(page,(rail-4,point[1]),(point[0]-3,point[1]),RED,.65)
            page.draw_circle(fitz.Point(*point),1.7,color=RED,fill=RED)
        text(page,arc_start[0]+9,(arc_start[1]+straight_end[1])/2+4,
             num(before["length"]),10,RED,True)
        after=tangents["after"]
        if 3<after["angle_deg"]<85:
            a=yz(*after["tangent"])
            b=yz(*after["far"])
            vector=np.asarray(b)-np.asarray(a)
            normal=np.asarray((-vector[1],vector[0]))/np.linalg.norm(vector)
            if normal[1]<0:
                normal=-normal
            a_dim=np.asarray(a)+18*normal
            b_dim=np.asarray(b)+18*normal
            for original,shifted in ((a,a_dim),(b,b_dim)):
                line(page,original,shifted+4*normal,RED,.55)
                line(page,shifted-4*normal,shifted+4*normal,RED,.8)
                page.draw_circle(fitz.Point(*original),1.7,color=RED,fill=RED)
            line(page,a_dim,b_dim,RED,.85)
            label=num(after["length"])
            center=(a_dim+b_dim)/2+16*normal
            tw=fitz.get_text_length(label,fontname="hebo",fontsize=10)
            text(page,center[0]-tw/2,center[1],label,10,RED,True)
    gauges=detail["flat_gauges"]
    for i,gauge in enumerate(gauges):
        y=gauge["y_fraction"]*width
        bottom=yz(y,gauge["base_z"])
        upper=yz(y,gauge["base_z"]+gauge["thickness"])
        x=bottom[0]+(17 if i==0 else 24)
        line(page,(x,upper[1]),(x,bottom[1]),RED,.8)
        for yy in (upper[1],bottom[1]):line(page,(x-5,yy),(x+5,yy),RED,.7)
        label=num(gauge["thickness"])
        text(page,x-6,upper[1]-11,label,10.5,RED,True)
    inset=fitz.Rect(810,765,1145,1035)
    page.draw_rect(inset,fill=PALE,color=LINE,width=.7)
    text(page,827,792,"BEND",11,INK,True)
    scale=min(12,130/bend["outer_radius"])
    ox,oy=990,(960 if direction>0 else 850)
    theta_values=np.linspace(np.pi,np.pi-direction*bend["arc_span"],45)
    for radius in (bend["inner_radius"],bend["outer_radius"]):
        points=[(ox+radius*scale*np.cos(t),oy-radius*scale*np.sin(t))
                for t in theta_values]
        for a,b in zip(points,points[1:]):line(page,a,b,INK,1.05)
    for t in (theta_values[0],theta_values[-1]):
        a=(ox+bend["inner_radius"]*scale*np.cos(t),
           oy-bend["inner_radius"]*scale*np.sin(t))
        b=(ox+bend["outer_radius"]*scale*np.cos(t),
           oy-bend["outer_radius"]*scale*np.sin(t))
        line(page,a,b,INK,1.05)
    inner_target=(1000,909 if direction>0 else 915)
    outer_target=(1000,876 if direction>0 else 950)
    for radius,label,target in ((bend["inner_radius"],"R"+num(bend["inner_radius"]),inner_target),
                                (bend["outer_radius"],"R"+num(bend["outer_radius"]),outer_target)):
        t=np.pi-direction*.72
        point=(ox+radius*scale*np.cos(t),oy-radius*scale*np.sin(t))
        line(page,point,(target[0]-7,target[1]-5),RED,.7)
        text(page,*target,label,11,RED,True)
    x_outer=ox-bend["outer_radius"]*scale
    x_inner=ox-bend["inner_radius"]*scale
    dim_y=oy+20*direction
    line(page,(x_outer,dim_y),(x_inner,dim_y),RED,.85)
    for x in (x_outer,x_inner):line(page,(x,dim_y-6),(x,dim_y+6),RED,.75)
    text(page,x_outer-3,dim_y+16 if direction>0 else dim_y-11,
         num(bend["thickness"]),10,RED,True)


def draw_stepped_side_dimensions(page, section):
    """Dimension the raised return absent from a selected bottom XY face."""
    panel=fitz.Rect(40,608,802,885)
    page.draw_rect(panel,fill=PALE,color=LINE,width=.8)
    text(page,55,632,"SIDE  |  VERIFIED STEP SECTION",12,INK,True)
    width,height=section["width"],section["height"]
    sx=(panel.x1-110-62)/width
    sz=125/height
    base_y=800
    xy=lambda y,z:(110+y*sx,base_y-z*sz)
    outline=section["outline"]
    for a,b in zip(outline,outline[1:]+outline[:1]):
        line(page,xy(*a),xy(*b),INK,1.3)
    front=xy(0,0)[0]
    lower=xy(section["lower_start"],0)[0]
    upper=xy(section["upper_end"],0)[0]
    rear=xy(width,0)[0]
    top=xy(0,height)[1]
    bottom=base_y

    def horizontal(a,b,rail,label,source_y):
        line(page,(a,rail),(b,rail),RED,.9)
        for x,origin_y in zip((a,b),source_y):
            line(page,(x,rail-5),(x,rail+5),RED,.8)
            line(page,(x,origin_y+3 if rail>origin_y else origin_y-3),
                 (x,rail-6 if rail>origin_y else rail+6),RED,.48)
        tw=fitz.get_text_length(label,fontname="hebo",fontsize=10)
        text(page,(a+b-tw)/2,rail-8,label,10,RED,True)

    # Two exterior overhangs locate the lower face and the raised return.
    horizontal(front,upper,652,num(section["upper_end"]),(top,top))
    horizontal(front,lower,832,num(section["lower_start"]),(top,bottom))
    line(page,(front,860),(rear,860),MUTED,.85)
    for x in (front,rear):line(page,(x,855),(x,865),MUTED,.8)
    value=num(width)
    tw=fitz.get_text_length(value,fontname="hebo",fontsize=9)
    text(page,(front+rear-tw)/2,852,value,9,MUTED,True)
    line(page,(75,top),(75,bottom),MUTED,.8)
    for y in (top,bottom):line(page,(70,y),(80,y),MUTED,.8)
    text(page,43,(top+bottom)/2+4,num(height),9,MUTED,True)
    gauge_x=(front+upper)/2
    gauge_low=xy(0,height-section["thickness"])[1]
    line(page,(gauge_x,top),(gauge_x,gauge_low),RED,.85)
    for y in (top,gauge_low):line(page,(gauge_x-5,y),(gauge_x+5,y),RED,.8)
    text(page,gauge_x+9,top+8,num(section["thickness"]),10,RED,True)


def draw_chamfer_panel_dimensions(page, feature, source_rect, dest_rect, shape):
    """Dimension both mirrored corner legs and the paired openings in-view."""
    length, width = feature["dimensions"][:2]
    project = lambda x, y: top_project(feature, source_rect, dest_rect, (x, y, 0))
    x0, y_bottom = project(0, 0)
    x1, y_top = project(length, width)
    x_left = project(shape["left_run"], 0)[0]
    x_right = project(length-shape["right_run"], 0)[0]
    y_left_chamfer = project(0, shape["left_rise"])[1]
    y_right_chamfer = project(length, shape["right_rise"])[1]
    centers = shape["hole_centers"]
    hole_left = project(*centers[0][:2])
    hole_right = project(*centers[1][:2])

    def horizontal(a, b, y, label, label_size=9):
        line(page, (a, y), (b, y), RED, .9)
        for x in (a, b): line(page, (x, y-5), (x, y+5), RED, .8)
        tw = fitz.get_text_length(label, fontname="hebo", fontsize=label_size)
        text(page, (a+b-tw)/2, y-7, label, label_size, RED, True)

    def vertical(x, upper, lower, label, right_side=True):
        line(page, (x, upper), (x, lower), RED, .9)
        for y in (upper, lower): line(page, (x-5, y), (x+5, y), RED, .8)
        tx = x+8 if right_side else x-28
        text(page, tx, (upper+lower)/2+3, label, 9, RED, True)

    # Hole centers: edge offset, center pitch, edge offset; one Ø callout.
    center_rail = hole_left[1] + 58
    for center in (hole_left, hole_right):
        line(page, (center[0], center[1]+9), (center[0], center_rail+5), RED, .6)
    for a,b,value in ((x0,hole_left[0],num(centers[0][0])),
                      (hole_left[0],hole_right[0],num(centers[1][0]-centers[0][0])),
                      (hole_right[0],x1,num(length-centers[1][0]))):
        horizontal(a,b,center_rail,value)
    vertical(x1+29, y_top, hole_right[1],
             num(width-centers[0][1]), True)
    # The small red center ring on the old draft was only a locator. The
    # actual grey perimeter is the STEP-confirmed Ø4.5 circular edge.
    diameter = shape["hole_diameter"]
    radius_page = (hole_right[0]-hole_left[0]) / (centers[1][0]-centers[0][0]) * diameter/2
    anchor = (hole_right[0]+radius_page, hole_right[1])
    elbow = (x1+19, hole_right[1]+16)
    line(page, anchor, elbow, RED, .8)
    text(page, x1+24, elbow[1]+8, f"2x Ø{num(diameter)}", 10, RED, True)

    # Two linear legs for each corner, matching the user's blue guides.
    lower_rail = y_bottom+14
    horizontal(x0,x_left,lower_rail,num(shape["left_run"]))
    horizontal(x_left,x_right,lower_rail,
               num(length-shape["left_run"]-shape["right_run"]))
    horizontal(x_right,x1,lower_rail,num(shape["right_run"]))
    vertical(x0-25,y_left_chamfer,y_bottom,num(shape["left_rise"]),False)
    vertical(x1+29,y_right_chamfer,y_bottom,num(shape["right_rise"]),True)


def top_projected_side_bores(plan, dimensions):
    """Project edge-entering, in-plane STEP cylinders onto a top-face plan.

    A Y-axis bore appears as a narrow slot in TOP, not as a circular hole.
    Its diameter belongs to FRONT; TOP needs its XY footprint and stations.
    Repeated analytic rims of one bore are collapsed to one footprint.
    """
    profile=plan.get("profile")
    if not profile or abs(profile["plane_z"]-dimensions[2])>.08:
        return []
    footprints={}
    for circle in plan["circular_coverage"]["secondary_features"]:
        axis=np.asarray(circle["axis"],float)
        normal=int(np.argmax(np.abs(axis)))
        if normal not in (0,1) or abs(axis[normal])<.995:
            continue
        levels=circle.get("axial_levels",[])
        if len(levels)<2:
            continue
        ordered=sorted(set(round(float(value),3) for value in levels))
        # One grouped cylinder may consist of separated, matching bores in
        # two flanges.  Never draw its min/max as one continuous hidden slot.
        spans=(list(zip(ordered[::2],ordered[1::2]))
               if len(ordered)>=4 and len(ordered)%2==0 else
               [(ordered[0],ordered[-1])])
        diameter=float(circle["diameter"])
        transverse=1-normal
        center=float(circle["center"][transverse])
        if (center-diameter/2<-.08 or
                center+diameter/2>dimensions[transverse]+.08):
            continue
        has_edge_span=any(min(abs(lo),abs(hi-dimensions[normal]))<=.08
                          for lo,hi in spans)
        for lo,hi in spans:
            if hi-lo<diameter or not (has_edge_span or
                    min(abs(lo),abs(hi-dimensions[normal]))<=.08):
                continue
            if normal==1:
                box=(center-diameter/2,lo,center+diameter/2,hi)
            else:
                box=(lo,center-diameter/2,hi,center+diameter/2)
            key=(normal,*(round(value,2) for value in box))
            footprints[key]=dict(axis=normal,box=box,diameter=diameter,
                                 center=center,
                                 entry=hi if abs(hi-dimensions[normal])<.08 else lo,
                                 depth=hi-lo)
    return list(footprints.values())


def draw_profile_dimensions(page, src, feature, dest_rect, plan,
                            step_available=False):
    """Put measured face dimensions on the full TOP orthographic linework.

    All stations are derived from the selected planar face, not case or family
    IDs.  When a 3D certificate is absent this page remains a partial drawing;
    the JSON audit routes that family to a single-part STEP.
    """
    source_rect=original_top_rect(src)
    page.show_pdf_page(dest_rect,src.parent,src.number,clip=source_rect,
                       keep_proportion=False)
    page.draw_rect(dest_rect,color=LINE,width=.8)
    profile = plan["profile"]
    side_bores=top_projected_side_bores(plan,feature["dimensions"])
    length, width = feature["dimensions"][:2]
    def xy(x,y):
        return top_project(feature,source_rect,dest_rect,(x,y,0))
    x0,y0=xy(0,0)
    scale=xy(1,0)[0]-x0

    shown_radii = set()
    for arc in profile.get("fillet_arcs", []):
        radius = float(arc["radius"])
        if radius <= MIN_DRAWN_DIMENSION_MM:
            continue
        key = round(radius, 2)
        if key in shown_radii:
            continue
        shown_radii.add(key)
        anchor = xy(*arc["midpoint"])
        label = f"R{num(radius)}"
        tx = max(dest_rect.x0 + 24, min(anchor[0] - 44, dest_rect.x1 - 65))
        ty = max(dest_rect.y0 + 55, min(anchor[1] - 24, dest_rect.y1 - 18))
        line(page, anchor, (tx + 15, ty + 3), RED, .65)
        text(page, tx, ty, label, 9, RED, True)
    for bore in side_bores:
        left,bottom,right,top=bore["box"]
        x_left,y_bottom=xy(left,bottom)
        x_right,y_top=xy(right,top)
        page.draw_rect(fitz.Rect(x_left,y_top,x_right,y_bottom),
                       color=MUTED,width=.9,dashes="[3 2] 0")

    def horizontal_chain(stations, rail, label_size=8.5, origins=None):
        stations = simplify_dimension_stations(stations)
        if not stations:
            return
        positions = [xy(x,0)[0] for x in stations]
        short_index=0
        for i,(a,b) in enumerate(zip(positions,positions[1:])):
            value = num(stations[i+1]-stations[i])
            tw = fitz.get_text_length(value,fontname="hebo",fontsize=label_size)
            line(page,(a,rail),(b,rail),RED,.75)
            for x in (a,b): line(page,(x,rail-4),(x,rail+4),RED,.7)
            if b-a >= tw+9:
                text(page,(a+b-tw)/2,rail-7,value,label_size,RED,True)
            else:
                label_y=rail+(15+12*(short_index%2)
                              if rail<dest_rect.y0+38 else -15-12*(short_index%2))
                short_index+=1
                line(page,((a+b)/2,rail),((a+b)/2,label_y-6),RED,.5)
                text(page,max(dest_rect.x0+5,min((a+b-tw)/2,dest_rect.x1-tw-5)),
                     label_y,value,label_size,RED,True)
        if origins:
            for x,y in origins:
                px,py=xy(x,y)
                line(page,(px,py+4),(px,rail-5),RED,.55)

    def vertical_chain(stations, rail, label_size=8):
        stations = simplify_dimension_stations(stations)
        if not stations:
            return
        positions = [xy(0,y)[1] for y in stations]
        short_index=0
        for i,(a,b) in enumerate(zip(positions,positions[1:])):
            upper,lower=min(a,b),max(a,b)
            value=num(stations[i+1]-stations[i])
            line(page,(rail,upper),(rail,lower),RED,.75)
            for y in (upper,lower):line(page,(rail-4,y),(rail+4,y),RED,.7)
            tw=fitz.get_text_length(value,fontname="hebo",fontsize=label_size)
            label_y=(upper+lower)/2+3
            if lower-upper<6:
                label_y+=12*(short_index%3-1)
                short_index+=1
            text(page,rail+6,label_y,value,label_size,RED,True)

    # When a narrow planar flange is only one face of a longer axial body,
    # the dashed in-plane projection needs its own STEP-derived station chain.
    # It subsumes the flange's short outline chain, avoiding a duplicate 7.5.
    axial_levels={0.,round(float(length),3)}
    for circle in plan["circular_coverage"]["all_features"]:
        if abs(float(circle["axis"][0]))>=.995:
            axial_levels.update(round(float(value),3)
                                for value in circle.get("axial_levels",[])
                                if -.05<=value<=length+.05)
    axial_levels=sorted(axial_levels)
    projected_chain=(len(axial_levels)>=4 and len(axial_levels)<=10 and
                     max(profile["x_outline_stations"])<.35*length and
                     any(bore["axis"]==0 for bore in side_bores))
    if projected_chain:
        # The dominant cylindrical housing or a short boss can lie wholly
        # behind the chosen planar face.  Draw its own projected envelope so
        # each axial chain segment has a visible geometric endpoint.
        projected_envelopes=set()
        for circle in plan["circular_coverage"]["all_features"]:
            if abs(float(circle["axis"][0]))<.995:
                continue
            diameter=float(circle["diameter"])
            if diameter<.25*width:
                continue
            levels=sorted(set(round(float(value),3)
                              for value in circle.get("axial_levels",[])))
            if len(levels)<2:
                continue
            spans=(list(zip(levels[::2],levels[1::2]))
                   if len(levels)>=4 and len(levels)%2==0 else
                   [(levels[0],levels[-1])])
            cy=float(circle["center"][1])
            for lo,hi in spans:
                if hi-lo<.5 or cy-diameter/2<-.08 or cy+diameter/2>width+.08:
                    continue
                key=(round(lo,2),round(hi,2),round(cy,2),round(diameter,2))
                if key in projected_envelopes:
                    continue
                projected_envelopes.add(key)
                left,bottom=xy(lo,cy-diameter/2)
                right,top=xy(hi,cy+diameter/2)
                page.draw_rect(fitz.Rect(left,top,right,bottom),
                               color=MUTED,width=.65,dashes="[2 2] 0")
    # Outline vertex stations capture rectangular notches and both legs of
    # diagonal corners.  Separate hole-center chains avoid coordinate tables.
    top_y = xy(0,width)[1]
    if projected_chain:
        horizontal_chain(axial_levels,top_y-42)
    else:
        horizontal_chain(profile["x_outline_stations"],top_y-27)
    y_axis_bores=[bore for bore in side_bores if bore["axis"]==1]
    if y_axis_bores:
        x_centers=sorted({round(bore["center"],3) for bore in y_axis_bores})
        horizontal_chain([0.,*x_centers,round(length,3)],top_y-48)
        for bore in y_axis_bores:
            x_center,y_center=xy(bore["center"],bore["entry"])
            line(page,(x_center,y_center),(x_center,top_y-53),RED,.5)
        # Equal drilling depths share one dimension value.
        depth_groups={round(bore["depth"],3):bore for bore in y_axis_bores}
        depth_rail=xy(length,0)[0]+42
        for index,(depth,bore) in enumerate(sorted(depth_groups.items())):
            if depth <= MIN_DRAWN_DIMENSION_MM:
                continue
            _,lo,_,hi=bore["box"]
            upper,lower=sorted((xy(0,lo)[1],xy(0,hi)[1]))
            rail=depth_rail+23*index
            line(page,(rail,upper),(rail,lower),RED,.8)
            for y in (upper,lower):line(page,(rail-4,y),(rail+4,y),RED,.7)
            text(page,rail+6,(upper+lower)/2+3,num(depth),8,RED,True)
    # End-drilled X-axis bores are hidden in this face view.  Their dashed
    # footprints show the positions, but a dimension from the end face to
    # the blind end is needed to define each distinct depth.  Group equal
    # diameters/depths on each end and anchor the witness lines to one real
    # projected footprint instead of repeating a callout for every bore.
    x_axis_bores=[bore for bore in side_bores if bore["axis"]==0]
    for entry_is_right in (False,True):
        end_bores=[bore for bore in x_axis_bores
                   if (bore["entry"]>length/2)==entry_is_right]
        depth_groups=defaultdict(list)
        for bore in end_bores:
            depth_groups[(round(bore["diameter"],2),
                          round(bore["depth"],2))].append(bore)
        for index,((diameter,depth),bores) in enumerate(
                sorted(depth_groups.items(),key=lambda item:-item[0][1])):
            if depth <= MIN_DRAWN_DIMENSION_MM:
                continue
            rail=top_y-62-24*index
            if rail<dest_rect.y0+63:
                break
            # Prefer the uppermost bore as the witness anchor.  The short
            # rail is offset upward; its numerical value uses a leader when
            # it cannot fit between the two ticks.
            bore=max(bores,key=lambda item:item["center"])
            lo,_,hi,_=bore["box"]
            x_left=xy(lo,0)[0]
            x_right=xy(hi,0)[0]
            source_y=xy(0,bore["center"]+diameter/2)[1]
            for x in (x_left,x_right):
                line(page,(x,source_y-2),(x,rail+6),RED,.5)
                line(page,(x,rail-4),(x,rail+4),RED,.75)
            line(page,(x_left,rail),(x_right,rail),RED,.8)
            label=f"{len(bores)}x {num(depth)}" if len(bores)>1 else num(depth)
            label_width=fitz.get_text_length(label,fontname="hebo",fontsize=8.5)
            if x_right-x_left>=label_width+8:
                label_x=(x_left+x_right-label_width)/2
            elif entry_is_right:
                label_x=x_left-label_width-9
            else:
                label_x=x_right+9
            text(page,label_x,rail-6,label,8.5,RED,True)
    vertical_chain(profile["y_outline_stations"],x0-33)
    bottom_y = xy(0,0)[1]
    hole_rows = profile["x_hole_rows"]
    if hole_rows and not side_bores:
        # The measured outline chain above already defines the full X span.
        # Remove the copied view's redundant overall-X arrow before drawing
        # hole-center chains in that same margin. Otherwise its label can sit
        # directly under a center witness line or a segment value.
        x_end = xy(length,0)[0]
        page.draw_rect(fitz.Rect(x0-6,bottom_y+8,x_end+6,dest_rect.y1-3),
                       color=None,fill=(.969,.973,.98))
    for row_index,row in enumerate(hole_rows):
        if row_index >= 3:
            break
        centers=[(x,row["y"]) for x in row["stations"][1:-1]]
        horizontal_chain(row["stations"],bottom_y+28+25*row_index,
                         origins=centers)
    hole_rail=xy(length,0)[0]+15
    vertical_chain(profile["y_hole_stations"],hole_rail)
    for y in profile["y_hole_stations"]:
        projected_y=xy(0,y)[1]
        matching=[c for c in profile["circular_features"]
                  if abs(c["center"][1]-y)<.06]
        if matching:
            furthest=max(c["center"][0]+c["diameter"]/2 for c in matching)
            anchor_x=xy(furthest,y)[0]
        else:
            anchor_x=xy(length,y)[0]
        if 0 <= hole_rail-anchor_x <= 100:
            line(page,(anchor_x,projected_y),(hole_rail-3,projected_y),RED,.45)

    # Each diameter is labelled once and points to the actual physical rim.
    for idx,(diameter,count) in enumerate(profile["diameters"].items()):
        match=next(c for c in profile["circular_features"]
                   if abs(c["diameter"]-diameter)<.002)
        cx,cy=xy(*match["center"][:2])
        r=diameter*scale/2
        label=f"{count}x Ø{num(diameter)}" if count>1 else f"Ø{num(diameter)}"
        label_width=fitz.get_text_length(label,fontname="hebo",fontsize=9)
        tx=min(cx+r+19,dest_rect.x1-label_width-20)
        ty=max(dest_rect.y0+60,cy-21-idx*4)
        line(page,(cx+r,cy),(tx-4,ty-4),RED,.6)
        text(page,tx,ty,label,9,RED,True)

    if plan["status"] != "geometry_certified" and step_available and not side_bores:
        text(page,dest_rect.x0+16,dest_rect.y1-12,
             "3D geometry audit incomplete: use the corresponding part STEP.",
             8,MUTED)


def draw_lid_wall_dimensions(page, certificate):
    """Dimension both sheet thicknesses and the wall drop of a certified lid."""
    panel=fitz.Rect(43, 608, 799, 748)
    page.draw_rect(panel,fill=PALE,color=LINE,width=.7)
    text(page,58,630,"WALL PROFILE  |  NTS",10,INK,True)
    # The diagram is schematic, with dimension values from a volume-verified
    # three-level solid.  Exaggeration keeps a 1 mm wall leg readable.
    outline=[(236,650),(606,650),(606,712),(589,712),
             (589,667),(236,667)]
    for a,b in zip(outline,outline[1:]+outline[:1]):
        line(page,a,b,INK,1.2)
    top=certificate["wall_thickness"]
    drop=certificate["wall_height"]
    for a,b,value in ((650,667,top),(667,712,drop)):
        line(page,(211,a),(211,b),RED,.8)
        for y in (a,b):line(page,(207,y),(215,y),RED,.8)
        text(page,181,(a+b)/2+3,num(value),9,RED,True)
    line(page,(589,730),(606,730),RED,.8)
    for x in (589,606):line(page,(x,726),(x,734),RED,.8)
    text(page,594,723,num(top),9,RED,True)


def coaxial_z(circles, dimensions):
    """Detect a rotational family from analytic concentric Z-axis rims."""
    if len(circles)<2 or abs(dimensions[0]-dimensions[1])>.02*max(dimensions[:2]):
        return False
    centers=np.array([c["center"][:2] for c in circles])
    return (np.max(np.ptp(centers,axis=0))<.08 and
            all(abs(c["axis"][2])>.995 for c in circles))


def common_cylinder_axis(circles):
    """Return the geometric common axis, independent of drawing view names."""
    if len(circles)<2:
        return None
    reference=np.asarray(circles[0]["axis"],float)
    axis=int(np.argmax(np.abs(reference)))
    if any(abs(circle["axis"][axis])<.995 for circle in circles):
        return None
    transverse=[index for index in range(3) if index!=axis]
    centers=np.asarray([circle["center"] for circle in circles],float)
    if np.max(np.ptp(centers[:,transverse],axis=0))>=.08:
        return None
    return axis


def draw_rotational_dimensions(page, src, feature, dest_rect, circles, axial=False,
                               step_available=False, keyed_bore=None):
    """Label concentric STEP diameters on the complete TOP projection."""
    source_rect=original_top_rect(src)
    page.show_pdf_page(dest_rect,src.parent,src.number,clip=source_rect,
                       keep_proportion=False)
    page.draw_rect(dest_rect,color=LINE,width=.8)
    def project(point):
        return top_project(feature,source_rect,dest_rect,
                           (point[0],point[1],0))
    diameters=sorted({round(c["diameter"],3) for c in circles},reverse=True)
    center=np.mean([c["center"][:2] for c in circles],axis=0)
    cx,cy=project(center)
    scale=project(center+np.array((1.,0.)))[0]-cx
    for i,d in enumerate(diameters):
        radius=d*scale/2
        keyed = keyed_bore is not None and abs(d-keyed_bore["bore_diameter"])<.03
        # Keep the bore label outside the neighbouring concentric rims.
        tx=dest_rect.x0+(145 if keyed else 505)
        ty=dest_rect.y0+(310 if keyed else 106+i*50)
        anchor=(cx-radius*.7,cy+radius*.7) if keyed else (cx+radius,cy)
        line(page,anchor,(tx+25 if keyed else tx-13,ty-4),RED,.75)
        text(page,tx,ty,f"Ø{num(d)}",11,RED,True)
    if keyed_bore:
        center=np.asarray(keyed_bore["center"],float)
        floor=np.asarray(keyed_bore["floor"],float)
        direction=np.asarray(keyed_bore["direction"],float)
        # Width is measured along the slot floor.  The second dimension is
        # its perpendicular offset from the analytic bore center, avoiding a
        # guessed dimension from the nominal circle tangent.
        outward=floor+direction*(13/scale)
        for actual,offset in zip(floor,outward):
            line(page,project(actual),project(offset),RED,.65)
        line(page,project(outward[0]),project(outward[1]),RED,.85)
        floor_mid=np.mean(floor,axis=0)
        width_pos=project(floor_mid+direction*(17/scale))
        text(page,width_pos[0]+5,width_pos[1]+3,num(keyed_bore["width"]),9,RED,True)
        radial_end=center+direction*keyed_bore["center_to_floor"]
        line(page,project(center),project(radial_end),RED,.65)
        radial_label=project(center+direction*(.48*keyed_bore["center_to_floor"]))
        text(page,radial_label[0]-9,radial_label[1]-11,
             num(keyed_bore["center_to_floor"]),9,RED,True)
        # The slot is rotated relative to local +X; include that angular
        # datum so the width and radial offset locate it unambiguously.
        cx,cy=project(center)
        reference=(cx+45,cy)
        line(page,(cx,cy),reference,RED,.45)
        angle=((keyed_bore["angle_deg"]+180)%360)-180
        if abs(angle)>.5:
            sweep=np.linspace(0,np.deg2rad(angle),max(10,int(abs(angle)/3)))
            arc=[(cx+33*np.cos(value),cy-33*np.sin(value)) for value in sweep]
            for start,end in zip(arc,arc[1:]):
                line(page,start,end,RED,.65)
            mid=np.deg2rad(angle/2)
            arc_mid=(cx+33*np.cos(mid),cy-33*np.sin(mid))
            theta=np.deg2rad(angle)
            label=(arc_mid[0]+42*np.sin(theta)+14*np.cos(theta),
                   arc_mid[1]+42*np.cos(theta)-14*np.sin(theta))
            line(page,arc_mid,(label[0]-3,label[1]-9),RED,.45)
            text(page,*label,f"{num(keyed_bore['angle_deg'])}°",9,RED,True)
    if step_available:
        note=("Other unresolved geometry: use the corresponding part STEP."
              if axial else
              "Axial profile and groove geometry require the corresponding part STEP.")
        text(page,dest_rect.x0+18,dest_rect.y1-13,note,8,MUTED)


def draw_rotational_axial_dimensions(page, feature, plan, mesh):
    """Front axial section from a verified center-plane cut, with Z dimensions."""
    axial=plan["rotational_axial"]
    panel=fitz.Rect(43,608,799,849)
    page.draw_rect(panel,fill=PALE,color=LINE,width=.7)
    text(page,58,632,"FRONT AXIAL SECTION  |  FLANGE & BOSS",11,INK,True)
    text(page,744,632,"NTS",9,MUTED)
    dims=feature["dimensions"]
    cx,cy=np.asarray(dims[:2])/2
    section=mesh.section(plane_origin=(0,float(cy),0),
                         plane_normal=(0,1,0))
    if section is None or len(section.discrete)!=2:
        raise ValueError("Verified axial section was lost during drawing")
    view_cx=373
    x_scale=6
    z_scale=9
    bottom=805
    def x_at(x):return view_cx+(x-cx)*x_scale
    def y_at(z):return bottom-z*z_scale
    for loop in section.discrete:
        points=[fitz.Point(x_at(point[0]),y_at(point[2]))
                for point in loop]
        page.draw_polyline(points,color=INK,width=1.05)
    page.draw_line(fitz.Point(view_cx,bottom+10),
                   fitz.Point(view_cx,y_at(dims[2])-10),
                   color=MUTED,width=.6,dashes="[5 2 1 2] 0")

    # Two axial dimensions explain the boss and flange heights without
    # repeating the three diameters already called out in the TOP view.
    shoulder=y_at(axial["boss_height"])
    top=y_at(dims[2])
    for yy,edge_x in ((bottom,x_at(cx+axial["boss_diameter"]/2)),
                      (shoulder,x_at(cx+axial["outer_diameter"]/2)),
                      (top,x_at(cx+axial["outer_diameter"]/2))):
        line(page,(edge_x+3,yy),(622,yy),RED,.45)
    for ya,yb,value in ((top,shoulder,axial["flange_height"]),
                        (shoulder,bottom,axial["boss_height"])):
        line(page,(626,ya),(626,yb),RED,.85)
        for yy in (ya,yb):line(page,(622,yy),(630,yy),RED,.75)
        text(page,637,(ya+yb)/2+3,num(value),10,RED,True)

    for groove in axial["grooves"]:
        ya,yb=y_at(groove["upper"]),y_at(groove["lower"])
        line(page,(211,ya),(211,yb),RED,.85)
        for yy in (ya,yb):
            line(page,(207,yy),(215,yy),RED,.75)
            line(page,(217,yy),(x_at(cx-axial["outer_diameter"]/2)-4,yy),
                 RED,.45)
        text(page,178,(ya+yb)/2+3,num(groove["upper"]-groove["lower"]),
             10,RED,True)
        radial_min=axial["outer_diameter"]/2-groove["radius"]
        anchor=(x_at(cx+radial_min),y_at(groove["mid"]))
        line(page,anchor,(527,650),RED,.75)
        text(page,533,652,f"R{num(groove['radius'])}",10,RED,True)
    if axial["bevel"]:
        bevel=axial["bevel"]
        radius=axial["boss_diameter"]/2-bevel["run"]/2
        anchor=(x_at(cx+radius),y_at(bevel["rise"]/2))
        line(page,anchor,(446,821),RED,.7)
        text(page,451,827,
             f"{num(bevel['run'])} x {num(bevel['rise'])}",9,RED,True)


def verified_grooved_axial_profile(feature, mesh, geometry_path):
    """Certify a coaxial flanged wheel, its groove and stepped axial bore."""
    dimensions=np.asarray(feature["dimensions"],float)
    if abs(dimensions[0]-dimensions[1])>.01*max(dimensions[:2]):
        return None
    center=dimensions[:2]/2
    cylinders=grouped_circles(feature["circles"])
    if len(cylinders)!=4 or any(
            abs(circle["axis"][2])<.995 or
            np.linalg.norm(np.asarray(circle["center"][:2])-center)>.15
            for circle in cylinders):
        return None
    cylinders=sorted(cylinders,key=lambda circle:-circle["diameter"])
    outer,boss,counterbore,bore=cylinders
    height=float(dimensions[2])
    outer_levels=outer["axial_levels"]
    if (abs(outer["diameter"]-max(dimensions[:2]))>.15 or
            not outer["diameter"]>boss["diameter"]*1.5 or
            not boss["diameter"]>counterbore["diameter"]>bore["diameter"] or
            len(outer_levels)!=4 or
            any(len(group["axial_levels"])!=2
                for group in (boss,counterbore,bore)) or
            abs(outer_levels[0])>.08 or
            abs(boss["axial_levels"][0]-outer_levels[-1])>.08 or
            abs(boss["axial_levels"][-1]-height)>.08 or
            abs(counterbore["axial_levels"][0])>.08 or
            abs(counterbore["axial_levels"][-1]-bore["axial_levels"][0])>.08 or
            abs(bore["axial_levels"][-1]-height)>.08):
        return None
    lip_lower,lip_upper=outer_levels[1:3]
    flange_height=outer_levels[-1]
    if (lip_lower<.2 or flange_height-lip_upper<.2 or
            lip_upper-lip_lower<.25*flange_height or
            abs(lip_lower-(flange_height-lip_upper))>.1):
        return None
    section=mesh.section(plane_origin=(0,float(center[1]),0),
                         plane_normal=(0,1,0))
    if section is None or len(section.discrete)!=2:
        return None
    points=np.vstack(section.discrete)
    radii=points[:,0]-center[0]
    z=points[:,2]
    selected=(radii>.55*outer["diameter"]/2)&(
        radii<outer["diameter"]/2-max(.6,.02*outer["diameter"]))&(
        z>lip_lower)&(z<lip_upper)
    curved=np.column_stack((radii[selected],z[selected]))
    if len(curved)<25:
        return None
    arc_center,groove_radius,residual=_circle_fit(curved)
    if (residual>.04 or groove_radius<.4 or
            abs(arc_center[1]-(lip_lower+lip_upper)/2)>.1):
        return None
    root_diameter=2*(arc_center[0]-groove_radius)
    if geometry_path is not None:
        ring_candidates=[edge for edge in _local_analytic_edges(feature,geometry_path)
                         if (not edge["cylindrical"] and
                             abs(edge["axis"][2])>.995 and
                             abs(edge["center"][2]-arc_center[1])<.1 and
                             abs(2*edge["radius"]-root_diameter)<.1)]
        if ring_candidates:
            root_diameter=float(np.median([2*edge["radius"]
                                           for edge in ring_candidates]))
    # Two independent horizontal cuts verify the claimed outside diameters.
    for cylinder,station in ((outer,lip_lower/2),
                             (boss,(flange_height+height)/2)):
        cut=mesh.section(plane_origin=(0,0,float(station)),
                         plane_normal=(0,0,1))
        if cut is None:
            return None
        actual=max(np.linalg.norm(np.asarray(loop)[:,:2]-center,axis=1).max()
                   for loop in cut.discrete)
        if abs(actual-cylinder["diameter"]/2)>.12:
            return None
    return dict(section=section,center=center,outer_diameter=outer["diameter"],
                boss_diameter=boss["diameter"],
                counterbore_diameter=counterbore["diameter"],
                bore_diameter=bore["diameter"],height=height,
                flange_height=flange_height,
                counterbore_depth=counterbore["axial_levels"][-1],
                lip_lower=lip_lower,lip_upper=lip_upper,
                groove_radius=groove_radius,
                groove_root_diameter=root_diameter,
                groove_center_z=float(arc_center[1]))


def add_grooved_axial_front_page(out,feature,profile):
    """Dimension the measured FRONT center-plane profile of a flanged wheel."""
    page=out.new_page(width=841.89,height=760)
    page.draw_rect(fitz.Rect(0,0,page.rect.width,72),fill=BLUE,color=None)
    text(page,42,40,
         f"{feature['family'].upper()}  |  FRONT AXIAL DIMENSIONS",
         17,WHITE,True)
    panel=fitz.Rect(42,103,800,708)
    page.draw_rect(panel,fill=PALE,color=LINE,width=.8)
    text(page,59,131,"FRONT  |  CENTER-PLANE SECTION",12,INK,True)
    text(page,748,131,"NTS",9,MUTED)
    cx=float(profile["center"][0])
    center_x=406
    x_scale=6.5
    z_scale=22
    bottom_y=603
    def x_at(x):return center_x+(x-cx)*x_scale
    def y_at(z):return bottom_y-z*z_scale
    for loop in profile["section"].discrete:
        points=[fitz.Point(x_at(point[0]),y_at(point[2])) for point in loop]
        page.draw_polyline(points,color=INK,width=.95)
    page.draw_line((center_x,y_at(profile["height"])-17),
                   (center_x,bottom_y+17),color=MUTED,width=.6,
                   dashes="[5 2 1 2] 0")
    def vertical_dimension(rail,stations,labels,from_x=None,label_side=1):
        for a,b,label in zip(stations,stations[1:],labels):
            ya,yb=y_at(a),y_at(b)
            line(page,(rail,ya),(rail,yb),RED,.8)
            for yy in (ya,yb):line(page,(rail-4,yy),(rail+4,yy),RED,.75)
            text_width=fitz.get_text_length(label,fontname="hebo",fontsize=9)
            label_x=rail+7 if label_side>0 else rail-text_width-7
            text(page,label_x,(ya+yb)/2+3,label,9,RED,True)
        if from_x is not None:
            for station in stations:
                yy=y_at(station)
                line(page,(from_x,yy),(rail-5 if rail>from_x else rail+5,yy),
                     RED,.4)
    flange=profile["flange_height"]
    height=profile["height"]
    lip_lo=profile["lip_lower"]
    lip_hi=profile["lip_upper"]
    left_edge=x_at(cx-profile["outer_diameter"]/2)
    right_edge=x_at(cx+profile["outer_diameter"]/2)
    vertical_dimension(168,[0,lip_lo,lip_hi,flange],
                       [num(lip_lo),num(lip_hi-lip_lo),num(flange-lip_hi)],
                       left_edge,-1)
    vertical_dimension(664,[0,flange,height],
                       [num(flange),num(height-flange)],right_edge)
    vertical_dimension(749,[0,height],[num(height)],None)
    depth=profile["counterbore_depth"]
    vertical_dimension(618,[0,depth],[num(depth)])
    line(page,(x_at(cx+profile["counterbore_diameter"]/2),y_at(depth)),
         (613,y_at(depth)),RED,.45)

    # Width dimensions use the two true cylinder silhouettes at their own
    # axial levels; the groove root is measured from the fitted toroidal arc.
    boss_r=profile["boss_diameter"]/2
    boss_line=y_at(height)+27
    boss_left,boss_right=x_at(cx-boss_r),x_at(cx+boss_r)
    line(page,(boss_left,boss_line),(boss_right,boss_line),RED,.8)
    for xx in (boss_left,boss_right):
        line(page,(xx,boss_line-4),(xx,boss_line+4),RED,.75)
    label=f"Ø{num(profile['boss_diameter'])}"
    width=fitz.get_text_length(label,fontname="hebo",fontsize=10)
    text(page,(boss_left+boss_right-width)/2,boss_line-7,label,10,RED,True)
    root_r=profile["groove_root_diameter"]/2
    root_y=y_at(profile["groove_center_z"])
    root_left,root_right=x_at(cx-root_r),x_at(cx+root_r)
    line(page,(root_left,root_y),(root_right,root_y),RED,.65)
    for xx in (root_left,root_right):
        line(page,(xx,root_y-4),(xx,root_y+4),RED,.7)
    text(page,342,root_y-8,f"Ø{num(profile['groove_root_diameter'])}",9,RED,True)
    arc_z=profile["groove_center_z"]-profile["groove_radius"]*.7
    arc_r=(profile["groove_root_diameter"]/2+
           profile["groove_radius"]*(1-np.sqrt(1-.7**2)))
    anchor=(x_at(cx+arc_r),y_at(arc_z))
    line(page,anchor,(597,390),RED,.7)
    text(page,602,393,f"R{num(profile['groove_radius'])}",9,RED,True)
    counter_r=profile["counterbore_diameter"]/2
    counter_left,counter_right=x_at(cx-counter_r),x_at(cx+counter_r)
    counter_y=bottom_y+36
    line(page,(counter_left,counter_y),(counter_right,counter_y),RED,.8)
    for xx in (counter_left,counter_right):
        line(page,(xx,counter_y-4),(xx,counter_y+4),RED,.7)
    label=f"Ø{num(profile['counterbore_diameter'])}"
    width=fitz.get_text_length(label,fontname="hebo",fontsize=9)
    text(page,(counter_left+counter_right-width)/2,counter_y-7,label,9,RED,True)
    bore_anchor=(x_at(cx+profile["bore_diameter"]/2),y_at((depth+height)/2))
    line(page,bore_anchor,(520,275),RED,.7)
    text(page,526,278,f"Ø{num(profile['bore_diameter'])}",9,RED,True)
    text(page,43,739,"AXIAL DIMENSIONS FROM VERIFIED STEP RIMS AND MESH SECTION  |  mm",
         9,MUTED)


def tapered_side_selection(plan):
    """Choose the less crowded X-Z or Y-Z projection for a repeated bore group."""
    height=plan["dimensions"][2]
    options=[]
    for group in plan["tapered_bores"]:
        if abs(group["bore_depth"]+group["taper_depth"]-height)>.08:
            continue
        for axis in (1,0):
            stations=sorted({round(center[axis],2) for center in group["centers"]})
            if len(stations)<=3 and all(
                    b-a>group["mouth_diameter"]+.2
                    for a,b in zip(stations,stations[1:])):
                options.append((len(stations), -group["count"],
                                0 if axis==1 else 1, axis, group, stations))
    if not options:
        return None
    _,_,_,axis,group,stations=min(options, key=lambda v:v[:3])
    return axis,group,stations


def draw_tapered_side_dimensions(page, plan, circles, selection):
    """Draw a verified conical-bore side projection and direct size/depth dimensions."""
    axis,group,stations=selection
    panel=fitz.Rect(43, 608, 799, 849)
    page.draw_rect(panel,fill=PALE,color=LINE,width=.7)
    name="SIDE  |  Y-Z" if axis==1 else "FRONT  |  X-Z"
    text(page,58,632,f"{name}  |  TAPERED BORES",11,INK,True)
    text(page,744,632,"NTS",9,MUTED)
    span=plan["dimensions"][axis]
    height=plan["dimensions"][2]
    scale=min(450/span,9)
    x0=190+(450-span*scale)/2
    top,bottom=690,783
    z_scale=(bottom-top)/height

    def x_at(pos): return x0+pos*scale
    mouth_top=group["mouth_at"]=="top"
    mouth_y=top if mouth_top else bottom
    throat=(top+group["taper_depth"]*z_scale if mouth_top
            else bottom-group["taper_depth"]*z_scale)
    far_y=bottom if mouth_top else top
    page.draw_rect(fitz.Rect(x0,top,x_at(span),bottom),color=INK,width=1.1)
    for station in stations:
        cx=x_at(station)
        outer=group["mouth_diameter"]*scale/2
        inner=group["bore_diameter"]*scale/2
        for sign in (-1,1):
            page.draw_line(fitz.Point(cx+sign*outer,mouth_y),
                           fitz.Point(cx+sign*inner,throat),
                           color=MUTED,width=.85,dashes="[3 2] 0")
            page.draw_line(fitz.Point(cx+sign*inner,throat),
                           fitz.Point(cx+sign*inner,far_y),
                           color=MUTED,width=.85,dashes="[3 2] 0")

    # One annotation covers all equal mouths, and one covers all equal bores.
    cx=x_at(stations[0])
    for diameter,rail,label in (
            (group["mouth_diameter"],665 if mouth_top else 813,
             f"{group['count']}x Ø{num(group['mouth_diameter'])}"),
            (group["bore_diameter"],813 if mouth_top else 665,
             f"{group['count']}x Ø{num(group['bore_diameter'])}")):
        left,right=cx-diameter*scale/2,cx+diameter*scale/2
        line(page,(left,rail),(right,rail),RED,.9)
        for x in (left,right):
            line(page,(x,rail-4),(x,rail+4),RED,.8)
        label_width=fitz.get_text_length(label,fontname="hebo",fontsize=9)
        text(page,(left+right-label_width)/2,rail-7,label,9,RED,True)
    # The vertical chain is measured from the exposed mouth to the straight
    # cylinder and then to the opposite outer face.
    rail_x=708
    for ya,yb,value in ((top,throat,group["taper_depth"] if mouth_top
                        else group["bore_depth"]),
                        (throat,bottom,group["bore_depth"] if mouth_top
                         else group["taper_depth"])):
        line(page,(rail_x,ya),(rail_x,yb),RED,.9)
        for yy in (ya,yb):
            line(page,(rail_x-4,yy),(rail_x+4,yy),RED,.8)
        text(page,rail_x+9,(ya+yb)/2+3,num(value),9,RED,True)
    # A separate through bore may be visible in this projection. Include it
    # only when STEP has analytic rims at both outer Z levels.
    through=[]
    for circle in circles:
        if (abs(circle["axis"][2])<.995 or
                not any(abs(z)<.08 for z in circle["z_levels"]) or
                not any(abs(z-height)<.08 for z in circle["z_levels"])):
            continue
        if any(np.linalg.norm(np.asarray(circle["center"][:2])-center)<.08
               for center in group["centers"]):
            continue
        through.append(circle)
    if len(through)==1:
        circle=through[0]
        cx=x_at(circle["center"][axis])
        radius=circle["diameter"]*scale/2
        for xx in (cx-radius,cx+radius):
            page.draw_line(fitz.Point(xx,top),fitz.Point(xx,bottom),
                           color=MUTED,width=.8,dashes="[3 2] 0")
        label=f"Ø{num(circle['diameter'])}"
        tx=cx+radius+17
        line(page,(cx+radius,top+10),(tx-4,675),RED,.65)
        text(page,tx,678,label,9,RED,True)


def verified_section_axial_bores(mesh, feature, section):
    """Accept only analytic end bores corroborated by mesh section loops.

    A circular feature does not invalidate an otherwise measured constant
    outline when it is a simple coaxial bore entering one section end.
    Unrecognized circles retain the normal supplementary drawing path.
    """
    groups=grouped_circles(feature["circles"])
    if not groups:
        return []
    axis=section["axis"]
    length=section["length"]
    other=section["other"]
    bores=[]
    def matching_inner_ring(station, center, radius):
        loops=_section(mesh,axis,station)
        if len(loops)<2:
            return False
        largest=max(range(len(loops)),key=lambda index:_area(loops[index]))
        for index,points in enumerate(loops):
            if index==largest:
                continue
            fitted_center,fitted_radius,residual=_circle_fit(points)
            if (np.linalg.norm(fitted_center-center)<.08 and
                    abs(fitted_radius-radius)<.08 and residual<.04):
                return True
        return False
    for group in groups:
        direction=np.asarray(group["axis"],float)
        levels=group["axial_levels"]
        if abs(direction[axis])<.995 or len(levels)!=2:
            return None
        lo,hi=levels
        if hi-lo<.1 or lo<-.08 or hi>length+.08:
            return None
        if abs(lo)<.08:
            entry=0.
            depth=hi
            near=min(.04,depth/4)
            beyond=min(length-.04,hi+.04)
        elif abs(hi-length)<.08:
            entry=length
            depth=length-lo
            near=length-min(.04,depth/4)
            beyond=max(.04,lo-.04)
        else:
            return None
        center=np.asarray(group["center"],float)[other]
        radius=float(group["diameter"])/2
        if not matching_inner_ring(near,center,radius):
            return None
        through=abs(lo)<.08 and abs(hi-length)<.08
        if not through and matching_inner_ring(beyond,center,radius):
            return None
        bores.append(dict(center=center.tolist(),diameter=2*radius,
                          entry=entry,depth=depth,through=through))
    return bores


def add_extruded_section_page(out, feature, section, axial_bores=None):
    """Draw the measured end profile of a verified constant-section solid."""
    page = out.new_page(width=841.89, height=690)
    page.draw_rect(fitz.Rect(0, 0, page.rect.width, 72), fill=BLUE, color=None)
    text(page, 42, 40,
         f"{feature['family'].upper()}  |  SCRIPTED DIMENSION SUPPLEMENT",
         17, WHITE, True)
    names = "XYZ"
    u_axis, v_axis = section["other"]
    section_title=(f"{names[u_axis]}{names[v_axis]} TOP OUTLINE  |  MEASURED PROFILE"
                   if section["axis"]==2 else
                   f"{names[u_axis]}{names[v_axis]} END FACE  |  CONSTANT SECTION")
    text(page, 42, 96,section_title,11,INK,True)
    outline = np.asarray(section["outline"], float)
    low = outline.min(axis=0)
    high = outline.max(axis=0)
    scale = min(350 / (high[0]-low[0]), 350 / (high[1]-low[1]))
    def xy(point):
        return (220+(float(point[0])-low[0])*scale,
                525-(float(point[1])-low[1])*scale)
    points = [xy(point) for point in outline]
    shape = page.new_shape()
    shape.draw_polyline(points)
    shape.finish(color=INK, width=1.15)
    shape.commit()

    def horizontal_chain(values, rail, caption=None):
        values = simplify_dimension_stations(values)
        if not values:
            return
        ticks = [xy((value, low[1]))[0] for value in values]
        line(page, (ticks[0], rail), (ticks[-1], rail), RED, .8)
        for tick in ticks:
            line(page, (tick, rail-4), (tick, rail+4), RED, .8)
        for start, finish, a, b in zip(ticks, ticks[1:], values, values[1:]):
            label = f"{b-a:.3f}".rstrip("0").rstrip(".")
            width = fitz.get_text_length(label, fontname="hebo", fontsize=9)
            text(page, (start+finish-width)/2, rail-7, label, 9, RED, True)
        if caption:
            text(page, ticks[0]-18, rail+17, caption, 9, MUTED, True)

    def vertical_chain(values, rail, caption=None):
        values = simplify_dimension_stations(values)
        if not values:
            return
        ticks = [xy((low[0], value))[1] for value in values]
        line(page, (rail, ticks[0]), (rail, ticks[-1]), RED, .8)
        for tick in ticks:
            line(page, (rail-4, tick), (rail+4, tick), RED, .8)
        for a, b, start, finish in zip(values, values[1:], ticks, ticks[1:]):
            label = f"{b-a:.3f}".rstrip("0").rstrip(".")
            text(page, rail-35, (start+finish)/2+3, label, 8.5, RED, True)
        if caption:
            text(page, rail-16, ticks[0]+18, caption, 9, MUTED, True)

    horizontal_chain(section["stations"][0], 550)
    horizontal_chain([low[0], high[0]], 585, names[u_axis])
    vertical_chain(section["stations"][1], 177)
    vertical_chain([low[1], high[1]], 125, names[v_axis])

    # The radius was independently fitted to every curved interval, so one
    # leader can carry the count without repeating the same dimension.
    radius = round(section["radius"], 2)
    label = f"{section['arc_count']}x R{num(radius)}"
    guess = np.asarray((section["stations"][0][-2]+radius*.7,
                        section["stations"][1][-2]+radius*.7))
    anchor = xy(outline[np.argmin(np.linalg.norm(outline-guess, axis=1))])
    line(page, anchor, (604, 137), RED, .7)
    page.draw_circle(fitz.Point(*anchor), 1.8, color=RED, fill=RED)
    text(page, 609, 136, label, 10, RED, True)

    recesses = section["end_recesses"]
    if len(recesses) == 1:
        recess = recesses[0]
        centers = np.asarray(recess["centers"])
        for center in centers:
            page.draw_circle(fitz.Point(*xy(center)),
                             max(1.3, recess["diameter"]*scale/2),
                             color=INK, width=.65)
        for dimension, axis in ((0, 0), (1, 1)):
            stations = sorted({round(float(center[axis]), 3) for center in centers})
            if len(stations) >= 2:
                chain = [float(low[axis]), *stations, float(high[axis])]
                if dimension == 0:
                    horizontal_chain(chain, 134)
                else:
                    vertical_chain(chain, 622)
        caption = (f"{len(centers)}x Ø{num(recess['diameter'])}  "
                   f"DEPTH {num(recess['depth'])}")
        target = xy(centers[np.lexsort((-centers[:, 1], centers[:, 0]))[0]])
        label_position = (target[0]+28, target[1]-24)
        line(page, target, (label_position[0]-3, label_position[1]+3), RED, .7)
        page.draw_circle(fitz.Point(*target), 1.8, color=RED, fill=RED)
        text(page, *label_position, caption, 9, RED, True)

    if axial_bores:
        for index,bore in enumerate(axial_bores):
            center=np.asarray(bore["center"],float)
            target=xy(center)
            radius=bore["diameter"]*scale/2
            hidden=(section["axis"]==2 and bore["entry"]==0)
            page.draw_circle(fitz.Point(*target),radius,color=INK,width=.85,
                             dashes="[3 2] 0" if hidden else None)
            anchor=(target[0]+radius,target[1])
            label=(f"Ø{num(bore['diameter'])}  " +
                   ("THRU" if bore["through"] else
                    f"DEPTH {num(bore['depth'])}"))
            if hidden:
                label+="  BOTTOM"
            tx=min(anchor[0]+28,645)
            ty=target[1]-14-22*index
            line(page,anchor,(tx-5,ty+4),RED,.7)
            page.draw_circle(fitz.Point(*anchor),1.7,color=RED,fill=RED)
            text(page,tx,ty,label,9,RED,True)
        if len(axial_bores)==1:
            center=axial_bores[0]["center"]
            horizontal_chain([float(low[0]),float(center[0]),float(high[0])],615)
            vertical_chain([float(low[1]),float(center[1]),float(high[1])],708)

    text(page, 42, 672,
         f"{names[section['axis']]} LENGTH {num(section['length'])}  |  UNITS mm",
         9, MUTED)


def append_three_blender_base(out, clean, page_number):
    """Keep the three orthographic and first three Blender panels."""
    source = clean[page_number]
    second_row_bottom = grid_boxes(source.rect.width, source.rect.height)[5][1]
    cutoff = source.rect.height - second_row_bottom + 6
    target = out.new_page(width=source.rect.width, height=cutoff)
    visible = fitz.Rect(0, 0, source.rect.width, cutoff)
    target.show_pdf_page(visible, clean, page_number,
                         clip=visible, keep_proportion=False)


def full_length_groove_profile(mesh):
    """Find repeated open troughs from either horizontal extrusion axis.

    The returned stations come from upward-facing mesh faces, not a family ID
    or a projected bounding box.  Reject interrupted/unequal grooves so a
    single side profile is only used when it really defines every trough.
    """
    bounds = mesh.bounds
    spans = bounds[1] - bounds[0]
    if spans[2] <= 0 or spans[2] > .25 * min(spans[:2]):
        return None
    top, bottom = bounds[1, 2], bounds[0, 2]
    candidates = []
    for along in (0, 1):
        across = 1 - along
        troughs = set()
        upper_faces = 0
        for triangle, normal in zip(mesh.triangles, mesh.face_normals):
            if normal[2] < .99:
                continue
            lo, hi = triangle.min(axis=0), triangle.max(axis=0)
            if hi[along] - lo[along] < .98 * spans[along]:
                continue
            level = (lo[2] + hi[2]) / 2
            if abs(level - top) < .02:
                upper_faces += 1
            elif bottom + .02 < level < top - .02:
                troughs.add((round(float(lo[across]), 3),
                             round(float(hi[across]), 3), round(float(level), 3)))
        grooves = sorted(troughs)
        if upper_faces == 0 or len(grooves) < 2:
            continue
        widths = [b - a for a, b, _ in grooves]
        depths = [top - z for _, _, z in grooves]
        starts = [a for a, _, _ in grooves]
        pitches = np.diff(starts)
        if (min(widths) < .01 * spans[across] or
                max(widths) - min(widths) > .02 * max(widths) or
                max(depths) - min(depths) > .02 * max(depths) or
                max(pitches) - min(pitches) > .02 * max(pitches) or
                grooves[0][0] <= bounds[0, across] + .01 or
                grooves[-1][1] >= bounds[1, across] - .01):
            continue
        candidates.append({"along": along, "across": across,
                           "grooves": grooves, "bounds": bounds,
                           "pitch": float(np.median(pitches)),
                           "width": float(np.median(widths)),
                           "depth": float(np.median(depths))})
    return max(candidates, key=lambda item: (
        len(item["grooves"]), spans[item["along"]]), default=None)


def add_full_length_groove_page(out, src, feature, profile):
    """Dimension verified grooves on the complete FRONT or SIDE linework."""
    page = out.new_page(width=841.89, height=430)
    page.draw_rect(fitz.Rect(0, 0, page.rect.width, 72), fill=BLUE, color=None)
    text(page, 42, 40,
         f"{feature['family'].upper()}  |  GROOVE DIMENSIONS", 17, WHITE, True)
    text(page, 42, 89, "Repeated grooves dimensioned in the longitudinal side projection.",
         9.2, MUTED)
    bounds = profile["bounds"]
    axis = profile["across"]
    grooves = profile["grooves"]
    start, end = bounds[0, axis], bounds[1, axis]
    top, bottom = bounds[1, 2], bounds[0, 2]
    trough = top - profile["depth"]
    view_name = "SIDE" if axis == 1 else "FRONT"
    normal=0 if axis==1 else 1
    source_rect,axes=projection_rect_and_axes(src,normal)
    assert axes==(axis,2)
    dest=fitz.Rect(80,128,760,350)
    page.show_pdf_page(dest,src.parent,src.number,clip=source_rect,
                       keep_proportion=False)
    page.draw_rect(dest,color=LINE,width=.75)
    dims=feature["dimensions"]
    pw,ph=source_rect.width,source_rect.height
    scale=min((pw-45)/dims[axis],(ph-55)/dims[2])
    ox=source_rect.x0+31+(pw-45-dims[axis]*scale)/2
    oy=source_rect.y0+ph-31-(ph-55-dims[2]*scale)/2
    x_at=lambda value:dest.x0+(ox+value*scale-source_rect.x0)*dest.width/pw
    y_at=lambda value:dest.y0+(oy-value*scale-source_rect.y0)*dest.height/ph
    left,right=x_at(start),x_at(end)
    top_y,bottom_y=y_at(top),y_at(bottom)

    def horizontal(a, b, rail, label, y_anchor=top_y):
        xa, xb = x_at(a), x_at(b)
        for x in (xa, xb):
            line(page, (x, y_anchor - 2), (x, rail - 4), RED, .65)
            line(page, (x, rail - 3), (x, rail + 3), RED, .8)
        line(page, (xa, rail), (xb, rail), RED, .8)
        label_width = fitz.get_text_length(label, fontname="helv", fontsize=8.2)
        text(page, (xa + xb - label_width) / 2, rail - 5, label, 8.2, RED)

    first_a, first_b, _ = grooves[0]
    horizontal(start, first_a, top_y-22, num(first_a - start))
    horizontal(first_a, first_b, top_y-5, num(profile["width"]))
    horizontal(grooves[0][0], grooves[1][0], top_y-42,
               num(profile["pitch"]) + " PITCH")
    horizontal(grooves[-1][1], end, top_y-22,
               num(end - grooves[-1][1]))
    horizontal(start, end, 369, num(end - start), bottom_y)

    def vertical(x, z1, z2, label):
        ya, yb = y_at(z1), y_at(z2)
        line(page, (x, ya), (x, yb), RED, .8)
        for y in (ya, yb):
            line(page, (x - 3, y), (x + 3, y), RED, .8)
        text(page, x + 6, (ya + yb) / 2 + 3, label, 8.2, RED)

    vertical(62, bottom, top, num(top - bottom))
    vertical(x_at(first_b) + 17, trough, top, num(profile["depth"]))
    vertical(774, bottom, trough, num(trough - bottom))
    text(page, 43, 405,
         f"{len(grooves)} EQUAL GROOVES  |  {num(bounds[1, profile['along']] - bounds[0, profile['along']])} mm FULL LENGTH",
         9.5, BLUE, True)
    return 1


def add_supplement(out, src, row, feature, circles, plate, chamfer, plan, mesh,
                   step_available=False):
    if plan.get("multi_surface_bend"):
        p=out.new_page(width=1190,height=1100)
        draw_multi_surface_bend(p,src,feature,plan)
        if step_available:
            text(p,43,p.rect.height-20,
                 f"STEP PROVIDED: provided_part_files/{feature['family']}.step",
                 9,RED,True)
        return
    rotational = (not plan["profile"] and
                  coaxial_z(circles,feature["dimensions"]))
    lid_certificate = (plan["certificate"] is not None and
                       plan["certificate"]["method"] ==
                       "lid-prism-volume-and-rings")
    tapered_side=tapered_side_selection(plan)
    rotational_axial=plan["rotational_axial"]
    profile_side_bores=bool(plan["profile"] and
                            top_projected_side_bores(plan,feature["dimensions"]))
    if plan.get("stepped_side"):
        page_height = 925
    elif plate:
        page_height = 890
    elif rotational_axial:
        page_height = 885
    elif tapered_side:
        page_height = 885
    elif chamfer or lid_certificate:
        page_height = 770
    elif plan["profile"] or rotational:
        page_height = 765 if profile_side_bores else 620
    else:
        page_height = 620
    p = out.new_page(width=841.89, height=page_height)
    p.draw_rect(fitz.Rect(0, 0, p.rect.width, 72), fill=BLUE, color=None)
    text(p, 42, 40, f"{feature['family'].upper()}  |  SCRIPTED DIMENSION SUPPLEMENT",
         17, WHITE, True)
    profile_subtitle = "Selected horizontal outline enlarged with geometric dimension chains."
    if plan["profile"] and abs(plan["profile"]["plane_z"] - feature["dimensions"][2]) < .06:
        profile_subtitle = "Enlarged TOP outline with dimensions measured from the model."
    subtitle = ("TOP outline and dimension chains redrawn from the model; the original seven views are on the preceding page."
                if plate else
                "TOP view with STEP-measured diameters and corner dimensions."
                if chamfer else
                profile_subtitle
                if plan["profile"] else
                ("TOP bore and key slot measured from the model."
                 if plan.get("keyed_bore") else
                 "Concentric STEP diameters shown directly in the top projection.")
                if rotational else
                ("TOP orthographic preview; exact unresolved geometry is in the supplied part STEP."
                 if step_available else "TOP orthographic projection with measured feature dimensions."))
    text(p, 42, 87, subtitle, 9.2, MUTED)
    dest_rect = fitz.Rect(40, 103, 802, 640 if profile_side_bores else 548)
    if plate:
        plate_top_dimension_view(p, feature, dest_rect, plate)
    elif plan["profile"] and not chamfer:
        draw_profile_dimensions(p, src, feature, dest_rect, plan, step_available)
    elif rotational:
        draw_rotational_dimensions(p,src,feature,dest_rect,circles,
                                   bool(rotational_axial),step_available,
                                   plan.get("keyed_bore"))
    else:
        source_rect = original_top_rect(src)
        p.show_pdf_page(dest_rect, src.parent, src.number, clip=source_rect,
                        keep_proportion=False)
        p.draw_rect(dest_rect, color=LINE, width=.8)
        if chamfer:
            draw_chamfer_panel_dimensions(p, feature, source_rect, dest_rect, chamfer)
    d = feature["dimensions"]
    info_y=671 if profile_side_bores else 579
    text(p, 43, info_y, "OVERALL XYZ: " + " x ".join(map(num, d)) + " mm", 10, bold=True)
    text(p, 500, info_y, f"SOURCE PAGE {row['pdf_page']}  |  {row['tier']}", 9, MUTED)
    if plate:
        draw_plate_bevel_views(p, plate)
    elif plan.get("stepped_side"):
        draw_stepped_side_dimensions(p,plan["stepped_side"])
    elif rotational_axial:
        draw_rotational_axial_dimensions(p,feature,plan,mesh)
    elif tapered_side:
        draw_tapered_side_dimensions(p,plan,circles,tapered_side)
    elif lid_certificate:
        draw_lid_wall_dimensions(p,plan["certificate"])
    if step_available:
        text(p, 43, p.rect.height-18,
             f"STEP PROVIDED: provided_part_files/{feature['family']}.step",
             9, RED, True)


def build(args):
    candidates = [r for r in json.loads(args.candidates.read_text()) if r["case"] == args.case]
    features = {f["family"]: f for f in json.loads(args.features.read_text())}
    src = fitz.open(args.input)
    # Recreate the seven source views from the same aligned meshes and Blender
    # renders, with neither the detail panels nor any A/B locator overlays.
    clean_buffer = io.BytesIO()
    rl_config.useA85 = 0
    pdf = canvas.Canvas(clean_buffer, pagesize=landscape(A4))
    page_width, page_height = landscape(A4)
    for row in candidates:
        feature = features[row["family"]]
        mesh = trimesh.load_mesh(args.meshes / (row["family"] + ".stl"), process=False)
        # Tessellated STEP faces can put a few STL vertices just outside the
        # analytic B-rep envelope. Keep the measured STEP dimensions as the
        # authority while rejecting a materially different aligned mesh.
        envelope_tolerance = mesh_envelope_tolerance(feature["dimensions"])
        if np.max(np.abs(mesh.extents - feature["dimensions"])) > envelope_tolerance:
            raise ValueError(f"Aligned STL differs from STEP/PDF envelope: {row['family']}")
        family = {"id": row["family"], "source_family_ids": [row["family"]],
                  "occurrence_ids": [row["family"]], "quantity": row["quantity"]}
        draw_complex_family_page(pdf, family, mesh.triangles,
                                 {row["family"]: np.asarray(feature["dimensions"])},
                                 args.renders, page_width, page_height)
        pdf.setStrokeColorRGB(.84, .85, .88)
        pdf.line(28, 25, page_width-28, 25)
        pdf.setFont("Helvetica", 6.5)
        pdf.setFillColorRGB(.40, .43, .50)
        pdf.drawString(28, 13, f"{args.case.upper()} | PARTS | UNITS: mm")
        pdf.drawRightString(page_width-28, 13,
                            f"SOURCE PAGE {row['pdf_page']} / {len(src)}")
        pdf.showPage()
    pdf.save()
    clean = fitz.open(stream=clean_buffer.getvalue(), filetype="pdf")
    out = fitz.open()
    audit = []
    for clean_index, row in enumerate(candidates):
        feature = features[row["family"]]
        page_number = row["pdf_page"] - 1
        # Standard pages can contain two families and begin with a shared title.
        # Match the exact family label on the indicated page instead of assuming
        # it is the first extracted text line.
        if (feature["page"] != page_number
                or row["family"].upper() not in src[page_number].get_text().splitlines()):
            raise ValueError(f"Family/source page mismatch: {row['family']}")
        circles = group_circles(feature["circles"])
        mesh = trimesh.load_mesh(args.meshes / (row["family"] + ".stl"), process=False)
        chamfer = symmetric_chamfer_panel(mesh, feature["dimensions"], circles)
        plan = analyze_family(feature,
                              args.meshes / (row["family"] + ".stl"),
                              args.features,args.geometry)
        plan["horizontal_face_levels"] = horizontal_face_levels(
            mesh, feature["dimensions"])
        plan["repeated_axial_profile"] = repeated_axial_profile(
            mesh, feature["dimensions"])
        plan["upper_end_chamfers"] = upper_end_chamfers(
            mesh, feature["dimensions"])
        plan["two_stage_coaxial_profile"] = two_stage_coaxial_plan(feature)
        plan["uninformative_profile_section"] = uninformative_profile_section(
            feature, plan)
        profile=plan["profile"]
        plan["profile_is_top"]=bool(
            profile and abs(float(profile["plane_z"])-feature["dimensions"][2])<.06)
        if profile and not plan["profile_is_top"] and not plan["projected_stations"]:
            # Recover only independently verified, projected structural
            # stations.  Internal face stations can lack a visible TOP edge.
            plan["projected_stations"]=projected_planar_stations(
                mesh,feature["dimensions"])
        plan["shaft_flats"] = milled_shaft_flats(
            mesh, grouped_circles(feature["circles"]), feature["dimensions"])
        vertical_bores = plan["vertical_axial_bores"]
        preferred_normal = choose_axial_projection_normal(
            feature["dimensions"], vertical_bores)
        plan["preferred_axial_projection_normal"] = preferred_normal
        plan["direct_axial_projection_normal"] = (preferred_normal if (
            vertical_bores and
            {item["side"] for item in vertical_bores} == {"top", "bottom"} and
            not any(abs(circle["axis"][preferred_normal]) > .985
                    and circle["diameter"] >= 3
                    for circle in circles) and
            not plan["two_stage_coaxial_profile"] and
            not plan["orthogonal_bores"] and
            not plan["stepped_side"]) else None)
        if coaxial_z(circles,feature["dimensions"]):
            plan["keyed_bore"] = detect_keyed_bore(mesh,circles,feature["dimensions"])
        try:
            plate = extract_geometry(args.meshes / (row["family"] + ".stl"),
                                     args.features, row["family"])
        except ValueError:
            plate = None
        step_available = bool(args.step_files_dir and
                              (args.step_files_dir/(row["family"]+".step")).is_file())
        if plan.get("step_first") and step_available:
            add_step_first_page(out,row,args.renders)
            audit.append({"family":row["family"],"tier":row["tier"],
                          "original_page":row["pdf_page"],
                          "presentation":"step_first",
                          "geometry_status":plan["status"],
                          "supplement_pages":0,
                          "additional_projection_pages":0,
                          "step_available":True,
                          "unresolved":plan["unresolved"]})
            continue
        groove_profile = full_length_groove_profile(mesh)
        if groove_profile is not None:
            append_three_blender_base(out, clean, clean_index)
            add_full_length_groove_page(out, clean[clean_index], feature,
                                        groove_profile)
            audit.append({"family": row["family"], "tier": row["tier"],
                          "original_page": row["pdf_page"],
                          "supplement_pages": 1,
                          "presentation": "dimensioned_groove_section",
                          "groove_count": len(groove_profile["grooves"]),
                          "additional_projection_pages": 0,
                          "step_available": step_available})
            continue
        append_three_blender_base(out, clean, clean_index)
        section = analyze_extruded_section(mesh, feature["dimensions"], plan["profile"])
        axial_bores=(verified_section_axial_bores(mesh,feature,section)
                     if section is not None else None)
        if section is not None and axial_bores is not None:
            add_extruded_section_page(out, feature, section, axial_bores)
            audit.append({"family": row["family"], "tier": row["tier"],
                          "original_page": row["pdf_page"],
                          "supplement_pages": 1,
                          "presentation": "verified_constant_section",
                          "section_axis": section["axis"],
                          "arc_count": section["arc_count"],
                          "axial_bore_count": len(axial_bores),
                          "end_recess_count": sum(len(item["centers"])
                                                  for item in section["end_recesses"]),
                          "step_available": step_available})
            continue
        if row.get("coaxial_stepped"):
            stations = add_coaxial_stepped_page(out, row, feature, mesh)
            if stations:
                audit.append({"family": row["family"], "tier": row["tier"],
                              "original_page": row["pdf_page"],
                              "supplement_pages": 1,
                              "axial_stations": stations,
                              "step_available": step_available})
                continue
        top_secondary=any(abs(circle["axis"][2])>.995
                          for circle in plan["circular_coverage"]["secondary_features"])
        # A planar-face supplement and an additional TOP projection would
        # repeat the same view.  For simple planar profiles, put the outline,
        # primary holes, and uncovered bores on one enlarged TOP page.
        preview_only=not (plate or chamfer or plan["profile"] or
                          coaxial_z(circles,feature["dimensions"]) or
                          plan["multi_surface_bend"] or plan["stepped_side"] or
                          plan["rotational_axial"] or plan["tapered_bores"] or
                          plan["certificate"])
        section_profile_only=bool(
            profile and not plan["profile_is_top"] and not (
                plate or chamfer or plan["multi_surface_bend"] or
                plan["stepped_side"] or plan["rotational_axial"] or
                plan["tapered_bores"] or plan["certificate"]))
        orthographic_top_for_profile=bool(
            section_profile_only and
            (plan["projected_stations"] or any(
                abs(circle["axis"][2])>.995
                for circle in plan["circular_coverage"]["all_features"])))
        plan["orthographic_top_for_profile"]=orthographic_top_for_profile
        unified_top=bool(orthographic_top_for_profile or
                         plan.get("projected_stations") or
                         (preview_only and top_secondary) or
                         (top_secondary and plan["tapered_bores"] and not (
            plate or chamfer or plan["multi_surface_bend"] or
            plan["stepped_side"] or plan["rotational_axial"] or
            plan["certificate"])) or
                         (plan["profile"] and top_secondary and not (
            plate or chamfer or plan["multi_surface_bend"] or
            plan["stepped_side"] or plan["rotational_axial"] or
            plan["tapered_bores"] or plan["certificate"])))
        # The base page already contains oblique model views. A sparse interior
        # slice without measurable openings must not become a model-only detail
        # page; use the dimensioned orthographic projections that follow.
        supplement_present=not section_profile_only and (
                            not unified_top and not preview_only
                            and not plan["uninformative_profile_section"])
        if supplement_present:
            add_supplement(out, clean[clean_index], row, feature, circles,
                           plate, chamfer, plan, mesh,
                           step_available)
        available_axial_normals = [normal for normal, _ in
            secondary_projection_batches(plan, unified_top)[0]
            if normal in (0, 1)]
        plan["inline_axial_projection_normal"] = (
            choose_axial_projection_normal(
                feature["dimensions"], vertical_bores,
                available_axial_normals)
            if (plan["direct_axial_projection_normal"] is None and
                vertical_bores and
                not plan.get("orthogonal_bores") and
                available_axial_normals) else None)
        extra_pages=(add_direct_axial_projection_page(
            out,feature,vertical_bores,mesh,
            plan["direct_axial_projection_normal"])
            if plan["direct_axial_projection_normal"] is not None else 0)
        if plan["direct_axial_projection_normal"] is not None and not extra_pages:
            plan["direct_axial_projection_normal"] = None
        secondary_pages,plotted,oblique,inline_drawn=add_secondary_projection_pages(
            out,clean[clean_index],feature,plan,step_available,unified_top,
            plan["inline_axial_projection_normal"])
        if not inline_drawn:
            plan["inline_axial_projection_normal"] = None
        extra_pages+=secondary_pages
        extra_pages+=add_orthogonal_bore_side_page(
            out,clean[clean_index],feature,plan,step_available)
        extra_pages+=add_shaft_flat_pages(
            out,clean[clean_index],feature,plan["shaft_flats"])
        if (plan["direct_axial_projection_normal"] is None and
                plan["inline_axial_projection_normal"] is None):
            extra_pages+=add_vertical_axial_bore_page(
                out,clean[clean_index],feature,vertical_bores,
                preferred_normal)
        axial_groove=(verified_grooved_axial_profile(feature,mesh,args.geometry)
                      if coaxial_z(circles,feature["dimensions"]) else None)
        if axial_groove is not None:
            add_grooved_axial_front_page(out,feature,axial_groove)
            extra_pages+=1
        # A face-on concentric-circle page defines the diameters, but cannot
        # define the axial lengths of the shoulders and recesses.  Use real
        # STEP rim planes for one longitudinal page when no other axial view
        # already supplies that chain.  This applies to any coaxial family,
        # independent of its filename or candidate classification.
        axial_stations=[]
        if (axial_groove is None and common_cylinder_axis(circles) is not None
                and not plan["rotational_axial"] and not plan["coaxial_steps"]
                and not plan["longitudinal_shaft_stations"]
                and not plan["two_stage_coaxial_profile"]):
            axial_stations=add_coaxial_stepped_page(
                out,row,feature,mesh,min_axial_interval=.35,
                min_paper_interval=8.)
            extra_pages+=bool(axial_stations)
        audit.append({"family": row["family"], "tier": row["tier"],
                      "original_page": row["pdf_page"], "cylindrical_groups": len(circles),
                      "supplement_pages": int(supplement_present),
                      "recognized_plate_geometry": bool(plate),
                      "recognized_chamfer_panel": bool(chamfer),
                      "uninformative_profile_section": plan["uninformative_profile_section"],
                      "geometry_status": plan["status"],
                      "additional_projection_pages": extra_pages,
                      "horizontal_face_levels": plan["horizontal_face_levels"],
                      "repeated_axial_profile": plan["repeated_axial_profile"],
                      "upper_end_chamfers": plan["upper_end_chamfers"],
                      "two_stage_coaxial_profile": plan["two_stage_coaxial_profile"],
                      "shaft_flats": plan["shaft_flats"],
                      "vertical_axial_bores": plan["vertical_axial_bores"],
                      "direct_axial_projection_normal": plan["direct_axial_projection_normal"],
                      "inline_axial_projection_normal": plan["inline_axial_projection_normal"],
                      "verified_axial_front": axial_groove is not None,
                      "axial_rim_stations": axial_stations,
                      "additional_cylindrical_groups": plotted,
                      "oblique_cylindrical_groups_in_step": oblique,
                      "step_available": step_available,
                      "certificate": plan["certificate"],
                      "unresolved": plan["unresolved"]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.set_metadata({"title": f"{args.case} medium parts detailed dimension trial",
                      "subject": "Scripted seven-view and STEP-derived dimensional supplement"})
    page_count=len(out)
    out.save(args.output, garbage=4, deflate=True)
    out.close()
    clean.close()
    src.close()
    args.output.with_suffix(".audit.json").write_text(json.dumps(audit, indent=2))
    print(args.output)
    print(f"{len(audit)} families, {page_count} pages")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--geometry", type=Path)
    parser.add_argument("--meshes", type=Path, required=True)
    parser.add_argument("--renders", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--step-files-dir", type=Path)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
