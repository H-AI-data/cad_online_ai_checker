"""按mechanical-seven-v2去重种类计算50及30%门槛。"""
from common.io import result


def assess(context):
    stats=context['stats']
    if not stats.get('complete') or not stats.get('total'):
        return result('unknown','装配或非标准件解析、去重及关联不完整，不能可靠计算数量和占比。',stats)
    total,nonstandard=stats['total'],stats['nonstandard']
    if not 0<=nonstandard<=total:
        return result('error','统计数据不一致，未形成验收结论。',stats)
    reasons=[]
    if total<50:
        reasons.append(f'总装去重后只有 {total} 种零件，少于50种')
    if nonstandard*10<total*3:
        reasons.append(f'非标准件 {nonstandard} 种，占比 {nonstandard/total:.2%}，少于30%')
    return result('failed' if reasons else 'passed','；'.join(reasons),stats)
