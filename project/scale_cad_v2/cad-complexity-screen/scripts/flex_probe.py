from pathlib import Path
import numpy as np
import trimesh

def loop_metrics(points, axis):
    uv=np.asarray(points)[:,[i for i in range(3) if i!=axis]]
    if np.linalg.norm(uv[0]-uv[-1])>1e-4:return None
    xy=uv[:-1]
    nxt=np.roll(xy,-1,axis=0)
    cross=xy[:,0]*nxt[:,1]-nxt[:,0]*xy[:,1]
    signed=cross.sum()/2
    area=abs(signed)
    if area<1e-4:return None
    center=np.array([((xy[:,0]+nxt[:,0])*cross).sum(),((xy[:,1]+nxt[:,1])*cross).sum()])/(6*signed)
    perimeter=np.linalg.norm(xy-nxt,axis=1).sum()
    circularity=4*np.pi*area/perimeter**2
    equivalent_diameter=2*np.sqrt(area/np.pi)
    return dict(area=float(area),perimeter=float(perimeter),diameter=float(equivalent_diameter),
                circularity=float(circularity),center=center.tolist())

def sections(mesh,fractions=np.linspace(.05,.95,19)):
    axis=int(np.argmax(mesh.extents)); extent=mesh.extents[axis]
    normal=np.zeros(3);normal[axis]=1
    rows=[]
    for f in fractions:
        point=mesh.bounds[0].copy();point[axis]+=extent*f
        section=mesh.section(plane_origin=point,plane_normal=normal)
        loops=[] if section is None else [m for poly in section.discrete if (m:=loop_metrics(poly,axis))]
        rows.append((round(float(f),3),sorted(loops,key=lambda v:v['area'],reverse=True)))
    return axis,rows

if __name__=='__main__':
    import sys
    p = Path(sys.argv[1])
    mesh=trimesh.load_mesh(p,process=False)
    axis,rows=sections(mesh)
    print('axis',axis,'extents',mesh.extents)
    for f,loops in rows:
        print(f,[(round(m['diameter'],2),round(m['circularity'],2),[round(v,2) for v in m['center']]) for m in loops[:4]])
