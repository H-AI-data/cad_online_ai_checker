"""先前置门禁，再自动路由及独立维度；任何独立入口均不能绕过门禁。"""
import argparse
from datetime import datetime
import importlib
import json
from pathlib import Path
import sys
import uuid

from .io import result,save_json,sha256,reserve_output
from .inventory import discover,is_solidworks,MODEL,DRAWING
from .preflight import run_preflight
from .geometry import analyze_step
from .vision import Judge,MODEL as DEFAULT_MODEL
from .reporting import write_reports
from .finalization import finalize,REGISTRY

PROJECT=Path(__file__).resolve().parents[1]
ORIGINAL_STEP_CHECK_SHA256='561c80ef138c650e1ca68ad557e029cb188b90312f3a77b11432b9110bcad134'
DIMENSIONS={
 '1':{'1':('dimension_01_correspondence','零件文件配套及内容对应'),'2':('dimension_02_complexity','零件复杂特征'),'3':('dimension_03_drawing_quality','尺寸标注及至少两个视图')},
 '2':{'1':('dimension_01_correspondence','总装和非标准件文件配套及内容对应'),'2':('dimension_02_quantity_ratio','去重数量及非标准件占比'),'3':('dimension_03_membership','非标准件属于总装')},
 '3':{'1':('dimension_01_correspondence','总装STEP/STP文件提供性检查'),'2':('dimension_02_unique_count','总装去重不少于100种')},
}


def positive(value):
    number=int(value)
    if number<=0:
        raise argparse.ArgumentTypeError('必须为正整数')
    return number


def nonnegative(value):
    number=int(value)
    if number<0:
        raise argparse.ArgumentTypeError('不能小于0')
    return number


def verify_input_snapshot(source,inventory,hashes,explicit=False,assembly_dir=None,nonstandard_dir=None):
    if not explicit:
        current=discover(source,assembly_dir,nonstandard_dir)
        if (set(map(str,current['files']))!=set(hashes) or current['gaps']!=inventory['gaps']
                or any(current.get(k)!=inventory.get(k) for k in ('layout','assembly_dir','nonstandard_dir'))):
            raise ValueError('运行过程中输入文件集合或目录结构发生变化，本次结论作废。')
    if any(Path(p).is_symlink() or not Path(p).is_file() or sha256(Path(p))!=digest for p,digest in hashes.items()):
        raise ValueError('运行过程中输入发生变化，本次结论作废。')


