"""原复杂特征筛选器的运行适配；此处不包含几何筛选规则。"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

from common.io import result,save_json,sha256

PROJECT=Path(__file__).resolve().parents[3]
SOURCE=PROJECT.parent/'scale_cad_v2'


def read_result(step,output):
    """核对本次原始结果来源，只翻译原筛选器的最终候选。"""
    output=Path(output)
    evidence={'执行引擎':'cad-complexity-screen 原始脚本','AI调用次数':0,
              '原始报告':str(output/'scans/complexity_auto_candidates.md'),
              '执行记录':str(output/'completion.json')}
    try:
        completed=json.loads((output/'completion.json').read_text())
        if completed['complete'] is not True or completed['source_unchanged'] is not True:
            raise ValueError('incomplete or changed source')
        if completed['coverage_gaps']:
            evidence['扫描缺口']=completed['coverage_gaps']
            return result('unknown','原始复杂特征扫描存在未完成项目，不能形成完整结论；详见执行记录。',evidence)
        spec=output/'parts/part-spec.json'
        families={f['id'] for f in json.loads(spec.read_text())['families']}
        selected=json.loads((output/'scans/complexity_step_selection.json').read_text())
        if selected['schema']!='cad-complexity-screen/step-selection-v1' or len(selected['cases'])!=1:
            raise ValueError('selection schema')
        record=selected['cases'][0]
        if (record['case']!='cad_current' or record['source_step_sha256']!=sha256(step)
                or record['part_spec_sha256']!=sha256(spec)):
            raise ValueError('source mismatch')
        rows=json.loads((output/'scans/complexity_auto_candidates.json').read_text())
        if not isinstance(rows,list) or not families or not isinstance(record['families'],list):
            raise ValueError('invalid families')
        labels=completed['reason_labels']
        ids=[];features=[]
        for row in rows:
            if (row['case']!='cad_current' or row['family'] not in families
                    or row['screen_level'] not in ('strong','expanded')
                    or not isinstance(row['reasons'],list) or not row['reasons']
                    or any(reason not in labels for reason in row['reasons'])
                    or row['reason']!=labels[row['reasons'][0]]):
                raise ValueError('invalid original candidate')
            ids.append(row['family'])
            features.extend(labels[r] for r in row['reasons'])
        if (len(ids)!=len(set(ids)) or sorted(ids)!=sorted(record['families'])):
            raise ValueError('candidate selection mismatch')
        evidence.update({'命中特征':list(dict.fromkeys(features)),'零件族数':len(families),
            '命中零件族数':len(rows),'扩展规则候选数':sum(r['screen_level']=='expanded' for r in rows),
            '候选明细':[{'零件族':r['family'],'命中特征':[labels[k] for k in r['reasons']],
                       '筛选级别':'强规则' if r['screen_level']=='strong' else '扩展规则'} for r in rows],
            '判定口径':'原始高召回筛选器至少命中一个候选即通过；强规则和扩展规则均保留。',
            '适用边界':'沿用原脚本的高召回筛选口径，可能误报或漏报；不等同于人工逐项确认。'})
        return result('passed' if rows else 'failed',
            '原始 cad-complexity-screen 已完整运行，但未命中任何复杂特征筛选规则，不满足本项要求。',evidence)
    except (OSError,ValueError,KeyError,TypeError,IndexError):
        return result('error','原始复杂特征结果缺失、来源不一致或格式不完整；未采用其他规则替代判断。',evidence)


def screen_complexity(step,output,*,source_root=None,freecad_python=None,freecad_lib=None,
                      blender=None,timeout=1800):
    source=Path(source_root or SOURCE).expanduser().resolve()
    output=Path(output).resolve();step=Path(step).resolve()
    if not (source/'cad-complexity-screen/scripts/run.py').is_file():
        return result('error','找不到指定的原始 cad-complexity-screen 代码，请检查 --complexity-source。')
    if output==source or source in output.parents or output in source.parents or output==step.parent or step.parent in output.parents:
        return result('error','复杂特征输出不得覆盖原源码或输入目录。')
    if output.exists() and any(output.iterdir()):
        return result('error','复杂特征输出必须是新目录，不能混用历史扫描结果。')
    candidates=([blender] if blender else [os.environ.get('BLENDER_BIN'),shutil.which('blender'),
        str(PROJECT/'tools/Blender.app/Contents/MacOS/Blender'),'/Applications/Blender.app/Contents/MacOS/Blender'])
    executable=next((shutil.which(str(p)) for p in candidates if p and shutil.which(str(p))),None)
    if not executable:
        return result('error','原始复杂特征流程需要 Blender 生成内部图册，请安装后使用 --blender 指定；未回退到LLM。')
    output.mkdir(parents=True,exist_ok=True)
    config=dict(source=str(source),step=str(step),output=str(output),blender=executable,
                freecad_python=freecad_python or sys.executable,
                freecad_lib=str(freecad_lib or Path(sys.prefix)/'lib'))
    save_json(output/'request.json',config)
    env=os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',MPLCONFIGDIR=str(output/'matplotlib'))
    print('复杂特征：准备同源内部图册并运行原 cad-complexity-screen（不调用AI）',file=sys.stderr,flush=True)
    log=output/'runner.log'
    try:
        with log.open('w') as stream:
            process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('worker.py')),
                str(output/'request.json')],stdout=stream,stderr=subprocess.STDOUT,env=env,
                cwd=output,start_new_session=True)
            try:
                code=process.wait(timeout=timeout)
            except (subprocess.TimeoutExpired,KeyboardInterrupt) as exc:
                try:
                    os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                if isinstance(exc,KeyboardInterrupt):
                    raise
                return result('error','原始复杂特征流程超时，未形成结论；可调整 --complexity-timeout。',{'运行日志':str(log)})
        if code:
            return result('error','原始复杂特征流程未执行完成，请查看运行日志；未回退到自写规则或LLM。',{'运行日志':str(log),'退出码':code})
        return read_result(step,output)
    except OSError:
        return result('error','无法启动原始复杂特征流程，请检查运行环境。',{'运行日志':str(log)})
