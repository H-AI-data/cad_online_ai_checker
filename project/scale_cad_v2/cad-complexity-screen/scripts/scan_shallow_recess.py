"""Screen aligned STL meshes for large, shallow, two-level planar surface details."""
import json
from pathlib import Path
from config import WORK
import numpy as np
import trimesh
from run_complexity_screen import current_case_source

selected = json.loads((WORK/'selected.json').read_text())
results=[]
for case in selected:
    root,_=current_case_source(case)
    manifest=json.loads((root/'aligned/manifest.json').read_text())['families']
    for family in manifest:
        mesh=trimesh.load_mesh(root/'aligned'/family['mesh'],process=False)
        tri=mesh.triangles
        best=None
        for axis in range(3):
            span=mesh.extents[axis]
            if span<2:continue
            for side in (-1,1):
                outer=mesh.bounds[0 if side==-1 else 1,axis]
                depths=np.round((tri[:,:,axis]-outer)*-side,3)
                values,counts=np.unique(depths[(depths>=-.001)&(depths<=.8)],return_counts=True)
                outer_count=int(counts[np.argmin(np.abs(values))]) if len(values) and abs(values[np.argmin(np.abs(values))])<.002 else 0
                if outer_count<500:continue
                floor_options=[]
                for value,count in zip(values,counts):
                    if .03<=value<=.8 and count>=200:
                        floor_options.append((int(count),float(value)))
                if not floor_options:continue
                floor_count,depth=max(floor_options)
                outer_tri=int(np.sum(np.all(np.abs(depths)<.002,axis=1)))
                floor_tri=int(np.sum(np.all(np.abs(depths-depth)<.002,axis=1)))
                if outer_tri<60 or floor_tri<30:continue
                signal=min(outer_count,floor_count)
                item=dict(case=case['name'],index=case['index'],family=family['id'],quantity=family['quantity'],
                          axis=axis,side=side,depth_mm=depth,outer_vertex_references=outer_count,
                          floor_vertex_references=floor_count,outer_planar_triangles=outer_tri,
                          floor_planar_triangles=floor_tri,signal=signal)
                if best is None or item['signal']>best['signal']:best=item
        if best:results.append(best)
    print(case['name'],'done',len(manifest),'families',flush=True)
results.sort(key=lambda r:-r['signal'])
(WORK/'shallow_recess_scan.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print('hits',len(results))
for r in results:print(r)
