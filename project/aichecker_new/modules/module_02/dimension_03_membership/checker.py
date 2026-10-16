"""非标准件必须均出现在总装中；几何初筛结合视觉确认。"""
from common.io import result
from common.checks import combine


def assess(context):
    evidence=context['membership']
    if not evidence.get('complete') or evidence.get('ambiguous'):
        return result('unknown','部分非标准件的总装归属存在歧义或解析不完整。',evidence)
    if evidence.get('foreign'):
        files='、'.join(str(item.get('source_file','未知文件')) if isinstance(item,dict) else str(item) for item in evidence['foreign'])
        return result('failed','部分非标准件在总装中没有匹配零件族：'+files,evidence)
    visual=context.get('membership_visual',[])
    answer=combine(visual)
    answer['evidence']={'geometry':evidence,'visual':visual}
    return answer
