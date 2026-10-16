# 几何驱动的自动标注与完整性交付

> 作者侧历史实验记录，入口为 `run_complete_annotation.py`；不是当前 Harbor 四档 `parts.pdf` 的默认构建流程。新题应按 [SKILL.md](SKILL.md) 和 [README.md](README.md) 使用 `build_parts.py`。下文的 case 数据只说明当时的试验结果，不作为当前脚本的默认参数或分档标准。

入口：`scripts/run_complete_annotation.py`。它针对一个 Harbor case 的「优先／普通」候选 family，生成带原三视图和四张 Blender 图的 `dimensioned_parts.pdf`、`dimension-plan.json`、`delivery-manifest.json`，以及需要时的 `provided_part_files/family_XXX.step`。图册是作者侧试验输出；脚本不会改写原 Harbor 题包。

流程完全由脚本执行：

1. 核对原始装配 STEP 的 SHA-256 与本轮网格 catalog，核对 family、页码、局部坐标变换和 STL 包围尺寸。不同批次的同名 family 不混用。
2. 从解析 STEP 圆边合并同轴圆弧与正反面圆环；从局部 STL 的水平平面提取闭合轮廓与内环。优先选择圆孔能与解析圆匹配的平面，而不是仅选择面积最大的平面。
3. 将轮廓拐点变为相邻边距尺寸链，将孔中心变为边距和中心距尺寸链；同径特征仅标一次直径。过短的相邻距离改用错位引线标值，不能因为文字放不进尺寸线就静默丢弃。折弯壳体不再只取单个水平面：在 TOP 投影中汇集不同高度和斜面的解析圆孔，按孔列标中心距与真实直径；从原始 STEP 的同心圆弧和 STL 截面交叉核验侧面弯曲半径、板厚以及局部叠层厚度。将 STEP 圆弧端点与相切的直线边匹配，在 SIDE 标注圆弧前的直壁长度及圆弧后的斜直段长度，使弯曲起止位置可判读。对角切边用 X/Y 两个投影腿长标注。只在真实圆边上放 Ø，中心标记不当成内径。对于贯通竖孔与正交盲孔共存的结构，先核对 STEP 的竖孔端面圆边和网格中截面；将 FRONT 中密集的圆柱分面线视为同一孔壁，标顶面盲孔的圆柱直段深度，并在 SIDE 标竖孔投影位置与侧孔的圆柱直段深度。有现成 SIDE 补充页时直接合并标注，否则补一张 SIDE 页；不按 family 编号处理。对于解析 STEP 能识别、且 STL 中间截面证实的重复锥形沉孔，自动选 X-Z 或 Y-Z 中投影孔位更少的一面，补画隐线侧视图，标锥口直径、直孔直径、锥段深度与直孔深度；锥口在上或在下都适用。对同轴回转法兰，若 STEP 圆环的轴向位置和网格中心截面吻合，补画 FRONT 轴向截面，标凸台高度、法兰厚度；圆弧环槽和底部倒角只在截面拟合通过后标 R 与腿长。没有通过截面验证的细节不画推测尺寸。
4. 独立核对解析 STEP 的每组圆柱特征是否已有可判读的尺寸链。主视图容纳不下的孔排，以及其他未覆盖的正交圆柱，按 TOP、FRONT、SIDE 投影方向各合成一张放大页：同一视角的不同孔径不重复铺图，同径孔只标一次 Ø，并用引线指到解析圆边；相同的中心距尺寸链去重，连续等距的孔距用 `Nx pitch` 标一次并保留各中心刻线。若普通平面轮廓补充页与孔位放大页同为 TOP，则将轮廓、孔径、孔距合并到一张 TOP 详细页。对没有单一平面轮廓的长壳体，脚本还会查找网格中面积、纵深和高度都足够的轴向端面及纵向壁面，从确实存在的平面提取 X/Y 尺寸站位；当两个方向均有多处可靠站位时，将其与孔位合到同一张 TOP 页，省去空白的 TOP 预览。曲面三角分面不会作为尺寸站位。**不生成单独的圆柱尺寸清单页**。斜轴孔及自由曲面仍由单件 STEP 精确表达，并在审计中单独计数。
5. 对已支持的实体结构重建体积和环拓扑。当前有「带矩形缺口与底面斜切的薄板」及「等厚顶板加侧壁的盖板」两种可认证结构。通过验证的 family 使用尺寸 PDF；其他结构的 PDF 只展示可确定的局部尺寸，不声称图纸已经完整。
6. 对未通过三维完整性验证的 family，自动从**同一原始装配 STEP**按本轮 `reference_index` 导出局部单实体 STEP。导出器核对源哈希、体积、面积、尺寸、局部坐标及重新读取后的实体数。解析曲面优先做刚体 BRep 变换，避免通用曲面转换改变体积。PDF 标出对应 `provided_part_files/family_XXX.step`；只有 STEP 实际存在才打印该提示。
7. 生成审计 manifest，逐 family 记录 `dimensioned_pdf` 或 `dimensioned_pdf_plus_step`、新增放大视图页数、斜轴圆柱数量、验证方法及无法解释的几何原因。`scripts/verify_annotation_batch.py` 对全部 PDF 页渲染、检查页数与文字边界，并核对每个 STEP 文件。

例如对 `cad_01_g001`，从仓库根目录运行：

