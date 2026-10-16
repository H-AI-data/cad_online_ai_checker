"""Build one lossless, labeled contact sheet for all medium-difficulty candidates."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", size)


def build(rows: list[dict], roots: dict[str, Path], output: Path) -> None:
    columns = 12
    tile_w, tile_h = 268, 204
    gap, margin = 12, 34
    title_h, section_h = 132, 48
    grouped = {}
    for row in rows:
        grouped.setdefault(row["case"], []).append(row)
    section_rows = sum((len(group) + columns - 1) // columns
                       for group in grouped.values())
    width = margin * 2 + columns * tile_w + (columns - 1) * gap
    height = (title_h + margin + len(grouped) * section_h
              + section_rows * (tile_h + gap) + margin)
    board = Image.new("RGB", (width, height), "#f3f6fa")
    draw = ImageDraw.Draw(board)
    navy, grey = "#203d5f", "#64758a"
    draw.rectangle((0, 0, width, title_h), fill=navy)
    draw.text((margin, 25), f"中等难度零件候选总览  |  {len(rows)} 个 family",
              font=font(38), fill="white")
    draw.text((margin, 78), "已剔除前次高难候选  ·  红色：优先增加尺寸信息  ·  蓝色：普通候选  ·  图像为外形缩略图",
              font=font(18), fill="#d9e6f2")
    y = title_h + margin
    for case_name, group in grouped.items():
        priority = sum(item["tier"] == "优先" for item in group)
        draw.text((margin, y + 4),
                  f"{case_name}    {len(group)} 件  |  优先 {priority} 件",
                  font=font(23), fill=navy)
        y += section_h
        for index, item in enumerate(group):
            column = index % columns
            row = index // columns
            x = margin + column * (tile_w + gap)
            top = y + row * (tile_h + gap)
            draw.rounded_rectangle((x, top, x + tile_w, top + tile_h),
                                   radius=9, fill="white", outline="#d6e0e9", width=2)
            accent = "#d65345" if item["tier"] == "优先" else "#3478ac"
            draw.rounded_rectangle((x + 1, top + 1, x + tile_w - 1, top + 7),
                                   radius=4, fill=accent)
            path = roots[case_name] / "renders" / item["family"] / "upper-oblique.png"
            if not path.is_file():
                raise FileNotFoundError(path)
            with Image.open(path) as source:
                source = source.convert("RGBA")
                picture = ImageOps.contain(source, (tile_w - 20, 137),
                                           Image.Resampling.LANCZOS)
            background = Image.new("RGBA", (tile_w - 20, 137), "#f7f9fb")
            background.alpha_composite(
                picture, ((background.width - picture.width) // 2,
                          (background.height - picture.height) // 2))
            board.paste(background.convert("RGB"), (x + 10, top + 12))
            draw.text((x + 11, top + 153), item["family"], font=font(17), fill=navy)
            draw.text((x + tile_w - 57, top + 154), item["tier"],
                      font=font(14), fill=accent)
            circles = item["circles"]
            reflex = item["thin_outline"].get("max_reflex", "—")
            draw.text((x + 11, top + 179),
                      f"圆心 {circles['circle_centers']} · 直径 {circles['diameter_types']} · 凹角 {reflex}",
                      font=font(13), fill=grey)
        y += ((len(group) + columns - 1) // columns) * (tile_h + gap)
    output.parent.mkdir(parents=True, exist_ok=True)
    board.save(output, optimize=True)
    print(f"Wrote {output}: {width}×{height}, {len(rows)} families")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads(args.candidates.read_text())
    cases = json.loads(args.manifest.read_text())
    roots = {case["name"]: Path(case["parts_workdir"]) for case in cases}
    if len({(row["case"], row["family"]) for row in rows}) != len(rows):
        raise ValueError("Duplicate candidates")
    if any(row["case"] not in roots for row in rows):
        raise ValueError("Candidate case is absent from manifest")
    build(rows, roots, args.output)


if __name__ == "__main__":
    main()
