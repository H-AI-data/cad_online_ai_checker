"""Render same-scale reference/candidate/difference orthographic projections."""

from __future__ import annotations

import struct
import itertools
import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


REFERENCE_ONLY = (54, 117, 191)
CANDIDATE_ONLY = (225, 112, 69)
OVERLAP = (61, 153, 112)
REFERENCE_AREA = (220, 233, 248)
CANDIDATE_AREA = (250, 231, 220)
OVERLAP_AREA = (220, 239, 229)
MODEL_FILL = (225, 228, 235)
MODEL_EDGE = (61, 68, 82)
BACKGROUND = (248, 249, 251)
FRAME = (190, 195, 205)
TEXT = (36, 41, 51)


def load_stl(path: Path) -> np.ndarray:
    data = path.read_bytes()
    if len(data) >= 84:
        count = struct.unpack_from("<I", data, 80)[0]
        if 84 + count * 50 == len(data):
            dtype = np.dtype(
                [
                    ("normal", "<f4", (3,)),
                    ("vertices", "<f4", (3, 3)),
                    ("attribute", "<u2"),
                ]
            )
            return np.frombuffer(data, dtype=dtype, offset=84, count=count)["vertices"].astype(float)
    vertices = []
    for line in data.decode("ascii", errors="ignore").splitlines():
        fields = line.strip().split()
        if fields[:1] == ["vertex"] and len(fields) == 4:
            vertices.append([float(value) for value in fields[1:]])
    if len(vertices) % 3:
        raise ValueError(f"Malformed STL: {path}")
    return np.asarray(vertices, float).reshape(-1, 3, 3)


def load_stl_parts(path: Path) -> list[np.ndarray]:
    items = sorted(path.glob("*.stl")) if path.is_dir() else [path]
    if not items:
        raise ValueError(f"No STL files in {path}")
    return [load_stl(item) for item in items]


def canonicalize(parts: list[np.ndarray], target_dims: np.ndarray,
                 flips: tuple[bool, bool, bool]):
    vertices = np.concatenate(parts).reshape(-1, 3)
    raw_min = vertices.min(axis=0)
    raw_dims = vertices.max(axis=0) - raw_min
    order = min(
        itertools.permutations(range(3)),
        key=lambda item: float(np.linalg.norm(raw_dims[list(item)] - target_dims)),
    )
    order = np.asarray(order, int)
    dims = raw_dims[order]
    transformed = []
    for part in parts:
        mesh = part[:, :, order] - raw_min[order]
        for axis, flip in enumerate(flips):
            if flip:
                mesh[:, :, axis] = dims[axis] - mesh[:, :, axis]
        transformed.append(mesh)
    return transformed, dims


def convex_hull(points: np.ndarray) -> list[tuple[float, float]]:
    unique = sorted(map(tuple, np.unique(np.round(points, 2), axis=0)))
    if len(unique) <= 2:
        return unique

    def cross(origin, first, second):
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (
            first[1] - origin[1]
        ) * (second[0] - origin[0])

    lower = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def projection_masks(
    parts: list[np.ndarray],
    axes: tuple[int, int],
    extent: tuple[float, float],
    size: tuple[int, int],
) -> tuple[Image.Image, Image.Image]:
    width, height = size
    area = Image.new("L", size, 0)
    lines = Image.new("L", size, 0)
    area_draw = ImageDraw.Draw(area)
    line_draw = ImageDraw.Draw(lines)
    sx = (width - 1) / extent[0]
    sy = (height - 1) / extent[1]
    for part in parts:
        projected = np.empty((part.shape[0], 3, 2), float)
        projected[:, :, 0] = part[:, :, axes[0]] * sx
        projected[:, :, 1] = (extent[1] - part[:, :, axes[1]]) * sy
        for triangle in projected:
            area_draw.polygon([tuple(point) for point in triangle], fill=255)
        hull = convex_hull(projected.reshape(-1, 2))
        if len(hull) >= 2:
            line_draw.line(hull + [hull[0]], fill=255, width=2, joint="curve")
    return area, lines


def dilate(mask: np.ndarray, radius: int = 2) -> np.ndarray:
    padded = np.pad(mask, radius)
    result = np.zeros_like(mask)
    height, width = mask.shape
    for dy in range(radius * 2 + 1):
        for dx in range(radius * 2 + 1):
            result |= padded[dy : dy + height, dx : dx + width]
    return result


def crop_with_margin(image: Image.Image, bbox: tuple[int, int, int, int], margin: int = 10):
    left, top, right, bottom = bbox
    return image.crop(
        (
            max(0, left - margin),
            max(0, top - margin),
            min(image.width, right + margin),
            min(image.height, bottom + margin),
        )
    )


def label_panel(panel: Image.Image, title: str) -> Image.Image:
    font = ImageFont.load_default(size=20)
    header = 42
    result = Image.new("RGB", (panel.width, panel.height + header), BACKGROUND)
    result.paste(panel, (0, header))
    draw = ImageDraw.Draw(result)
    draw.text((12, 11), title, fill=TEXT, font=font)
    return result


