"""Run every current complex-part screen and build a reviewable candidate preview.

Inputs are the already-generated part-spec, aligned STL, and parts.pdf for the
selected Harbor cases. This does not export STEP or change any Harbor input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from config import WORK, HARBOR, OUTPUT, SCRIPTS


REPORT = WORK / 'complexity_auto_candidates.md'
DATA = WORK / 'complexity_auto_candidates.json'
STEP_SELECTION = WORK / 'complexity_step_selection.json'
PDF = OUTPUT / '复杂零件自动筛选预览.pdf'
OVERVIEW = OUTPUT / '复杂零件自动筛选总览.png'

SCANNERS = [
    'scan_profiles.py',
    'scan_periodic_radial_profiles.py',
    'scan_localized_serrations.py',
    'scan_repeated_edge_notches.py',
    'scan_curved_profiles.py',
    'scan_dense_holes.py',
    'scan_mixed_depth_apertures.py',
    'scan_multiaxis_annotation_gaps.py',
    'scan_longitudinal_geometry.py',
    'scan_paired_protrusions.py',
    'scan_perforated_panels.py',
    'scan_shallow_recess.py',
    'confirm_shallow_text.py',
    'scan_visual_text.py',
]

REASON_PRIORITY = [
    ('shallow_text', '浅凹疑似文字'),
    ('flexible_tail', '疑似柔性引线'),
    ('curved_end_tube', '圆筒末端弯管'),
    ('curved_profile', '连续曲线截面'),
    ('complex_extrusion', '高复杂挤压截面'),
    ('periodic_radial_profile', '密集周期齿形'),
    ('localized_end_serrations', '局部端面密集齿形'),
    ('repeated_edge_notches', '闭环内缘重复凹齿'),
    ('compact_concave', '复杂凹槽截面'),
    ('perforated_panel', '大开窗薄板'),
    ('dense_holes', '紧凑多孔槽件'),
    ('mixed_depth_apertures', '多类或分层孔槽'),
    ('multiaxis_annotation_gap', '多轴分层孔与未解释内轮廓'),
    ('parallel_members', '异形双纵梁与端座'),
    ('multi_rod_end_blocks', '圆筒与多杆端座'),
    ('topology', '复杂孔槽拓扑'),
    ('high_density', '高密度紧凑实体'),
    ('paired_protrusions', '主体两侧局部凸起'),
    ('dish_stem_transition', '盘体与杆部截面突变'),
    ('broad_profile', '复杂截面（扩展）'),
    ('topology_recall', '孔槽拓扑（扩展）'),
    ('shallow_recess', '浅表细节（扩展）'),
]


def load(name: str):
    return json.loads((WORK / name).read_text())


def current_case_source(case: dict) -> tuple[Path, dict]:
    """Select the spec whose family labels match the current input PDF."""
    import fitz

    case_name = case['name']
    pdf = HARBOR / case_name / 'environment/input/parts.pdf'
    if not pdf.is_file():
        raise FileNotFoundError(pdf)
    page_families = {}
    with fitz.open(pdf) as document:
        for page in document:
            text = page.get_text()
            if page.number == 0 or text.startswith('FAMILY INDEX'):
                continue
            matches = list(re.finditer(r'\bFAMILY_\d+\b', text))
            for i, match in enumerate(matches):
                label = match.group().lower()
                if label in page_families:
                    raise ValueError(f'Duplicate PDF family page: {case_name} {label}')
                segment = text[match.end():matches[i + 1].start() if i + 1 < len(matches) else None]
                quantity = re.search(r'QTY\s+(\d+)', segment)
                dimensions = re.search(r'LOCAL-AXIS X x Y x Z:\s*([\d.]+) x ([\d.]+) x ([\d.]+)', segment)
                page_families[label] = {
                    'quantity': int(quantity.group(1)) if quantity else None,
                    'dimensions': sorted(float(v) for v in dimensions.groups()) if dimensions else None,
                }
    candidates = ([Path(case['parts_workdir'])] if case.get('parts_workdir')
                  else [WORK / f"parts_{case['index']}_v2", WORK / f"parts_{case['index']}"])
    for root in candidates:
        spec_path = root / 'part-spec.json'
        if not spec_path.is_file():
            continue
        spec = json.loads(spec_path.read_text())
        if set(page_families) != {f['id'] for f in spec['families']}:
            continue
        manifest = {f['id']: f for f in json.loads((root / 'aligned/manifest.json').read_text())['families']}
        for family in spec['families']:
            fid = family['id']
            page = page_families[fid]
            if page['quantity'] is not None and page['quantity'] != family['quantity']:
                raise ValueError(f'PDF/spec quantity mismatch: {case_name} {fid}')
            if page['dimensions'] is not None:
                expected = sorted(manifest[fid]['dimensions_xyz_mm'])
                if any(abs(a - b) > max(.5, .01 * b)
                       for a, b in zip(page['dimensions'], expected)):
                    raise ValueError(f'PDF/spec dimensions mismatch: {case_name} {fid}')
        return root, spec
    raise ValueError(f'No part-spec matches current parts.pdf for {case_name}; regenerate or remap before preview')


def write_shallow_review(case_count: int, family_count: int,
                         excluded: set[tuple[str, str]],
                         visual_watchlist: bool = True) -> None:
    rows = load('shallow_text_scan.json')
    lines = [
        '# 浅凹面文字筛选（高召回）', '',
        f'{case_count} 题、现版 {family_count} 个 family：'
        f'放宽浅表几何判据得到 {len(rows)} 个初筛命中，'
        f'其中 {len(excluded)} 个简单形状已从主清单排除；'
        f'其中 {sum(r["text_candidate"] for r in rows)} 个在 FRONT/SIDE/TOP '
        '至少一个视图中，OCR 字框与同一视图的浅凹面投影区域重合。'
        'OCR 未命中的浅表候选仍保留在高召回清单中。', '',
        '几何门槛：距外表面 0.03–0.8 mm 的第二平面，外表面与第二平面分别至少有'
        ' 500/200 个网格顶点引用和 60/30 个共面三角面。'
        '空间命中要求同一视图中的 OCR 片段含至少 3 个连续拉丁字母，'
        '字框与浅凹面投影至少重合 15 个像素且占字框面积至少 2%。'
        '这是疑似文字信号，不是准确的铭文转录，也不单独决定是否提供 STEP。', '',
        '|题目|family|原 PDF 页|层差 mm|重合的 OCR 片段|状态|',
        '|---|---|---:|---:|---|---|',
    ]
    for row in sorted(rows, key=lambda r: (r['case'], r['family'])):
        tokens = '、'.join(token.replace('|', '/') for token in row['text_region_tokens']) or '无'
        fid = row['family']
        if (row['case'], fid) in excluded:
            status = '简单形状；已从主清单排除'
        else:
            status = '空间重合；疑似文字' if row['text_candidate'] else '仅浅凹几何；待复核'
        lines.append(f"|{row['case']}|{fid}|{row['source_page']}|"
                     f"{row['depth_mm']:.3f}|{tokens}|{status}|")
    if visual_watchlist:
        lines += ['', f'另对现版全部 {family_count} 个 family 的 FRONT/SIDE/TOP 视图独立做 OCR；'
                  '该结果只写入 `visual_text_watchlist.md`，不能单独使零件入选。'
                  'OCR 会误读几何边、孔和尺寸线；其他朝向或非拉丁文字仍可能漏检。', '']
    else:
        lines += ['', '独立的全量视图 OCR 观察清单已跳过；浅凹区域重合 OCR 仍正常运行。', '']
    (WORK / 'shallow_text_candidates.md').write_text('\n'.join(lines))


def main(reuse_scans: bool = False, no_preview: bool = False,
         skip_visual_watchlist: bool = False) -> None:
    cases = load('selected.json')
    configured = {case['name'] for case in cases}
    present = {path.name for path in HARBOR.glob('cad_*') if path.is_dir()}
    if configured != present or len(cases) != len(configured):
        raise ValueError(f'Case inventory mismatch: configured={sorted(configured)}, '
                         f'Harbor={sorted(present)}')
    if not reuse_scans:
        for script in SCANNERS:
            if skip_visual_watchlist and script == 'scan_visual_text.py':
                (WORK / 'visual_text_scan.json').write_text('[]')
                continue
            print(f'RUN {script}', flush=True)
            subprocess.run([sys.executable, str(SCRIPTS / script)], check=True)
    elif skip_visual_watchlist:
        (WORK / 'visual_text_scan.json').write_text('[]')

    entries: dict[tuple[str, str], dict] = {}
    shape_context: dict[tuple[str, str], dict] = {}
    case_roots = {}
    total_families = 0

    def add_current(case: str, family: str, reason: str) -> None:
        entries[(case, family)]['reasons'].add(reason)

    for case in cases:
        case_name = case['name']
        root, spec = current_case_source(case)
        case_roots[case_name] = root
        catalog = json.loads((root / 'geometry/catalog.json').read_text())['parts']
        manifest = json.loads((root / 'aligned/manifest.json').read_text())['families']
        by_family = {row['id']: row for row in manifest}
        occurrence = {row['id']: row for row in spec['occurrences']}
        if len(spec['families']) != len(manifest):
            raise ValueError(f"Spec/manifest family mismatch: {case_name}")
        total_families += len(spec['families'])
        for family in spec['families']:
            fid = family['id']
            if fid not in by_family:
                raise ValueError(f"Missing aligned mesh: {case_name} {fid}")
            dims = family['sorted_size_mm']
            aspect = float(dims[-1] / dims[0])
            representative = occurrence[family['occurrence_ids'][0]]
            metrics = representative['mechanical_metrics']
            euler = int(metrics['euler_number'])
            source_part = catalog[representative['reference_index']]
            source_size = sorted(source_part['size'])
            representative_size = sorted(representative['size_wdh_mm'])
            if any(abs(a - b) > max(.1, .01 * b)
                   for a, b in zip(source_size, representative_size)):
                raise ValueError(f"Source/catalog mismatch: {case_name} {fid}")
            faces = int(source_part['triangles'])
            entries[(case_name, fid)] = {
                'case': case_name, 'family': fid, 'quantity': int(family['quantity']),
                'euler': euler, 'triangles': faces, 'aspect': aspect,
                'reasons': set(),
            }
            aligned = by_family[fid]
            section_change = aligned.get('local_shape_change', {})
            shape_context[(case_name, fid)] = {
                'watertight': bool(metrics['watertight']),
                'fill': float(family['bbox_fill_ratio']),
                'span_change': section_change.get('span_change_ratios') or [],
                # Scale-free surface-to-volume measure; unlike A/V in mm^-1,
                # it can be compared across parts of different sizes.
                'surface_compactness': (float(family['surface_area_mm2'])
                                        / float(family['volume_mm3']) ** (2 / 3)
                                        if family['volume_mm3'] > 0 else float('inf')),
            }
            if metrics['watertight'] and euler <= -60:
                add_current(case_name, fid, 'topology')
            elif metrics['watertight'] and euler <= -40:
                add_current(case_name, fid, 'topology_recall')
            if (faces >= 150_000 and family['bbox_fill_ratio'] >= .1
                    and aspect <= 3 and euler < 0):
                add_current(case_name, fid, 'high_density')

    mapping = {
        'periodic_radial_profile_scan.json': 'periodic_radial_profile',
        'localized_serration_scan.json': 'localized_end_serrations',
        'repeated_edge_notch_scan.json': 'repeated_edge_notches',
        'dense_hole_scan.json': 'dense_holes',
        'perforated_panel_scan.json': 'perforated_panel',
        'curved_profile_scan.json': 'curved_profile',
        'paired_protrusion_scan.json': 'paired_protrusions',
    }
    for filename, reason in mapping.items():
        for row in load(filename):
            add_current(row['case'], row['family'], reason)
    for row in load('mixed_depth_apertures_scan.json'):
        add_current(row['case'], row['family'], 'mixed_depth_apertures')
    for row in load('multiaxis_annotation_gap_scan.json'):
        add_current(row['case'], row['family'], 'multiaxis_annotation_gap')
    for row in load('longitudinal_geometry_scan.json'):
        for reason in row['signals']:
            add_current(row['case'], row['family'], reason)
    for row in load('profile_scan.json'):
        shape = shape_context[(row['case'], row['family'])]
        span_change = shape['span_change']
        # A broad formed head changing into a narrow stem is missed by
        # mid-shaft profile medians. Require two-axis section collapse and
        # compact material to distinguish brackets, gutters and springs.
        if (shape['watertight'] and row['aspect'] <= 2
                and shape['fill'] <= .08
                and len(span_change) >= 2 and min(span_change) >= .75
                and shape['surface_compactness'] <= 20):
            add_current(row['case'], row['family'], 'dish_stem_transition')
        if row['profile_candidate']:
            add_current(row['case'], row['family'], row['profile_class'])
        # Concavity and section variation also describe simple bent sheets
        # and stepped turned parts.  The relaxed profile gate requires many
        # significant reflex corners plus a distinctly longer contour than
        # its convex hull; all three section samples contribute via medians.
        if (row['median_reflex'] >= 20
                and row['median_deficit'] >= .12
                and row['median_perimeter'] >= 1.25):
            add_current(row['case'], row['family'], 'broad_profile')
    for row in load('shallow_text_scan.json'):
        if row['text_candidate']:
            add_current(row['case'], row['family'], 'shallow_text')
    for row in load('shallow_recess_scan.json'):
        add_current(row['case'], row['family'], 'shallow_recess')
    visual_hits = {(row['case'], row['family']): row
                   for row in load('visual_text_scan.json') if row['text_candidate']}

    # Suppress a weak reason only when it is the sole reason and the geometry
    # has a repeatable simple construction.  Independent difficulty signals
    # (including overlapping OCR) always keep their candidate.
    import trimesh
    from shape_simplifiers import (
        simple_annular_ring, simple_turned_cylinder,
        regular_two_row_perforation,
    )

    simple_shape_exclusions = []
    for (case_name, fid), entry in sorted(entries.items()):
        reasons = entry['reasons']
        if reasons not in ({'shallow_recess'}, {'topology_recall'}):
            continue
        mesh = trimesh.load_mesh(case_roots[case_name] / 'aligned' / f'{fid}.stl',
                                 process=False)
        label = None
        if reasons == {'shallow_recess'} and simple_annular_ring(mesh, entry['euler']):
            label = '少量同心台阶的简单环件'
        elif reasons == {'shallow_recess'} and simple_turned_cylinder(mesh, entry['euler']):
            label = '少量径向台阶的简单旋转柱件'
        elif reasons == {'topology_recall'} and regular_two_row_perforation(mesh):
            label = '两排等大、等距的重复小孔'
        if label:
            simple_shape_exclusions.append((case_name, fid, label))
            reasons.clear()

    exclusion_lines = [
        '# 简单形状排除记录', '',
        '仅当浅凹面或放宽拓扑为唯一入选原因，且模型满足通用的简单旋转体/规则排孔特征时排除。'
        '若同时有文字、柔性引线、复杂曲线或其他独立命中，则保留在主清单。', '',
        '|题目|family|排除依据|', '|---|---|---|',
    ]
    exclusion_lines += [f'|{case}|{fid}|{label}|'
                        for case, fid, label in simple_shape_exclusions]
    exclusion_lines.append('')
    (WORK / 'simple_shape_exclusions.md').write_text('\n'.join(exclusion_lines))
    write_shallow_review(len(cases), total_families,
                         {(case, fid) for case, fid, _ in simple_shape_exclusions},
                         not skip_visual_watchlist)

    rows = []
    for key in sorted(entries):
        row = entries[key]
        if not row['reasons']:
            continue
        row['reasons'] = [reason for reason, _ in REASON_PRIORITY
                          if reason in row['reasons']]
        row['reason'] = next(label for reason, label in REASON_PRIORITY
                             if reason == row['reasons'][0])
        row['screen_level'] = ('strong' if any(reason in row['reasons'] for reason in
                               ('shallow_text', 'flexible_tail', 'curved_end_tube',
                                'curved_profile',
                                'complex_extrusion', 'compact_concave',
                                'periodic_radial_profile',
                                'localized_end_serrations',
                                'repeated_edge_notches',
                                'perforated_panel', 'dense_holes', 'topology',
                                'mixed_depth_apertures', 'parallel_members',
                                'multi_rod_end_blocks',
                                'high_density', 'paired_protrusions',
                                'multiaxis_annotation_gap',
                                'dish_stem_transition')) else 'expanded')
        row['visual_ocr_signal'] = key in visual_hits
        rows.append(row)

    DATA.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    selected_by_case = {}
    for row in rows:
        selected_by_case.setdefault(row['case'], []).append(row['family'])
    STEP_SELECTION.write_text(json.dumps({
        'schema': 'cad-complexity-screen/step-selection-v1',
        'cases': [{
            'case': case['name'],
            'part_spec_sha256': hashlib.sha256(
                (case_roots[case['name']] / 'part-spec.json').read_bytes()
            ).hexdigest(),
            'source_step_sha256': json.loads(
                (case_roots[case['name']] / 'geometry/catalog.json').read_text()
            )['source_sha256'],
            'families': sorted(selected_by_case.get(case['name'], [])),
        } for case in cases],
    }, ensure_ascii=False, indent=2))
    candidate_keys = {(row['case'], row['family']) for row in rows}
    watch = [
        '# 视图 OCR 可疑文字清单（不作为入选条件）', '',
        ('本次跳过独立全量视图 OCR；浅凹区域重合 OCR 不受影响。' if skip_visual_watchlist else
         'OCR 来自 parts.pdf 的 FRONT/SIDE/TOP 模型裁图，macOS Vision 识别结果可能把孔、边和尺寸线当成字。'),
        '这些片段仅供人工查看，不证明 STEP 中有刻字，也不会单独使零件进入复杂零件候选。', '',
        '|题目|family|OCR 片段|是否由几何规则入选|',
        '|---|---|---|---|',
    ]
    for key, visual in sorted(visual_hits.items()):
        snippets = '、'.join(f"{token['view']}:{token['text'].replace('|', '/')}"
                            for token in visual['ocr_tokens'])
        watch.append(f"|{key[0]}|{key[1]}|{snippets}|"
                     f"{'是' if key in candidate_keys else '否；仅列观察清单'}|")
    watch.append('')
    (WORK / 'visual_text_watchlist.md').write_text('\n'.join(watch))
    expanded_count = sum(row['screen_level'] == 'expanded' for row in rows)
    lines = [
        '# 复杂零件自动筛选候选（高召回）', '',
        f'从 {len(cases)} 题、{total_families} 个 family 自动扫描得到 {len(rows)} 个候选；'
        f'其中 {expanded_count} 个仅由放宽规则命中。'
        '独立运行时供优先复核；parts-pdf 默认把全部入选 family '
        '写入带来源哈希的 complexity_step_selection.json 供导出。', '',
        '流水线：拓扑/网格密度、多孔槽与分层沉孔、纵向多构件和弯管、大开窗薄板、复杂截面、'
        '主体两侧局部凸起、盘体到杆部的截面突变、密集周期齿形与闭环内缘重复凹齿、'
        '浅凹面与同视图局部 OCR 重合。所有规则直接使用与现版 parts.pdf 匹配的 family spec；'
        '候选不依赖人工预览清单或特定 family 编号。高召回扩展将 Euler 门槛放宽至 -40，'
        '截面扩展规则同时要求至少 20 个凹转角、凹度和轮廓周长证据，单纯折弯或台阶变化不足以入选；'
        '仅由浅凹或放宽拓扑命中的简单旋转体、两排规则等距孔板会排除；'
        '其余可疑浅表细节即使 OCR 未读出字也保留。',
        ('独立的全量 FRONT/SIDE/TOP OCR 观察清单本次跳过；' if skip_visual_watchlist else
         '全量 FRONT/SIDE/TOP OCR 只写入独立观察清单，不单独决定入选；'),
        '在浅凹几何已命中时，只有 OCR 字框与该视图浅凹面投影区域重合，才标注疑似文字。'
        'OCR 结果不是铭文的可信转录。文字可因朝向、刻字深度、语言或图像清晰度而漏检；'
        '规则形状也可能误报，需逐件复核。', '',
        '|题目|family|数量|Euler|三角面|长短轴比|原因|',
        '|---|---|---:|---:|---:|---:|---|',
    ]
    for row in rows:
        lines.append(f"|{row['case']}|{row['family']}|{row['quantity']}|{row['euler']}|"
                     f"{row['triangles']}|{row['aspect']:.2f}|{row['reason']}|")
    lines += ['', '各候选的全部命中规则见 `complexity_auto_candidates.json`。', '']
    REPORT.write_text('\n'.join(lines))
    print(f'{total_families} families; {len(rows)} candidates; '
          f'{expanded_count} expanded-only')
    if not no_preview:
        subprocess.run([sys.executable, str(SCRIPTS / 'build_complexity_preview.py'),
                        '--report', str(REPORT), '--pdf-out', str(PDF),
                        '--image-out', str(OVERVIEW)], check=True)
        subprocess.run([sys.executable, str(SCRIPTS / 'verify_complexity_screen.py')],
                       check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reuse-scans', action='store_true',
                        help='Use existing scan JSONs instead of recomputing geometry/OCR.')
    parser.add_argument('--no-preview', action='store_true',
                        help='Write report/JSON only; skip PDF and PNG previews.')
    parser.add_argument('--skip-visual-watchlist', action='store_true',
                        help='Skip independent full-page OCR; keep shallow-region OCR.')
    args = parser.parse_args()
    if args.skip_visual_watchlist and not args.no_preview:
        parser.error('--skip-visual-watchlist requires --no-preview')
    main(args.reuse_scans, args.no_preview, args.skip_visual_watchlist)
