"""PDF/DXF只读渲染；无视图裁掉、外部底图或页数截断时保留覆盖缺口。"""
from pathlib import Path
from .io import sha256


def render_drawing(path,output,max_pages=12):
    path,output=Path(path),Path(output)
    data=dict(file=str(path),images=[],pages=[],complete=False,warnings=[])
    try:
        before=sha256(path)
        output.mkdir(parents=True,exist_ok=True)
        if path.suffix.lower()=='.pdf':
            import pymupdf
            with pymupdf.open(path) as doc:
                if doc.needs_pass or not len(doc):
                    raise ValueError('加密或空图纸')
                for number in range(min(max_pages,len(doc))):
                    page=doc[number]
                    scale=min(2.5,2200/max(page.rect.width,page.rect.height))
                    image=output/f'page_{number+1:04d}.png'
                    page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False).save(image)
                    data['images'].append(str(image.resolve()))
                    data['pages'].append(dict(page=number+1,text=page.get_text()[:16000]))
                data['complete']=len(doc)<=max_pages
        elif path.suffix.lower()=='.dxf':
            import ezdxf
            from ezdxf.addons.drawing import Frontend,RenderContext
            from ezdxf.addons.drawing.config import Configuration,ColorPolicy,BackgroundPolicy,ImagePolicy
            from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            doc=ezdxf.readfile(path)
            layouts=[doc.modelspace()]+[layout for layout in doc.layouts if layout.name!='Model' and len(layout)]
            external_types={'IMAGE','PDFUNDERLAY','DWFUNDERLAY','DGNUNDERLAY'}
            # INSERT中的嵌套块也可能带外部图像；保守扫描所有块，不静默忽略。
            external=any(block.block.is_xref or any(e.dxftype() in external_types for e in block)
                         for block in doc.blocks)
            for number,layout in enumerate(layouts[:max_pages],1):
                external |= any(e.dxftype() in external_types for e in layout)
                fig=plt.figure(figsize=(12,9),dpi=160)
                try:
                    ax=fig.add_axes([0,0,1,1])
                    config=Configuration(color_policy=ColorPolicy.BLACK,background_policy=BackgroundPolicy.WHITE,image_policy=ImagePolicy.IGNORE)
                    Frontend(RenderContext(doc),MatplotlibBackend(ax),config=config).draw_layout(layout,finalize=True)
                    image=output/f'layout_{number:04d}.png'
                    fig.savefig(image,dpi=160,facecolor='white')
                finally:
                    plt.close(fig)
                data['images'].append(str(image.resolve()))
                text=[e.plain_text() if e.dxftype()=='MTEXT' else e.dxf.text for e in layout if e.dxftype() in ('TEXT','MTEXT')]
                data['pages'].append(dict(page=number,layout=layout.name,text='\n'.join(text)[:16000]))
            data['complete']=len(layouts)<=max_pages and not external
            if external:
                data['warnings'].append('DXF包含外部引用或底图，未加载，无法完整检查。')
        else:
            raise ValueError('不支持的图纸格式')
        if not data['complete']:
            data['warnings'].append('图纸未完整读取，不能将部分结果视为全部通过。')
        if sha256(path)!=before:
            raise ValueError('输入发生变化')
        data['source_sha256']=before
    except Exception:
        data['complete']=False
        data['warnings'].append('图纸渲染失败，文件可能损坏、加密或格式不支持。')
    return data
