# 分层子装配计划

当前格式见 [cad_03_g009 六组示例](cad03-group-plan.json)。计划与同轮 `catalog.json`、`meshes.json.gz`、`part-spec.json` 配套；`source_sha256` 必须与 catalog 一致。`catalog`、`meshes`、`part_spec` 可写绝对路径，也可写相对于计划文件目录的路径。计划本身属于作者工作文件，不放入公开题包。

| 字段 | 含义 |
|---|---|
| `schema_version` | 当前为 `1` |
| `catalog`, `meshes`, `part_spec` | 同轮抽取的几何清单、显示网格、family 映射 |
| `source_sha256` | 源 STEP 哈希，用于阻止跨轮混用 |
| `groups` | 依据真实装配关系判断的子结构体；每个实体 ID 只能出现一次且全部实体必须被覆盖 |
| `stages` | 组 ID 的装入顺序，每组恰好出现一次 |
| `iso_direction`, `case_title` | 总览相机方向与页面标题 |

每个 group 需要 `id`、`name`、`parts`、`color`。`description` 和 `install_note` 写可由现有图纸支持的简短说明；`explode_axis`、`explode_step_mm` 和 `camera` 控制组内示意爆炸及视角，`approach` 控制装入示意的组间位移。它们是排版参数，不是制造尺寸或经验证的拆卸路径。各组的 family 数量由 spec 与 `reference_index` 的映射计算，不在计划中手写。

先确定主体或基准组，再按机械关系组织导向、轴系、驱动、执行端等组。每组渲染原位和爆炸图；逐步装入图将已装组弱化，当前新增零件保留细节。每一步的 TOP/FRONT/SIDE 占各自的一张 A3 页；尺寸优先覆盖新增零件的主要中心距及其与已装组的位置关系，避免重复画先前组的内部尺寸。图中的 `P1`、`P2` 等是本步自动挑选的几何中心基准点，`R` 是相邻已装组基准点；不是孔中心或配合点，不能将这些距离写成配合公差。

示例的 `solid_XXXX` ID 及六组划分只对 `cad_03_g009` 当前 source SHA 有效。新模型需要查看原位总览、实体清单和安装关系后重新编写 plan；脚本只验证覆盖、哈希和映射，不能替人判断子结构体是否合理。
