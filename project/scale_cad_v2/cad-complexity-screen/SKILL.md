---
name: cad-complexity-screen
description: 从 parts.pdf、零件族 spec 与对齐 STL 中，以纯脚本高召回筛选难以完整尺寸标注的 CAD 零件，并输出可由 parts-pdf 使用的带来源校验的 STEP 交付清单。
---

# 复杂 CAD 零件筛选

使用本 skill 的 [run.py](scripts/run.py) 运行筛选；几何分析、三视图 OCR、弱信号排除、汇总和预览均由脚本完成。规则不含特定 case/family 白名单，目标是高召回；低精度信号会保留合理的待复核件。独立运行时结果是复核候选；`parts-pdf` 的四档构建将困难候选及后续几何升级件提供单件 STEP，并在导出及出图前核对来源。筛选脚本本身不写 Harbor input，也不导出 STEP。

## 输入与运行

需要 macOS 的 Swift Vision、Python 3.12+，以及 `numpy`、`scipy`、`trimesh`、`PyMuPDF`、`Pillow`、`reportlab`。使用当前环境的 `python`，缺少依赖时安装 [requirements.txt](scripts/requirements.txt)。输入格式见 [数据契约](references/input-contract.md)。每题需有 `environment/input/parts.pdf`，并有与该 PDF family 编号、数量和尺寸一致的 `part-spec.json`、`aligned/manifest.json`、`aligned/family_*.stl`、`geometry/catalog.json`。若对应关系无法验证，停止，不按编号猜测。

在 `scale_cad_v2` 根目录先用 `--check-inputs` 核对映射，再完整运行：

```bash
MANIFEST=/absolute/path/to/selected.json
HARBOR=/absolute/path/to/full-baseline-harbor
WORK=/absolute/path/to/author-work

python cad-complexity-screen/scripts/run.py --manifest "$MANIFEST" --harbor-dir "$HARBOR" --work-dir "$WORK" --check-inputs
python cad-complexity-screen/scripts/run.py --manifest "$MANIFEST" --harbor-dir "$HARBOR" --work-dir "$WORK"
```

`HARBOR` 必须指向本轮完整图册基线目录。`--work-dir` 保存扫描中间文件和候选报告，默认也保存预览 PDF/PNG；可用 `--output-dir` 单独指定预览目录。脚本只读取 Harbor case 及对应几何缓存，不写入题目的 `environment/input/`。默认重新扫描所有几何和 OCR。只有在**输入及扫描规则未变**、只需重建候选汇总/预览时，才使用 `--reuse-scans`；否则缓存结果可能过期。`--no-preview` 可跳过预览和页码校验。

主要结果：`complexity_auto_candidates.json`（全部理由与强/扩展信号）、`complexity_step_selection.json`（供 `parts-pdf` 自动导出 STEP 的每题 family 清单和来源哈希）、同名候选 `.md`、`simple_shape_exclusions.md`、`shallow_text_candidates.md`、`visual_text_watchlist.md`、`复杂零件自动筛选预览.pdf`、`复杂零件自动筛选总览.png` 和 `full_rescan_validation.json`。清单格式见 [数据契约](references/input-contract.md)。回归校验不能代替人工标注集上的召回率测量。

筛选输入必须是含完整 FRONT/SIDE/TOP 的图册。`parts-pdf` 自动交付模式将该基线留在作者侧；困难件交付页使用三张 Blender 图并提供单件 STEP。交付 PDF 不能直接作为筛选输入，筛选应使用与 spec 同源的完整基线。

## 判读边界

规则覆盖复杂截面/曲线、闭环内缘重复凹齿、局部端面密集齿形、孔槽拓扑与分层开孔、多轴孔和未解释内轮廓的组合、纵向多构件及弯管、主体两侧局部凸起、盘体过渡到细杆的截面突变、浅凹面疑似文字等。三视图 OCR 单独命中只进观察清单；疑似文字入选要求 OCR 框与**同一视图**的浅凹几何投影重合。仅由弱信号命中的简单旋转件或规则双排孔板可自动排除，其他独立信号出现时保留。细节、已知局限和规则来源见 [规则说明](references/rules.md)。

