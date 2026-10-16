"""仅调度原项目基线生成和原复杂度入口，不实现或修改筛选规则。

准备顺序来自 parts_pdf/scripts/build_parts.py 的 auto_screen 分支。
不运行该构建器后续的成品图册分层/STEP导出，也不读取用户图纸冒充内部图册。
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def snapshot(source):
    folders=('cad-complexity-screen','parts_pdf','step-exploded-view','cad-dimension-sheets')
    return {str(p.relative_to(source)):digest(p) for folder in folders
            for p in sorted((source/folder).rglob('*'))
            if p.is_file() and p.suffix in ('.py','.swift','.md') and '__pycache__' not in p.parts}


def profile_coverage_gaps(rows,family_ids):
    """只检验原输出的证据覆盖，不重新计算截面或评判复杂程度。

    原扫描器对正常空截面和捕获的异常都记录 None，无法从结果区分。
    因此不能把此类缺口当成已经充分检查的阴性证据。
    """
    if (not isinstance(rows,list) or len(rows)!=len(family_ids)
            or {r.get('family') for r in rows}!=set(family_ids)
            or any(r.get('case')!='cad_current' for r in rows)):
        return ['截面扫描的零件族覆盖与本次输入不一致。']
    gaps=[]
    for row in rows:
        sections=row.get('sections')
        if not isinstance(sections,list) or len(sections)!=3 or any(section is None for section in sections):
            gaps.append(f'{row["family"]}：截面证据不可用；原输出无法区分正常空截面与捕获的异常，保留无法判定。')
    return gaps


def main(request):
    config=json.loads(Path(request).read_text())
    source,step,out=(Path(config[k]) for k in ('source','step','output'))
    before=snapshot(source);input_hash=digest(step)
    save(out/'source_provenance.json',{'source_root':str(source),'sha256':before})
    parts_root=source/'parts_pdf/scripts';work=out/'parts'
    work.mkdir();aligned=work/'aligned';parts=work/'parts';renders=work/'renders'
    spec=work/'part-spec.json';mesh=work/'geometry/meshes.json.gz'
    harbor=out/'harbor';pdf=harbor/'cad_current/environment/input/parts.pdf'
    pdf.parent.mkdir(parents=True)
    scans=out/'scans';commands=[]
    os.environ['CAD_PARTS_ALIGNMENT_CACHE']=str(work/'alignment-cache')
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    python=sys.executable
    native=config['freecad_python'];lib=['--freecad-lib',config['freecad_lib']]

    def run(*command):
        command=list(map(str,command))
        commands.append({'command':command,'completed':False})
        save(out/'commands.json',commands)
        print('执行原脚本：'+str(command[1] if len(command)>1 else command[0]),flush=True)
        subprocess.run(command,check=True)
        commands[-1]['completed']=True
        save(out/'commands.json',commands)

    def original(script,*flags):
        run(python,parts_root/script,*flags)

    run(python,source/'step-exploded-view/scripts/pipeline.py','extract',step,work/'geometry',
        '--freecad-python',native,*lib)
    original('extract_part_spec.py','--step',mesh,'--output',spec)
    sys.path.insert(0,str(parts_root))
    from extract_part_spec import load_occurrence_meshes
    parts.mkdir()
    for index,part in enumerate(load_occurrence_meshes(mesh)):
        part.export(parts/f'part_{index:05d}.stl')
    original('export_aligned_family_meshes.py','--spec',spec,'--parts-dir',parts,'--output-dir',aligned)
    manifest=aligned/'manifest.json'
    ids=[family['id'] for family in json.loads(manifest.read_text())['families']]
    save(work/'blender-depth-ids.json',ids)
    run(config['blender'],'--background','--factory-startup','-t','4','--python-exit-code','1',
        '--python',parts_root/'blender_render_part_depth.py','--','--batch-ids-json',work/'blender-depth-ids.json',
        '--meshes-dir',aligned,'--manifest',manifest,'--output-root',renders)
    original('generate_parts_pdf_six_views.py','--spec',spec,'--parts-dir',parts,'--manifest',manifest,
        '--renders-dir',renders,'--output',pdf,'--product-title','CAD PARTS',
        '--footer-label','CAD PARTS | PARTS | UNITS: mm','--all-family-depth-views')
    run(native,source/'cad-dimension-sheets/scripts/extract_step.py','--step',step,'--out',work/'analytic',
        '--catalog',work/'geometry/catalog.json',*lib)
    plan=work/'feature-annotations.json'
    original('annotate_parts_pdf.py','--prepare','--input',pdf,'--plan',plan,'--spec',spec,
        '--geometry',work/'analytic/manual_geometry.json','--meshes',mesh,'--parts-dir',parts)
    original('annotate_parts_pdf.py','--input',pdf,'--output',pdf,'--plan',plan)
    inventory=out/'selected.json'
    save(inventory,[dict(name='cad_current',index=0,parts_workdir=str(work))])
    entry=source/'cad-complexity-screen/scripts/run.py'
    command=[python,entry,'--manifest',inventory,'--harbor-dir',harbor,'--work-dir',scans]
    run(*command,'--check-inputs')
    run(*command,'--no-preview')
    # Read only original declarations for provenance/Chinese labels, never copy predicates.
    declarations={}
    for node in ast.parse((entry.parent/'run_complexity_screen.py').read_text()).body:
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in ('SCANNERS','REASON_PRIORITY'):
                    declarations[target.id]=ast.literal_eval(node.value)
    # Original scanner catches section errors as null, not as an "error" field.
    profile=json.loads((scans/'profile_scan.json').read_text())
    gaps=profile_coverage_gaps(profile,ids)
    sys.stdout.flush()
    log=(out/'runner.log').read_text(errors='replace')
    gaps.extend(line for line in log.splitlines() if re.search(r'cad_current\s+family_\w+: skipped ',line))
    unchanged=before==snapshot(source) and input_hash==digest(step)
    save(out/'completion.json',dict(complete=True,source_unchanged=unchanged,coverage_gaps=gaps,
        engine='cad-complexity-screen',scanners=declarations['SCANNERS'],
        reason_labels=dict(declarations['REASON_PRIORITY']),commands_file=str(out/'commands.json')))
    if not unchanged:
        raise RuntimeError('运行期间原源码或输入改变')


if __name__=='__main__':
    main(sys.argv[1])
