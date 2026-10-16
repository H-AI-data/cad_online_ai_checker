"""独立中文维度报告、前置报告和总览。"""
from pathlib import Path
from .io import LABELS,CODES,save_json


def write_reports(output,module,preflight,routing,rows,judge=None,source=None,finalization=None):
    llm=dict(calls=judge.calls if judge else 0,cache_hits=judge.cache_hits if judge else 0)
    summary=dict(module=module,input=source,preflight=preflight,routing=routing,results=rows,llm=llm)
    statuses=[preflight['status'],routing['status'],*[r['status'] for r in rows]]
    acceptance=None
    if finalization is not None:
        summary['finalization']=finalization
        final_status=finalization['status']
        if final_status!='skipped' or finalization.get('evidence',{}).get('mode')!='partial':
            statuses.append(final_status)
        acceptance={'passed':'accepted','failed':'rejected','error':'error'}.get(final_status)
        if acceptance is None:
            acceptance='not_submitted' if finalization.get('evidence',{}).get('mode')=='partial' else 'incomplete'
        if 'error' in statuses:
            acceptance='error'
        summary['acceptance_status']=acceptance
    code=max(CODES[s] for s in statuses)
    summary.update(exit_code=code,execution_status='error' if code==2 else 'completed')
    save_json(output/'summary.json',summary)
    lines=['# CAD新版验收结果','',f'识别模块：{module or "未确定"}',
        f'前置检查：{LABELS[preflight["status"]]}',f'输入分类：{LABELS[routing["status"]]}','',routing['reason'],'',
        f'AI实际调用：{llm["calls"]} 次；缓存命中：{llm["cache_hits"]} 次。','']
    if preflight['status']!='passed':
        lines+=['## 前置未通过的文件','']
        lines += [f'- {r["file"]}：{r["reason"]}' for r in preflight.get('files',[]) if r['status']!='passed']
        lines+=['',preflight['reason'],'']
    if finalization is not None:
        accepted_labels={'accepted':'通过并已入库','rejected':'重复提交，拒绝验收','error':'运行错误，未确认验收',
                         'not_submitted':'仅单项评测，未入库','incomplete':'未满足完整验收条件，未入库'}
        lines+=['## 最终查重入库','',f'验收结论：{accepted_labels[acceptance]}',
                f'终检状态：{LABELS[finalization["status"]]}',finalization['reason'],'',
                '[查看最终查重入库报告](finalization/report.md)','']
        save_json(output/'finalization/report.json',dict(module=module,input=source,**finalization))
        evidence=finalization.get('evidence',{})
        content=['# 最终查重入库','',f'结果：{LABELS[finalization["status"]]}','',finalization['reason'],'']
        if evidence.get('action')=='registered':
            content+=['本次编码未重复，已记录到查重库。','']
        for key,label in [('path','查重文件'),('sha256','SHA-256编码'),('registry','查重库'),
                          ('backup','入库前备份'),('script','调用脚本')]:
            if evidence.get(key):
                content.append(f'- {label}：{evidence[key]}')
        matches=evidence.get('matches',[])
        if matches:
            content+=['','## 已有重复记录','']
            content += [f'- 数据集：{m.get("dataset","")}；任务：{m.get("task","")}；文件：{m.get("path","")}' for m in matches]
        content+=['','按原始文件字节计算SHA-256，文件名和路径不参与编码；不判断几何等价。','']
        (output/'finalization/report.md').write_text('\n'.join(content),encoding='utf-8')
    for row in rows:
        target=output/f'module_{int(module):02d}'/row['dimension'] if module else output/'unclassified'
        data=dict(module=module,input=source,**row,llm=llm)
        save_json(target/'report.json',data)
        content=[f'# {row["title"]}','',f'结果：{LABELS[row["status"]]}','',row['reason'],'','## 判断依据','']
        # JSON evidence is available separately; human summary stays Chinese.
        evidence=row.get('evidence',{})
        if 'solidworks_files' in evidence:
            content.append('- SolidWorks文件：'+('已提供' if evidence['solidworks_files'] else '未提供'))
            content += [f'- SolidWorks路径：{path}' for path in evidence['solidworks_files']]
            content.append('- '+evidence['solidworks_scope'])
        if 'step_files' in evidence:
            content.append('- STEP/STP文件：'+('已提供' if evidence['step_files'] else '未提供'))
            content += [f'- STEP/STP路径：{path}' for path in evidence['step_files']]
            if evidence.get('scope'):
                content.append('- '+evidence['scope'])
        for key,label in [('total','去重零件种类'),('nonstandard','非标准件种类'),('standard','标准件种类'),('ratio','非标准件比例')]:
            if key in evidence:
                value=evidence[key]
                if key=='ratio' and value is not None:
                    value=f'{value:.2%}'
                content.append(f'- {label}：{value}')
        if '执行引擎' in evidence:
            content.append(f'- 执行引擎：{evidence["执行引擎"]}')
            content.append(f'- 本项AI调用：{evidence.get("AI调用次数",0)} 次')
            if '命中特征' in evidence:
                content.append('- 命中特征：'+('、'.join(evidence['命中特征']) or '无'))
            for key in ('零件族数','命中零件族数','扩展规则候选数','判定口径','适用边界'):
                if key in evidence:
                    content.append(f'- {key}：{evidence[key]}')
            for key in ('原始报告','执行记录'):
                if key in evidence:
                    content.append(f'- [{key}](<{evidence[key]}>)')
        if '运行日志' in evidence:
            content.append(f'- [运行日志](<{evidence["运行日志"]}>)')
        content+=['','完整几何、视觉证据及文件对应关系见同目录 report.json；前置文件报告见本次输出的 preflight/。','']
        (target/'report.md').write_text('\n'.join(content),encoding='utf-8')
        lines += [f'- [{row["title"]}]({(target/"report.md").relative_to(output).as_posix()})：{LABELS[row["status"]]}。{row["reason"]}']
    (output/'summary.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return summary
