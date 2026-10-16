"""Corroborate OCR with the projected position of a shallow planar recess."""

import json
import math
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from config import WORK, HARBOR, SCRIPTS

import numpy as np
import pymupdf as fitz
import trimesh
from PIL import Image, ImageDraw

from run_complexity_screen import current_case_source

ROI_DIR = WORK / 'complexity_review/shallow_recess/text_roi'
ROI_DIR.mkdir(parents=True, exist_ok=True)
rows = json.loads((WORK / 'shallow_recess_scan.json').read_text())
cases = {row['name']: row for row in json.loads((WORK / 'selected.json').read_text())}


def grid_box(page, axis):
    gap = 8
    width = (page.rect.width - 56 - gap * 2) / 3
    height = (page.rect.height - 130 - gap * 2) / 3
    column = {0: 1, 1: 0, 2: 2}[axis]
    return (28 + column * (width + gap), page.rect.height - 92 - height,
            width, height)


def ocr_bbox(raw):
    values = [float(value) for value in re.findall(r'-?\d+(?:\.\d+)?(?:e[-+]?\d+)?', raw)]
    if len(values) != 4:
        raise ValueError(f'Unparseable Vision boundingBox: {raw}')
    return values


roots = {}
for case_name in {row['case'] for row in rows}:
    root, spec = current_case_source(cases[case_name])
    valid_families = {family['id'] for family in spec['families']}
    if any(row['family'] not in valid_families for row in rows if row['case'] == case_name):
        raise ValueError(f'Shallow recess family does not match current PDF: {case_name}')
    roots[case_name] = root

paths = []
masks = {}
for row in rows:
    case_name = row['case']
    current_family = row['family']
    pdf = HARBOR / case_name / 'environment/input/parts.pdf'
    with fitz.open(pdf) as document:
        matches = [page for page in document
                   if page.get_text().startswith(current_family.upper() + '\n')]
        if len(matches) != 1:
            raise ValueError((case_name, current_family, len(matches)))
        page = matches[0]
        if 'UPPER OBLIQUE' not in page.get_text():
            raise ValueError(f'Unsupported page layout: {case_name} {current_family}')
        mesh_path = roots[case_name] / 'aligned' / f'{current_family}.stl'
        mesh = trimesh.load_mesh(mesh_path, process=False)
        triangles = mesh.triangles
        axis = row['axis']
        row['current_family'] = current_family
        row['source_page'] = page.number + 1
        outer = mesh.bounds[0 if row['side'] == -1 else 1, axis]
        depths = np.round((triangles[:, :, axis] - outer) * -row['side'], 3)
        floor = triangles[np.all(np.abs(depths - row['depth_mm']) < .002, axis=1)]
        if len(floor) != row['floor_planar_triangles']:
            raise ValueError(f'Floor triangle count changed: {case_name} {current_family}')
        row['view_crops'] = {}
        row['recess_pixels_by_view'] = {}
        for view_axis, view, axes in ((1, 'FRONT', (0, 2)),
                                      (0, 'SIDE', (1, 2)),
                                      (2, 'TOP', (0, 1))):
            x, y, width, height = grid_box(page, view_axis)
            minimum = mesh.bounds[0, list(axes)]
            extent = mesh.extents[list(axes)]
            drawing_width, drawing_height = width - 45, height - 55
            scale = min(drawing_width / max(extent[0], 1e-6),
                        drawing_height / max(extent[1], 1e-6))
            offset_x = x + 31 + (drawing_width - extent[0] * scale) / 2
            offset_y = y + 31 + (drawing_height - extent[1] * scale) / 2
            pad = 2.0
            clip = fitz.Rect(offset_x - pad,
                             page.rect.height - (offset_y + extent[1] * scale + pad),
                             offset_x + extent[0] * scale + pad,
                             page.rect.height - (offset_y - pad))
            pix = page.get_pixmap(matrix=fitz.Matrix(5, 5), clip=clip, alpha=False)
            image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            path = ROI_DIR / f'{case_name}_{current_family}_{view.lower()}_model.png'
            image.save(path)
            paths.append(path)
            row['view_crops'][view] = str(path)
            mask = Image.new('L', image.size)
            draw = ImageDraw.Draw(mask)
            for triangle in floor:
                points = []
                for vertex in triangle:
                    pdf_x = offset_x + (vertex[axes[0]] - minimum[0]) * scale
                    pdf_y = offset_y + (vertex[axes[1]] - minimum[1]) * scale
                    points.append(((pdf_x - clip.x0) * 5,
                                   (page.rect.height - pdf_y - clip.y0) * 5))
                draw.polygon(points, fill=255)
            masks[str(path)] = np.asarray(mask) > 0
            row['recess_pixels_by_view'][view] = int(masks[str(path)].sum())

found = defaultdict(list)
for start in range(0, len(paths), 150):
    result = subprocess.run(['swift', str(SCRIPTS / 'ocr_surface.swift'),
                             *(str(path) for path in paths[start:start + 150])],
                            check=True, capture_output=True, text=True)
    current_path = None
    for line in result.stdout.splitlines():
        if line.startswith('FILE\t'):
            current_path = line.split('\t', 1)[1]
        elif current_path and '\t' in line:
            token, confidence, bounds = line.split('\t', 2)
            found[current_path].append({'text': token, 'confidence': float(confidence),
                                        'bbox_uv': ocr_bbox(bounds)})

for row in rows:
    hits = []
    row['ocr_tokens'] = []
    for view, path in row['view_crops'].items():
        mask = masks[path]
        image_height, image_width = mask.shape
        for token in found[path]:
            x, y, width, height = token['bbox_uv']
            x0 = max(0, math.floor(x * image_width))
            x1 = min(image_width, math.ceil((x + width) * image_width))
            y0 = max(0, math.floor((1 - y - height) * image_height))
            y1 = min(image_height, math.ceil((1 - y) * image_height))
            box_area = max(1, (x1 - x0) * (y1 - y0))
            overlap = int(mask[y0:y1, x0:x1].sum())
            token['view'] = view
            token['recess_overlap_pixels'] = overlap
            token['recess_overlap_fraction'] = round(overlap / box_area, 4)
            token['region_overlap'] = overlap >= 15 and overlap / box_area >= .02
            if re.search(r'[A-Za-z]{3,}', token['text']) and token['region_overlap']:
                hits.append(token)
            row['ocr_tokens'].append(token)
    row['text_candidate'] = bool(hits)
    row['text_region_overlap'] = bool(hits)
    row['text_region_tokens'] = [f"{token['view']}:{token['text']}" for token in hits]

(WORK / 'shallow_text_scan.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
print('geometric candidates', len(rows), 'spatial OCR matches', sum(row['text_candidate'] for row in rows))
for row in rows:
    print(row['case'], row['current_family'], row['depth_mm'],
          row['text_candidate'], [(t['view'], t['text'], t['recess_overlap_fraction'])
                                  for t in row['ocr_tokens']])
