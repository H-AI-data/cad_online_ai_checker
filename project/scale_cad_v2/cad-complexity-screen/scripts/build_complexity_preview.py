"""Collect screened family pages into a review PDF and a visual index image."""

from __future__ import annotations

import argparse
import re
from io import BytesIO
from pathlib import Path
from config import WORK, HARBOR, OUTPUT

import fitz
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


REPORT = WORK / 'complexity_auto_candidates.md'
PDF_OUT = OUTPUT / '复杂零件自动筛选预览.pdf'
IMAGE_OUT = OUTPUT / '复杂零件自动筛选总览.png'


def candidates(report: Path = REPORT) -> list[dict]:
    rows = []
    for line in report.read_text().splitlines():
        if not line.startswith('|cad_'):
            continue
        case, family, quantity, euler, triangles, aspect, reason = line.strip('|').split('|')
        rows.append(dict(case=case, family=family.upper(), quantity=int(quantity),
                         euler=int(euler), triangles=int(triangles),
                         aspect=float(aspect), reason=reason))
    if not rows:
        raise ValueError('No candidate rows found in report')
    return rows


def cover_page(rows: list[dict], first_source_page: int = 2,
               total_count: int | None = None, index_number: int = 1,
               index_count: int = 1) -> bytes:
    pdfmetrics.registerFont(TTFont('HeitiSC', '/System/Library/Fonts/STHeiti Medium.ttc', subfontIndex=1))
    stream = BytesIO()
    page_w, page_h = landscape(A4)
    cv = canvas.Canvas(stream, pagesize=(page_w, page_h))
    cv.setFillColor(HexColor('#25496e'))
    cv.rect(0, page_h - 91, page_w, 91, fill=1, stroke=0)
    cv.setFillColorRGB(1, 1, 1)
    cv.setFont('HeitiSC', 23)
    cv.drawString(36, page_h - 54, '复杂零件 PDF 页面预览')
    cv.setFont('HeitiSC', 10)
    cv.drawRightString(page_w - 36, page_h - 54,
                       f'{total_count or len(rows)} 个优先预览候选')
    cv.setFillColor(HexColor('#1f2937'))
    cv.setFont('HeitiSC', 10)
    cv.drawString(36, page_h - 117, '本文件收录原 parts.pdf 页面；每页保留原图尺寸，供放大检查。')
    cv.drawString(36, page_h - 134, '筛选结果是待复核候选，不代表每个 family 都需要提供 STEP。')

    y = page_h - 168
    if len(rows) > 24:
        column_width = (page_w - 82) / 2
        split = (len(rows) + 1) // 2
        for column in range(2):
            x0 = 34 + column * (column_width + 14)
            cv.setFillColor(HexColor('#dce7f2'))
            cv.roundRect(x0, y - 7, column_width, 24, 5, fill=1, stroke=0)
            cv.setFillColor(HexColor('#243b53'))
            cv.setFont('HeitiSC', 9)
            for dx, label in [(8, '页'), (42, '题目'), (143, 'Family'), (230, '原页'), (267, '原因')]:
                cv.drawString(x0 + dx, y, label)
            for local_index, row in enumerate(rows[column * split:(column + 1) * split]):
                index = first_source_page + column * split + local_index
                row_y = y - 26 - local_index * 20
                if local_index % 2 == 0:
                    cv.setFillColor(HexColor('#f2f6fa'))
                    cv.rect(x0, row_y - 5, column_width, 19, fill=1, stroke=0)
                cv.setFillColor(HexColor('#27384c'))
                cv.setFont('HeitiSC', 8.6)
                for dx, value in [(8, str(index)), (42, row['case']), (143, row['family']),
                                  (230, str(row['source_page'])), (267, row['reason'])]:
                    cv.drawString(x0 + dx, row_y, value)
    else:
        cv.setFillColor(HexColor('#dce7f2'))
        cv.roundRect(34, y - 7, page_w - 68, 24, 5, fill=1, stroke=0)
        cv.setFillColor(HexColor('#243b53'))
        cv.setFont('HeitiSC', 10)
        for x, label in [(45, '预览页'), (115, '题目'), (275, 'Family'), (407, '原 PDF 页'), (515, '筛选原因')]:
            cv.drawString(x, y, label)
        y -= 26
        row_step = 14 if len(rows) > 22 else 16 if len(rows) > 20 else 18
        for index, row in enumerate(rows, first_source_page):
            if index % 2 == 0:
                cv.setFillColor(HexColor('#f2f6fa'))
                cv.rect(34, y - 5, page_w - 68, row_step - 1, fill=1, stroke=0)
            cv.setFillColor(HexColor('#27384c'))
            cv.setFont('HeitiSC', 9.4)
            for x, value in [(45, str(index)), (115, row['case']), (275, row['family']),
                             (407, str(row['source_page'])), (515, row['reason'])]:
                cv.drawString(x, y, value)
            y -= row_step
    cv.setFillColor(HexColor('#5d7186'))
    cv.setFont('HeitiSC', 8.5)
    cv.drawString(36, 31, '原图来源：Harbor 各题 environment/input/parts.pdf')
    cv.drawRightString(page_w - 36, 31, f'目录 / INDEX {index_number}/{index_count}')
    cv.showPage()
    cv.save()
    return stream.getvalue()


