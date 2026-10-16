# STEP → parts.pdf

本目录从装配 STEP 提取零件族，并生成 Harbor 题包中的 `parts.pdf`。零件族按七项稳定机械指标合并；图册和评分应采用同一轮提取结果。详细约束见 [SKILL.md](SKILL.md)。

完整成品样例见 [`example_harbor`](../example_harbor/README.md)。需要判断复杂件分档或详细尺寸覆盖时，参照 [`cad_82_g001` 复核样例](references/cad_82_g001_review.md)的页码与作者侧证据。

## 当前使用的四档图册流程

`scripts/build_parts.py` 从原始 STEP 自动生成四档成品；下列脚本也可用于复用同一轮作者缓存来重建图册。几何、对齐网格、Blender 渲染、特征记录和基础图册必须同源。

1. **准备基础图册。** 从 STEP 提取并合并零件族，对齐每族代表零件；为所有 family 渲染基础图册需要的视图。`generate_parts_pdf_six_views.py --all-family-depth-views` 生成不含 Detail A/B 的完整基础图册。这个完整版本也供复杂度筛选使用。
2. **确定分档。** `cad-complexity-screen` 先筛出难以可靠自动标注的零件；在同源完整图册基线上运行 `screen_medium_parts.py` 筛中等件，再由 `augment_medium_candidates.py` 补充同轴件并把深凹、多轴且局部变化明显的零件升级为 STEP 档。倾斜的多级轴件等无法安全用正投影尺寸描述的形状也进入 STEP 档。困难档以 `provided_part_files/` 中**实际存在且已校验**的单件 STEP 为准；没有文件时不得在 PDF 中声称已提供。
3. **生成中等件补充页。** `build_medium_detail_book.py` 从模型几何生成孔径、孔位、孔深、轮廓分段、弧半径、轴向分段和键槽尺寸。基础页保留三视线稿与三张模型图；补充页根据特征轴线、可见性、孔位分离度和标注空间选择完整 FRONT/SIDE/TOP 线稿或其放大正投影，在对应几何边上补标。截面仅用于后台测量验证，不作为公开的尺寸画面。重复贯通槽选择能看到槽深的完整正投影，标槽宽、深度、节距、端距和剩余厚度。轴向孔深统一使用一套布局与绘图函数：从同源几何验证外部或内部入口和终止位置，按实际文字宽度与空白区域放标签，并在真实孔位画深度段；标签放在对应入口面一侧。已有投影能容纳时直接补标，否则生成使用相同画法的放大投影尺寸页，空间仍不足则分批分页，不漏孔组。独立放大投影还从面积显著的网格平面提取横向轮廓站位及高度层，默认补齐该视图可定义的肩位、开口和凹口尺寸链。多级回转件从共同几何主轴与 STEP 圆边站位取得沿轴尺寸。只标从模型可靠测得的几何，不生成通用 Detail A/B。弧线采样点先拟合为圆弧，重复直径、半径和孔距合并标注。
4. **合成整本图册。** `build_four_tier_parts.py` 按下表选择页面，生成封面索引及 `parts.pdf`。它会读取实际 STEP 文件夹决定困难档，不重新导出 STEP。常规新题及规则回归应重新生成中等件图册和整本 PDF；已有题目的局部回修可用 `replace_four_tier_family_pages.py` 重排目录，但要同步同源中等页与审计，并运行最终结构审核。

详细页必须承载由几何验证的尺寸。内部或底部水平面可用于核对数值；最终应将可见的轮廓、孔位与孔深标在完整正投影上。若某尺寸在三视图中没有可靠落点，不画虚构的引线，并提供单件 STEP。基础页已有的斜视图负责展示外形。

| 档位 | 内容 |
|---|---|
| 基础 | 三视线稿和四张模型图 |
| 轻量 | 三视线稿、少量几何尺寸和四张模型图 |
| 详细 | 三视线稿、三张模型图，以及必要的详细尺寸页 |
| STEP | 单件 STEP 路径，以及上方 3/4、相反方向 3/4、顶视三张模型图 |

分档优先级为 **实际提供的 STEP → 中等件候选 → 基础/轻量**。基础和轻量沿用对齐网格的深度视图判定，不根据零件名称另写题目专用规则。普通视图不标小于 3 mm 的圆径；详细页只标能从模型可靠测得的尺寸。补充页按尚未定义的几何选择视向，避免同角度重复出图。详细页默认不画不大于 0.2 mm 的局部尺寸；整族仍有至少 3 个大于 0.2 mm 且小于 1 mm 的红色数值标注时，`build_parts.py` 自动将其转为提供单件 STEP，`build_four_tier_parts.py` 会拒绝遗漏这一步的密集页。

对已有题目经人工复核确认标注覆盖不足而升级 STEP 的 family，在源 `reference.step` 同目录保存 `step_tier_overrides.json`，记录 `schema: harbor/step-tier-overrides-v1`、源 STEP SHA-256、part-spec SHA-256 与 family 清单。若从原始源路径向已有题包重建，入口也会查找该题包的 `tests/reference/step_tier_overrides.json`。默认 `build_parts.py` 仍运行几何筛选，并把通过来源校验的复核清单并入 STEP 档；生成新题时没有该文件就按原流程运行。

