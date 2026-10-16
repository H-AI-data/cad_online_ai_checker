"""Add a few STEP-derived feature dimensions to an existing nine-grid parts PDF.
Private inputs must match the original family specification and mesh tessellation.
"""
from __future__ import annotations
import argparse,json,re,math,os
from pathlib import Path
import numpy as np
import fitz
from extract_part_spec import load_occurrence_meshes
from generate_parts_pdf import align_to_local_axes, canonicalize, match_parts
from render_three_views import load_stl_parts
from general_dimension_engine import grouped_circles


def repeated_horizontal_pitches(circles, dimensions, axes):
    """Center pitches that the enlarged repeated-hole chain will label once."""
    u, v = axes
    normal = next(axis for axis in range(3) if axis not in axes)
    groups = {}
    for circle in grouped_circles(circles):
        if abs(circle["axis"][normal]) < .985:
            continue
        key = (round(circle["diameter"], 1),
               round(circle["center"][v] / max(.2, .01 * dimensions[v])))
        groups.setdefault(key, set()).add(round(circle["center"][u], 2))
    values = set()
    for stations in groups.values():
        if len(stations) < 3:
            continue
        intervals = [round(b - a, 2) for a, b in
                     zip(sorted(stations), sorted(stations)[1:])]
        if max(intervals) - min(intervals) <= .02:
            ordered = sorted(stations)
            values.update(round(right - left, 2)
                          for i, left in enumerate(ordered)
                          for right in ordered[i + 1:])
    return values

def prepare(args):
    spec=json.loads(args.spec.read_text());geo=json.loads(args.geometry.read_text())
    meshes=load_occurrence_meshes(args.meshes);doc=fitz.open(args.input)
    # The PDF aligns the canonicalized occurrence STLs.  Aligning the mesh JSON
    # again in world coordinates can choose a slightly different oriented box
    # for a long, nearly symmetric part, even though both meshes are identical.
    # Reuse the PDF's exact triangulation and frame for annotation anchors.
    pdf_parts=None
    if args.parts_dir:
        stl_parts,envelope=canonicalize(load_stl_parts(args.parts_dir))
        if not np.allclose(envelope,np.asarray(spec['envelope_wdh_mm'],float),atol=.2):
            raise ValueError('Occurrence STL envelope does not match specification')
        pdf_parts=match_parts(stl_parts,spec)
    # This phase is read-only; extract vector-heavy page text only once.
    page_texts=[page.get_text() for page in doc]
    assert len(meshes)==len(spec['occurrences'])==len(geo['parts'])
    occ={o['id']:o for o in spec['occurrences']};result=[]
    for family in spec['families']:
        ident=family['id']
        # Standard three-view layout may have two families OR one final family.
        # Both retain their generated dimensions and are not nine-grid pages.
        standard=[p for p in doc if page_texts[p.number].startswith('STANDARD PART FAMILY DETAILS | THREE VIEWS')
                  and re.search(r'\b'+re.escape(ident.upper())+r'\b',page_texts[p.number])]
        if len(standard)>1:raise ValueError(f'Duplicate standard family page: {ident}')
        if standard:
            page=standard[0];layout='standard'
        else:
            matches=[p for p in doc if page_texts[p.number].startswith(ident.upper()+'\n')]
            if len(matches)!=1:
                raise ValueError(f'Expected exactly one family page: {ident}')
            page=matches[0]
            if ('3 VIEWS PROVIDED' in page_texts[page.number] or
                    'DIFFICULT PART | STEP + THREE BLENDER VIEWS'
                    in page_texts[page.number]):
                print('STEP-provided three-view family:',ident,flush=True)
                continue
            if len(re.findall(r'FAMILY_\d+',page_texts[page.number]))>1:
                raise ValueError(f'Unexpected shared complex-family page: {ident}')
            layout='nine-grid'
        index=occ[family['occurrence_ids'][0]]['reference_index']
        # The original PDF loaded STL coordinates (float32); reproduce this frame.
        source=meshes[index].triangles.astype(np.float32).astype(float)
        pdf_source=pdf_parts[family['occurrence_ids'][0]] if pdf_parts is not None else source
        raw_bounds=np.stack([source.reshape(-1,3).min(axis=0),source.reshape(-1,3).max(axis=0)])
        pdf_bounds=np.stack([pdf_source.reshape(-1,3).min(axis=0),pdf_source.reshape(-1,3).max(axis=0)])
        offset=raw_bounds[0]-pdf_bounds[0]
        if np.max(np.abs((raw_bounds[1]-raw_bounds[0])-(pdf_bounds[1]-pdf_bounds[0])))>.02:
            raise ValueError(f'{ident}: occurrence STL does not match source mesh')
        aligned,dims=align_to_local_axes(pdf_source)
        header=re.search(r'LOCAL-AXIS X x Y x Z: ([\d.]+) x ([\d.]+) x ([\d.]+)',page_texts[page.number])
        if layout=='nine-grid' and header and np.max(abs(np.array(list(map(float,header.groups())))-dims))>.16:
            raise ValueError(f'{ident}: local dimensions mismatch; do not reuse this PDF')
        a=pdf_source.reshape(-1,3);b=aligned.reshape(-1,3);take=np.linspace(0,len(a)-1,min(len(a),1500)).astype(int)
        transform=np.linalg.lstsq(np.c_[a[take],np.ones(len(take))],b[take],rcond=None)[0]
        assert np.max(abs(np.c_[a[take],np.ones(len(take))]@transform-b[take]))<.001
        transform[3]-=offset@transform[:3]
        curves=[]
        arc_totals={}
        for edge in geo['parts'][index]['circles']:
            key=tuple(np.round([*edge['center'],edge['radius'],*edge['axis']],4))
            arc_totals[key]=arc_totals.get(key,0)+edge.get('arc_span',2*math.pi if edge.get('full_circle') else 0)
        for c in geo['parts'][index]['circles']:
            if not c.get('cylindrical'):continue
            if arc_totals[tuple(np.round([*c['center'],c['radius'],*c['axis']],4))]<math.pi-.01:continue
            axis=np.array(c['axis'])@transform[:3];axis/=np.linalg.norm(axis)
            def convert(v):return (np.r_[np.array(v)+geo['origin_raw'],1]@transform).tolist()
            curves.append(dict(radius=c['radius'],axis=axis.tolist(),center=convert(c['center']),rim=convert(c['rim'])))
        result.append(dict(family=ident,page=page.number,layout=layout,
                           dimensions=dims.tolist(),circles=curves,
                           source_index=index,world_to_local=transform.tolist()))
        print('Prepared',ident,flush=True)
    args.plan.write_text(json.dumps(result))

