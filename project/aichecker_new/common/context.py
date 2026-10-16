"""本轮共享证据准备；只有门禁通过且路由确定后才能调用。"""
from pathlib import Path
import sys
from .io import result
from .checks import pair_documents,combine
from .drawings import render_drawing
from .geometry import match_families
from modules.module_01.dimension_01_correspondence.checker import check_solidworks
from modules.module_01.dimension_02_complexity.engine import screen_complexity


def prepare(module,selection,inventory,models,judge,output,max_pages,complexity_options=None):
    steps,drawings=inventory['steps'],inventory['drawings']
    ctx={'models':models,'documents':{},'stats':{},'membership_visual':[]}
    remaining=max_pages
    def documents(paths):
        nonlocal remaining
        for path in paths:
            key=str(path)
            if key in ctx['documents']:
                continue
            print(f'读取工程图：{path.name}',file=sys.stderr,flush=True)
            info=render_drawing(path,output/'drawings'/f'file_{len(ctx["documents"])+1:04d}',remaining)
            remaining=max(0,remaining-len(info['pages']))
            ctx['documents'][key]=info
    def pairing(model_paths,drawing_paths):
        if model_paths and len(model_paths)==len(drawing_paths):
            documents(drawing_paths)
        return pair_documents(model_paths,drawing_paths,models,ctx['documents'],judge)
    if module=='2':
        a,n=Path(inventory['assembly_dir']),Path(inventory['nonstandard_dir'])
        assembly_steps=[p for p in steps if a in p.parents]
        nonstandard_steps=[p for p in steps if n in p.parents]
        if '1' in selection:
            ctx['correspondence']=combine([
                pairing(assembly_steps,[p for p in drawings if a in p.parents]),
                pairing(nonstandard_steps,[p for p in drawings if n in p.parents])])
        assembly=models[str(assembly_steps[0])]
        parts=[models[str(p)] for p in nonstandard_steps]
        membership=match_families(assembly,parts)
        if not parts:
            membership['complete']=False
        ctx['membership']=membership
        total=len(assembly['families']);nonstandard=len(set(membership['matched_family_ids']))
        ctx['stats']=dict(total=total,nonstandard=nonstandard,standard=total-nonstandard,
            ratio=nonstandard/total if total else None,complete=membership['complete'],
            merge_policy='mechanical-seven-v2',matched_family_ids=membership['matched_family_ids'],
            foreign=membership['foreign'],ambiguous=membership['ambiguous'])
        if '3' in selection and not membership['foreign']:
            targets={f['id']:f for f in assembly['families']}
            for match in membership.get('matches',[]):
                model=models[match['source_file']]
                source=next(f for f in model['families'] if f['id']==match['family_id'])
                target=targets[match['assembly_family_id']]
                observed=judge.assess('membership',source.get('images',[])+target.get('images',[]),
                    dict(source_file=Path(match['source_file']).name,source_metrics=source['metrics'],
                         assembly_metrics=target['metrics'],source_image_count=len(source.get('images',[]))))
                ctx['membership_visual'].append(dict(**observed,match=match))
    else:
        model=models[str(steps[0])]
        if '1' in selection:
            if module=='1':
                ctx['solidworks']=[str(p) for p in inventory.get('solidworks',[])]
                provision=check_solidworks(ctx['solidworks'])
                ctx['correspondence']=pairing(steps,drawings) if provision['status']=='passed' else provision
            else:
                ctx['steps']=[str(p) for p in steps]
        ctx['stats']=dict(complete=model['complete'],total=len(model['families']),
                          occurrence_count=model.get('occurrence_count'),merge_policy='mechanical-seven-v2')
        if module=='1':
            if '2' in selection:
                ctx['complexity']=screen_complexity(steps[0],
                    output/'module_01/dimension_02_complexity/original_engine',**(complexity_options or {}))
            if '3' in selection:
                if not drawings:
                    ctx['drawing_quality']=result('failed','没有PDF/DXF工程图，无法提供尺寸标注和视图。')
                else:
                    documents(drawings)
                    docs=[ctx['documents'][str(p)] for p in drawings]
                    if any(not d['complete'] for d in docs):
                        ctx['drawing_quality']=result('unknown','部分工程图未完整读取，不能确认尺寸标注和视图要求。')
                    else:
                        ctx['drawing_quality']=judge.assess('drawing_quality',[i for d in docs for i in d['images']],
                            dict(drawings=[dict(file=Path(d['file']).name,pages=d['pages']) for d in docs]))
    return ctx
