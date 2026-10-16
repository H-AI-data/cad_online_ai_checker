# 几何算法来源

## 复杂特征：直接运行原脚本

模块一维度二不使用本地重写的复杂特征算法，直接执行相邻 `scale_cad_v2/cad-complexity-screen/scripts/run.py`，由它执行原核心脚本及全部14个扫描器。`modules/module_01/dimension_02_complexity/worker.py` 只按原 `parts_pdf/scripts/build_parts.py` 的基线准备顺序调度原提取、对齐、Blender、PDF和标注脚本。原阈值、组合条件、聚合和简单形状排除均不修改。

运行前后核验四个相关原组件中全部 `.py/.swift/.md` 文件哈希，并保存 `source_provenance.json`；原输出的 STEP 和 part-spec 哈希再次核验。原 `REASON_PRIORITY` 仅用于中文翻译，直接从源码静态读取，不复制一份筛选条件。此功能运行时依赖指定原项目（区别于下方原样复制的去重代码）。旧LLM复杂度入口已删除，无任何自写规则或AI回退。

## 七指标去重与解析

`mechanical_families.py` 原样复制自本地项目 `scale_cad_v2/parts_pdf/scripts/mechanical_families.py`，版本为 `mechanical-seven-v2`。原7项行为回归复制到 `tests/test_mechanical_families.py`，仅修改模块导入路径。执行时不依赖或导入旧项目。

复制核验SHA-256：`095186d4a7458c1f74b680379ca51883eb5c79b2bd13aeba6d8216b967ee34fe`。

合并严格保留七项指标：watertight、euler_number 精确一致；volume、surface_area、principal_inertia（逐分量）、convex_hull_volume、convex_hull_area 采用 `abs(a-b) <= 0.01 * min(abs(a), abs(b))`，零值必须一致。积分平均曲率仅记录，不参与分族；分族是源引用顺序、首个兼容族、complete-link，不能用包围盒或安装朝向代替。

`common/geometry_worker.py` 的 `_entry`、`_name`、`_has_failures`、`_check_product_graph` 及 `read_step` 的外部引用防护、XDE遍历改编自 `ai_checker/modules/module_02/common/assembly.py`。保留内存读取、大小/实体/层级/实例上限、拒绝外部引用和转换错误报告；新增实例变换、叶定义缓存与网格精度验证，不使用旧指纹分组算法。

随附总装输入只读统计为70,406,194字节、725,958实体行，因此新实现安全限额设置为256MiB和2,000,000实体；保留64层、20,000实例、默认300秒子进程限制，超过限制返回unknown。源STEP产品定义/装配关系与XDE转换树相互核验，结构证据不足不按文件名或solid数量猜测。

每个叶定义的全部solid独立缝合后组合网格。几何质量由BRep体积、表面积、主惯量与网格误差及逐级网格收敛检查验证（0.25%），不满足返回不完整。去重算法完全在网格七指标上执行。多实体单零件仍算一次实例。

`watertight=false` 不独立表示读取不完整：随附发热板的有效闭合BRep网格有36条稳定非流形缝边、0条边界边，七指标必须保留 `watertight=false/euler_number=12`。完整性检查依赖有效BRep、无游离低维几何、质量误差和包括两项拓扑指标的细化收敛；不修补网格来伪造watertight。

Numba CPU Z-buffer逐像素深度渲染、Pillow组合为两张合图，覆盖六个正交方向和两侧等轴测，避免单纯三角形中心排序造成内部管壁透出。盲孔底与表面平行时采用实际深度跳变轮廓辅助辨识，不改变网格拓扑。不提供精确制造尺寸；封闭内部或投影中被遮挡的特征不能据此判不存在。单族渲染按主惯量局部坐标，整体渲染保留XDE实例变换。文件级分析在限时子进程中运行。
