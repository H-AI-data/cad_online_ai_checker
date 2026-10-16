import json
from pathlib import Path
from config import WORK
import trimesh
from run_complexity_screen import current_case_source

work = WORK
selected=json.loads((work/'selected.json').read_text())
rows=[]
for case in selected:
    root,spec=current_case_source(case)
    occurrence={o['id']:o for o in spec['occurrences']}
    for family in spec['families']:
        metrics=occurrence[family['occurrence_ids'][0]]['mechanical_metrics']
        if not metrics['watertight'] or metrics['euler_number']>-30:continue
        mesh=trimesh.load_mesh(root/'aligned'/f"{family['id']}.stl",process=False)
        dims=sorted(float(x) for x in mesh.extents)
        flatness=dims[1]/dims[0]
        planar_aspect=dims[2]/dims[1]
        local_fill=family['volume_mm3']/(dims[0]*dims[1]*dims[2])
        triangles=len(mesh.faces)
        if not (flatness>=5 and planar_aspect<=4 and triangles>=20000
                and .2<=local_fill<=.75):continue
        rows.append(dict(case=case['name'],index=case['index'],family=family['id'],
                         quantity=family['quantity'],euler=metrics['euler_number'],
                         triangles=triangles,flatness=flatness,planar_aspect=planar_aspect,
                         local_fill=local_fill))
(work/'perforated_panel_scan.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False))
print('hits',len(rows))
for row in rows:print(row)
