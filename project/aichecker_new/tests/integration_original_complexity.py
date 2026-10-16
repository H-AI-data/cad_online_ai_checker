"""手动正例集成：生成36孔试件，实际运行原STEP门禁和原复杂度引擎。

python tests/integration_original_complexity.py /绝对路径/新的测试目录
输出保留以便检查；不修改用户输入、不调用AI，不加入默认快速单元测试。
"""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    import cadquery as cq
    from common.pipeline import main as check
    root=Path(sys.argv[1]).resolve()
    root.mkdir(parents=True,exist_ok=False)
    source=root/'输入';source.mkdir()
    plate=cq.Workplane('XY').box(50,50,8).faces('>Z').workplane().rarray(7,7,6,6).hole(3)
    cq.exporters.export(plate,str(source/'36孔测试件.step'))
    output=root/'结果'
    code=check(['--input-dir',str(source),'--module','1','--dimension','2','--llm','off',
                '--output-dir',str(output)])
    summary=json.loads((output/'summary.json').read_text())
    assert code==0,summary
    assert summary['preflight']['status']=='passed',summary
    assert summary['results'][0]['status']=='passed',summary
    assert summary['llm']['calls']==0,summary
    completion=json.loads((output/'module_01/dimension_02_complexity/original_engine/completion.json').read_text())
    assert completion['source_unchanged'] and len(completion['scanners'])==14,completion
    print('正例集成通过：原STEP门禁、14个原扫描器、真实候选、零AI调用。')


if __name__=='__main__':
    main()
