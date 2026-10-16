# 模块3 · 维度2：总装去重不少于100种

本文件夹的 checker.py 负责此项判定，evaluate.py / run.sh 是独立入口。
输入是一套完整资料所在目录，递归读取子目录；不依赖其他维度报告，必须先通过本模块全部相关 STEP 的前置检查。

```bash
bash run.sh --input-dir "/你的输入目录" --llm auto
```

本维度使用本地几何去重；`--llm off` 或 `auto` 均不调用 AI，也不要求工程图或 SolidWorks。独立运行不能绕过前置检查，不需再传模块和维度参数；单项评测不查重入库。
输出为每次新建的 outputs/run_.../summary.md 和对应 module_03/dimension_02_unique_count/report.md，同时有JSON证据。
详情见 [新版说明](../../../README.md)。
