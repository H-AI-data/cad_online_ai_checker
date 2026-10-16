# 模块1 · 维度2：零件复杂特征

本文件夹的 `engine.py` / `worker.py` 只做输入适配与结果翻译，实际复杂特征判断直接调用 `scale_cad_v2/cad-complexity-screen/scripts/run.py` 和其原始筛选脚本，不重写规则、不调用LLM、不使用模型置信度。
输入是一套完整资料所在目录，递归读取子目录；不依赖其他维度报告，必须先通过本模块全部相关 STEP 的前置检查。

```bash
bash run.sh --input-dir "/你的输入目录" --llm off
```

本项 `--llm off` 和 `--llm auto` 都执行相同的原始本地规则，AI调用为0。独立运行不能绕过前置检查，不需再传模块和维度参数。

原 Skill 要求同源 `parts.pdf`、`part-spec.json`、对齐STL和几何缓存。适配器按原 `parts_pdf/scripts/build_parts.py` 的基线准备流程，调用原项目脚本自动生成这些内部资料；它们只写入本次输出的 `original_engine/`，不替代用户工程图、不要求用户额外提供、不修改输入和旧源码。原入口先做 `--check-inputs`，再执行全部14个扫描器，`--no-preview` 只省略候选预览，不省略特征检测。

证据完整时，原候选非空输出“通过”；完整运行而没有候选时输出“不通过”及中文原因；运行缺依赖、超时、来源不符等输出“运行错误”，明确跳过部分扫描时为“无法判定”。原截面输出若出现空值，无法区分正常空截面与原脚本捕获的异常，适配层保守标记证据缺口、输出“无法判定”，不修改原候选或重算复杂度。原强规则和扩展规则候选均保留，报告列出区别。这是原工具的高召回筛选口径，可能误报或漏报，不宣称百分之百准确。

依赖原项目目录、Conda环境及Blender。默认查找 `BLENDER_BIN`、PATH、项目内 `tools/Blender.app` 和 `/Applications/Blender.app`；可用 `--blender` 指定。原项目可通过 `--complexity-source` 指定，完整流程默认超时1800秒，可用 `--complexity-timeout` 调整。所有依赖就绪后检查过程不联网。

原始候选、来源哈希、命令及日志分别保存在 `original_engine/scans/`、`source_provenance.json`、`commands.json`、`runner.log`；`completion.json` 记录扫描清单和原代码是否保持不变。
输出为每次新建的 outputs/run_.../summary.md 和对应 module_01/dimension_02_complexity/report.md，同时有JSON证据。
详情见 [新版说明](../../../README.md)。
