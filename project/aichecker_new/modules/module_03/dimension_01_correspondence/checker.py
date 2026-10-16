"""总装STEP/STP文件提供性检查。"""
from pathlib import Path

from common.io import result


SCOPE='仅检查是否提供实际存在的普通STEP/STP文件；不要求工程图，不检查图纸数量或内容，不渲染图纸，不调用AI。'


def assess(context):
    files=[str(path) for path in context.get('steps',[])]
    evidence={'step_files':files,'scope':SCOPE}
    if not files:
        return result('failed','未提供总装STEP/STP文件。',evidence)
    invalid=[]
    for value in files:
        path=Path(value)
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in ('.step','.stp'):
            invalid.append(value)
    if invalid:
        evidence['invalid_step_files']=invalid
        return result('failed','总装STEP/STP文件必须是实际存在的普通.step或.stp文件（不区分大小写）。',evidence)
    return result('passed','通过',evidence)
