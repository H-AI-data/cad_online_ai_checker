# 模块2

本模块所有维度都先执行原 STEP 前置检查；任意前置不通过或运行错误，后续均未执行。

从本目录运行：

```bash
bash run.sh --input-dir "/你的输入目录" --dimension all --llm auto
```

可单选 --dimension 1 / 2 / 3。去重采用 mechanical-seven-v2，不按重复安装数计数。
详细输入规则、环境配置和输出说明见 [项目说明](../../README.md)。

