# 模块1 · 维度1：零件文件配套及内容对应

本文件夹的 checker.py 负责此项判定，evaluate.py / run.sh 是独立入口。
输入是一套完整资料所在目录，递归读取子目录；不依赖其他维度报告，必须先通过本模块全部相关 STEP 的前置检查。

本维度要求同时提供：单零件 STEP/STP、至少一份 SolidWorks 文件、对应 PDF/DXF 工程图。

- SolidWorks 以最后一个扩展名忽略大小写后以 `.sld` 开头识别，例如 `.sldprt`、`.sldjoin`、`.SLDASM`、`.sld`；`零件.sldprt.bak` 不算，隐藏文件和符号链接不作为有效提交文件。
- SolidWorks 只检查是否提供，不读取 CAD 内容，不要求同名或数量一一对应，也不核验与 STEP 的内容一致性。
- 缺 SolidWorks 时输出中文“不通过”，不执行本维度后续图纸渲染和一致性 AI。
- 提供后，仍按原规则检查 STEP 与 PDF/DXF 文件数量，再由 AI 判断是否为同一零件；仅文件齐全不代表本维度通过。

```bash
bash run.sh --input-dir "/你的输入目录" --llm auto
```

也可只提交明确指定的文件，不扫描周边文件：

```bash
bash run.sh --step "/路径/零件.step" \
  --solidworks "/路径/零件.sldjoin" --drawing "/路径/零件.pdf" --llm auto
```

`--solidworks` 可重复，必须与 `--step` 同用；目录输入自动递归查找，不需此参数。

--llm off 禁止网络调用，需视觉判断的内容保留为“无法判定”；明确缺 SolidWorks 仍判为“不通过”。独立运行不能绕过前置检查，不需再传模块和维度参数，也不会查重入库。
输出为每次新建的 outputs/run_.../summary.md 和对应 module_01/dimension_01_correspondence/report.md，同时有JSON证据。
详情见 [新版说明](../../../README.md)。
