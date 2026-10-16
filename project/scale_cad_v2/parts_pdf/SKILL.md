---
name: parts-pdf
description: 从装配 STEP 自动生成 Harbor 零件图册和困难件单件 STEP；适用于本仓库的四档出图、详细标注与交付审核。
---

# Harbor 零件图册

本 skill 与本仓库的 `parts_pdf/scripts/`、`cad-complexity-screen/`、`cad-dimension-sheets/` 和 `step-exploded-view/` 配套使用。分享时应包含这些依赖及 [README.md](README.md)，不能只复制本文件。运行环境需要可导入 FreeCAD 的宿主机 Python、Blender 和 README 所列 Python 包。

## 默认入口

常规新题只运行 `scripts/build_parts.py --step <装配.step> --work-dir <空的私有目录> --output <parts.pdf>`。该入口负责同源几何提取、零件族合并、复杂度筛选、尺寸图、困难件 STEP 导出、四档组册和发布前审核；不要让默认流程依赖某一道题的作者缓存或 case/family 特判。回修已有题目时可以复用经来源校验的同轮缓存，但最终规则须在默认路径上复现。构建参数和依赖见 [README.md](README.md)。修改筛选规则时另读 [复杂度筛选 skill](../cad-complexity-screen/SKILL.md)。

作者缓存和审计 JSON 留在 `--work-dir`，不放进公开 `environment/input/`。图册、解析 STEP、局部网格和单件 STEP 必须来自同一装配 STEP、同一代表实体和同一局部坐标变换；不能将 assembly.pdf 的装配坐标当作零件局部尺寸。来源、family 和变换不匹配时停止，不按名称猜测。

## 出图判断

- 先识别特征轴、真实几何边界和可测量的尺寸，再按可见性、投影分离度和标注空间选择 FRONT/SIDE/TOP 正投影。截面可在作者侧验证尺寸，公开详细页要把标注落在完整的三视线稿或其放大正投影上；不以稀疏的内部切片轮廓代替完整视图。需要显示不可见特征时，可在对应正投影上用经几何验证的虚线表达；无法可靠落点的尺寸交由单件 STEP。视图名称只是输出标签，不是选图规则。详细页应承载新增尺寸，避免重复没有新增信息的模型图。
- 当数值与落点都由同源 STEP 或网格核实、空间足够而必要性仍不确定时，优先标出。相同直径和等距特征可合并；不能以固定标注数上限丢掉其他已验证尺寸。不得从渲染像素推断尺寸或把圆柱网格分面当成制造边。
- 同心直径的端视图不足以定义多级回转件；孔径以外，还要检查孔深、轴向站位、肩位、壁厚、凹口与重复特征。某投影能显示这些几何但脚本只画了孔深时，应修复通用特征提取或布局，而不是补一张特定 family 的 PDF。
- 无法可靠表达的几何在作者侧记录未解决项；默认筛选判为困难或中等页没有有效局部尺寸时，提供单件 STEP。不能把局部尺寸页或无错误的结构审计称作完整制造图。CAD 本身也不能推断公差、粗糙度、设计基准和工艺。
- 人工复核已确认标注覆盖不足、决定改供单件 STEP 的现有题目，可在 `tests/reference/step_tier_overrides.json` 保存源 STEP 与 part-spec 哈希及 family 清单。默认 `build_parts.py` 会在几何筛选后合并该清单并从中等页移除对应 family；不在绘图代码中写题号或 family 特判。verifier 应要求清单中的单件 STEP 真正出现在其可见的 `provided_part_files/`。

## 已脚本化的规则在哪里

| 内容 | 默认实现 |
|---|---|
| 零件族合并、来源及局部坐标 | [`extract_part_spec.py`](scripts/extract_part_spec.py)、[`mechanical_families.py`](scripts/mechanical_families.py)、[`export_aligned_family_meshes.py`](scripts/export_aligned_family_meshes.py)；合并策略为 `mechanical-seven-v2`，修改后运行 [对应回归](scripts/test_mechanical_families.py) |
| 复杂件与中等件筛选 | [`cad-complexity-screen`](../cad-complexity-screen/SKILL.md)；包括多轴、深凹、局部齿形和倾斜多级轴 |
| 基础与轻量页的圆边、孔距和标签避让 | [`annotate_parts_pdf.py`](scripts/annotate_parts_pdf.py)、[`build_four_tier_parts.py`](scripts/build_four_tier_parts.py) |
| 中等页的轮廓、孔、槽、轴肩、凹口、后台截面测量与正投影尺寸布局 | [`general_dimension_engine.py`](scripts/general_dimension_engine.py)、[`build_medium_detail_book.py`](scripts/build_medium_detail_book.py)；孔深标签、内部入口、投影轮廓站位及放不下时的退路也在这里 |
| 过密小尺寸与无有效详细页的 STEP 升级 | [`dimension_density.py`](scripts/dimension_density.py)、[`build_parts.py`](scripts/build_parts.py)；阈值以脚本为准 |
| 单件 STEP 的来源、实体和惯性主轴校验 | [`export_family_steps.py`](scripts/export_family_steps.py)、[`inertia_frame.py`](scripts/inertia_frame.py)、[`verify_step_bundle.py`](scripts/verify_step_bundle.py) |
| 四档页面、目录、公开文案和交付结构 | [`build_four_tier_parts.py`](scripts/build_four_tier_parts.py)、[`audit_four_tier_output.py`](scripts/audit_four_tier_output.py)；公开 PDF 不写内部渲染器名称，STEP 提示必须有对应文件 |
| 已有题目的单族页面替换及目录重排 | [`replace_four_tier_family_pages.py`](scripts/replace_four_tier_family_pages.py)；使用后仍须核对同源中等页、审计和 STEP 状态 |

普通视图的圆径下限为 3 mm；局部距离不套用此下限。尺寸密度筛选及短尺寸链的精确门槛由上述脚本统一维护，不在 skill 里另抄一套数值。零件族分组不可用包围盒、安装朝向、名称或 family 编号替代脚本的机械指标。

## 审核边界

默认构建先运行脚本化审核，再发布 `parts.pdf`。优先读取 `medium-dimensions.audit.json`、尺寸密度结果和 `four_tier_layout_audit.json`；仅查看报告的异常页或真正新型几何，不让 agent 逐页重复检查常规索引、STEP 路径和文字越界。结构审核只保证页面与文件对应，尚不能证明每个可标注特征都已覆盖。发现已验证而漏画的特征，应在几何驱动脚本和默认路径中补规则，并用相关回归或一题的重生成验证。

遇到分档边界、局部几何是否已被尺寸定义、投影视图是否重复等需要判断的情况，按 [cad_82_g001 复核样例](references/cad_82_g001_review.md) 查对应的成品页和作者侧证据。样例中的 family 编号只用于定位，不是新题的白名单或默认参数；先看本题几何和审计，再按同一判断问题处理。

若题包的独立 verifier 只读取 `tests/`，交付前运行 [`sync_provided_part_files.py`](scripts/sync_provided_part_files.py) 同步单件 STEP；具体打包约定见 [HARBOR_PACKAGE.md](../HARBOR_PACKAGE.md)。
