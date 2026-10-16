# 模块3 · 维度1：总装STEP/STP文件提供性检查

本文件夹的 checker.py 负责此项判定，evaluate.py / run.sh 是独立入口。
输入是一套完整资料所在目录，递归读取子目录；不依赖其他维度报告，必须先通过本模块全部相关 STEP 的前置检查。

本维度只检查是否提供 `.stp/.step` 文件（扩展名忽略大小写）：提供则“通过”，缺失或文件路径无效则中文说明。不要求 PDF/DXF 或 SolidWorks，不检查图纸数量、不渲染图纸、不调用 LLM 核验内容对应；即使附带图纸也不做这些检查。

为兼容既有调用，保留 `dimension_01_correspondence` 目录名。原 STEP 有效性前置与模块分类仍执行；没有 STEP 时前置报告不通过，本维度未执行，损坏模型也不能绕过前置。只通过本维度不代表满足整个模块的100种门槛。

```bash
bash run.sh --input-dir "/你的输入目录" --llm off
```

或只指定总装文件：

```bash
bash run.sh --step "/路径/总装.STP" --llm off
```

`--llm off` 或 `auto` 均不调用 AI，不需要密钥。独立运行不能绕过前置检查，不需再传模块和维度参数；本单项测试不会查重入库。
输出为每次新建的 outputs/run_.../summary.md 和对应 module_03/dimension_01_correspondence/report.md，同时有JSON证据。
详情见 [新版说明](../../../README.md)。
