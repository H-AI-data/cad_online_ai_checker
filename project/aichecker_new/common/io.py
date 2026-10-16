"""只读输入、独立输出和稳定中文结果。"""
import hashlib
import json
from pathlib import Path

LABELS={'passed':'通过','failed':'不通过','unknown':'无法判定','error':'运行错误','skipped':'未执行'}
CODES={'passed':0,'failed':1,'unknown':1,'error':2,'skipped':1}


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def save_json(path,data):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def result(status,reason,evidence=None):
    return dict(status=status,reason='通过' if status=='passed' else reason,evidence=evidence or {})


def reserve_output(output,source):
    output,source=Path(output).resolve(),Path(source).resolve()
    if output==source or output in source.parents or source in output.parents:
        raise ValueError('输出与输入目录不得互相包含。')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('输出必须是新建或空目录，不能覆盖历史结果。')
    output.mkdir(parents=True,exist_ok=True)
    return output
