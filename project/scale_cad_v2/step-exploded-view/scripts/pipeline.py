"""Portable command wrapper. No model-specific IDs or displacement rules."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent

def run(args):
    print('Running:', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str,args)), check=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command', required=True)
    x=sub.add_parser('extract');x.add_argument('step',type=Path);x.add_argument('output',type=Path)
    x.add_argument('--freecad-python',type=Path)
    x.add_argument('--freecad-lib',type=Path)
    x.add_argument('--tolerance',type=float,default=.15)
    x=sub.add_parser('init-plan');x.add_argument('output',type=Path);x.add_argument('--title',default='EXPLODED ASSEMBLY')
    x=sub.add_parser('render');x.add_argument('output',type=Path);x.add_argument('--blender');x.add_argument('--assembled',action='store_true')
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    if a.command=='extract':
        src=a.step.resolve(strict=True)
        freecad_python = str(a.freecad_python or os.environ.get('FREECAD_PYTHON', 'python'))
        freecad_lib = a.freecad_lib or os.environ.get('FREECAD_LIB')
        if not shutil.which(freecad_python):
            p.error('FreeCAD Python not found; set FREECAD_PYTHON or pass --freecad-python')
        if (out/'catalog.json').exists() or (out/'plan.json').exists():
            p.error('Use a fresh output directory; extraction could invalidate an existing plan.')
        # Stage a normal readable copy, leaving source permissions unchanged.
        staged=out/'source.step'
        if staged.exists():p.error('Output contains source.step; choose a fresh directory.')
        shutil.copyfile(src,staged);staged.chmod(0o644)
        out.chmod(0o755)
        try:
            run([freecad_python,HERE/'extract_step.py',staged,out,
                 '--tolerance',a.tolerance]+(['--freecad-lib',freecad_lib] if freecad_lib else []))
        finally:
            staged.unlink(missing_ok=True)
        catalog_path=out/'catalog.json';catalog=json.loads(catalog_path.read_text())
        catalog['source_name']=src.name;catalog_path.write_text(json.dumps(catalog,indent=2))
    elif a.command=='init-plan':
        target=out/'plan.json'
        if target.exists():p.error('plan.json exists; edit it rather than overwrite it.')
        cat=json.loads((out/'catalog.json').read_text())
        plan=dict(schema_version=1,source_sha256=cat['source_sha256'],
                  catalog_sha256=hashlib.sha256((out/'catalog.json').read_bytes()).hexdigest(),
                  title=a.title,direction=[1.35,-1.8,1.1],resolution=[1800,2200],
                  padding=1.22,color=[.82,.82,.82],
                  offsets={q['id']:[0,0,0] for q in cat['parts']},callouts=[],guides=[])
        target.write_text(json.dumps(plan,indent=2))
        print('Created zero-offset plan. Inspect assembled view, then edit offsets/callouts.')
    else:
        blender = shutil.which(a.blender or os.environ.get('BLENDER_BIN', 'blender'))
        if not blender:
            p.error('Blender not found; set BLENDER_BIN or pass --blender')
        run([blender,'-b','-t','4','--python-exit-code','1','--python',HERE/'render.py','--',out]+(['--assembled'] if a.assembled else []))
        run([sys.executable,HERE/'annotate.py',out]+(['--assembled'] if a.assembled else []))
if __name__=='__main__':main()
