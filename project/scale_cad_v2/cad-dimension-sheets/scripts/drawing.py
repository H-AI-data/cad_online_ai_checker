"""Shared measured projection and density-aware dimension layout."""
import math,itertools
from collections import defaultdict,Counter
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics

W,H=1684,1190
ink='#283540';dimension_red='#ff0000';blue='#15869b';gray='#c9cfd4'
colors=['#284f72','#836239','#517a70','#735f85','#577b94','#856663']
DIM_FONT=22
MIN_STATION_MM=.5
CHAIN_PADDING=20
PARTS=[];BORES=[];c=None;page=0
log=[];dimensions=[];leaders={};routing=[];panel_bounds={}
def text(x,y,t,size=14,color=ink,anchor='start'):
    c.setFont('CJK',size);c.setFillColor(HexColor(color))
    (c.drawCentredString if anchor=='middle' else c.drawRightString if anchor=='end' else c.drawString)(x,y,str(t))
def line(a,b,color=dimension_red,width=1.5,dash=None):
    c.setStrokeColor(HexColor(color));c.setLineWidth(width);c.setDash(dash or [])
    c.line(*a,*b);c.setDash([])
def dot(p):c.setFillColor(HexColor(dimension_red));c.circle(*p,3.0,stroke=0,fill=1)
def label(x,y,s,vertical=False):
    c.saveState();c.translate(x,y)
    if vertical:c.rotate(90)
    tw=pdfmetrics.stringWidth(s,'CJK',DIM_FONT)+12
    c.setFillColor(HexColor('#ffffff'));c.rect(-tw/2,-3,tw,DIM_FONT+4,stroke=0,fill=1)
    text(0,0,s,DIM_FONT,dimension_red,'middle');c.restoreState()
def families(ids):return [p for p in PARTS if int(p['family'][-3:]) in ids]
def extrema(ps,axis,high=False):
    return (max if high else min)((v for p in ps for poly in p['paths'] for v in poly),key=lambda v:v[axis])
def feature_anchor(p,axis,high=False):return extrema([p],axis,high)
def draw(ps,axes,box,title='',bounds=None,multicolor=True):
    a,b=axes;x,y,w,h=box
    low=[min(p['low'][k] for p in ps) for k in axes] if bounds is None else bounds[0]
    high=[max(p['high'][k] for p in ps) for k in axes] if bounds is None else bounds[1]
    sc=min(w/max(high[0]-low[0],.1),h/max(high[1]-low[1],.1))
    ox=x+(w-(high[0]-low[0])*sc)/2;oy=y+(h-(high[1]-low[1])*sc)/2
    def pt(v):return ox+(v[a]-low[0])*sc,oy+(v[b]-low[1])*sc
    if title:text(x,y+h+25,title,17,blue)
    c.saveState()
    if bounds is not None:
        clip=c.beginPath();clip.rect(ox,oy,(high[0]-low[0])*sc,(high[1]-low[1])*sc)
        c.clipPath(clip,stroke=0,fill=0)
    for i,p in enumerate(ps):
        c.setStrokeColor(HexColor(colors[int(p['family'][-3:])%len(colors)] if multicolor else ink));c.setLineWidth(1.05)
        path=c.beginPath()
        for poly in p['paths'] + p.get('silhouettes',{}).get(f'{a},{b}',[]):
            if len(poly)<2:continue
            path.moveTo(*pt(poly[0]))
            for v in poly[1:]:path.lineTo(*pt(v))
        c.drawPath(path)
    c.restoreState()
    panel_bounds[id(pt)]=(ox,oy,ox+(high[0]-low[0])*sc,oy+(high[1]-low[1])*sc)
    return pt,sc,low,high
def hd(p,q,y,pt,a,name,prefix=''):
    v=abs(p[a]-q[a])
    if v<.05:return
    p2,q2=pt(p),pt(q)
    dimensions.append(dict(kind='h',group=id(pt),p=p2,q=q2,rail=y,label=prefix+f'{v:.2f}',value=v))
    log.append(dict(page=page,name=name,mm=v,axis=a,start=p,end=q))
def vd(p,q,x,pt,b,name,prefix=''):
    v=abs(p[b]-q[b])
    if v<.05:return
    p2,q2=pt(p),pt(q)
    dimensions.append(dict(kind='v',group=id(pt),p=p2,q=q2,rail=x,label=prefix+f'{v:.2f}',value=v))
    log.append(dict(page=page,name=name,mm=v,axis=b,start=p,end=q))
