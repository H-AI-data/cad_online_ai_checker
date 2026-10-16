"""High-recall OCR scan of every selected family orthographic model view."""

import json
import re
import subprocess
from pathlib import Path
from config import WORK, HARBOR, SCRIPTS

import fitz
from PIL import Image

OUT = WORK / 'complexity_review/visual_text_crops'
OUT.mkdir(parents=True, exist_ok=True)
selected = json.loads((WORK / 'selected.json').read_text())
rows = []
paths = []
for case in selected:
    name = case['name']
    document = fitz.open(HARBOR / name / 'environment/input/parts.pdf')
    pages = {}
    for page in document:
        text = page.get_text()
        if page.number == 0 or text.startswith('FAMILY INDEX'):
            continue
        for label in re.findall(r'\bFAMILY_\d+\b', text):
            pages[label.lower()] = page
    for fid, page in sorted(pages.items()):
        row = {'case': name, 'index': case['index'], 'family': fid,
               'source_page': page.number + 1, 'crops': {}}
        for view, x0, x1 in [('FRONT', .06, .31), ('SIDE', .38, .64),
                             ('TOP', .70, .96)]:
            clip = fitz.Rect(page.rect.width * x0, page.rect.height * .24,
                             page.rect.width * x1, page.rect.height * .35)
            pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=clip, alpha=False)
            image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            path = OUT / f'{name}_{fid}_{view.lower()}.png'
            image.save(path)
            paths.append(path)
            row['crops'][view] = str(path)
        rows.append(row)
    document.close()
    print(name, len(pages), 'crops', flush=True)

found = {}
for start in range(0, len(paths), 150):
    batch = paths[start:start + 150]
    result = subprocess.run(['swift', str(SCRIPTS / 'ocr_surface.swift'),
                             *(str(p) for p in batch)],
                            check=True, capture_output=True, text=True)
    current = None
    for line in result.stdout.splitlines():
        if line.startswith('FILE\t'):
            current = line.split('\t', 1)[1]
            found[current] = []
        elif current and '\t' in line:
            token, confidence, _ = line.split('\t', 2)
            found[current].append({'text': token, 'confidence': float(confidence)})
    print('OCR', min(start + len(batch), len(paths)), '/', len(paths), flush=True)
for row in rows:
    row['ocr_tokens'] = [dict(view=view, **token)
                         for view, path in row['crops'].items()
                         for token in found.get(path, [])]
    # Standalone OCR has no geometric corroboration, so require a longer
    # word-like run than the shallow-recess-confirmation pass does. This
    # reject short OCR strings that can be formed by repeated circular holes.
    row['text_candidate'] = any(re.search(r'[A-Za-z]{4,}', token['text'])
                                for token in row['ocr_tokens'])
(WORK / 'visual_text_scan.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
hits = [row for row in rows if row['text_candidate']]
print('families', len(rows), 'visual-text candidates', len(hits))
for row in hits:
    print(row['case'], row['family'],
          [f"{token['view']}:{token['text']}" for token in row['ocr_tokens']
           if re.search(r'[A-Za-z]{3,}', token['text'])])
