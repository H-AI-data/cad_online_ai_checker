"""递归发现当前提交；目录角色只是路由线索，不代替STEP内容解析。"""
from pathlib import Path
import re

MODEL={'.step','.stp'}
DRAWING={'.pdf','.dxf'}


def is_solidworks(path):
    return Path(path).suffix.lower().startswith('.sld')


def discover(root,assembly_dir=None,nonstandard_dir=None):
    root=Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('输入文件夹不存在。')
    files,gaps=[],[]
    for p in sorted(root.rglob('*')):
        relative=p.relative_to(root)
        if any(part.startswith('.') for part in relative.parts):
            continue
        if p.is_symlink() or any((root/Path(*relative.parts[:i])).is_symlink() for i in range(1,len(relative.parts))):
            gaps.append(f'{relative}：符号链接未作为提交文件读取。')
            continue
        if p.is_file() and (p.suffix.lower() in MODEL|DRAWING or is_solidworks(p)):
            files.append(p)
    steps=[p for p in files if p.suffix.lower() in MODEL]
    drawings=[p for p in files if p.suffix.lower() in DRAWING]
    solidworks=[p for p in files if is_solidworks(p)]
    dirs=[p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith('.')]
    def explicit(value):
        p=(root/Path(value)).resolve()
        if root not in p.parents or not p.is_dir() or any(q.is_symlink() for q in [root/Path(value)]):
            raise ValueError('资料子目录必须位于输入目录内。')
        return p
    if bool(assembly_dir)!=bool(nonstandard_dir):
        raise ValueError('必须同时指定总装和非标准件两个目录。')
    if assembly_dir:
        a,n=explicit(assembly_dir),explicit(nonstandard_dir)
    else:
        candidates_a=[p for p in dirs if re.search(r'(?i)总装|装配|assembly',p.name)]
        candidates_n=[p for p in dirs if re.search(r'(?i)非标准|零件|non.?standard|parts?',p.name) and p not in candidates_a]
        a=candidates_a[0] if len(candidates_a)==1 else None
        n=candidates_n[0] if len(candidates_n)==1 else None
    if a is not None and n is not None:
        if a==n or a in n.parents or n in a.parents:
            raise ValueError('总装与非标准件目录必须相互独立。')
        if any(a not in p.parents and n not in p.parents for p in steps+drawings):
            gaps.append('双目录之外还有模型或图纸，不能忽略这些额外文件。')
        layout='package'
    else:
        layout='single' if len(steps)==1 else 'unknown'
    return dict(root=str(root),steps=steps,drawings=drawings,solidworks=solidworks,files=files,gaps=gaps,layout=layout,
                assembly_dir=str(a) if a else None,nonstandard_dir=str(n) if n else None)
