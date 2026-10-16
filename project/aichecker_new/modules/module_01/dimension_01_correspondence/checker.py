"""须提供SolidWorks文件；STEP与图纸继续核验数量和内容对应。"""
from common.io import result


def check_solidworks(paths):
    evidence=dict(solidworks_files=[str(p) for p in paths],
        solidworks_scope='SolidWorks仅检查提供性，未核验内容一致性；不解析文件，不要求与STEP同名、同数量或同内容。')
    return result('passed' if paths else 'failed',
        '缺少SolidWorks文件：须提供至少一个最后扩展名以.sld开头的文件（不区分大小写，含.sld）。',evidence)


def assess(context):
    provision=check_solidworks(context.get('solidworks',[]))
    if provision['status']!='passed':
        return provision
    correspondence=context['correspondence']
    return result(correspondence['status'],correspondence['reason'],
                  {**correspondence.get('evidence',{}),**provision['evidence']})