def make_overview(rows: list[dict], docs: dict[str, fitz.Document], output: Path = IMAGE_OUT) -> None:
    cols, card_w, card_h, top = 4, 600, 500, 90
    rows_count = (len(rows) + cols - 1) // cols
    image = Image.new('RGB', (cols * card_w, top + rows_count * card_h), '#eaf0f5')
    draw = ImageDraw.Draw(image)
    font_file = '/System/Library/Fonts/STHeiti Medium.ttc'
    title_font = ImageFont.truetype(font_file, 36)
    name_font = ImageFont.truetype(font_file, 25)
    meta_font = ImageFont.truetype(font_file, 19)
    draw.text((28, 19), f'复杂零件总览  ·  {len(rows)} 个候选', font=title_font, fill='#17365a')
    for i, row in enumerate(rows):
        x = (i % cols) * card_w
        y = top + (i // cols) * card_h
        draw.rounded_rectangle((x + 10, y + 8, x + card_w - 10, y + card_h - 10),
                               radius=12, fill='white', outline='#c3d0dd', width=2)
        draw.text((x + 28, y + 22), f"{i+2:02d}  {row['case']} / {row['family']}",
                  font=name_font, fill='#16375b')
        draw.text((x + 28, y + 55), f"原 PDF 第 {row['source_page']} 页  ·  {row['reason']}",
                  font=meta_font, fill='#65788a')
        page = docs[row['case']][row['source_page'] - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False)
        thumb = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
        thumb.thumbnail((card_w - 52, card_h - 115), Image.Resampling.LANCZOS)
        image.paste(thumb, (x + (card_w - thumb.width) // 2, y + 91))
    image.save(output, optimize=True)


def main(report: Path = REPORT, pdf_out: Path = PDF_OUT,
         image_out: Path = IMAGE_OUT) -> None:
    rows = candidates(report)
    docs = {}
    page_maps = {}
    try:
        for row in rows:
            if row['case'] not in docs:
                docs[row['case']] = fitz.open(HARBOR / row['case'] / 'environment/input/parts.pdf')
                page_map = {}
                for page_index, page in enumerate(docs[row['case']]):
                    page_text = page.get_text()
                    if page_index == 0 or page_text.startswith('FAMILY INDEX'):
                        continue
                    for family_label in re.findall(r'\bFAMILY_\d+\b', page_text):
                        page_map.setdefault(family_label, []).append(page_index)
                page_maps[row['case']] = page_map
            doc = docs[row['case']]
            matches = page_maps[row['case']].get(row['family'], [])
            if len(matches) != 1:
                raise ValueError((row['case'], row['family'], matches))
            row['source_page'] = matches[0] + 1
            row['source_index'] = matches[0]
        index_count = (len(rows) + 37) // 38
        combined = fitz.open()
        toc = []
        for index_page in range(index_count):
            chunk = rows[index_page * 38:(index_page + 1) * 38]
            first_source_page = index_count + 1 + index_page * 38
            index_doc = fitz.open(stream=cover_page(chunk, first_source_page,
                                                    len(rows), index_page + 1,
                                                    index_count), filetype='pdf')
            combined.insert_pdf(index_doc)
            index_doc.close()
            toc.append([1, f'候选索引 {index_page + 1}/{index_count}', index_page + 1])
        for output_page, row in enumerate(rows, index_count + 1):
            combined.insert_pdf(docs[row['case']], from_page=row['source_index'],
                                to_page=row['source_index'])
            toc.append([1, f"{row['case']} / {row['family']}", output_page])
        combined.set_toc(toc)
        combined.save(pdf_out, garbage=4, deflate=True)
        combined.close()
        make_overview(rows, docs, image_out)
    finally:
        for doc in docs.values():
            doc.close()
    with fitz.open(pdf_out) as check:
        assert len(check) == len(rows) + index_count
        for i, row in enumerate(rows, index_count):
            assert row['family'] in check[i].get_text()
    print(pdf_out)
    print(image_out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, default=REPORT)
    parser.add_argument('--pdf-out', type=Path, default=PDF_OUT)
    parser.add_argument('--image-out', type=Path, default=IMAGE_OUT)
    args = parser.parse_args()
    main(args.report, args.pdf_out, args.image_out)
