# 当前分层装配图流程

以下实例对应本仓库的 [cad_03_g009 示例题包](../../example_harbor/cad_03_g009)。六组分法见 [group plan](cad03-group-plan.json)；其实体 ID 与源哈希只适用于这道题。其他模型要重新检查几何并编写分组计划。源码中的尺寸与轮廓逻辑不含本题的实体 ID。

从同一份装配 STEP 生成几何清单、网格和 family spec，并把示例计划复制到本次独立工作目录。计划中的相对输入路径从**计划文件所在目录**解析；输出的渲染缓存也留在该目录。

```bash
REPO=/path/to/scale_cad_v2
CASE="$REPO/example_harbor/cad_03_g009"
WORK=/absolute/path/to/new-empty-assembly-work
mkdir -p "$WORK"
python "$REPO/step-exploded-view/scripts/pipeline.py" extract \
  "$CASE/tests/reference/reference.step" "$WORK/geometry"
python "$REPO/parts_pdf/scripts/extract_part_spec.py" \
  --step "$WORK/geometry/meshes.json.gz" --output "$WORK/part-spec.json"
cp "$REPO/cad-dimension-sheets/references/cad03-group-plan.json" "$WORK/group-plan.json"
```

检查 `group-plan.json` 的 `source_sha256`、每组 `parts`、`stages` 顺序与刚提取的 `catalog.json`。若新一轮抽取改变了实体 ID 或 family 合并结果，不要硬套示例计划；按这轮几何重写分组。

```bash
python "$REPO/assembly-substructures/scripts/render_assembly.py" \
  "$WORK/group-plan.json"

python "$REPO/assembly-substructures/scripts/build_orthographic.py" \
  "$WORK/group-plan.json"

python "$REPO/assembly-substructures/scripts/build_assembly_book.py" \
  "$WORK/group-plan.json" "$WORK/assembly.pdf"

python "$REPO/cad-xray-dxf/scripts/generate_dxf.py" \
  --step "$CASE/tests/reference/reference.step" \
  --output-dir "$CASE/environment/input"
```

`build_assembly_book.py` 自动调用 `build_pdf.py` 与每组的 `build_detailed_stage.py`。默认把每步 TOP/FRONT/SIDE 按原 A3 尺寸横排在一张宽幅页上；基本页数为 `1 + 3 × 组数`，较长的零件族清单会增加独立页。显式传 `--separate-view-pages` 才使用旧的单视图单页排版，基本页数为 `1 + 5 × 组数`。组内图的短清单最多 25 个 family；更多时排到独立清单页。三视尺寸图的底部短清单最多 16 个 family；更多时仅显示摘要并指向独立清单页。三个出图脚本都运行 `family_layout.py` 的文字区域校验，清单越过面板或压到页脚时必须修复后重出。直径引线若被后绘制的实体投影遮住，就在该视图省略；例如 G3 俯视图不再把 Ø40 引线指向圆盘面，正视图仍可标轴径。需要单独调试某一步时可运行 `build_detailed_stage.py group-plan.json G6 G6_dimensioned.pdf`。新流程依赖 Blender、NumPy、OpenCV、ezdxf、Pillow、ReportLab 与 PyMuPDF；字体默认为本机 `/opt/X11/share/system_fonts/Supplemental/Arial Unicode.ttf`，换机器时在 `build_assembly_book.py` 命令后传 `--font /absolute/CJK-font.ttf`。实际运行前用 `--help` 核对所用脚本的参数。

作者侧逐步 DXF 和正交尺寸页描述各步**装入后**的位置，不是爆炸图；爆炸示意位移不能读作安装尺寸。正交轮廓追踪显示网格，弧线为近似；X/Y/Z 位置数字取自 CAD 清单。每步核对新增零件与先前组的关系，三视方向为 TOP=(X,Y)、FRONT=(X,Z)、SIDE=(Y,Z)，不逐件重定向。出图后逐页渲染 PDF 并目视检查文字裁切、清单框与页脚、尺寸端点、轮廓缺失及重叠；自动校验只覆盖 family 标签的区域，不能替代目视检查。未经过几何验证的接触或装配可达性不作承诺。

`cad-dimension-sheets/scripts/extract_step.py` 仍提供解析 BRep 特征缓存，供零件标注等流程使用。`render_sheets.py` 与旧 plan 属于旧式整机分组页的兼容路径，不参与上面的分层装配图生成。
