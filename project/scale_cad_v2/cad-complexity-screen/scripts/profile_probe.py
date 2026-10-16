import json
import argparse
from pathlib import Path
import numpy as np
import trimesh
from scipy.spatial import ConvexHull

def rdp(points, epsilon):
    if len(points) <= 2:
        return points
    p0, p1 = points[0], points[-1]
    vec = p1 - p0
    length = np.linalg.norm(vec)
    if length < 1e-9:
        distances = np.linalg.norm(points - p0, axis=1)
    else:
        delta=points-p0
        distances = np.abs(vec[0]*delta[:,1]-vec[1]*delta[:,0]) / length
    index = int(np.argmax(distances))
    if distances[index] <= epsilon:
        return points[[0, -1]]
    return np.concatenate([rdp(points[:index+1], epsilon)[:-1], rdp(points[index:], epsilon)])

def closed_rdp(points, epsilon):
    points = np.asarray(points, float)
    if np.linalg.norm(points[0]-points[-1]) < 1e-6:
        points = points[:-1]
    start = int(np.argmin(points[:,0]))
    end = int(np.argmax(np.linalg.norm(points-points[start],axis=1)))
    if start > end: start,end=end,start
    a = rdp(points[start:end+1], epsilon)
    b = rdp(np.concatenate([points[end:],points[:start+1]]), epsilon)
    return np.concatenate([a[:-1],b[:-1]])

def profile(mesh, fraction, epsilon=.5):
    ext = mesh.extents
    axis = int(np.argmax(ext))
    normal = np.zeros(3); normal[axis]=1
    origin = mesh.bounds[0].copy(); origin[axis] += ext[axis]*fraction
    section=mesh.section(plane_origin=origin,plane_normal=normal)
    if section is None: return None
    result=[]
    for loop in section.discrete:
        points=closed_rdp(np.asarray(loop)[:,[i for i in range(3) if i!=axis]], epsilon)
        if len(points)<3: continue
        prev=np.roll(points,1,axis=0); nxt=np.roll(points,-1,axis=0)
        e1=points-prev; e2=nxt-points
        cross=e1[:,0]*e2[:,1]-e1[:,1]*e2[:,0]
        sine=cross/(np.linalg.norm(e1,axis=1)*np.linalg.norm(e2,axis=1)+1e-9)
        area=np.sum(points[:,0]*nxt[:,1]-points[:,1]*nxt[:,0])/2
        reflex=int(np.sum(sine*np.sign(area)<-0.25))
        hull=ConvexHull(points)
        result.append((len(points),reflex,round(abs(area),2),round(1-abs(area)/hull.volume,3),round(np.linalg.norm(nxt-points,axis=1).sum()/hull.area,3)))
    return sorted(result,key=lambda x:x[2],reverse=True)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='Inspect three cross-sections of an aligned STL')
    parser.add_argument('mesh',type=Path)
    args=parser.parse_args()
    mesh=trimesh.load_mesh(args.mesh,process=False)
    print(json.dumps([profile(mesh,f) for f in (.3,.5,.7)]))
