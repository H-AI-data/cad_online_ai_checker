# 运行与配置

## 环境及命令

需要宿主机 FreeCAD、Blender、Python 3 + Pillow。常规命令使用 `python` 与 `blender`；执行 STEP 提取的 Python 须能导入 `FreeCAD` 与 `Part`。环境不同时设置 `FREECAD_PYTHON`、`FREECAD_LIB`、`BLENDER_BIN`，或传对应覆盖参数。

用绝对路径替换示例；shell 中始终引用路径。命令不要求在 skill 目录执行。

```bash
python /path/to/step-exploded-view/scripts/pipeline.py extract /path/to/model.step /path/to/output
python /path/to/step-exploded-view/scripts/pipeline.py init-plan /path/to/output
python /path/to/step-exploded-view/scripts/pipeline.py render /path/to/output --assembled
# 查看 assembled_preview.png、catalog.json，编辑 plan.json 后：
python /path/to/step-exploded-view/scripts/pipeline.py render /path/to/output
```

`extract` 可加 `--tolerance 0.15`，单位 mm，控制显示网格弦差；微小件需要更小值，大型模型可适当增大。BRep 原几何不被修改。提取时会暂存一份 0644 的 STEP 副本并在结束后清理，源文件不变。已有 catalog 或 plan 的目录禁止再次提取，避免不声不响破坏 ID 对应关系。

Pillow 缺失时用任务专用虚拟环境安装并使用其中的 Python 运行 wrapper。标注字体按 macOS Arial、Linux DejaVu、Windows Arial 查找；可用 `EXPLODED_FONT=/path/to/font.ttf` 覆盖，中文图例必须选择覆盖中文字形的字体。

## catalog.json

`source_sha256` 绑定 STEP 内容。`parts` 每项含：

- `id` / `label`：本次导出的 `solid_0001` 等，不保证保留原 STEP 名称。
- `bbox_min`、`bbox_max`、`center`、`size`：显示网格在源 world frame 中的毫米坐标/尺寸。
- `volume_mm3`：原 BRep 体积。
- `cylinder_axes`：按表面积排序的最多三组圆柱轴方向，是装配轴线的候选提示；没有给出唯一机械轴，也不代表已识别轴承或孔的功能。
- `triangles`：显示网格规模。

不要修改生成的 catalog 来改显示名称，应在 callouts 中命名。单实体 STEP 能渲染，但不能按原零件爆炸；开放曲面 STEP 会明确报错。

## plan.json

`init-plan` 生成与本次 catalog 绑定的完整计划。初始所有位移为零、无标注，**不是自动完成的爆炸布局**。

```json
{
  "schema_version": 1,
  "source_sha256": "由 init-plan 写入",
  "catalog_sha256": "由 init-plan 写入",
  "title": "CAD ASSEMBLY",
  "direction": [1.35, -1.8, 1.1],
  "resolution": [1800, 2200],
  "padding": 1.22,
  "color": [0.82, 0.82, 0.82],
  "offsets": {
    "solid_0001": [0, 0, 0],
    "solid_0002": [0, 0, 200]
  },
  "callouts": [
    {"part": "solid_0001", "text": "Base", "side": "left"},
    {"part": "solid_0002", "text": "Cover", "side": "right", "anchor": [50, 30, 120]}
  ],
  "guides": [{"start": [50, 30, 120], "end": [50, 30, 320]}]
}
```

这个片段仅说明字段，不能覆盖真实计划中的校验和或删去其它零件。

- `offsets`：每件的**最终平移向量**，源世界坐标、mm。必须覆盖全部 ID；即使不动也填 `[0,0,0]`。分组可用临时 Python 根据 ID/几何选择来批量更新，但应检查选择命中情况。
- `direction`：从主体指向相机的方向，任意非零向量，脚本归一化。改变它不会旋转零件。
- `resolution`：模型渲染画布宽、高，256–12000；标注脚本另加页边和图例，所以最终 PNG 尺寸更大。
- `padding`：自动取景留白倍率，至少 1.05；不要把主体塞到画布边缘，否则没有引线空间。
- `callouts`：按列表顺序编号 1…N；`part` 用一个代表零件，`text` 可以描述其子装配。`side` 可为 `auto`、`left`、`right`。
- `anchor`：可选，零件展开前源世界坐标中的表面点，渲染时自动加该件 offset；不提供则用包围盒中心。不要填屏幕像素坐标。
- `guides`：可选，**展开后世界坐标**的起止点；按细灰虚线投影并绘于实体后面。它只是示意辅助线，不自动推导真实装配路径。

渲染时会验证校验和、ID 完整性、位移有限性和标注目标。不要绕过校验和错误；检查是否换过源文件、改过 catalog 或复制了其它模型的计划。

## 输出与重复使用

`assembled.png` / `assembled_preview.png`：原装配视图。

`exploded.png` / `exploded_preview.png`：最终标注图及预览。

`assembled.blend` / `exploded.blend`：每个 solid 一个 mesh 对象，保留可编辑位置和相机。几何单位数值为 mm；这是显示场景，不替代原始 BRep STEP。

`*_render.png` / `*_labels.json`：未标注渲染图与投影坐标，便于单独重排标注。

只改字体、引线样式或图例代码后，可直接执行 `annotate.py OUTPUT`，不用重渲染。改 plan 的位移、callouts、direction 或 resolution 后需执行 `pipeline.py render`。每次渲染禁用 `.blend1` 备份，仅覆盖本次输出目录中的同名产物。

宿主机 FreeCAD Python 必须先 `import FreeCAD` 再 `import Part`。相机裁剪范围随模型尺寸设置，防止毫米单位和相机距离较大时渲染为全透明。渲染代码按横/竖画幅分别计算正交视域。


## 按画面方向微调附件

对于“稍微向左/右/上/下”，使用 `scripts/nudge_plan.py` 将相机视平面位移转换为 world offset；不旋转零件。`--right-mm` 正数向右、负数向左；`--up-mm` 正数向上、负数向下。单位是视平面中的模型毫米，不是像素，最终视觉距离会受自动取景影响。

```bash
python /path/to/step-exploded-view/scripts/nudge_plan.py \
  --plan /path/to/output/plan.json --part SELECTED_ID \
  --right-mm -20 --up-mm 0 --relative-to current
python /path/to/step-exploded-view/scripts/pipeline.py render /path/to/output
```

把 `SELECTED_ID` 换为当前导出中已确认的附件 ID。默认 `current` 在已有 offset 上累加；若此前已拉得过远，选 `--relative-to original` 从源装配位置计算新的侧移，替换旧 offset。可重复 `--part` 让同组附件整体移动。先小幅试移，再查看完整轮廓、相邻遮挡和与安装位置的距离。

此命令检查 plan 与相邻 catalog 的绑定并直接更新 plan，打印位移前后值；不会修改其它零件或重算 `guides`。已有辅助线需另行核对，标注 anchor 会随零件自动移动。相机恰好沿竖直方向时，脚本拒绝猜测画面横轴，应明确设置世界坐标位移。这是示意构图工具，不验证碰撞或实际拆卸路径。
