"""直接运行用户指定的STEP健康检查；任何不确定或运行错误都不能放行。"""
import json
from pathlib import Path
import re
import subprocess
import sys

from .io import sha256,save_json

_NON_CODE=re.compile(rb"'(?:''|[^'])*'|/\*.*?\*/",re.S)
_EXTERNAL=re.compile(rb'\b(?:[A-Z_]*(?:EXTERNAL|EXTERNALLY|DOCUMENT)[A-Z_]*|PRODUCT_DEFINITION_REFERENCE[A-Z_]*)\s*\(|\b(?:REFERENCE|ANCHOR)\s*;',re.I)


def run_preflight(paths,output,script,python,timeout=300,freecad_python=None,freecad_lib=None):
    paths=list(paths)
    output=Path(output)
    records=[]
    for index,path in enumerate(paths,1):
        path=Path(path).resolve()
        row=dict(file=str(path),status='error',reason='STEP前置检查未完整完成。')
        print(f'前置检查 {index}/{len(paths)}：{path.name}',file=sys.stderr,flush=True)
        report=output/f'step_{index:04d}.json'
        try:
            if path.stat().st_size>256*1024*1024:
                raise ValueError('STEP超过256MB安全上限。')
            if _EXTERNAL.search(_NON_CODE.sub(b' ',path.read_bytes())):
                row.update(status='failed',reason='STEP含外部或文档引用，请先导出为自包含文件。')
                records.append(row)
                continue
            before=sha256(path)
            if not Path(script).is_file():
                raise ValueError('找不到指定的 step检查.py。')
            output.mkdir(parents=True,exist_ok=True)
            command=[str(python),str(script),str(path),'--output',str(report.resolve())]
            if freecad_python:
                command+=['--freecad-python',str(freecad_python)]
            if freecad_lib:
                command+=['--freecad-lib',str(freecad_lib)]
            process=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                   timeout=timeout,check=False)
            row['exit_code']=process.returncode
            if process.returncode not in (0,1):
                raise ValueError('原STEP检查脚本运行错误，请检查FreeCAD环境和文件；后续检查未执行。')
            data=json.loads(report.read_text(encoding='utf-8'))
            if (data.get('source_sha256')!=before or sha256(path)!=before or
                    Path(data.get('source','')).resolve()!=path):
                raise ValueError('前置报告来源不一致或输入发生变化。')
            summary=data['summary']
            required=('invalid_solid_count','not_closed_count','non_positive_volume_count','parts_with_errors_count')
            valid=(summary.get('all_solids_pass') is True and type(summary.get('solid_count')) is int
                   and summary['solid_count']>0 and all(type(summary.get(k)) is int and summary[k]==0 for k in required))
            row.update(report=str(report.resolve()),summary=summary,source_sha256=before)
            if process.returncode==0 and valid:
                row.update(status='passed',reason='通过')
            else:
                reasons=[]
                for key,label in zip(required,('无效实体','未闭合实体','体积非正实体','读取出错实体')):
                    if summary.get(key):
                        reasons.append(f'{label} {summary[key]} 个')
                if not summary.get('solid_count'):
                    reasons.append('未发现实体')
                row.update(status='failed',reason='；'.join(reasons) or '原检查脚本未确认所有实体通过。')
        except subprocess.TimeoutExpired:
            row['reason']='STEP前置检查超时；后续检查未执行。'
        except ValueError as exc:
            row['reason']=str(exc) if any('\u4e00'<=c<='\u9fff' for c in str(exc)) else '前置报告格式不正确。'
        except Exception:
            row['reason']='前置检查无法运行或报告无法读取；请检查FreeCAD环境、路径和文件。'
        records.append(row)
    status=('failed' if not paths else 'error' if any(r['status']=='error' for r in records)
            else 'failed' if any(r['status']!='passed' for r in records) else 'passed')
    data=dict(status=status,files=records,reason='通过' if status=='passed' else
              '没有STEP/STP输入。' if not paths else 'STEP前置关卡未通过，后续检查全部停止。')
    save_json(output/'summary.json',data)
    return data
