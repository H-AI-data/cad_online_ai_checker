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
        euler=metrics['euler_number']
        if not metrics['watertight'] or euler>-30:continue
        mesh=trimesh.load_mesh(root/'aligned'/f"{family['id']}.stl",process=False)
        aspect=float(max(mesh.extents)/min(mesh.extents))
        faces=len(mesh.faces)
        if aspect>5 or faces<20000:continue
        rows.append(dict(case=case['name'],index=case['index'],family=family['id'],
                         quantity=family['quantity'],euler=euler,triangles=faces,
                         local_aspect=aspect))
(work/'dense_hole_scan.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False))
print('hits',len(rows))
for row in rows:print(row)
