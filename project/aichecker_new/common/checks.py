"""内容配对协调；文件名仅优化比较顺序，最终须有视觉匹配证据。"""
from pathlib import Path
from .io import result


def combine(rows):
    if not rows:
        return result('unknown','没有可检查的资料。')
    status=next((s for s in ('error','failed','unknown','skipped') if any(r['status']==s for r in rows)),'passed')
    reason='；'.join(r['reason'] for r in rows if r['status']!='passed')
    return result(status,reason,dict(items=rows))


def _assignment(matrix,allowed):
    assigned={}
    def visit(i,seen):
        for j in range(len(matrix)):
            if j in seen or matrix[i][j]['status'] not in allowed:
                continue
            seen.add(j)
            if j not in assigned or visit(assigned[j],seen):
                assigned[j]=i
                return True
        return False
    if all(visit(i,set()) for i in range(len(matrix))):
        return [(i,j) for j,i in sorted(assigned.items())]
    return None


def pair_documents(steps,drawings,models,documents,judge):
    if not steps or not drawings or len(steps)!=len(drawings):
        return result('failed',f'文件不满足一一对应：STEP/STP {len(steps)} 个，PDF/DXF {len(drawings)} 个；两类均须提供且数量相等。')
    count=len(steps)
    if count>200:
        return result('unknown','文件配对超过200个模型的安全上限。')
    matrix=[[result('unknown','尚未比较。') for _ in drawings] for _ in steps]
    evaluated=set()
    def compare(i,j):
        model,doc=models.get(str(steps[i]),{}),documents.get(str(drawings[j]),{})
        if not model.get('complete') or not doc.get('complete'):
            observation=result('unknown','模型或图纸解析不完整，不能确认内容对应。')
        else:
            observation=judge.assess('correspondence',model.get('images',[])+doc.get('images',[]),
                dict(model_file=Path(steps[i]).name,drawing_file=Path(drawings[j]).name,
                     model_image_count=len(model.get('images',[])),
                     unique_family_count=len(model.get('families',[])),
                     geometry_evidence=model.get('geometry_evidence',{}),drawing_pages=doc.get('pages',[])))
        matrix[i][j]=observation
        evaluated.add((i,j))
    priority=[(i,j) for i,s in enumerate(steps) for j,d in enumerate(drawings)
              if Path(s).stem.casefold()==Path(d).stem.casefold()]
    for i,j in priority:
        compare(i,j)
    assignment=_assignment(matrix,{'passed'})
    if assignment is None:
        for i in range(count):
            for j in range(count):
                if (i,j) not in evaluated:
                    compare(i,j)
            assignment=_assignment(matrix,{'passed'})
            if assignment is not None:
                break
    rows=[dict(step=str(steps[i]),drawing=str(drawings[j]),**matrix[i][j]) for i,j in sorted(evaluated)]
    if assignment is not None:
        return result('passed','通过',dict(pairs=[dict(step=str(steps[i]),drawing=str(drawings[j])) for i,j in assignment],comparisons=rows))
    possible=_assignment(matrix,{'passed','unknown'}) is not None
    details='；'.join(f'{Path(row["step"]).name}与{Path(row["drawing"]).name}：{row["reason"]}'
                     for row in rows if row['status']!='passed') if count==1 else ''
    return result('unknown' if possible else 'failed',
                  ('无法确认所有模型和图纸存在一一对应关系，部分比较证据不足。' if possible else
                  '模型与图纸内容无法形成一一对应；存在不属于同一零件或装配体的文件。')+details,dict(comparisons=rows))