def axis_point(p,normal):
    circles=[q for q in p['circles'] if abs(q['axis'][normal])>.999 and q['radius']>8]
    keep=[i for i in range(3) if i!=normal]
    groups=Counter(tuple(round(q['center'][j],3) for j in keep) for q in circles)
    if not groups:return p['center']
    key=groups.most_common(1)[0][0];point=list(p['center'])
    for j,v in zip(keep,key):point[j]=v
    return point

def small_chain(ps,axes,pt,rail,extra=()):
    """Dimension consecutive projected end extents, without publishing coordinates."""
    a,b=axes
    anchors=[feature_anchor(p,a,high) for p in ps for high in (False,True)]+list(extra)
    anchors.sort(key=lambda p:p[a])
    points=[]
    for p in anchors:
        if not points or p[a]-points[-1][a] >= MIN_STATION_MM:points.append(p)
    # Merge crowded segments until every number fits inside its own span.
    # Keep real endpoints and favor retaining shaft-axis stations.
    def clearance(i):
        p,q=points[i:i+2]
        width=pdfmetrics.stringWidth(f'{q[a]-p[a]:.2f}','CJK',16)
        return pt(q)[0]-pt(p)[0]-width-CHAIN_PADDING
    while len(points)>2:
        i=min(range(len(points)-1),key=clearance)
        if clearance(i)>=0:break
        candidates=[k for k in (i,i+1) if 0<k<len(points)-1]
        def removal_cost(k):
            protected=any(abs(points[k][a]-p[a])<.05 for p in extra)
            return protected,points[k+1][a]-points[k-1][a]
        del points[min(candidates,key=removal_cost)]
    if len(points)<3:return
    # Every retained station remains an actual feature; no averaged / snapped endpoints.
    spans=[(p,q,q[a]-p[a]) for p,q in zip(points,points[1:])]
    mids=[(pt(p)[0]+pt(q)[0])/2 for p,q,v in spans]
    for p in points:
        x,y=pt(p)
        line((x,y+4),(x,rail+5),dimension_red,1.2);dot((x,rail))
    line((pt(points[0])[0],rail),(pt(points[-1])[0],rail))
    for (p,q,v),mid in zip(spans,mids):
        label(mid,rail+8,f'{v:.2f}')
        log.append(dict(page=page,name='incremental projected chain',mm=v,
                        axis=a,start=p,end=q))
    total=points[-1][a]-points[0][a]
    assert abs(sum(v for p,q,v in spans)-total)<1e-7
    text((pt(points[0])[0]+pt(points[-1])[0])/2,rail+42,
         f'分段尺寸链：相加 = {total:.2f} mm',13,blue,'middle')
def hole_groups(ps,axes):
    a,b=axes;normal=next(k for k in range(3) if k not in axes);ids={p['id'] for p in ps};groups=defaultdict(dict)
    for q in BORES:
        if q['part'] not in ids or abs(q['axis'][normal])<.999 or 2*q['radius']<3:continue
        key=(q['family'],round(2*q['radius'],2))
        groups[key][(round(q['center'][a],2),round(q['center'][b],2))]=q
    return sorted(groups.items(),key=lambda x:(-len(x[1]),-x[0][1]))
def hole_labels(ps,axes,pt,xx,top,maxcount=6):
    groups=hole_groups(ps,axes)[:maxcount]
    for i,((fam,diam),positions) in enumerate(groups):
        target=max(positions.values(),key=lambda q:q['center'][axes[0]])
        rim=list(target['center']);rim[axes[0]]+=target['radius']
        loc=pt(rim)
        leaders.setdefault(id(pt),dict(xx=xx,items=[]))['items'].append((loc,f'{IDENTITY_PREFIX}{fam[-3:]}  Ø{diam:.2f}'))
        log.append(dict(page=page,name='cylindrical bore '+fam,mm=diam))
    return groups

def crosses(a,b,c,d):
    def cross(p,q,r):return (q[0]-p[0])*(r[1]-p[1])-(q[1]-p[1])*(r[0]-p[0])
    return cross(a,b,c)*cross(a,b,d)<-1e-5 and cross(c,d,a)*cross(c,d,b)<-1e-5