def render_view(
    name: str,
    axes: tuple[int, int],
    ref_parts: list[np.ndarray],
    candidate_parts: list[np.ndarray],
    extent: tuple[float, float],
    output: Path,
) -> None:
    max_side = 820
    scale = min(max_side / extent[0], max_side / extent[1])
    size = (
        max(120, int(round(extent[0] * scale))),
        max(120, int(round(extent[1] * scale))),
    )
    ref_area, ref_lines = projection_masks(ref_parts, axes, extent, size)
    candidate_area, candidate_lines = projection_masks(candidate_parts, axes, extent, size)

    union = np.maximum(np.asarray(ref_area), np.asarray(candidate_area))
    ys, xs = np.nonzero(union)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    panels = []
    for title, area, lines in (
        ("REFERENCE", ref_area, ref_lines),
        ("CANDIDATE", candidate_area, candidate_lines),
    ):
        rgb = Image.new("RGB", size, BACKGROUND)
        rgb.paste(MODEL_FILL, mask=area)
        rgb.paste(MODEL_EDGE, mask=lines)
        panels.append(label_panel(crop_with_margin(rgb, bbox), title))

    ref_array = np.asarray(ref_area) > 0
    candidate_array = np.asarray(candidate_area) > 0
    ref_line_array = np.asarray(ref_lines) > 0
    candidate_line_array = np.asarray(candidate_lines) > 0
    diff = np.full((size[1], size[0], 3), BACKGROUND, dtype=np.uint8)
    diff[ref_array & candidate_array] = OVERLAP_AREA
    diff[ref_array & ~candidate_array] = REFERENCE_AREA
    diff[~ref_array & candidate_array] = CANDIDATE_AREA
    diff[ref_line_array] = REFERENCE_ONLY
    diff[candidate_line_array] = CANDIDATE_ONLY
    near_overlap = (ref_line_array & dilate(candidate_line_array)) | (
        candidate_line_array & dilate(ref_line_array)
    )
    diff[near_overlap] = OVERLAP
    panels.append(label_panel(crop_with_margin(Image.fromarray(diff), bbox), "DIFFERENCE"))

    panel_width = max(panel.width for panel in panels)
    panel_height = max(panel.height for panel in panels)
    gap = 18
    sheet = Image.new("RGB", (panel_width * 3 + gap * 2, panel_height), BACKGROUND)
    for index, panel in enumerate(panels):
        x = index * (panel_width + gap) + (panel_width - panel.width) // 2
        y = panel_height - panel.height
        sheet.paste(panel, (x, y))
        ImageDraw.Draw(sheet).rectangle(
            (index * (panel_width + gap), 0, index * (panel_width + gap) + panel_width - 1, panel_height - 1),
            outline=FRAME,
            width=1,
        )
    sheet.save(output, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--reference-flips", default="000",
                        help="Three 0/1 flags for reflected reference axes")
    parser.add_argument("--candidate-flips", default="000",
                        help="Three 0/1 flags for reflected candidate axes")
    args = parser.parse_args()
    for name in ("reference_flips", "candidate_flips"):
        if len(getattr(args, name)) != 3 or set(getattr(args, name)) - {"0", "1"}:
            parser.error(f"--{name.replace('_', '-')} must contain three 0/1 flags")
    ref_path, candidate_path, output_dir = args.reference, args.candidate, args.output_dir
    ref_raw = load_stl_parts(ref_path)
    candidate_raw = load_stl_parts(candidate_path)
    reference_vertices = np.concatenate(ref_raw).reshape(-1, 3)
    reference_dims = np.sort(np.ptp(reference_vertices, axis=0))[::-1]
    ref, ref_dims = canonicalize(ref_raw, reference_dims,
                                 tuple(flag == "1" for flag in args.reference_flips))
    candidate, candidate_dims = canonicalize(candidate_raw, reference_dims,
                                             tuple(flag == "1" for flag in args.candidate_flips))
    canvas_dims = np.maximum(ref_dims, candidate_dims)

    output_dir.mkdir(parents=True, exist_ok=True)
    render_view(
        "front",
        (0, 2),
        ref,
        candidate,
        (canvas_dims[0], canvas_dims[2]),
        output_dir / "front-comparison.png",
    )
    render_view(
        "side",
        (1, 2),
        ref,
        candidate,
        (canvas_dims[1], canvas_dims[2]),
        output_dir / "side-comparison.png",
    )
    render_view(
        "top",
        (0, 1),
        ref,
        candidate,
        (canvas_dims[0], canvas_dims[1]),
        output_dir / "top-comparison.png",
    )
    np.savetxt(output_dir / "dimensions-mm.txt", np.vstack([ref_dims, candidate_dims]), fmt="%.4f")


if __name__ == "__main__":
    main()
