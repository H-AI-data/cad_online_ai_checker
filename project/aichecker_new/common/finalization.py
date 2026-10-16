"""所有维度通过后的终检：只选择目标和调用用户指定的SHA-256事务。"""
import importlib.util
from pathlib import Path

from .io import result,sha256

CAD_ROOT=Path(__file__).resolve().parents[3]
REGISTRY=CAD_ROOT/'step查重.json'
SCRIPT=CAD_ROOT/'step_sha256_dedup.py'


def register_step(path,registry,**kwargs):
    """直接复用指定脚本；import不执行其旧固定数据集main。"""
    try:
        script_hash=sha256(SCRIPT)
        spec=importlib.util.spec_from_file_location('aichecker_original_step_registry',SCRIPT)
        if spec is None or spec.loader is None:
            raise ValueError('missing loader')
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if sha256(SCRIPT)!=script_hash:
            raise ValueError('source changed')
        answer=module.check_and_register(path,registry,**kwargs)
        if (not isinstance(answer,dict) or answer.get('status') not in ('passed','failed','error')
                or not isinstance(answer.get('reason'),str) or not isinstance(answer.get('evidence'),dict)):
            raise ValueError('invalid registry response')
        answer['evidence'].update(script=str(SCRIPT),script_sha256=script_hash)
        return answer
    except Exception:
        # Unknown outcomes are never retried automatically: the transaction may have committed.
        return result('error','查重脚本加载或执行异常，无法确认最终入库结果；请核对查重库，不会自动重试。',
                      {'registry':str(registry),'action':'unknown'})


def finalize(module,requested_dimension,expected_dimensions,preflight,routing,rows,
             inventory,hashes,output,registry,verify_inputs=None):
    if requested_dimension!='all':
        return result('skipped','仅单项评测，未执行完整模块验收，本次不查重、不入库。',{'mode':'partial'})
    eligible=(module in ('1','2','3') and preflight['status']==routing['status']=='passed'
        and len(rows)==len(expected_dimensions) and bool(expected_dimensions)
        and {r['dimension'] for r in rows}==set(expected_dimensions)
        and all(r['status']=='passed' for r in rows))
    if not eligible:
        return result('skipped','本模块尚未全部检查通过，未执行最终查重，也未写入查重库。',{'mode':'incomplete'})
    try:
        steps=[Path(p) for p in inventory['steps']]
        if module=='2':
            assembly=Path(inventory['assembly_dir'])
            steps=[p for p in steps if assembly in p.parents]
        if len(steps)!=1:
            raise ValueError('ambiguous target')
        step=steps[0]
        if step.is_symlink() or not step.is_file() or step.suffix.lower() not in ('.step','.stp'):
            raise ValueError('invalid target')
        expected=hashes.get(str(step))
        if not expected or sha256(step)!=expected:
            raise ValueError('input changed')
        return register_step(step,registry,module=module,run_id=Path(output).name,
                             expected_sha256=expected,verify_inputs=verify_inputs)
    except (OSError,ValueError,KeyError,TypeError):
        return result('error','最终查重目标不唯一、文件缺失或检查后内容改变，未执行入库。',{'action':'none'})
