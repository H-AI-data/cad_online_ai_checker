"""总装按原七指标去重后不少于100种零件。"""
from common.io import result


def assess(context):
    stats=context['stats']
    if not stats.get('complete'):
        return result('unknown','装配解析或去重不完整，不能可靠判断100种门槛。',stats)
    count=stats['total']
    return result('passed' if count>=100 else 'failed',f'总装去重后有 {count} 种零件，少于100种。',stats)
