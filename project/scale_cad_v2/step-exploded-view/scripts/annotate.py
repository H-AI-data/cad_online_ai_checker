"""Pillow annotation, white page, blue callouts, optional grey guides."""
import argparse
import json
import math
import os
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--assembled',action='store_true');a=p.parse_args()
out=a.output;prefix='assembled' if a.assembled else 'exploded'
meta=json.loads((out/f'{prefix}_labels.json').read_text());im=Image.open(out/f'{prefix}_render.png').convert('RGBA')
# Catch blank/clipped renders before issuing a seemingly successful deliverable.
if im.getchannel('A').getbbox() is None:raise ValueError('Render is blank; inspect camera clipping/geometry')
w,h=im.size;margin=max(160,round(w*.13));top=150
labels=meta['labels'];rows=math.ceil(len(labels)/3);footer=max(110,rows*38+65)
page=Image.new('RGB',(w+2*margin,h+top+footer),'white');draw=ImageDraw.Draw(page)
font_paths=[os.environ.get('EXPLODED_FONT',''),'/System/Library/Fonts/Supplemental/Arial.ttf',
'/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','C:/Windows/Fonts/arial.ttf']
font_path=next((s for s in font_paths if s and Path(s).is_file()),None)
if not font_path:raise RuntimeError('No suitable font; set EXPLODED_FONT to a TTF file')
def font(size):return ImageFont.truetype(font_path,size)
def dashed(start,end):
    x,y=start;dx,dy=end[0]-x,end[1]-y;length=math.hypot(dx,dy)
    for t in range(0,int(length),20):
        u=min(t+10,length)
        draw.line((x+dx*t/length,y+dy*t/length,x+dx*u/length,y+dy*u/length),fill='#a5afbe',width=2)
for g in meta['guides']:
    dashed((g['start'][0]+margin,g['start'][1]+top),(g['end'][0]+margin,g['end'][1]+top))
page.paste(im,(margin,top),im);draw=ImageDraw.Draw(page)
draw.text((65,40),meta['title'],font=font(40),fill='#202631')
draw.text((65,98),'ASSEMBLED / ORTHOGRAPHIC' if a.assembled else 'EXPLODED ASSEMBLY / ORTHOGRAPHIC',font=font(21),fill='#687181')
blue='#244ed8'
for l in labels:
    l['x']=l['xy'][0]+margin;l['y']=l['xy'][1]+top
    if l['side']=='auto':l['side']='left' if l['x']<page.width/2 else 'right'
for side in ('left','right'):
    group=sorted([l for l in labels if l['side']==side],key=lambda l:l['y'])
    min_y,max_y=top+45,top+h-45
    gap=min(120,(max_y-min_y)/max(len(group)-1,1))
    if gap<55:raise ValueError('Too many callouts; split detail sheets or increase resolution')
    ys=[max(min_y,min(max_y,l['y'])) for l in group]
    for i in range(1,len(ys)):ys[i]=max(ys[i],ys[i-1]+gap)
    if ys and ys[-1]>max_y:
        ys[-1]=max_y
        for i in range(len(ys)-2,-1,-1):ys[i]=min(ys[i],ys[i+1]-gap)
    for l,y in zip(group,ys):
        x=margin*.4 if side=='left' else page.width-margin*.4;r=23
        sx=x+(r if side=='left' else -r);tx,ty=l['x'],l['y']
        draw.line([(sx,y),(tx,ty)],fill=blue,width=2)
        angle=math.atan2(ty-y,tx-sx);u,v=13,5
        draw.polygon([(tx,ty),(tx-u*math.cos(angle)+v*math.sin(angle),ty-u*math.sin(angle)-v*math.cos(angle)),(tx-u*math.cos(angle)-v*math.sin(angle),ty-u*math.sin(angle)+v*math.cos(angle))],fill=blue)
        draw.ellipse((x-r,y-r,x+r,y+r),fill='white',outline=blue,width=2)
        draw.text((x,y),str(l['number']),font=font(22),anchor='mm',fill=blue)
line_y=top+h+10;draw.line((65,line_y,page.width-65,line_y),fill='#d9dee7',width=2)
col_width=(page.width-130)/3
for i,l in enumerate(labels):
    text=f"{l['number']:02d}  {l['title']}";f=font(22)
    while draw.textlength(text,font=f)>col_width-20 and f.size>12:f=font(f.size-1)
    if draw.textlength(text,font=f)>col_width-20:raise ValueError('Callout legend too long; shorten text')
    draw.text((65+(i%3)*col_width,line_y+20+(i//3)*38),text,font=f,fill='#202631')
draw.text((65,page.height-32),f"{meta['occurrence_count']} source solids / "+('Original placement' if a.assembled else 'Illustrative offsets; no disassembly-path validation'),font=font(17),fill='#687181')
page.save(out/f'{prefix}.png')
page.thumbnail((1200,1400));page.save(out/f'{prefix}_preview.png')
print(out/f'{prefix}.png')
