"""Render every sheet for visual QA and fail on text outside the page."""
import argparse
import json
from pathlib import Path
import pymupdf
from PIL import Image, ImageDraw


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pdf', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(args.pdf)
    errors = []
    for i, page in enumerate(doc):
        page.get_pixmap(matrix=pymupdf.Matrix(1600/page.rect.width, 1600/page.rect.width), alpha=False).save(args.out/f'page_{i+1:02d}.png')
        for block in page.get_text('blocks'):
            if block[0] < 0 or block[1] < 0 or block[2] > page.rect.width or block[3] > page.rect.height:
                errors.append(i+1)
    for start in range(0, len(doc), 6):
        contact = Image.new('RGB', (1600, 1740), '#e8edf1')
        for offset in range(min(6, len(doc)-start)):
            im = Image.open(args.out/f'page_{start+offset+1:02d}.png')
            im.thumbnail((770, 540))
            x, y = (offset % 2)*800+15, (offset//2)*580+25
            contact.paste(im, (x, y))
            ImageDraw.Draw(contact).text((x, y-18), str(start+offset+1), fill='black')
        contact.save(args.out/f'contact_{start//6+1:02d}.png')
    result = {'pages': len(doc), 'text_overflow_pages': sorted(set(errors)),
              'visual_review_required': True}
    (args.out/'qa.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
