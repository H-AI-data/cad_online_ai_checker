"""Preserve opaque-image masks, overall view dimensions, and matching detail locators."""
import math
import numpy as np
import fitz
PANEL=(.968627,.972549,.980392)
MUTED=(.407843,.439216,.501961)
ACCENT=(.05,.48,.61)

def view_geometry(page,dims,axes):
    vi=((0,2),(1,2),(0,1)).index(tuple(axes))
    pw=(page.rect.width-72)/3;ph=(page.rect.height-146)/3
    x=28+vi*(pw+8);y=92
    ew,eh=dims[axes[0]],dims[axes[1]]
    scale=min((pw-45)/max(ew,1e-6),(ph-55)/max(eh,1e-6))
    left=x+31+(pw-45-ew*scale)/2
    bottom=y+ph-31-(ph-55-eh*scale)/2
    return x,y,pw,ph,left,bottom,scale

def draw_locator(page,dims,axes,center,half,label):
    x,y,pw,ph,left,bottom,scale=view_geometry(page,dims,axes)
    low=np.maximum(0,np.array(center)[axes]-half)
    high=np.minimum(np.array(dims)[axes],np.array(center)[axes]+half)
    if np.any(high<=low):return
    box=fitz.Rect(left+low[0]*scale,bottom-high[1]*scale,left+high[0]*scale,bottom-low[1]*scale)
    page.draw_rect(box,color=ACCENT,width=.8,dashes='[3 2] 0')
    labelx=min(box.x1+2,x+pw-13);labely=max(y+30,box.y0-2)+(12 if label=="B" else 0)
    page.draw_rect((labelx-1,labely-10,labelx+9,labely+2),color=None,fill=PANEL)
    page.insert_text((labelx,labely),label,fontsize=9,fontname='hebo',color=ACCENT)

def draw_overall(page,dims):
    def line(a,b,color=MUTED,width=.55):page.draw_line(a,b,color=color,width=width)
    def arrow(a,b):
        line(a,b);angle=math.atan2(b[1]-a[1],b[0]-a[0])
        for p,d in ((a,angle),(b,angle+math.pi)):
            for delta in (-.42,.42):line(p,(p[0]+5*math.cos(d+delta),p[1]+5*math.sin(d+delta)))
    for axes in ((0,2),(1,2),(0,1)):
        x,y,pw,ph,left,bottom,scale=view_geometry(page,dims,axes)
        right=left+dims[axes[0]]*scale;top=bottom-dims[axes[1]]*scale
        rail=y+ph-17
        for xx in (left,right):line((xx,bottom),(xx,rail),(.84,.85,.88),.4)
        for yy in (bottom,top):line((x+17,yy),(left,yy),(.84,.85,.88),.4)
        arrow((left,rail),(right,rail));arrow((x+17,bottom),(x+17,top))
        t=f'{"XYZ"[axes[0]]} {dims[axes[0]]:.2f}'.rstrip('0').rstrip('.')
        page.insert_text(((left+right-fitz.get_text_length(t,fontsize=6.5))/2,rail-3),t,fontsize=6.5,color=MUTED)
        t=f'{"XYZ"[axes[1]]} {dims[axes[1]]:.2f}'.rstrip('0').rstrip('.')
        page.insert_text((x+14,(bottom+top+fitz.get_text_length(t,fontsize=6.5))/2),t,fontsize=6.5,color=MUTED,rotate=90)

def restore_masked_images(doc,page):
    # get_image_info may resolve equal RGB digests to the unmasked duplicate.
    # Resolve original images by their explicit SMask, then composite on PANEL.
    from PIL import Image
    import io
    for xref,smask,*_ in page.get_images():
        if not smask:continue
        base=fitz.Pixmap(doc,xref);mask=fitz.Pixmap(doc,smask)
        rgba=fitz.Pixmap(base,mask)
        im=Image.open(io.BytesIO(rgba.tobytes('png'))).convert('RGBA')
        bg=Image.new('RGBA',im.size,tuple(round(c*255) for c in PANEL)+(255,))
        bg.alpha_composite(im)
        payload=io.BytesIO();bg.convert('RGB').save(payload,format='PNG')
        for box in page.get_image_rects(xref):
            page.draw_rect(box,color=None,fill=PANEL)
            page.insert_image(box,stream=payload.getvalue(),overlay=True)