对已复核且通用详细尺寸生成器可以完整表达的轻量件，可在同一目录保存 `medium_tier_overrides.json`，使用 `schema: harbor/medium-tier-overrides-v1` 和相同的来源、part-spec 哈希字段。默认入口会从同源特征记录取得页码与数量，将这些 family 加入详细档并运行通用尺寸生成器；已进入 STEP 档的 family 不允许同时加入详细档。

在 `parts_pdf/` 目录下，各脚本的完整参数可用以下命令查看：

```sh
python scripts/generate_parts_pdf_six_views.py --help
python scripts/build_medium_detail_book.py --help
python scripts/build_four_tier_parts.py --help
```

## 基础构建入口

在 `parts_pdf/` 目录下，需要从原始 STEP 开始建立几何、对齐网格和 Blender 渲染时，可以使用：

```sh
python scripts/build_parts.py \
  --step /absolute/reference.step \
  --work-dir /absolute/parts-work \
  --output /absolute/parts.pdf
```

`--work-dir` 必须是空目录。该入口运行困难件与中等件筛选、单件 STEP 导出、详细尺寸页生成和四档合成。输入若使用已有困难件筛选清单，则只生成基础交付图册；四档自动流程使用默认筛选模式。完整运行会渲染零件，耗时随 family 数量增长。

## 主要脚本

| 脚本 | 用途 |
|---|---|
| `build_parts.py` | 从原始 STEP 在宿主机生成四档零件图册及困难件 STEP |
| `export_aligned_family_meshes.py` | 对齐代表零件并生成逐族网格 |
| `blender_render_part_depth.py` | 渲染正交视图；困难件使用三张指定视图 |
| `generate_parts_pdf_six_views.py` | 生成完整基础图册 |
| `annotate_parts_pdf.py` | 为普通三视图增加少量解析几何尺寸 |
| `build_medium_detail_book.py` | 为中等件生成三视图和详细补充尺寸页 |
| `general_dimension_engine.py`、`keyed_bore_profile.py` | 提取轮廓、圆弧、孔和键槽尺寸 |
| `build_four_tier_parts.py` | 合成四档图册、封面索引并核对 STEP 页面 |
| `replace_four_tier_family_pages.py` | 已有题目单族页面回修和目录页码重排；中等页审计需另行同步 |
| `audit_four_tier_output.py` | 默认构建发布前逐页检查索引、文字边界、公开文案及 STEP 文件对应关系，输出作者侧 JSON |
| `inertia_frame.py` | 按质心和主惯量轴计算单件 STEP 的规范坐标系 |
| `normalize_provided_steps.py` | 将已有的 `provided_part_files/` 原地归一化，并输出逐件校验报告 |

已有题包需要单独归一化时，在 `parts_pdf/` 目录运行以下命令。脚本会原地更新 STEP，执行前先备份该文件夹；如果题包有独立 verifier，完成后运行 `sync_provided_part_files.py` 同步 `tests/` 中的副本。

```bash
STEP_DIR=/absolute/path/to/environment/input/provided_part_files
REPORT_DIR=/absolute/path/to/report-directory
mkdir -p "$REPORT_DIR"
python scripts/normalize_provided_steps.py \
  --step-dir "$STEP_DIR" --report "$REPORT_DIR/principal-frame.json"
```

## 依赖与交付检查

使用 Python 3.12+，安装 numpy、scipy、trimesh、networkx、Shapely、Pillow、reportlab、PyMuPDF、fast-simplification，并提供 Blender 4.x 或兼容版本。常规命令直接使用 `python` 和 `blender`；执行 FreeCAD 工作脚本的 Python 须能导入 `FreeCAD` 与 `Part`。环境不同时设置 `FREECAD_PYTHON`、`FREECAD_LIB`、`BLENDER_BIN` 或传对应覆盖参数。全部生成过程在宿主机执行，不调用容器。任何导出结果均须通过实体数、体积、面积和局部坐标校验。

成品目录应包含 `parts.pdf`，以及仅为困难档提供的 `provided_part_files/family_XXX.step`。完整装配 STEP、网格、渲染缓存和扫描用完整图册不放入题包。单件 STEP 将质心放在原点，三个惯性主轴按主惯量从小到大对齐 X/Y/Z；PDF 视图仍采用出图坐标系，朝向可能不同。导出时校验来源、有效实体数、体积、面积、质心与惯性张量；图册中的每条 STEP 提示必须能找到对应文件。

默认构建在替换公开 `parts.pdf` 前运行结构审核，结果保存在作者工作目录的 `four_tier_layout_audit.json`。确定性错误会阻止交付；只有审核提示的异常或新型几何需要人工查看对应页面和源模型。轻量页默认按真实空白继续放置可验证的尺寸，没有固定每视图标注数量上限；拿不准是否需要标注时，优先保留有可靠数值和落点的尺寸。自动结构审核不能证明所有制造细节已经定义。题包文件清单和公开/私有目录约定见 [Harbor 交付约定](../HARBOR_PACKAGE.md)。
