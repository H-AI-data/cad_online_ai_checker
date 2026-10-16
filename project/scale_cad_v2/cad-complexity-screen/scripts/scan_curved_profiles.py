import json
from pathlib import Path
from config import WORK
import statistics
import numpy as np
import trimesh
from profile_probe import closed_rdp
from run_complexity_screen import current_case_source

work = WORK
profiles=json.loads((work/'profile_scan.json').read_text())
rows=[]
spec_cache={}
case_by_name={case['name']:case for case in json.loads((work/'selected.json').read_text())}
for row in profiles:
    if row['median_reflex']<3 or row['median_deficit']<.08 or row['median_perimeter']<1.04:
        continue
    if row['case'] not in spec_cache:
        root,spec=current_case_source(case_by_name[row['case']])
        spec_cache[row['case']]=(root,{f['id']:f for f in spec['families']})
    root,families=spec_cache[row['case']]
    family=families[row['family']]
    mesh=trimesh.load_mesh(root/'aligned'/f"{row['family']}.stl",process=False)
    dims=sorted(float(v) for v in mesh.extents)
    length_to_width=dims[2]/dims[1]
    local_fill=family['volume_mm3']/float(np.prod(mesh.extents))
    if length_to_width<6 or local_fill<.5:continue
    axis=int(np.argmax(mesh.extents))
    normal=np.zeros(3);normal[axis]=1
    scores=[]
    for fraction in (.3,.5,.7):
        origin=mesh.bounds[0].copy();origin[axis]+=fraction*mesh.extents[axis]
        section=mesh.section(plane_origin=origin,plane_normal=normal)
        if section is None:continue
        loops=[]
        for path in section.discrete:
            uv=np.asarray(path)[:,[j for j in range(3) if j!=axis]]
            if len(uv)<4:continue
            nxt=np.roll(uv,-1,axis=0)
            area=abs(np.sum(uv[:,0]*nxt[:,1]-uv[:,1]*nxt[:,0]))/2
            loops.append((area,uv))
        if not loops:continue
        uv=max(loops,key=lambda x:x[0])[1]
        scores.append(len(closed_rdp(uv,.1))-len(closed_rdp(uv,.5)))
    if len(scores)<3 or min(scores)<6:continue
    rows.append(dict(case=row['case'],index=row['index'],family=row['family'],
                     quantity=row['quantity'],length_to_width=length_to_width,
                     local_fill=local_fill,reflex=row['median_reflex'],
                     deficit=row['median_deficit'],perimeter=row['median_perimeter'],
                     curve_scores=scores,median_curve_score=statistics.median(scores)))
(work/'curved_profile_scan.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False,default=lambda x:x.item()))
print('hits',len(rows))
for row in rows:print(row)
