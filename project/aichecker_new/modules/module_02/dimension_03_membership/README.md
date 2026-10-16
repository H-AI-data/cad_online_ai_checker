# 模块2 · 维度3：非标准件属于总装

本文件夹的 checker.py 负责此项判定，evaluate.py / run.sh 是独立入口。
输入是一套完整资料所在目录，递归读取子目录；不依赖其他维度报告，必须先通过本模块全部相关 STEP 的前置检查。

```bash
bash run.sh --input-dir "/你的输入目录" --llm auto
```

--llm off 禁止网络调用，需视觉判断的内容保留为“无法判定”。独立运行不能绕过前置检查，不需再传模块和维度参数。
输出为每次新建的 outputs/run_.../summary.md 和对应 module_02/dimension_03_membership/report.md，同时有JSON证据。
详情见 [新版说明](../../../README.md)。

