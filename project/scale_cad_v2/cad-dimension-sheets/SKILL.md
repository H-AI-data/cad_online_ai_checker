---
name: cad-dimension-sheets
description: 从装配 STEP 的同源几何缓存制作分层子装配 assembly.pdf：逐组渲染、爆炸示意、装入位置和每步同页的 TOP/FRONT/SIDE 尺寸图。用于装配关系说明与尺寸核对；单件零件图由 parts-pdf 处理。
---

# 分层装配尺寸图

现行出图入口是相邻 [assembly-substructures](../assembly-substructures/README.md) 的确定性脚本。先看 [运行流程](references/workflow.md)，再参考 [分组计划格式](references/plan.md)。本目录旧的 `render_sheets.py`/`drawing.py` 用于维护既有平面分组图；新的分层 `assembly.pdf` 使用 `assembly-substructures/scripts/`。`scripts/extract_step.py` 仍用于提取解析 STEP 特征，也被零件图流程调用，不能随旧出图示例删除。

从**同一次源 STEP** 提取 `catalog.json`、`meshes.json.gz` 和带 family 归属的 `part-spec.json`。在计划中由人根据几何确定有意义的子结构体、组内零件和装配顺序；脚本不凭文件名或固定零件编号猜测分组。每个实体恰好归属一个组，源哈希、实体数和 family 对应关系必须核对一致。装配视图保留源模型的全局位置与朝向，不能逐件做 PCA 对齐；组内爆炸距离仅为示意。

常规装配出图用 `assembly-substructures/scripts/render_assembly.py` 运行宿主机 `blender`；线程数已有默认值。`build_orthographic.py` 默认只写与分组计划同目录的 `orthographic.json`，`build_assembly_book.py` 默认三视同页。产题时只显式传本题的计划、原始 STEP、私有工作目录和 PDF/DXF 交付位置；例行渲染及排版参数无需重复指定。

依计划先渲染总览、各组原位与爆炸视图，以及逐步装入后的正交视图，再生成 `assembly.pdf`。`build_assembly_book.py` 默认将每步 TOP/FRONT/SIDE 按原 A3 尺寸横排为一张宽幅页：总览一页，每组的组成与 family 数量、装入位置各一页。零件族多时自动增添清单页，不能假定页数固定。组内页的短清单最多 25 个 family；超过后另排完整清单页。正交页的底部短清单最多 16 个 family；超过后只保留数量摘要，指向该组完整清单页。生成器检查每页 family 文字是否落在清单区域内，溢出即失败；交付前仍需逐页目视检查。详细正交页强调本步新增零件，已装好的组用浅色整体轮廓作参照；标注外形和可识别的几何特征，不显示 P/R 零件中心点及其间距。直径引线只在当前投影中有可见圆边时绘制，不能让引线终点落在被遮挡的实体表面。逐步装入的网格轮廓 DXF 只在明确需要时生成并留在作者侧；公开的完整装配三视 DXF 使用 [cad-xray-dxf](../cad-xray-dxf/SKILL.md) 从源 STEP 生成。

轮廓来自显示网格的正交投影追踪，位置尺寸来自 CAD 几何清单；它是可编辑、可量测的装配说明图，不是 STEP BRep 的精确隐藏线图，也不自动推断孔轴配合、接触面、公差、紧固顺序或无碰撞装入路径。出图后核对三个视角的方向、数值和尺寸端点，逐页检查可读性与裁切。

当前实例是 [example_harbor/cad_03_g009](../example_harbor/cad_03_g009)，其 [assembly.pdf](../example_harbor/cad_03_g009/environment/input/assembly.pdf) 使用上述 19 页排版。六组、97 个 occurrence 的 [分组计划示例](references/cad03-group-plan.json) 使用相对输入路径。示例的实体 ID 和 source SHA **只适用于这道题**，新模型应重新提取并分组，不套用该计划。题包目录及当前文件名见 [Harbor 交付约定](../HARBOR_PACKAGE.md)。