def render_annotations():
    # A separate exterior dimension rail for each span; short spans closest to the drawing.
    groups=defaultdict(list)
    for d in dimensions:groups[(d['group'],d['kind'])].append(d)
    for (group_id,kind),ds in groups.items():
        ds.sort(key=lambda d:d['value'])
        base=max(d['rail'] for d in ds)
        rows=[];placements=[]
        for d in ds:
            if kind in ('h','v'):
                k=0 if kind=='h' else 1
                mid=(d['p'][k]+d['q'][k])/2
                half=pdfmetrics.stringWidth(d['label'],'CJK',DIM_FONT)/2+12
                interval=(min(d['p'][k],d['q'][k],mid-half)-8,
                          max(d['p'][k],d['q'][k],mid+half)+8)
                available=[i for i,row in enumerate(rows)
                           if all(interval[1]<v[0] or interval[0]>v[1] for v in row)]
                i=available[0] if available else len(rows)
                if i==len(rows):rows.append([])
                rows[i].append(interval)
            else:i=len(placements)
            placements.append((i,d))
        spacing=42
        direction=-1
        bounds=panel_bounds.get(group_id)
        if kind=='h' and bounds:
            mean_y=sum(v['p'][1]+v['q'][1] for v in ds)/(2*len(ds))
            direction=1 if bounds[3]-mean_y < mean_y-bounds[1] else -1
            base=(bounds[3]+48) if direction==1 else (bounds[1]-48)
            if direction==1 and base+spacing*(len(rows)-1)>H-145:
                raise ValueError('Top dimension rails exceed available blank space; revise panel layout')
        if direction==-1 and rows and base-spacing*(len(rows)-1) < (110 if kind=='h' else 65):
            raise ValueError(f'Page {page}: {kind} {len(rows)} rails at {base}; split detail selection instead of shrinking labels')
        for i,d in placements:
            rail=base+direction*spacing*i;p,q=d['p'],d['q']
            routing.append(dict(page=page,kind=kind,rail=rail,row=i,label=d['label']))
            if kind=='h':
                for z in (p,q):
                    sign=1 if rail>z[1] else -1
                    start=(z[0],z[1]+sign*4)
                    line(start,(z[0],rail+sign*5),dimension_red,1.2);dot((z[0],rail))
                line((p[0],rail),(q[0],rail));label((p[0]+q[0])/2,rail+5,d['label'])
            else:
                for z in (p,q):line((z[0]-4,z[1]),(rail-5,z[1]),dimension_red,1.2);dot((rail,z[1]))
                line((rail,p[1]),(rail,q[1]));label(rail-9,(p[1]+q[1])/2,d['label'],True)
    occupied=[]
    for group_id,group in leaders.items():
        items=group['items'];n=len(items)
        bounds=panel_bounds.get(group_id)
        if bounds:
            for anchor,t in items:
                width=pdfmetrics.stringWidth(t,'CJK',20)+8
                x0,y0,x1,y1=bounds
                choices=[]
                for offset in (0,42,-42,84,-84):
                    choices.extend([(x0-width-28,anchor[1]+offset),
                                    (x1+28,anchor[1]+offset),
                                    (anchor[0]-width/2+offset,y1+48),
                                    (anchor[0]-width/2+offset,y0-60)])
                valid=[]
                for x,y in choices:
                    box=(x-8,y-4,x+width+8,y+32)
                    if box[0]<55 or box[2]>W-55 or box[1]<100 or box[3]>H-140:continue
                    if any(not(box[2]<a or box[0]>c or box[3]<b or box[1]>d) for a,b,c,d in occupied):continue
                    if not(box[2]<x0 or box[0]>x1 or box[3]<y0 or box[1]>y1):continue
                    end=(x if anchor[0]<x+width/2 else x+width,y)
                    valid.append((math.dist(anchor,end),x,y,end,box))
                if not valid:raise ValueError('No nearby blank label position; split detail or revise panel')
                _,x,y,end,box=min(valid,key=lambda q:q[0])
                line(anchor,end);line((x,y),(x+width,y));dot(anchor)
                text(x,y+8,t,20,dimension_red)
                occupied.append(box)
                routing.append(dict(page=page,label=t,anchor=anchor,label_origin=(x,y),placement='nearest exterior blank'))
            continue
        if not n:continue
        # Both bore and circle callouts share one ordered label column.
        end=min(W-55,max(group['xx']+140,max(p[0] for p,t in items)+155))
        elbow=end-115
        gap=52
        middle=sum(p[1] for p,t in items)/n
        low=max(355,min(middle-(n-1)*gap/2,1005-(n-1)*gap))
        slots=[low+i*gap for i in range(n)]
        def score(order):
            segments=[(items[k][0],(elbow,slots[j])) for j,k in enumerate(order)]
            count=sum(crosses(*segments[i],*segments[j]) for i in range(n) for j in range(i))
            length=sum(math.dist(a,b) for a,b in segments)
            return count,length
        order=tuple(sorted(range(n),key=lambda k:(items[k][0][1],items[k][0][0])))
        original=score(tuple(reversed(range(n))))[0]
        if n<=7:
            order=min(itertools.permutations(range(n)),key=score)
        else:
            current=score(order)
            for _ in range(12):
                best,bs=order,current
                for i in range(n):
                    for j in range(i):
                        trial=list(order);trial[i],trial[j]=trial[j],trial[i];trial=tuple(trial);s=score(trial)
                        if s<bs:best,bs=trial,s
                if bs>=current:break
                order,current=best,bs
        for j,k in enumerate(order):
            p,t=items[k];q=(elbow,slots[j])
            line(p,q);line(q,(end,slots[j]));dot(p)
            text(end,slots[j]+8,t,20,dimension_red,'end')
        routing.append(dict(page=page,callouts=n,original_order_crossings=original,optimized_crossings=score(order)[0]))
    dimensions.clear();leaders.clear()


