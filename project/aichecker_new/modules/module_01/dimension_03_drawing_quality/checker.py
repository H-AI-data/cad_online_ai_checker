"""图纸有实际尺寸标注且同件至少两个工程视图。"""
def assess(context):
    return context['drawing_quality']
