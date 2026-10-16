import json
from pathlib import Path
from config import WORK
import statistics
import trimesh
from profile_probe import profile
from run_complexity_screen import current_case_source

work = WORK
selected=json.loads((work/'selected.json').read_text())
rows=[]
for case in selected:
    idx=case['index']; name=case['name']
    root,spec=current_case_source(case)
    manifest=json.loads((root/'aligned/manifest.json').read_text())['families']
    spec_by_family={f['id']:f for f in spec['families']}
    for fam in manifest:
        mesh=trimesh.load_mesh(root/'aligned'/fam['mesh'],process=False)
        sections=[]
        for fraction in (.3,.5,.7):
            try:
                loops=profile(mesh,fraction)
                sections.append(loops[0] if loops else None)
            except Exception as exc:
                sections.append(None)
        valid=[x for x in sections if x]
        if valid:
            reflex=statistics.median(x[1] for x in valid)
            deficit=statistics.median(x[3] for x in valid)
            perimeter=statistics.median(x[4] for x in valid)
        else:
            reflex=deficit=perimeter=0
        dims=spec_by_family[fam['id']]['sorted_size_mm']
        aspect=dims[-1]/dims[0]
        fill=spec_by_family[fam['id']]['bbox_fill_ratio']
        compact=(len(valid)>=2 and .18<=deficit<=.45
                 and perimeter>=1.18 and aspect<=4
                 and (reflex>=8 or (reflex>=6 and fill>=.4)))
        extrusion=(len(valid)>=2 and reflex>=30 and deficit>=.45
                   and perimeter>=1.6 and aspect>4)
        flagged=compact or extrusion
        rows.append(dict(case=name,index=idx,family=fam['id'],quantity=fam['quantity'],sections=sections,
                         median_reflex=reflex,median_deficit=deficit,median_perimeter=perimeter,
                         aspect=aspect,bbox_fill=fill,profile_candidate=bool(flagged),
                         profile_class=('complex_extrusion' if extrusion else 'compact_concave' if compact else None)))
    print(name,'done',len(manifest),'families',flush=True)

(work/'profile_scan.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False,default=lambda x:x.item()))
hits=[r for r in rows if r['profile_candidate']]
print('total',len(rows),'profile candidates',len(hits))
for r in hits: print(r['case'],r['family'],r['quantity'],r['profile_class'],r['median_reflex'],r['median_deficit'],r['median_perimeter'])