def annotate(args):
    doc=fitz.open(args.input);plans=json.loads(args.plan.read_text());audit=[]
    enhanced=bool(getattr(args,'enhanced',False))
    # In the default enhanced mode, placement and free space determine how
    # many verified dimensions fit.  Sparse mode retains its smaller budget.
    diameter_limit=None if enhanced else 2
    interval_limit=None if enhanced else 2
    axial_threshold=.985 if enhanced else .995
    min_paper_diameter=3 if enhanced else 8
    for family in plans:
        if family.get('layout','nine-grid')!='nine-grid':continue
        p=doc[family['page']];W,H=p.rect.width,p.rect.height;gap=8;pw=(W-72)/3;ph=(H-146)/3
        dims=family['dimensions'];circles=family['circles'];used=set();reserved=[];count=0
        # Occupancy is used only for layout; every number comes from STEP geometry.
        pix=p.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
        raster=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)
        def blank(rect):
            x0,y0,x1,y1=rect
            if any(not(x1<a or x0>c or y1<b or y0>d) for a,b,c,d in reserved):return False
            tile=raster[max(0,int(y0*2)):min(pix.height,int(y1*2)+1),max(0,int(x0*2)):min(pix.width,int(x1*2)+1)]
            return tile.size>0 and np.mean(np.min(tile,axis=2)<190)<.006
        views=[]
        for vi,axes in enumerate(((0,2),(1,2),(0,1))):
            x=28+vi*(pw+gap);y=92
            sc=min((pw-45)/dims[axes[0]],(ph-55)/dims[axes[1]])
            ox=x+31+(pw-45-dims[axes[0]]*sc)/2
            oy=y+ph-31-(ph-55-dims[axes[1]]*sc)/2
            def project(v,axes=axes,sc=sc,ox=ox,oy=oy):return np.array([ox+v[axes[0]]*sc,oy-v[axes[1]]*sc])
            views.append((axes,x,y,sc,project))
        # Group equal diameters within each projection; use blank-space checks
        # before adding any additional distinct size.
        for axes,x,y,sc,project in views:
            normal=next(k for k in range(3) if k not in axes);options=[];used=set();used_anchors=[]
            for c in circles:
                diameter=2*c['radius'];key=round(diameter,2)
                if (key in used or abs(c['axis'][normal])<axial_threshold or
                        diameter<max(3,.006*max(dims)) or
                        diameter*sc<min_paper_diameter):continue
                # A coaxial circular outer envelope is a true diameter.  Its
                # X/Y overall spans do not clearly convey that relation.
                outer_round=(normal==2 and abs(dims[0]-dims[1])<.01*max(dims[:2])
                             and abs(diameter-dims[0])<.01*max(dims[:2]))
                if diameter>.98*max(dims) and not outer_round:continue
                point=project(c['rim'])
                xx,yy=np.rint(point*2).astype(int)
                patch=raster[max(0,yy-3):yy+4,max(0,xx-3):xx+4]
                if patch.size==0 or not np.any(np.min(patch,axis=2)<190):continue
                t=f'Ø{diameter:.2f}';tw=fitz.get_text_length(t,fontname='helv',fontsize=8)
                for lx in (x+8,x+pw-tw-8,x+pw-tw-5,
                           x+.4*pw,x+.6*pw):
                    for baseline in (y+30,y+38,y+ph-36,y+ph/2,y+ph-20):
                        box=(lx-3,baseline-9,lx+tw+3,baseline+4)
                        if not blank(box):continue
                        end=np.array([lx if point[0]<lx+tw/2 else lx+tw,baseline+3])
                        options.append((np.linalg.norm(end-point),point,end,lx,baseline,t,box,key))
            available_count=(diameter_limit if diameter_limit is not None else
                             len({candidate[-1] for candidate in options}))
            for _ in range(available_count):
                available=[v for v in options if v[-1] not in used and blank(v[-2]) and all(np.linalg.norm(v[1]-a)>=8 for a in used_anchors)]
                if not available:break
                _,point,end,lx,baseline,t,box,key=min(available,key=lambda z:z[0])
                p.insert_text((lx,baseline),t,fontsize=8,fontname='helv',color=(1,0,0))
                elbow=end+np.array([-8 if end[0]<=lx else 8,0])
                p.draw_line(fitz.Point(*point),fitz.Point(*elbow),color=(1,0,0),width=.65)
                p.draw_line(fitz.Point(*elbow),fitz.Point(*end),color=(1,0,0),width=.65)
                p.draw_circle(fitz.Point(*point),1.2,color=None,fill=(1,0,0));reserved.append(box);used.add(key);used_anchors.append(point);count+=1
                audit.append(dict(family=family['family'],kind='diameter',view=list(axes),label=t,page=p.number+1))
        # One local axial interval or aligned hole-center distance; never a bounding box difference.
        options=[]
        for vi,(axes,x,y,sc,project) in enumerate(views):
            chain_pitches = (repeated_horizontal_pitches(circles, dims, axes)
                             if enhanced else set())
            for i,c in enumerate(circles):
                for q in circles[i+1:]:
                    a=np.array(c['center']);b=np.array(q['center']);delta=b-a
                    if abs(np.dot(c['axis'],q['axis']))<.999:continue
                    for axis in axes:
                        val=abs(delta[axis]);others=[k for k in range(3) if k!=axis]
                        if np.max(abs(delta[others]))>.03 or val<max(.01*max(dims),8/sc) or val>=.98*dims[axis]:continue
                        if axis == axes[0] and round(val, 2) in chain_pitches:
                            continue
                        normal=next(k for k in range(3) if k not in axes)
                        if abs(c['axis'][axis])<.995 and abs(c['axis'][normal])<.995:continue
                        aa=project(a);bb=project(b);horizontal=axis==axes[0]
                        # Reserve a short exterior rail near the feature and keep labels horizontal.
                        t=f'{val:.2f}';tw=fitz.get_text_length(t,fontname='helv',fontsize=8)
                        for rail in ((y+30,y+ph-35) if horizontal else (x+42,x+pw-18)):
                            if horizontal:
                                u=np.array([aa[0],rail]);v=np.array([bb[0],rail]);lx=(u[0]+v[0]-tw)/2;by=rail-4
                            else:
                                u=np.array([rail,aa[1]]);v=np.array([rail,bb[1]]);lx=rail-tw-4;by=(u[1]+v[1])/2
                            box=(lx-3,by-9,lx+tw+3,by+3)
                            if box[0]<x+6 or box[2]>x+pw-6 or box[1]<y+20 or box[3]>y+ph-25 or not blank(box):continue
                            options.append((np.linalg.norm(aa-u)+np.linalg.norm(bb-v),aa,bb,u,v,lx,by,t,box,vi))
        for vi in range(3):
            used_intervals=set()
            available_count=(interval_limit if interval_limit is not None else
                             len({candidate[-3] for candidate in options
                                  if candidate[-1]==vi}))
            for _ in range(available_count):
                available=[v for v in options if v[-1]==vi and v[-3] not in used_intervals and blank(v[-2])
                           and not any(not(max(v[3][0],v[4][0])+2<a or min(v[3][0],v[4][0])-2>c or
                                           max(v[3][1],v[4][1])+2<b or min(v[3][1],v[4][1])-2>d)
                                       for a,b,c,d in reserved)]
                if not available:break
                _,a,b,u,v,lx,by,t,box,_=min(available,key=lambda z:z[0])
                for aa,bb in ((a,u),(b,v),(u,v)):p.draw_line(fitz.Point(*aa),fitz.Point(*bb),color=(1,0,0),width=.65)
                for point in (u,v):p.draw_circle(fitz.Point(*point),1.2,color=None,fill=(1,0,0))
                p.insert_text((lx,by),t,fontsize=8,fontname='helv',color=(1,0,0));count+=1
                reserved.append(box);reserved.append((min(u[0],v[0])-2,min(u[1],v[1])-2,max(u[0],v[0])+2,max(u[1],v[1])+2));used_intervals.add(t)
                audit.append(dict(family=family['family'],kind='local interval',view=list(views[vi][0]),label=t,page=p.number+1))
        print(f"{family['family']}: {count} annotations",flush=True)
    cover=doc[0]
    statements=[s for b in cover.get_text('dict')['blocks'] for line in b.get('lines',[]) for s in line['spans'] if 'Line views show overall dimensions' in s['text']]
    for span in statements:cover.add_redact_annot(span['bbox'],fill=None)
    if statements:
        cover.apply_redactions(images=0,graphics=0)
        for span in statements:cover.insert_text(span['origin'],'Line views retain overall dimensions; sparse local diameters and distances are added (mm).',fontsize=8,fontname='helv',color=(.19,.21,.26))
    tmp=args.output.with_suffix('.annotating.pdf');doc.save(tmp,garbage=4,deflate=True);doc.close();os.replace(tmp,args.output);args.output.chmod(0o644)
    args.plan.with_suffix('.audit.json').write_text(json.dumps(audit,indent=2))

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path)
    ap.add_argument('--plan',type=Path,required=True);ap.add_argument('--prepare',action='store_true')
    annotation_mode=ap.add_mutually_exclusive_group()
    annotation_mode.add_argument('--enhanced',dest='enhanced',action='store_true',default=True,
                                 help='Default: place additional measurable local dimensions where space permits')
    annotation_mode.add_argument('--sparse',dest='enhanced',action='store_false',
                                 help='Use the older, lower local-dimension limits')
    for key in ('spec','geometry','meshes','parts-dir'):ap.add_argument('--'+key,type=Path)
    a=ap.parse_args()
    if a.prepare:
        if not all((a.spec,a.geometry,a.meshes)):ap.error('prepare requires spec, geometry and meshes')
        prepare(a)
    else:
        if not a.output:ap.error('output required')
        annotate(a)
if __name__=='__main__':main()
