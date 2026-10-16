"""Blender background renderer; use pipeline.py render."""
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

args=sys.argv[sys.argv.index('--')+1:];out=Path(args[0]);assembled='--assembled' in args
cat=json.loads((out/'catalog.json').read_text());plan=json.loads((out/'plan.json').read_text())
assert plan['source_sha256']==cat['source_sha256'],'Source checksum mismatch'
assert plan['catalog_sha256']==hashlib.sha256((out/'catalog.json').read_bytes()).hexdigest(),'Catalog changed; regenerate/remap plan'
with gzip.open(out/'meshes.json.gz','rt') as f:meshes=json.load(f)
ids={q['id'] for q in cat['parts']}
assert ids and len(ids)==len(cat['parts'])==len(meshes)
assert {m['id'] for m in meshes}==ids
assert set(plan['offsets'])==ids,'Provide exactly one offset for every exported solid'
def vec(v):
    assert len(v)==3 and all(isinstance(x,(int,float)) and math.isfinite(x) for x in v),v
    return Vector(v)
for delta in plan['offsets'].values():vec(delta)
direction=vec(plan['direction']);assert direction.length>0;direction.normalize()
w,h=plan.get('resolution',[1800,2200]);assert all(isinstance(x,int) and 256<=x<=12000 for x in [w,h])
padding=plan.get('padding',1.22);assert math.isfinite(padding) and padding>=1.05
for c in plan.get('callouts',[]):
    assert c['part'] in ids and c.get('side','auto') in ['auto','left','right']
    if 'anchor' in c:vec(c['anchor'])
for g in plan.get('guides',[]):vec(g['start']);vec(g['end'])
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.context.preferences.filepaths.save_version=0
scene=bpy.context.scene;scene.render.engine='BLENDER_WORKBENCH'
shade=scene.display.shading;shade.light='STUDIO';shade.studiolight_rotate_z=.5
shade.color_type='SINGLE';shade.single_color=tuple(plan.get('color',[.82,.82,.82]))
shade.show_shadows=True;shade.show_cavity=True;shade.cavity_type='BOTH'
shade.curvature_ridge_factor=1.4;shade.curvature_valley_factor=1.2
shade.show_object_outline=True;shade.object_outline_color=(.035,.04,.055)
scene.render.film_transparent=True;scene.view_settings.view_transform='Standard'
scene.render.resolution_x=w;scene.render.resolution_y=h;scene.render.resolution_percentage=100
scene.display.render_aa='32';objects={}
for m in meshes:
    mesh=bpy.data.meshes.new(m['id']);mesh.from_pydata(m['vertices'],[],m['faces']);mesh.update()
    obj=bpy.data.objects.new(m['id'],mesh);scene.collection.objects.link(obj)
    obj.location=(0,0,0) if assembled else plan['offsets'][m['id']]
    objects[m['id']]=obj
bpy.context.view_layer.update()
points=[obj.matrix_world@Vector(p) for obj in objects.values() for p in obj.bound_box]
low=Vector([min(p[j] for p in points) for j in range(3)])
high=Vector([max(p[j] for p in points) for j in range(3)])
center=(low+high)/2;extent=max((high-low).length,1e-3)
bpy.ops.object.camera_add();cam=bpy.context.object;cam.name='ASSEMBLED' if assembled else 'EXPLODED'
cam.data.type='ORTHO';scene.camera=cam
cam.location=center+direction*extent*4;cam.rotation_euler=(-direction).to_track_quat('-Z','Y').to_euler()
cam.data.clip_start=max(extent*1e-5,1e-6);cam.data.clip_end=extent*12
bpy.context.view_layer.update()
local=[cam.matrix_world.inverted()@p for p in points]
lo=[min(p[j] for p in local) for j in (0,1)];hi=[max(p[j] for p in local) for j in (0,1)]
cam.location+=cam.rotation_euler.to_matrix()@Vector(((lo[0]+hi[0])/2,(lo[1]+hi[1])/2,0))
# Blender's AUTO sensor fit uses horizontal ortho span for landscape images.
cam.data.ortho_scale=max(hi[0]-lo[0],(hi[1]-lo[1])*w/h)*padding if w>=h else max(hi[1]-lo[1],(hi[0]-lo[0])*h/w)*padding
bpy.context.view_layer.update()
def project(p):
    q=world_to_camera_view(scene,cam,Vector(p));return [q.x*w,(1-q.y)*h]
labels=[]
if not assembled:
    for n,c in enumerate(plan.get('callouts',[]),1):
        obj=objects[c['part']]
        anchor=(Vector(c['anchor'])+obj.location) if 'anchor' in c else sum([obj.matrix_world@Vector(v) for v in obj.bound_box],Vector())/8
        labels.append(dict(number=n,title=c['text'],xy=project(anchor),side=c.get('side','auto')))
guides=[] if assembled else [dict(start=project(g['start']),end=project(g['end'])) for g in plan.get('guides',[])]
prefix='assembled' if assembled else 'exploded'
metadata=dict(title=plan['title'],width=w,height=h,labels=labels,guides=guides,
              occurrence_count=len(objects),assembled=assembled)
(out/f'{prefix}_labels.json').write_text(json.dumps(metadata,indent=2))
scene.render.filepath=str(out/f'{prefix}_render.png')
bpy.ops.wm.save_as_mainfile(filepath=str(out/f'{prefix}.blend'))
bpy.ops.render.render(write_still=True)
