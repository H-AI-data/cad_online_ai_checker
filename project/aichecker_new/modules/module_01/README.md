# 模块1

本模块所有维度都先执行原 STEP 前置检查；任意前置不通过或运行错误，后续均未执行。

完整模块输入需有单零件 STEP/STP、至少一份 SolidWorks 文件和对应 PDF/DXF 工程图。第一维度按最后一个扩展名以 `.sld` 开头识别 SolidWorks（忽略大小写，如 `.sldprt`、`.sldjoin`），仅检查提供，不核验其与 STEP 的内容一致性。STEP 与工程图仍需数量一一对应并通过 AI 内容核对。缺 SolidWorks 则第一维度不通过，不能入库；单独测试第二、三维度不要求 SolidWorks。

从本目录运行：

```bash
bash run.sh --input-dir "/你的输入目录" --dimension all --llm auto
```

可单选 --dimension 1 / 2 / 3。去重采用 mechanical-seven-v2，不按重复安装数计数。
详细输入规则、环境配置和输出说明见 [项目说明](../../README.md)。
