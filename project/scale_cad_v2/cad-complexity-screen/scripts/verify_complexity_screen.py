"""Validate full Harbor coverage, current family IDs, preview, and recall."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pymupdf

from run_complexity_screen import HARBOR, WORK, OUTPUT, REASON_PRIORITY, current_case_source


def read(name):
    return json.loads((WORK / name).read_text())


def main():
    cases = read('selected.json')
    present = {path.name for path in HARBOR.glob('cad_*') if path.is_dir()}
    assert {case['name'] for case in cases} == present
    family_keys = set()
    family_counts = {}
    case_roots = {}
    for case in cases:
        root, spec = current_case_source(case)
        case_roots[case['name']] = root
        keys = {(case['name'], family['id']) for family in spec['families']}
        assert not (family_keys & keys)
        family_keys |= keys
        family_counts[case['name']] = len(keys)

    rows = read('complexity_auto_candidates.json')
    keys = [(row['case'], row['family']) for row in rows]
    assert len(keys) == len(set(keys))
    assert set(keys) <= family_keys
    assert {(row['case'], row['family']) for row in read('profile_scan.json')} == family_keys
    assert {(row['case'], row['family']) for row in read('visual_text_scan.json')} == family_keys
    selection = read('complexity_step_selection.json')
    assert selection['schema'] == 'cad-complexity-screen/step-selection-v1'
    assert {row['case'] for row in selection['cases']} == set(case_roots)
    assert len(selection['cases']) == len(case_roots)
    for row in selection['cases']:
        root = case_roots[row['case']]
        assert row['part_spec_sha256'] == hashlib.sha256(
            (root / 'part-spec.json').read_bytes()).hexdigest()
        assert row['source_step_sha256'] == json.loads(
            (root / 'geometry/catalog.json').read_text())['source_sha256']
        assert row['families'] == sorted(family for case, family in keys
                                         if case == row['case'])

    baseline_path = WORK / 'complexity_auto_candidates_before_full_rescan.json'
    baseline_available = baseline_path.is_file()
    baseline = json.loads(baseline_path.read_text()) if baseline_available else []
    baseline_keys = {(row['case'], row['family']) for row in baseline}
    missing_baseline = sorted(baseline_keys - set(keys))
    if missing_baseline:
        raise AssertionError(f'Previously selected families lost: {missing_baseline}')

    preview = OUTPUT / '复杂零件自动筛选预览.pdf'
    index_pages = (len(rows) + 37) // 38
    with pymupdf.open(preview) as document:
        assert len(document) == len(rows) + index_pages
        for index, row in enumerate(rows, index_pages):
            assert row['family'].upper() in document[index].get_text()
        page_count = len(document)

    known_reasons = {reason for reason, _ in REASON_PRIORITY}
    assert all(set(row['reasons']) <= known_reasons for row in rows)
    summary = {
        'case_count': len(cases), 'family_count': len(family_keys),
        'family_counts': family_counts,
        'candidate_count': len(rows),
        'candidates_by_case': dict(sorted(Counter(row['case'] for row in rows).items())),
        'expanded_only_count': sum(row['screen_level'] == 'expanded' for row in rows),
        'baseline_available': baseline_available,
        'baseline_candidate_count': len(baseline_keys),
        'baseline_lost': missing_baseline,
        'new_since_baseline': sorted(set(keys) - baseline_keys) if baseline_available else [],
        'preview_pages': page_count,
        'visual_ocr_observations': sum(row['text_candidate'] for row in read('visual_text_scan.json')),
        'shallow_recess_rows': len(read('shallow_recess_scan.json')),
        'shallow_text_matches': sum(row['text_candidate'] for row in read('shallow_text_scan.json')),
        'longitudinal_hits': len(read('longitudinal_geometry_scan.json')),
    }
    (WORK / 'full_rescan_validation.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
