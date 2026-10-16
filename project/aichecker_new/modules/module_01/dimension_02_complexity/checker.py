"""新规则：确认至少一项指定复杂特征才通过。"""
def assess(context):
    return context['complexity']