```bash
WORK=/path/to/private-work
python parts_pdf/scripts/run_complete_annotation.py \
  --case cad_01_g001 \
  --step "$WORK/model_0/reference.step" \
  --spec "$WORK/parts_0/part-spec.json" \
  --catalog "$WORK/parts_0/geometry/catalog.json" \
  --geometry "$WORK/parts_0/analytic/manual_geometry.json" \
  --features "$WORK/parts_0/feature-annotations.json" \
  --meshes "$WORK/parts_0/aligned" \
  --renders "$WORK/parts_0/renders" \
  --candidates "$WORK/中等难度零件候选.json" \
  --input /path/to/harbor/cad_01_g001/environment/input/parts.pdf \
  --work-dir "$WORK/general_annotation_cad01"
```

其他 case 只需传入其同批次 `model_N/reference.step`、`parts_N` 缓存和 Harbor PDF；图形算法没有 family 编号分支。单件 STEP 导出默认调用宿主机 `python`；若其无法导入 FreeCAD，设置 `FREECAD_PYTHON` 和 `FREECAD_LIB`。

已有同批次 case inventory 时，可以用一个命令处理候选清单中的所有 case：

```bash
python parts_pdf/scripts/run_batch_annotation.py \
  --inventory "$WORK/complexity_selection_contract_test/selected.json" \
  --candidates "$WORK/中等难度零件候选.json" \
  --harbor-root /path/to/harbor \
  --input-fallback-root "$WORK/step_provision_staging" \
  --output-root "$WORK/general_annotation_batch"
```

试跑可加 `--cases cad_01_g001 cad_02_g002`。当 Harbor 的 `parts.pdf` 与本轮 family／页码不符时，只能改用 `--input-fallback-root` 下逐页核对匹配的同批 PDF。批跑遇到来源哈希、family 对应或 STEP 回读验证失败会直接停止，不继续生成似是而非的交付。

已做跨 case 实测：`cad_01_g001` 的 6 件中 2 件通过几何验证、4 件自动导出 STEP；`cad_02_g002` 的 9 件中 1 件通过验证、8 件自动导出 STEP。高召回版本的两份 PDF 分别为 12、19 页；跨孔排共享 X 基准的特征不会重复生成放大页。`cad_02_g002` 的 `family_021` 与 `family_022` 分别检出了上锥口与下锥口，自动补画了 SIDE Y-Z 孔形和深度标注；`cad_02_g002` 的 `family_004` 和 `cad_01_g001` 的 `family_008` 均触发了同一轴向法兰规则。测试结果说明流程能够保守地批量交付，同时也说明目前的**纯 PDF 完整标注覆盖率仍低**；不能把其余 family 的局部尺寸页称作完整工程图。

10 个 Harbor case 的高召回复跑输出在 `$WORK/general_annotation_high_recall_10cases`：247 个候选 family、1007 页 PDF，其中 513 页是额外的正交图形标注页；1253 组原先没有完整图形标注的正交圆柱特征进入这些放大页，108 组斜轴圆柱由单件 STEP 承载。244 件附有单件 STEP，另 3 件通过尺寸图的体积／拓扑完整性认证。`scripts/verify_annotation_batch.py` 已渲染每一页并核对页数、文字边界、旧式圆柱清单页缺失和 STEP 文件存在性，10 个 case 均无审计问题。检查记录见 `render_qa.json`，图形抽样见 `qa_first_projection_montage.png`。这些数字是**候选中等难度 family 的交付与圆柱特征覆盖**，不是所有几何特征均可由 PDF 重建的证明。

这里的「完整」指**交付的几何定义完整**：通过验证时由尺寸图表达，未通过时由尺寸图加 STEP 表达。仅从 CAD 几何无法推断设计基准、公差、表面粗糙度和制造工艺，因此该流程不宣称自动生成可直接投产的完整工程图。要提高纯 PDF 的覆盖率，应继续添加经过体积和拓扑验证的通用结构识别器，例如折弯板、回转体和复杂壳体；不能通过调低验证阈值将遗漏伪装为完整标注。

## 按实际标注缺口筛选 STEP 的试验

`scripts/screen_annotation_feasibility.py` 直接检查**已有的详细尺寸 PDF**，不重新导出零件、不重生成图纸，也不核验原有 STEP。`annotation_feasibility.py` 先用圆柱组数量、直径种类、斜轴、投影重叠和轮廓拐点发现可能难以标注的零件，再检查实际图纸是否缺少解析直径、遗漏斜轴特征、尺寸文字严重重叠、标注量明显不足，或不规则轮廓没有另一个视向。**仅有复杂几何或未通过严格三维重建认证，不会新增 STEP**。规则不包含 case 或 family 编号，判据及实测数据写入 JSON。这是轻量筛选，不是图纸完整性的数学证明。

```bash
python parts_pdf/scripts/screen_annotation_feasibility.py \
  --case cad_01_g001 \
  --candidates "$WORK/中等难度零件候选.json" \
  --features "$WORK/parts_0/feature-annotations.json" \
  --dimensioned-dir "$WORK/general_annotation_high_recall_10cases/cad_01_g001" \
  --output "$WORK/general_annotation_high_recall_10cases/cad_01_g001/annotation-feasibility-screen.json"
```

在 `cad_01_g001` 的 6 个中等候选上，脚本筛出 0 个需要新增 STEP 的零件。`family_007/008/014/019` 均保留详细尺寸图；原题包和原有 6 个高难 STEP 未改动。即使 `family_019` 的几何密度较高，其现有补充页已显示解析直径和其他尺寸，所以不会仅凭密度将它提升为 STEP 件。旧的严格几何认证结果仍保存在作者侧，但不用于本次 STEP 优先判定。