筛选阈值适用于同一套网格生成流程。换用不同三角化或 PDF 版式时，应先检查命中和漏检样本，必要时校准；没有独立人工标注集时，不报告统计学意义上的 recall/precision。

对局部密集齿形、复杂凹口和详细页过密尺寸的分档边界，可查 [`cad_82_g001` 复核样例](../parts_pdf/references/cad_82_g001_review.md)中的成品页与作者侧 JSON；family 编号只用于定位样例，不参与新题筛选。

## 中等难度与困难件分档

在高难筛选后，使用**同源完整图册基线**运行 `scripts/screen_medium_parts.py`。它排除困难 family，再结合 STEP 解析圆特征、沿长轴截面，以及最薄轴方向的三层外轮廓，找出需要更多尺寸信息的零件。对最薄跨度与次薄跨度之比不大于 0.60 的零件采样三层轮廓；只有凹角、转折或层间变化等几何证据达到门槛才进入中等档。具体组合条件见 [规则说明](references/rules.md)。输出 `中等难度零件候选.md` 和同名 JSON；入选不代表详细图纸已完整。

```bash
python cad-complexity-screen/scripts/screen_medium_parts.py \
  --manifest "$MANIFEST" \
  --harbor-dir "$HARBOR" \
  --work-dir "$WORK"
```

对**每题**运行 `scripts/augment_medium_candidates.py`：输入本轮候选 JSON、同源 `feature-annotations.json`、spec 和困难件 STEP 目录；输出用于出图的中等候选及困难 family 清单。它补入同轴多级变径件，并将强烈深凹、多轴与局部形状突变的组合件、局部端面密集齿形件归入 STEP 档；主轴倾斜到无法安全标注真实轴向长度时也归入 STEP 档。规则只用几何，不按视图名、case 或 family 编号判断。困难件须先导出并验证局部单实体 STEP，再生成详细图册与四档 `parts.pdf`；没有实际 STEP 文件，不得在公开 PDF 中声称已提供。筛选结果的 `pdf_page` 只对应本轮完整基线。

简单/轻量档只保留单页能定义其局部几何的零件。即使外形小或孔数少，只要同一轴线上有相邻两级不同直径、两级各自有可验证的轴向长度，就归入中等详细档；端视图的两个 Ø 标签不能替代纵向长度链。对外圆台阶和内孔台阶使用同一几何判据，先核对圆心、轴向及相接的 STEP 圆边平面；轴线倾斜超过安全投影阈值时沿现有规则提供 STEP。

中等件详细图生成后，还须读取逐族审核记录：若几何未认证，且没有补充尺寸页或附加正投影页，自动升级为 STEP 档并移除空白详细页。不能仅因未认证就升级已有有效局部标注的零件；完整判据见 [规则说明](references/rules.md)。

```bash
python cad-complexity-screen/scripts/augment_medium_candidates.py \
  --candidates "$WORK/中等难度零件候选.json" \
  --features "$PARTS_WORK/feature-annotations.json" \
  --spec "$PARTS_WORK/part-spec.json" \
  --case "$CASE" \
  --step-files-dir "$EXISTING_DIFFICULT_STEPS" \
  --meshes "$PARTS_WORK/aligned" \
  --output "$CASE_WORK/medium_candidates.json" \
  --difficult-output "$CASE_WORK/difficult_families.json"
```

其中 `PARTS_WORK` 是 manifest 中该题的 `parts_workdir`，`EXISTING_DIFFICULT_STEPS` 是作者侧已验证的困难件 STEP 目录，`CASE_WORK` 是本题作者工作目录。目录与输入必须属于同一轮 spec 和源 STEP。
