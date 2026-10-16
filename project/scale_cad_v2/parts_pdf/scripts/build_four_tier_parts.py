#!/usr/bin/env python3
"""Assemble a four-tier parts book from existing case-matched drawing assets.

The simple/light split uses the depth-view gate; medium and STEP sets come
from the candidate and difficult-part selections. Basic/light pages use four
Blender views and medium/STEP pages use three. No A/B detail detection, zoom
panel, or STEP re-export takes place here.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import io
import hashlib
import json
from pathlib import Path
import re
import shutil

import fitz
import numpy as np
import trimesh

from annotate_parts_pdf import annotate
from endface_socket import recessed_hex_sockets
from general_dimension_engine import grouped_circles
from generate_parts_pdf_six_views import (
    STEP_VIEWS, grid_boxes, step_view_boxes, tight_view_image,
)
from mesh_feature_recovery import top_circular_rims
from dimension_density import scan_medium_dimension_density


LABELS = {
    "simple": "BASIC THREE VIEWS + 4 MODEL VIEWS",
    "light": "DIMENSIONED THREE VIEWS + 4 MODEL VIEWS",
    "medium": "DETAILED DIMENSIONS",
    "step": "STEP PROVIDED",
    "pdf": "VERIFIED SOURCE PDF DRAWING",
}
COLORS = {
    "simple": (0.37, 0.47, 0.56),
    "light": (0.15, 0.45, 0.67),
    "medium": (0.08, 0.43, 0.44),
    "step": (0.66, 0.22, 0.16),
    "pdf": (0.38, 0.28, 0.62),
}


def source_pages(document):
    pages = {}
    for page in document:
        first = page.get_text().splitlines()[:1]
        if first and first[0].startswith("FAMILY_"):
            family = first[0].split()[0].lower()
            if family in pages:
                raise ValueError(f"Repeated base family page: {family}")
            pages[family] = page.number
    return pages


def medium_pages(document):
    pages = defaultdict(list)
    for page in document:
        # PDF content streams need not return the visual header first after a
        # composed image or imported projection is added.  Identify the one
        # header anywhere on the page, independent of extraction order.
        headings = {match.group(0).lower()
                    for line in page.get_text().splitlines()
                    if (match := re.match(r"^FAMILY_\d+\b", line))}
        if len(headings) != 1:
            raise ValueError(f"Unidentified medium page: {page.number + 1}")
        family = headings.pop()
        pages[family].append(page.number)
    return pages


def tier(feature, medium, difficult, depth_flags, source_pdf=frozenset()):
    family = feature["family"]
    if family in source_pdf:
        return "pdf"
    if family in difficult:
        return "step"
    if family in medium:
        return "medium"
    # Preserve the original convex-hull/surface/section gate.  A shape that
    # looks like a simple ring can still need the Blender view and dimensions.
    return "light" if depth_flags[family] else "simple"


def panel_rect(page, index):
    x, y, width, height = grid_boxes(page.rect.width, page.rect.height)[index]
    return fitz.Rect(x, page.rect.height - y - height,
                     x + width, page.rect.height - y)


def header(page, family, quantity, kind, show_kind=True):
    page.draw_rect(fitz.Rect(0, 0, page.rect.width, 67),
                   fill=(.13, .30, .45), color=None)
    page.insert_text((30, 39), family.upper(), fontsize=20,
                     fontname="hebo", color=(1, 1, 1))
    page.insert_text((page.rect.width - 160, 39), f"QTY {quantity}",
                     fontsize=11, fontname="hebo", color=(1, 1, 1))
    if show_kind:
        page.insert_text((30, 89), LABELS[kind], fontsize=10,
                         fontname="hebo", color=COLORS[kind])


def draw_panel(target, source, source_page, source_index, dest):
    clip = panel_rect(source[source_page], source_index)
    target.show_pdf_page(dest, source, source_page,
                         clip=clip, keep_proportion=True)
    target.draw_rect(dest, color=(.81, .84, .88), width=.7)


def draw_simple(out, base, page_number, family, quantity):
    page = out.new_page(width=1190, height=730)
    header(page, family, quantity, "simple")
    width = (page.rect.width - 84) / 3
    for index in range(3):
        draw_panel(page, base, page_number, index,
                   fitz.Rect(24 + index * (width + 18), 110,
                             24 + index * (width + 18) + width, 390))
    blender_width = (page.rect.width - 90) / 4
    for index in range(4):
        x = 24 + index * (blender_width + 14)
        draw_panel(page, base, page_number, index + 3,
                   fitz.Rect(x, 428, x + blender_width, 700))


def add_light_pitch(page, source, page_number, dests, feature):
    """Dimension repeated, aligned hole centers in the enlarged line views."""
    dims = feature["dimensions"]
    circles = grouped_circles(feature["circles"])
    source_text = source[page_number].get_text()
    source_page = source[page_number]
    width, height = source_page.rect.width, source_page.rect.height
    panel_width, panel_height = (width - 72) / 3, (height - 146) / 3
    for index, (u, v, normal) in enumerate(((0, 2, 1), (1, 2, 0), (0, 1, 2))):
        visible = [item for item in circles if abs(item["axis"][normal]) >= .985]
        groups = defaultdict(list)
        for item in visible:
            groups[round(item["diameter"], 2)].append(item)
        choices = []
        for group in groups.values():
            for first in group:
                aligned = [item for item in group
                           if abs(item["center"][v] - first["center"][v])
                           <= max(.2, .01 * dims[v])]
                stations = sorted({round(float(item["center"][u]), 2)
                                   for item in aligned})
                if (len(stations) >= 2 and len(stations) <= 6 and
                        stations[-1] - stations[0] >= max(5., .15 * dims[u])):
                    choices.append((len(stations), stations[-1] - stations[0],
                                    stations, aligned))
        if not choices:
            continue
        _, _, stations, aligned = max(choices, key=lambda item: item[:2])
        intervals = [round(b - a, 2) for a, b in zip(stations, stations[1:])]
        if len(intervals) == 1 and re.search(
                rf"(?<![\d.]){intervals[0]:.2f}(?!\d)", source_text):
            continue
        dest = dests[index]
        clip = panel_rect(source_page, index)
        factor = min(dest.width / clip.width, dest.height / clip.height)
        margin_x = dest.x0 + (dest.width - clip.width * factor) / 2
        margin_y = dest.y0 + (dest.height - clip.height * factor) / 2
        box_x = 28 + index * (panel_width + 8)
        source_scale = min((panel_width - 45) / dims[u],
                           (panel_height - 55) / dims[v])
        source_ox = box_x + 31 + (panel_width - 45 - dims[u] * source_scale) / 2
        source_oy = 92 + panel_height - 31 - (panel_height - 55 - dims[v] * source_scale) / 2
        def project(value_u, value_v):
            px = source_ox + value_u * source_scale
            py = source_oy - value_v * source_scale
            return (margin_x + (px - clip.x0) * factor,
                    margin_y + (py - clip.y0) * factor)
        xs = [project(value, aligned[0]["center"][v])[0] for value in stations]
        if any(xs[j + 1] - xs[j] < 16 for j in range(len(xs) - 1)):
            continue
        rail_y = dest.y0 + 79
        red = (.78, .13, .18)
        page.draw_line(fitz.Point(xs[0], rail_y), fitz.Point(xs[-1], rail_y),
                       color=red, width=.8)
        for value, x in zip(stations, xs):
            page.draw_line(fitz.Point(x, rail_y - 4), fitz.Point(x, rail_y + 4),
                           color=red, width=.8)
            center = next(item for item in aligned
                          if abs(item["center"][u] - value) < .1)
            cx, cy = project(center["center"][u], center["center"][v])
            page.draw_line(fitz.Point(cx, cy - 3), fitz.Point(x, rail_y + 4),
                           color=red, width=.45)
            page.draw_circle(fitz.Point(cx, cy), 2, color=red, fill=red)
        if len(set(intervals)) == 1 and len(intervals) > 1:
            label = f"{len(intervals)}x {intervals[0]:.2f}"
            text_width = fitz.get_text_length(label, fontname="hebo", fontsize=10)
            page.insert_text(((xs[0] + xs[-1] - text_width) / 2, rail_y - 8),
                             label, fontsize=10, fontname="hebo", color=red)
        else:
            for left, right, value in zip(xs, xs[1:], intervals):
                label = f"{value:.2f}"
                text_width = fitz.get_text_length(label, fontname="hebo", fontsize=9)
                if right - left >= text_width + 8:
                    page.insert_text(((left + right - text_width) / 2, rail_y - 8),
                                     label, fontsize=9, fontname="hebo", color=red)
        # Repeated, symmetric end offsets locate the entire pattern without
        # another long center-to-center dimension below the part.
        left_offset, right_offset = stations[0], dims[u] - stations[-1]
        edge_left = project(0, aligned[0]["center"][v])[0]
        edge_right = project(dims[u], aligned[0]["center"][v])[0]
        if (len(stations) >= 3 and left_offset >= 3 and
                left_offset <= .12 * dims[u] and
                abs(left_offset - right_offset) <= .1 and
                xs[0] - edge_left >= 12 and edge_right - xs[-1] >= 12):
            end_y = dest.y0 + 207
            for left, right in ((edge_left, xs[0]), (xs[-1], edge_right)):
                page.draw_line((left, end_y), (right, end_y), color=red, width=.8)
                for x in (left, right):
                    page.draw_line((x, end_y - 4), (x, end_y + 4),
                                   color=red, width=.8)
            page.insert_text((xs[0] + 7, end_y - 6),
                             f"2x {left_offset:.2f}",
                             fontsize=9, fontname="hebo", color=red)


def add_light_axial_diameters(page, source, page_number, dests, feature,
                              mesh_path):
    """Show an unlabelled outer shaft diameter on its visible side profile.

    An end view can hide a short neck behind a larger concentric rim.  A mesh
    section at the middle of each analytic cylindrical span proves that its
    radius is the outside envelope before a FRONT/TOP leader is added.
    """
    measured={round(float(value),2) for value in
              re.findall(r"Ø(\d+(?:\.\d+)?)",source[page_number].get_text())}
    groups=grouped_circles(feature["circles"])
    candidates=[]
    dims=feature["dimensions"]
    mesh=None
    for group in groups:
        diameter=float(group["diameter"])
        if diameter < 3:
            continue
        if round(diameter,2) in measured:
            continue
        axis=int(np.argmax(np.abs(group["axis"])))
        if axis not in (0,1) or abs(group["axis"][axis])<.985:
            continue
        levels=group["axial_levels"]
        if len(levels)<2:
            continue
        if mesh is None:
            mesh=trimesh.load_mesh(mesh_path,process=False)
        center=np.asarray(group["center"],float)
        for lo,hi in zip(levels,levels[1:]):
            if hi-lo<max(.08,.005*dims[axis]):
                continue
            station=(lo+hi)/2
            origin=center.copy()
            origin[axis]=station
            normal=np.eye(3)[axis]
            section=mesh.section(plane_origin=origin,plane_normal=normal)
            if section is None or not len(section.vertices):
                continue
            transverse=[k for k in range(3) if k!=axis]
            radial=np.linalg.norm(section.vertices[:,transverse]-
                                  center[transverse],axis=1)
            if abs(float(np.max(radial))-diameter/2)>max(.06,.02*diameter):
                continue
            candidates.append((diameter,axis,station,center))
            break
    if not candidates:
        return
    width,height=source[page_number].rect.width,source[page_number].rect.height
    panel_width,panel_height=(width-72)/3,(height-146)/3
    snapshot=page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
    raster=np.frombuffer(snapshot.samples,dtype=np.uint8).reshape(
        snapshot.height,snapshot.width,3)
    red=(.78,.13,.18)
    occupied=[]
    for diameter,axis,station,center in candidates:
        views=((0,(0,2)),(2,(0,1))) if axis==0 else ((1,(1,2)),)
        placed=False
        for index,axes in views:
            dest=dests[index]
            clip=panel_rect(source[page_number],index)
            factor=min(dest.width/clip.width,dest.height/clip.height)
            margin_x=dest.x0+(dest.width-clip.width*factor)/2
            margin_y=dest.y0+(dest.height-clip.height*factor)/2
            box_x=28+index*(panel_width+8)
            scale=min((panel_width-45)/dims[axes[0]],
                      (panel_height-55)/dims[axes[1]])
            ox=box_x+31+(panel_width-45-dims[axes[0]]*scale)/2
            oy=92+panel_height-31-(panel_height-55-dims[axes[1]]*scale)/2
            def project(u,v):
                sx=ox+u*scale
                sy=oy-v*scale
                return (margin_x+(sx-clip.x0)*factor,
                        margin_y+(sy-clip.y0)*factor)
            anchor=project(station,center[axes[1]]+diameter/2)
            label=f"Ø{diameter:.2f}"
            tw=fitz.get_text_length(label,fontname="hebo",fontsize=9)
            locations=[]
            for dx in (12,-tw-12,20,-tw-20):
                for dy in (-12,-20,22):
                    lx=anchor[0]+dx
                    by=anchor[1]+dy
                    box=(lx-3,by-10,lx+tw+3,by+4)
                    if (box[0]<dest.x0+5 or box[2]>dest.x1-5 or
                            box[1]<dest.y0+32 or box[3]>dest.y1-8 or
                            any(not(box[2]<a or box[0]>c or box[3]<b or box[1]>d)
                                for a,b,c,d in occupied)):
                        continue
                    tile=raster[max(0,int(box[1]*2)):min(snapshot.height,int(box[3]*2)+1),
                                max(0,int(box[0]*2)):min(snapshot.width,int(box[2]*2)+1)]
                    if tile.size and np.mean(np.min(tile,axis=2)<190)<.006:
                        locations.append((abs(dx)+abs(dy),lx,by,box))
            if not locations:
                continue
            _,lx,by,box=min(locations)
            end=(lx if lx>anchor[0] else lx+tw,by-3)
            page.draw_line(anchor,end,color=red,width=.7)
            page.draw_circle(anchor,1.7,color=red,fill=red)
            page.insert_text((lx,by),label,fontsize=9,fontname="hebo",color=red)
            occupied.append(box)
            measured.add(round(diameter,2))
            placed=True
            break
        if placed:
            continue


def add_light_endface_sockets(page, source, page_number, dests, feature,
                              mesh_path):
    """Place a measured hexagon across-flats dimension in its end view."""
    mesh = trimesh.load_mesh(mesh_path, process=False)
    sockets = recessed_hex_sockets(
        mesh, grouped_circles(feature["circles"]), feature["dimensions"])
    if not sockets:
        return
    dims = feature["dimensions"]
    source_page = source[page_number]
    panel_width = (source_page.rect.width - 72) / 3
    panel_height = (source_page.rect.height - 146) / 3
    red = (.78, .13, .18)
    for socket in sockets:
        axis = socket["axis"]
        index, axes = {0: (1, (1, 2)), 1: (0, (0, 2)),
                       2: (2, (0, 1))}[axis]
        dest = dests[index]
        clip = panel_rect(source_page, index)
        factor = min(dest.width / clip.width, dest.height / clip.height)
        margin_x = dest.x0 + (dest.width - clip.width * factor) / 2
        margin_y = dest.y0 + (dest.height - clip.height * factor) / 2
        box_x = 28 + index * (panel_width + 8)
        scale = min((panel_width - 45) / dims[axes[0]],
                    (panel_height - 55) / dims[axes[1]])
        ox = box_x + 31 + (panel_width - 45 - dims[axes[0]] * scale) / 2
        oy = (92 + panel_height - 31 -
              (panel_height - 55 - dims[axes[1]] * scale) / 2)

        def project(u, v):
            px, py = ox + u * scale, oy - v * scale
            return np.array((margin_x + (px - clip.x0) * factor,
                             margin_y + (py - clip.y0) * factor))

        center = np.asarray(socket["center"], dtype=float)
        normal = np.asarray(socket["normal"], dtype=float)
        half = socket["across_flats"] / 2
        first, last = (project(*(center + sign * half * normal))
                       for sign in (-1, 1))
        direction = last - first
        perpendicular = np.array((-direction[1], direction[0]))
        perpendicular /= np.linalg.norm(perpendicular)
        if perpendicular[0] < 0:
            perpendicular = -perpendicular
        center_pdf = project(*center)
        radius_pdf = abs(project(center[0] + socket["outer_diameter"] / 2,
                                 center[1])[0] - center_pdf[0])
        offset = min(radius_pdf + 15, dest.x1 - max(first[0], last[0]) - 20)
        if offset < 15:
            continue
        shift = perpendicular * offset
        marks = [first + shift, last + shift]
        page.draw_line(marks[0], marks[1], color=red, width=.85)
        for source_point, mark in ((first, marks[0]), (last, marks[1])):
            page.draw_line(source_point, mark + 5 * perpendicular,
                           color=red, width=.55)
            tangent = direction / np.linalg.norm(direction)
            page.draw_line(mark - 5 * tangent, mark + 5 * tangent,
                           color=red, width=.85)
        label = f"HEX A/F {socket['across_flats']:.2f}"
        label_width = fitz.get_text_length(label, fontname="hebo", fontsize=9)
        label_x = min(dest.x1 - label_width - 5,
                      max(dest.x0 + 5, (marks[0][0] + marks[1][0] - label_width) / 2))
        label_y = max(dest.y0 + 34, min(marks[0][1], marks[1][1]) - 10)
        page.insert_text((label_x, label_y), label,
                         fontsize=9, fontname="hebo", color=red)


def draw_light(out, annotated, base, page_number, family, quantity, feature,
               mesh_path):
    page = out.new_page(width=1190, height=730)
    header(page, family, quantity, "light")
    width = (page.rect.width - 84) / 3
    line_view_rects = []
    for index in range(3):
        dest = fitz.Rect(24 + index * (width + 18), 110,
                         24 + index * (width + 18) + width, 390)
        draw_panel(page, annotated, page_number, index, dest)
        line_view_rects.append(dest)
    add_light_pitch(page, annotated, page_number, line_view_rects, feature)
    add_light_axial_diameters(page, annotated, page_number, line_view_rects,
                              feature, mesh_path)
    add_light_endface_sockets(page, annotated, page_number, line_view_rects,
                              feature, mesh_path)
    blender_width = (page.rect.width - 90) / 4
    for index in range(4):
        x = 24 + index * (blender_width + 14)
        draw_panel(page, base, page_number, index + 3,
                   fitz.Rect(x, 428, x + blender_width, 700))


def draw_step(out, family, quantity, renders_dir, *, display_family=None):
    """Make a three-view page for a source family and optional legacy PDF label."""
    manifest = json.loads((renders_dir / family / "render_manifest.json").read_text())
    if manifest.get("view_version") != "upper-opposite-grid-linework-v3":
        raise ValueError(f"Outdated Blender render for {family}")
    views = manifest.get("views", {})
    image_paths = []
    for name, _ in STEP_VIEWS:
        image = views.get(name, {}).get("image")
        path = renders_dir / family / image if image else None
        if not path or not path.is_file():
            raise FileNotFoundError(f"Missing Blender {name} view for {family}")
        image_paths.append(path)
    page = out.new_page(width=842, height=595)
    header(page, display_family or family, quantity, "step", show_kind=False)
    page.insert_text((30, 89), "3 VIEWS PROVIDED",
                     fontsize=10, fontname="hebo", color=COLORS["step"])
    page.insert_text((30, 111), f"STEP PROVIDED: provided_part_files/{family}.step",
                     fontsize=10, fontname="hebo", color=COLORS["step"])
    for (name, title), image_path, (x, y, width, height) in zip(
            STEP_VIEWS, image_paths, step_view_boxes(page.rect.width, page.rect.height)):
        top = page.rect.height - y - height
        panel = fitz.Rect(x, top, x + width, top + height)
        page.draw_rect(panel, fill=(.965, .97, .975), color=(.81, .84, .88))
        page.insert_text((x + 8, top + 17), title,
                         fontsize=8, fontname="hebo", color=(.22, .29, .36))
        image_rect = fitz.Rect(x + 8, top + 25, x + width - 8,
                               top + height - 5)
        if name == "top":
            image_stream = io.BytesIO()
            cropped = tight_view_image(image_path)
            cropped.save(image_stream, format="PNG")
            # PyMuPDF can stretch square image streams to a wide destination
            # despite keep_proportion=True.  Fit the destination explicitly.
            fit = min(image_rect.width / cropped.width,
                      image_rect.height / cropped.height)
            fitted = fitz.Rect(
                image_rect.x0 + (image_rect.width - cropped.width * fit) / 2,
                image_rect.y0 + (image_rect.height - cropped.height * fit) / 2,
                image_rect.x0 + (image_rect.width + cropped.width * fit) / 2,
                image_rect.y0 + (image_rect.height + cropped.height * fit) / 2)
            page.insert_image(fitted, stream=image_stream.getvalue(),
                              keep_proportion=False)
        else:
            page.insert_image(image_rect, filename=str(image_path),
                              keep_proportion=True)


def cover_page_groups(rows):
    """Keep index rows clear of the fixed footer on every cover page."""
    first_row_y, row_pitch, footer_y, row_clearance = 161, 18, 570, 20
    capacity = (footer_y - row_clearance - first_row_y) // row_pitch + 1
    return [rows[start:start + capacity]
            for start in range(0, len(rows), capacity)] or [[]]


def draw_cover(out, rows, index_rows, page_ranges, case,
               cover_number=1, cover_count=1, title=None):
    page = out.new_page(width=842, height=595)
    page.draw_rect(fitz.Rect(0, 0, 842, 91), fill=(.13, .30, .45), color=None)
    page.insert_text((31, 43), title or f"{case.split('_')[-1].upper()}  |  CAD PARTS", fontsize=23,
                     fontname="hebo", color=(1, 1, 1))
    index_note = f"  |  index {cover_number}/{cover_count}" if cover_count > 1 else ""
    page.insert_text((31, 67),
                     f"Four-tier drawing set  |  units: mm{index_note}", fontsize=10,
                     color=(1, 1, 1))
    counts = Counter(row["tier"] for row in rows)
    summary = (f"{counts['simple']} basic   |   {counts['light']} light   |   "
               f"{counts['medium']} detailed   |   {counts['step']} STEP   |   "
               f"{counts['pdf']} source PDF")
    page.insert_text((31, 116), summary, fontsize=10, fontname="hebo")
    page.insert_text((31, 143), "FAMILY", fontsize=8, fontname="hebo")
    page.insert_text((168, 143), "QTY", fontsize=8, fontname="hebo")
    page.insert_text((237, 143), "DRAWING LEVEL", fontsize=8, fontname="hebo")
    page.insert_text((715, 143), "PAGES", fontsize=8, fontname="hebo")
    for index, row in enumerate(index_rows):
        y = 161 + index * 18
        if index % 2 == 0:
            page.draw_rect(fitz.Rect(27, y - 13, 811, y + 4),
                           fill=(.96, .97, .98), color=None)
        family, kind = row["family"], row["tier"]
        first, last = page_ranges[family]
        page.insert_text((31, y), family.upper(), fontsize=8, fontname="hebo")
        page.insert_text((168, y), str(row["quantity"]), fontsize=8)
        page.insert_text((237, y), LABELS[kind], fontsize=8,
                         color=COLORS[kind])
        page.insert_text((715, y), str(first) if first == last else f"{first}-{last}",
                         fontsize=8)
    page.insert_text((31, 570),
                     "Verified source PDF drawings are grouped after the generated drawings.",
                     fontsize=8, color=(.37, .42, .48))


def source_pdf_pages(out, family, quantity, records):
    """Embed verified original pages at native scale with an indexed family header."""
    for record in records:
        path = Path(record["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"Source PDF changed after validation: {path}")
        with fitz.open(path) as source:
            if len(source) != record["pages"]:
                raise ValueError(f"Source PDF page count changed: {path}")
            for index, original in enumerate(source):
                width, height = original.rect.width, original.rect.height
                page = out.new_page(width=width + 40, height=height + 90)
                page.draw_rect(fitz.Rect(0, 0, page.rect.width, 60),
                               fill=(.13, .30, .45), color=None)
                page.insert_text((20, 38), family.upper(), fontsize=18,
                                 fontname="hebo", color=(1, 1, 1))
                page.insert_text((page.rect.width - 115, 38), f"QTY {quantity}",
                                 fontsize=10, fontname="hebo", color=(1, 1, 1))
                page.insert_text((20, 76), "VERIFIED SOURCE PDF DRAWING",
                                 fontsize=9, fontname="hebo", color=COLORS["pdf"])
                page.show_pdf_page(fitz.Rect(20, 85, 20 + width, 85 + height),
                                   source, index)


def build(args):
    features = json.loads(args.features.read_text())
    feature_by_family = {item["family"]: item for item in features}
    candidate_rows = [row for row in json.loads(args.candidates.read_text())
                      if row["case"] == args.case]
    medium = {row["family"] for row in candidate_rows}
    if args.medium is not None:
        dense = sorted(family for family, result in
                       scan_medium_dimension_density(args.medium).items()
                       if result['step_required_for_density'])
        if dense:
            raise ValueError(f"Dense submillimetre dimensions require STEP: {dense}")
        audit_path = args.medium.with_suffix(".audit.json")
        if audit_path.is_file():
            audit = json.loads(audit_path.read_text())
            audit_families = [row["family"] for row in audit]
            if (len(audit_families) != len(set(audit_families))
                    or set(audit_families) != medium):
                raise ValueError("Medium detail audit does not match candidate families")
            unannotated = sorted(
                row["family"] for row in audit
                if (row.get("geometry_status") == "step_required"
                    and not row.get("step_available")
                    and row.get("supplement_pages", 0) == 0
                    and row.get("additional_projection_pages", 0) == 0))
            if unannotated:
                raise ValueError(
                    f"Medium families have no local dimension page; "
                    f"promote to STEP before assembly: {unannotated}")
    difficult = {path.stem for path in args.step_files_dir.glob("family_*.step")}
    families = {feature["family"] for feature in features}
    depth_manifest = json.loads(args.manifest.read_text())
    depth_flags = {row["id"]: bool(row["show_depth_views"])
                   for row in depth_manifest["families"]}
    if not medium <= families or not difficult <= families or medium & difficult:
        raise ValueError("Tier inputs overlap or refer to unknown families")
    if set(depth_flags) != families:
        raise ValueError("Depth-view manifest does not match feature families")
    spec = json.loads(args.spec.read_text())
    source_pdf = {}
    if args.source_drawings_json:
        drawing_manifest = json.loads(args.source_drawings_json.read_text())
        if drawing_manifest.get("schema") != "harbor/verified-source-drawings-v1":
            raise ValueError("Unsupported verified source drawing manifest")
        if drawing_manifest.get("part_spec_sha256") != hashlib.sha256(args.spec.read_bytes()).hexdigest():
            raise ValueError("Source drawing family mapping differs from part spec")
        source_pdf = drawing_manifest["families"]
        if not set(source_pdf) <= families:
            raise ValueError("Source PDF mapping has unknown families")
        if set(source_pdf) & (medium | difficult):
            raise ValueError("Source PDF families must be excluded from medium and STEP tiers")
    quantity = {row["id"]: row["quantity"] for row in spec["families"]}
    rows = [{"family": feature["family"],
             "tier": tier(feature, medium, difficult, depth_flags, source_pdf),
             "quantity": quantity[feature["family"]]}
            for feature in features]
    rows.sort(key=lambda row: (row["tier"] == "pdf", row["family"]))
    if {row["family"] for row in rows} != families:
        raise ValueError("Duplicate feature family")
    for row in rows:
        if row["tier"] != "light":
            continue
        feature = feature_by_family[row["family"]]
        mesh_path = args.meshes / f"{row['family']}.stl"
        feature["circles"].extend(top_circular_rims(mesh_path, feature["circles"]))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    working = args.output.parent.parent.parent / "author_work"
    working.mkdir(parents=True, exist_ok=True)
    annotated_path = working / "light_annotations.pdf"
    annotation_plan = working / "light_annotation_plan.json"
    with fitz.open(args.base) as base:
        base_pages = source_pages(base)
        if set(base_pages) != families:
            raise ValueError("Base PDF does not contain exactly the feature families")
        light_plan = []
        for feature in features:
            if tier(feature, medium, difficult, depth_flags, source_pdf) == "light":
                light_plan.append({**feature, "page": base_pages[feature["family"]],
                                   "layout": "nine-grid"})
        annotation_plan.write_text(json.dumps(light_plan))
    annotate(argparse.Namespace(input=args.base, output=annotated_path,
                                plan=annotation_plan, enhanced=True))

    if medium and (args.medium is None or not args.medium.is_file()):
        raise ValueError("Medium candidates require a detailed PDF")
    with fitz.open(args.base) as base, fitz.open(annotated_path) as annotated, \
            (fitz.open(args.medium) if medium else fitz.open()) as detailed:
        base_pages = source_pages(base)
        detailed_pages = medium_pages(detailed)
        if set(detailed_pages) != medium:
            raise ValueError("Detailed PDF family set differs from candidate set")
        cover_groups = cover_page_groups(rows)
        page_ranges = {}
        page_number = len(cover_groups) + 1
        for row in rows:
            if row["tier"] == "medium":
                count = len(detailed_pages[row["family"]])
            elif row["tier"] == "pdf":
                count = sum(record["pages"] for record in source_pdf[row["family"]])
            else:
                count = 1
            page_ranges[row["family"]] = (page_number, page_number + count - 1)
            page_number += count
        out = fitz.open()
        for cover_number, index_rows in enumerate(cover_groups, start=1):
            draw_cover(out, rows, index_rows, page_ranges, args.case,
                       cover_number, len(cover_groups), args.title)
        for row in rows:
            family = row["family"]
            kind = row["tier"]
            if kind == "simple":
                draw_simple(out, base, base_pages[family], family, row["quantity"])
            elif kind == "light":
                draw_light(out, annotated, base, base_pages[family],
                           family, row["quantity"], feature_by_family[family],
                           args.meshes / (family + ".stl"))
            elif kind == "medium":
                for source_page in detailed_pages[family]:
                    out.insert_pdf(detailed, from_page=source_page, to_page=source_page)
            elif kind == "pdf":
                source_pdf_pages(out, family, row["quantity"], source_pdf[family])
            else:
                draw_step(out, family, row["quantity"], args.renders)
        if len(out) != page_number - 1:
            raise ValueError("Cover page index and assembled page count differ")
        for page in out:
            text = page.get_text()
            if "DETAIL A" in text or "DETAIL B" in text:
                raise ValueError(f"A/B detail remains on page {page.number + 1}")
            if ("BLENDER" in text.upper() or
                    "DIFFICULT PART | STEP +" in text.upper()):
                raise ValueError(
                    f"Public PDF has superseded wording on page {page.number + 1}")
            if not text.strip():
                raise ValueError(f"Empty page {page.number + 1}")
        out.set_metadata({"title": args.case + " four-tier parts drawings"})
        out.save(args.output, garbage=4, deflate=True)
        out.close()

    delivery_steps = args.output.parent / "provided_part_files"
    delivery_steps.mkdir(exist_ok=True)
    for family in sorted(difficult):
        shutil.copy2(args.step_files_dir / f"{family}.step",
                     delivery_steps / f"{family}.step")
    audit = {"case": args.case, "pages": page_number - 1,
             "tiers": {key: [row["family"] for row in rows if row["tier"] == key]
                       for key in LABELS},
             "page_ranges": page_ranges,
             "provided_part_files": sorted(difficult)}
    (working / "four_tier_audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False))
    print(json.dumps({"pdf": str(args.output), "pages": page_number - 1,
                      "tiers": {key: len(value) for key, value in audit["tiers"].items()}},
                     ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--title", help="Cover title for the finished parts book")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="Original aligned manifest containing show_depth_views")
    parser.add_argument("--meshes", type=Path, required=True,
                        help="Aligned family STL directory for non-cylindrical circular rims")
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--step-files-dir", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--medium", type=Path,
                        help="Detailed PDF, required when medium candidates exist")
    parser.add_argument("--renders", type=Path, required=True,
                        help="Family Blender render cache for newly selected STEP pages")
    parser.add_argument("--source-drawings-json", type=Path,
                        help="Validated family-to-source-PDF mapping from this build")
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