def main(argv=None,fixed_module=None,fixed_dimension=None):
    parser=argparse.ArgumentParser(description='CAD三类输入自动验收；先执行原STEP检查，后续检查不能绕过门禁。')
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--input-dir',type=Path,help='一套提交资料所在目录，递归读取')
    group.add_argument('--step',type=Path,help='模块1/3的单个STEP/STP文件；模块3无需提供工程图')
    parser.add_argument('--drawing',type=Path,action='append',default=[],help='显式工程图路径，可重复，仅与--step配合')
    parser.add_argument('--solidworks',type=Path,action='append',default=[],help='显式SolidWorks文件路径（最后扩展名以.sld开头），可重复，仅与--step配合')
    parser.add_argument('--module',choices=[fixed_module] if fixed_module else ['auto','1','2','3'],default=fixed_module or 'auto')
    parser.add_argument('--dimension',choices=[fixed_dimension] if fixed_dimension else ['all','1','2','3'],default=fixed_dimension or 'all')
    parser.add_argument('--assembly-dir',help='模块2的总装子目录，路径相对输入目录')
    parser.add_argument('--nonstandard-dir',help='模块2的非标准件子目录，路径相对输入目录')
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--dedup-registry',type=Path,default=REGISTRY,
                        help='完整模块通过后的查重库；正式默认step查重.json，测试请使用副本')
    parser.add_argument('--step-check-script',type=Path,default=PROJECT.parent.parent/'step检查.py')
    parser.add_argument('--freecad-python',default=sys.executable)
    parser.add_argument('--freecad-lib',default=str(Path(sys.prefix)/'lib'))
    parser.add_argument('--step-timeout',type=positive,default=300)
    parser.add_argument('--geometry-timeout',type=positive,default=600)
    parser.add_argument('--complexity-source',type=Path,default=PROJECT.parent/'scale_cad_v2',help='原始复杂特征项目目录')
    parser.add_argument('--blender',help='原图册生成器所需的Blender可执行文件')
    parser.add_argument('--complexity-timeout',type=positive,default=1800,help='复杂特征完整本地流程超时秒数')
    parser.add_argument('--llm',choices=['off','auto'],default='auto')
    parser.add_argument('--llm-config',type=Path,default=PROJECT.parent/'key.txt')
    parser.add_argument('--model',default=DEFAULT_MODEL)
    parser.add_argument('--max-llm-calls',type=nonnegative,default=40)
    parser.add_argument('--llm-timeout',type=positive,default=120)
    parser.add_argument('--max-pages',type=positive,default=200)
    args=parser.parse_args(argv)
    if args.drawing and not args.step:
        parser.error('--drawing 仅与 --step 配合，目录输入请提供完整目录。')
    if args.solidworks and not args.step:
        parser.error('--solidworks 仅与 --step 配合，目录输入请提供完整目录。')
    if args.module in DIMENSIONS and args.dimension!='all' and args.dimension not in DIMENSIONS[args.module]:
        parser.error('该模块不存在所选维度。')
    source=(args.input_dir or args.step.parent).expanduser().resolve()
    output=args.output_dir or PROJECT/'outputs'/('run_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8])
    try:
        output=reserve_output(output,source)
    except (ValueError,OSError) as exc:
        print(str(exc),file=sys.stderr)
        return 2
    module=args.module if args.module!='auto' else None
    preflight=result('error','尚未执行前置检查。');preflight['files']=[]
    routing=result('unknown','尚未确认输入类型。')
    ctx={};judge=None;fatal=None;hashes={};inventory={}
    try:
        if not args.step_check_script.is_file() or sha256(args.step_check_script)!=ORIGINAL_STEP_CHECK_SHA256:
            raise ValueError('找不到原始 step检查.py，或脚本内容与指定原始版本不一致；不能替代前置检查。')
        if args.step:
            files=[args.step,*args.drawing,*args.solidworks]
            if any(p.is_symlink() or not p.is_file() for p in files):
                raise ValueError('显式输入必须是实际存在的普通文件，不能是符号链接。')
            if args.step.suffix.lower() not in MODEL or any(p.suffix.lower() not in DRAWING for p in args.drawing):
                raise ValueError('显式输入的模型或图纸扩展名不正确。')
            if any(not is_solidworks(p) for p in args.solidworks):
                raise ValueError('显式SolidWorks文件的最后扩展名必须以.sld开头（不区分大小写，含.sld）。')
            inventory=dict(root=str(source),layout='single',steps=[args.step.resolve()],
                drawings=[p.resolve() for p in args.drawing],solidworks=[p.resolve() for p in args.solidworks],
                files=[p.resolve() for p in files],gaps=[],assembly_dir=None,nonstandard_dir=None)
        else:
            inventory=discover(source,args.assembly_dir,args.nonstandard_dir)
        hashes={str(p):sha256(p) for p in inventory['files']}
        if inventory['layout']=='package' and module is None:
            module='2'
        preflight=run_preflight(inventory['steps'],output/'preflight',args.step_check_script,
            sys.executable,args.step_timeout,args.freecad_python,args.freecad_lib)
        if preflight['status']=='passed':
            if inventory['gaps']:
                routing=result('unknown','；'.join(inventory['gaps']))
            elif inventory['layout']=='unknown':
                routing=result('unknown','无法识别单件/总装输入或双目录角色；请只提交一套资料，必要时指定两个子目录。')
            else:
                models={}
                for index,p in enumerate(inventory['steps'],1):
                    print(f'解析几何 {index}/{len(inventory["steps"])}：{p.name}',file=sys.stderr,flush=True)
                    model=analyze_step(p,output/'geometry'/f'model_{index:04d}',args.geometry_timeout)
                    model['source_file']=str(p)
                    models[str(p)]=model
                actual=None
                if inventory['layout']=='single':
                    model=models[str(inventory['steps'][0])]
                    actual={'part':'1','assembly':'3'}.get(model['kind']) if model['complete'] else None
                else:
                    a=Path(inventory['assembly_dir'])
                    candidates=[models[str(p)] for p in inventory['steps'] if a in p.parents]
                    if len(candidates)==1 and candidates[0]['kind']=='assembly' and candidates[0]['complete']:
                        actual='2'
                if actual is None:
                    routing=result('unknown','STEP内容或装配结构不足以确认输入类型；总装目录还须有唯一完整总装模型。')
                elif module is not None and actual!=module:
                    routing=result('unknown',f'实际输入属于模块{actual}，与指定模块{module}不符，未强行套用规则。')
                else:
                    module=actual
                    routing=result('passed','通过',dict(module=module,layout=inventory['layout']))
                    if args.dimension!='all' and args.dimension not in DIMENSIONS[module]:
                        raise ValueError('自动识别出的模块不存在所选维度。')
                    selection=list(DIMENSIONS[module]) if args.dimension=='all' else [args.dimension]
                    judge=Judge(args.llm_config,output/'llm_cache',args.llm,args.model,args.max_llm_calls,args.llm_timeout)
                    from .context import prepare
                    ctx=prepare(module,selection,inventory,models,judge,output,args.max_pages,
                        dict(source_root=args.complexity_source,freecad_python=args.freecad_python,
                             freecad_lib=args.freecad_lib,blender=args.blender,timeout=args.complexity_timeout))
                save_json(output/'geometry.json',models)
        else:
            routing=result('skipped','前置检查未通过，未继续解析分类或执行后续检查。')
        verify_input_snapshot(source,inventory,hashes,bool(args.step),args.assembly_dir,args.nonstandard_dir)
    except Exception as exc:
        fatal='运行中发生错误，未形成完整验收结论。'
        if isinstance(exc,ValueError) and any('\u4e00'<=c<='\u9fff' for c in str(exc)):
            fatal=str(exc)
        routing=result('error',fatal)
    rows=[]
    choices=DIMENSIONS.get(module,{'all':('unclassified','后续检查')})
    if args.dimension!='all' and module:
        choices={n:value for n,value in choices.items() if n==args.dimension}
    for number,(dimension,title) in choices.items():
        if fatal:
            report=result('error',fatal)
        elif preflight['status']!='passed' or routing['status']!='passed':
            report=result('skipped',routing['reason'])
        else:
            try:
                report=importlib.import_module(f'modules.module_{int(module):02d}.{dimension}.checker').assess(ctx)
            except Exception:
                report=result('error','本维度判定失败，未形成可靠结论。')
        rows.append(dict(dimension=dimension,title=title,**report))
    # Recheck after dimension assessment as well, immediately before the only write-capable stage.
    if not fatal and routing['status']=='passed':
        try:
            verify_input_snapshot(source,inventory,hashes,bool(args.step),args.assembly_dir,args.nonstandard_dir)
        except (OSError,ValueError):
            fatal='最终查重前输入文件或目录发生变化，已取消验收和入库。'
            routing=result('error',fatal)
            for row in rows:
                row.update(result('error',fatal))
    save_json(output/'context.json',dict(context=ctx,source_sha256=hashes))
    finalization=finalize(module,args.dimension,[value[0] for value in DIMENSIONS.get(module,{}).values()],
        preflight,routing,rows,inventory,hashes,output,args.dedup_registry.expanduser().absolute(),
        verify_inputs=lambda: verify_input_snapshot(source,inventory,hashes,bool(args.step),
                                                   args.assembly_dir,args.nonstandard_dir))
    print('最终查重入库：'+('通过，已记录到查重库。' if finalization['status']=='passed' else finalization['reason']),
          file=sys.stderr,flush=True)
    summary=write_reports(output,module,preflight,routing,rows,judge,str(source),finalization=finalization)
    print(json.dumps(dict(execution_status=summary['execution_status'],exit_code=summary['exit_code'],
        module=module,acceptance_status=summary['acceptance_status'],summary=str(output/'summary.md'),output_dir=str(output)),ensure_ascii=False))
    return summary['exit_code']