IDENTITY_PREFIX = "F"


def select_clear_arc_anchor(circle, parts, axes, pt, visible, min_clearance=5):
    """Choose a true trimmed-edge point separated from unrelated projected lines."""
    import numpy as np
    if 'rim_candidates' not in circle:
        raise ValueError('Geometry cache lacks trimmed arc candidates; re-run extract_step.py')
    candidates=[v for v in circle['rim_candidates'] if visible(v)]
    if not candidates:return None
    center=np.array(pt(circle['center']))
    # Projection is near-face-on here. Ignore only segments on this same circle.
    radius=np.linalg.norm(np.array(pt(candidates[0]))-center)
    segments=[]
    for part in parts:
        for poly in part['paths']+part.get('silhouettes',{}).get(f'{axes[0]},{axes[1]}',[]):
            points=np.array([pt(v) for v in poly])
            if len(points)<2:continue
            a,b=points[:-1],points[1:]
            same=(np.abs(np.linalg.norm(a-center,axis=1)-radius)<.1)&(np.abs(np.linalg.norm(b-center,axis=1)-radius)<.1)
            segments.extend(zip(a[~same],b[~same]))
    if not segments:return dict(circle,rim=candidates[len(candidates)//2],anchor_clearance_pt=float('inf'))
    seg=np.array(segments);a=seg[:,0];delta=seg[:,1]-a
    denom=np.maximum((delta*delta).sum(axis=1),1e-12)
    scored=[]
    for candidate in candidates:
        point=np.array(pt(candidate));t=np.clip(((point-a)*delta).sum(axis=1)/denom,0,1)
        clearance=float(np.linalg.norm(a+t[:,None]*delta-point,axis=1).min())
        scored.append((clearance,candidate))
    clearance,candidate=max(scored,key=lambda entry:entry[0])
    return dict(circle,rim=candidate,anchor_clearance_pt=clearance) if clearance>=min_clearance else None


def circle_faces_view(circle, axes):
    """A diameter needs a near-face-on circular outline, not a side silhouette."""
    if circle.get('curve_kind','circle') != 'circle':return False
    axis=circle['axis']
    length=math.sqrt(sum(v*v for v in axis))
    normal=next(k for k in range(3) if k not in axes)
    return length > 1e-12 and abs(axis[normal])/length >= .95


def crowded_anchor_indices(points, clearance=24):
    """Suppress every ambiguous anchor, not an arbitrary winner in a cluster."""
    return {i for i, p in enumerate(points)
            if any(i != j and math.dist(p, q) < clearance
                   for j, q in enumerate(points))}


def detailed_dimensions(ps, axes, pt, panel):
    """Explicit local chains retain short spans; annotation routing staggers them."""
    start_log=len(log)
    by_id={p['id']:p for p in ps}
    seen=set()
    bounds=panel.get('crop')
    reference=panel.get('reference_size_mm', max((max(p.get('size',[0])) for p in ps),default=0))
    if bounds is not None:reference=max(bounds[1][i]-bounds[0][i] for i in (0,1))
    threshold=panel.get('min_dimension_ratio',.01)*reference
    def visible(p):
        return bounds is None or all(bounds[0][i]-.001 <= p[axis] <= bounds[1][i]+.001 for i,axis in enumerate(axes))
    def queue(p,q,axis,name):
        if axis not in axes:return
        value=abs(p[axis]-q[axis])
        if value+1e-8<threshold or not visible(p) or not visible(q):return
        component=axes.index(axis)
        if abs(pt(p)[component]-pt(q)[component])<panel.get('min_dimension_span_pt',16):return
        key=(axis,round(min(p[axis],q[axis]),3),round(max(p[axis],q[axis]),3))
        if key in seen:return
        seen.add(key)
        if axis==axes[0]:hd(p,q,panel.get('horizontal_rail',255),pt,axis,name)
        else:vd(p,q,panel.get('vertical_rail',250),pt,axis,name)
    for chain in panel.get('chains',[]):
        axis=chain['axis']
        if axis not in axes:continue
        selected=[by_id[ident] for ident in chain['parts']]
        anchors=[]
        for part in selected:
            if chain.get('features','ends')=='circles':
                anchors.extend(q['center'] for q in part['circles']
                               if abs(q['axis'][axis])>.999 and q['radius']>=chain.get('min_radius',2))
            else:
                anchors.extend([feature_anchor(part,axis),feature_anchor(part,axis,True)])
        for ident in chain.get('axis_centers',[]):
            anchors.append(axis_point(by_id[ident['part']],ident['normal']))
        points=[]
        for p in sorted(anchors,key=lambda p:p[axis]):
            if not points or p[axis]-points[-1][axis]>=chain.get('min_station_mm',.15):points.append(p)
        for p,q in zip(points,points[1:]):queue(p,q,axis,'local adjacent feature chain')
    for measurement in panel.get('distances',[]):
        axis=measurement['axis']
        if axis not in axes:continue
        anchors=[]
        for end in measurement['ends']:
            part=by_id[end['part']]
            anchors.append(axis_point(part,end['normal']) if end['end']=='axis' else
                           feature_anchor(part,axis,end['end']=='max'))
        queue(*anchors,axis,measurement.get('label','adjacent end-face distance'))
    maxcount=panel.get('max_diameters',6)
    diameter_ids=panel.get('diameter_parts',list(by_id))
    per_part=panel.get('diameters_per_part',2)
    candidates=[]
    for ident in diameter_ids:
        part=by_id[ident];radii={}
        for circle in part['circles']:
            if not circle_faces_view(circle, axes):continue
            if not circle.get('cylindrical',False) or circle['radius']<panel.get('min_diameter_radius',2) or circle['radius']>panel.get('max_diameter_radius',float('inf')):continue
            key=round(circle['radius'],3)
            if 2*circle['radius']+1e-8<max(3,threshold):continue
            chosen_circle=select_clear_arc_anchor(circle,ps,axes,pt,visible,
                                                 panel.get('min_arc_anchor_clearance_pt',5))
            if chosen_circle is None:continue
            if key not in radii or chosen_circle['anchor_clearance_pt']>radii[key]['anchor_clearance_pt']:
                radii[key]=chosen_circle
        # Larger analytic circular edges first; no gear pitch diameter inference.
        choices=sorted(radii.values(),key=lambda q:-q['radius'])[:per_part]
        candidates.append([(part,q) for q in choices])
    selected=[]
    for depth in range(per_part):
        for items in candidates:
            if depth<len(items):selected.append(items[depth])
    chosen=selected[:maxcount]
    crowded=crowded_anchor_indices([pt(q['rim']) for part,q in chosen],
                                   panel.get('min_leader_anchor_spacing_pt',24))
    for i,(part,q) in enumerate(chosen):
        if i in crowded:
            routing.append(dict(page=page, deferred_to_detail=True,
                                reason='crowded diameter anchors', part=part['id'],
                                mm=2*q['radius'], anchor=pt(q['rim'])))
            continue
        diam=2*q['radius'];fam=part['family']
        rim=q['rim'];loc=pt(rim)
        leaders.setdefault(id(pt),dict(xx=1400,items=[]))['items'].append((loc,f'{IDENTITY_PREFIX}{fam[-3:]} Ø{diam:.2f}'))
        log.append(dict(page=page,name='analytic cylinder diameter '+part['id'],mm=diam,center=q['center'],radius=q['radius'],
                        edge_index=q.get('edge_index'), rim=q['rim'], axis=q['axis'], view_axes=list(axes),
                        projected_ratio=abs(q['axis'][next(k for k in range(3) if k not in axes)])/math.sqrt(sum(v*v for v in q['axis']))))
    for entry in log[start_log:]:
        entry['minimum_mm']=threshold
        entry['detail_crop']=bounds
