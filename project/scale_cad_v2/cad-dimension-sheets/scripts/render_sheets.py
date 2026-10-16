"""Render configured assembly groups, additive dimensions and bore leaders to PDF."""
import argparse
import json
import os
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import drawing as d


def order_sheets(groups):
    """Keep each parent's details immediately after it, preserving sibling order."""
    ids = [g['id'] for g in groups if 'id' in g]
    if len(ids) != len(set(ids)):
        raise ValueError('Sheet IDs must be unique')
    parents = {g['id']: g for g in groups if 'id' in g}
    for g in groups:
        if 'detail_of' in g:
            parent = parents.get(g['detail_of'])
            if parent is None or 'detail_of' in parent:
                raise ValueError('detail_of must reference a non-detail parent sheet ID')
    result = []
    for parent in groups:
        if 'detail_of' in parent:
            continue
        result.append(parent)
        if 'id' in parent:
            result.extend(g for g in groups if g.get('detail_of') == parent['id'])
    return result


def select(panel, key='families'):
    ids = panel.get(key)
    result = d.PARTS if ids is None else d.families(ids)
    if not result:
        raise ValueError(f'Empty family selection: {ids}')
    if ids is not None:
        found = {int(p['family'][-3:]) for p in result}
        if set(ids)-found:
            raise ValueError(f'Unknown families: {set(ids)-found}')
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', type=Path, required=True, help='Directory from extract_step.py')
    ap.add_argument('--plan', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--font', type=Path, help='CJK TrueType font; detected by default')
    args = ap.parse_args()
    if args.font is None:
        args.font = next((Path(value) for value in
                          (os.environ.get('CAD_CJK_FONT'),
                           '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
                           '/opt/X11/share/system_fonts/Supplemental/Arial Unicode.ttf')
                          if value and Path(value).is_file()), None)
    if not args.font or not args.font.is_file():
        ap.error('CJK font not found; pass --font or set CAD_CJK_FONT')
    data = json.loads((args.data/'manual_geometry.json').read_text())
    if data['units'] != 'mm':
        raise ValueError('Only mm geometry is accepted')
    d.IDENTITY_PREFIX = 'F' if 'mapping_max_center_error_mm' in data else 'S'
    d.PARTS = data['parts']
    if any('silhouettes' not in part for part in d.PARTS):
        raise ValueError('Geometry cache lacks surface outlines; rerun extract_step.py')
    d.BORES = json.loads((args.data/'bores.json').read_text())
    plan = json.loads(args.plan.read_text())
    groups = order_sheets(plan['groups'])
    if not 1 <= len(groups) <= int(plan.get('max_sheets_per_view',10)) <= 10:
        raise ValueError('Use at most 10 sheets per view, including enlarged details')
    d.CHAIN_PADDING = float(plan.get('chain_padding_pt', 20))
    d.MIN_STATION_MM = float(plan.get('min_station_mm', .5))
    if d.CHAIN_PADDING < 10 or d.MIN_STATION_MM <= 0:
        raise ValueError('Padding must be >=10 pt; station tolerance must be positive')
    pdfmetrics.registerFont(TTFont('CJK', str(args.font)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    d.c = canvas.Canvas(str(args.output), pagesize=(d.W, d.H), pageCompression=1)
    d.c.setTitle(plan.get('title', 'CAD Assembly Dimensions'))
    for view, axes in [('FRONT / 前视', (0, 2)), ('SIDE / 侧视', (1, 2)), ('TOP / 顶视', (0, 1))]:
        a, b = axes
        for n, original_group in enumerate(groups, 1):
            group = dict(original_group)
            group.update(original_group.get('view_overrides', {}).get(view.split(' / ')[0], {}))
            if d.page:
                d.c.showPage()
            d.page += 1
            before = len(d.log)
            d.text(55, d.H-52, plan.get('title', 'CAD'), 27)
            d.text(315, d.H-52, f'{view} {n}/{len(groups)} | {group["title"]}', 23)
            d.text(55, d.H-88, '装配投影尺寸；零件造型细节请结合 parts.pdf。', 15)
            d.text(d.W-55, d.H-87, 'mm / ° | NTS', 15, d.blue, 'end')
            d.line((55, d.H-106), (d.W-55, d.H-106), d.gray)
            panels = group.get('panels', [group])
            if not 1 <= len(panels) <= 2:
                raise ValueError('Use one or two assembly panels per page')
            for j, panel in enumerate(panels):
                ps = select(panel)
                box = (315, 310, 1010, 665) if len(panels) == 1 else (145+j*785, 330, 515, 530)
                pt, sc, lo, hi = d.draw(ps, axes, box, panel.get('subtitle', ''),bounds=panel.get('crop'))
                for region in panel.get('detail_regions', []):
                    corners=[]
                    for xy in region['bounds']:
                        v=[0,0,0];v[a],v[b]=xy;corners.append(pt(v))
                    (x0,y0),(x1,y1)=corners
                    for p,q in [((x0,y0),(x1,y0)),((x1,y0),(x1,y1)),((x1,y1),(x0,y1)),((x0,y1),(x0,y0))]:
                        d.line(p,q,d.blue,1.4,[6,4])
                    d.text(x1+8,y1+8,region['label'],22,d.blue)
                if panel.get('crop'):
                    d.text(55,d.H-120,'局部放大；只标裁框内的真实特征，未旋转零件。',16,d.blue)
                if panel.get('dimension_mode', plan.get('dimension_mode', 'legacy')) == 'detailed':
                    d.detailed_dimensions(ps, axes, pt, panel)
                    continue
                d.hd(d.extrema(ps, a), d.extrema(ps, a, True), 280, pt, a, 'group width')
                d.vd(d.extrema(ps, b), d.extrema(ps, b, True), box[0]-65, pt, b, 'group height')
                chain_parts = select(panel, 'chain_families') if 'chain_families' in panel else ps
                if {p['id'] for p in chain_parts}-{p['id'] for p in ps}:
                    raise ValueError('Chain endpoints must belong to drawn parts')
                extra = []
                for shaft in panel.get('shafts', []):
                    candidates = d.families([shaft['family']])
                    if len(candidates) != 1:
                        raise ValueError('Shaft family must identify one occurrence')
                    extra.append(d.axis_point(candidates[0], shaft['axis']))
                d.small_chain(chain_parts, axes, pt, 965, extra)
                xx = 1400 if len(panels) == 1 else 630+j*785
                d.hole_labels(ps, axes, pt, xx, 925, panel.get('max_bores', 5))
                # Explicit interval dimensions keep semantically important short distances.
                for measurement in panel.get('distances', []):
                    axis = measurement['axis']
                    if axis not in (0, 1, 2):
                        raise ValueError('Dimension axis must be 0, 1 or 2')
                    if axis not in axes:
                        continue
                    anchors = []
                    for endpoint in measurement['ends']:
                        if endpoint['end'] not in ('min', 'max'):
                            raise ValueError('Endpoint end must be min or max')
                        matches = [p for p in ps if p['id'] == endpoint['part']]
                        if len(matches) != 1:
                            raise ValueError('Dimension endpoint is not a unique visible part')
                        anchors.append(d.feature_anchor(matches[0], axis, endpoint['end'] == 'max'))
                    if len(anchors) != 2:
                        raise ValueError('Distance requires two endpoints')
                    if axis == a:
                        d.hd(*anchors, 230, pt, axis, measurement.get('label', 'projected distance'))
                    else:
                        d.vd(*anchors, box[0]-100, pt, axis, measurement.get('label', 'projected distance'))
            count=len(d.log)-before
            if plan.get('enforce_dimension_budget',False) and not plan.get('min_annotations',8) <= count <= 20:
                raise ValueError(f'Page {d.page}: {count} annotations; revise selection to 8–20')
            d.render_annotations()
            d.text(55, 54, '普通图省略小于所示零件最长尺寸 1% 的标注；局部图按裁框尺度筛选。未标短段不可视为零。', 12)
            d.text(55, 32, 'Ø 表示直径，数值取自解析圆柱面，不推断孔贯穿或齿轮节圆；S 为源实体编号。', 11)
            d.text(d.W-55, 32, f'{d.page} | {len(d.log)-before} 项标注', 12, d.blue, 'end')
    d.c.save()
    args.output.with_suffix('.dimensions.json').write_text(json.dumps(d.log, ensure_ascii=False, indent=2))
    args.output.with_suffix('.routing.json').write_text(json.dumps(d.routing, indent=2))
    print(json.dumps({'pdf': str(args.output), 'pages': d.page, 'dimensions': len(d.log)}))


if __name__ == '__main__':
    main()
