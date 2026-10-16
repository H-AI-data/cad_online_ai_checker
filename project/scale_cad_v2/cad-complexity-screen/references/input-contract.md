# 输入数据契约

`--manifest` 是 JSON 数组，每项至少有：

```json
{
  "name": "cad_01_g001",
  "index": 0,
  "parts_workdir": "/absolute/path/to/parts_0"
}
```

`name` 与 Harbor 顶层 `cad_*` 子目录一一对应；`index` 是原工作流使用的源序号，需保持稳定；`parts_workdir` 应使用绝对路径，指向该题本版图册的私有构建目录。manifest 可保留额外字段，脚本会忽略未使用的字段。Harbor 顶层若存在额外 `cad_*` 目录，运行器会停止，以免遗漏题目。

每个题目必须存在：

- `<harbor-dir>/<name>/environment/input/parts.pdf`
- `<parts_workdir>/part-spec.json`
- `<parts_workdir>/aligned/manifest.json` 与每个 `family_*.stl`
- `<parts_workdir>/geometry/catalog.json`
- `<parts_workdir>/feature-annotations.json` 与 `analytic/manual_geometry.json`（同源 STEP 解析缓存；多轴分层孔规则使用。个别无法解析的 family 可缺席，不会被该规则凭空判入选）

PDF 页内的 `FAMILY_###`、`QTY`、`LOCAL-AXIS X x Y x Z` 必须能与 spec 和 aligned manifest 对上。页结构需是当前 `parts_pdf` skill 生成的格式，含 `UPPER OBLIQUE` 与 FRONT/SIDE/TOP 三视图。OCR 用 macOS Vision；PDF 裁图位置依赖该版式。只有原始完整零件工程图而没有此格式的 `parts.pdf` 时，此脚本不能直接扫描，需另建适配器，不得强行按页码猜测 family。

## STEP 交付清单

扫描同时写出 `complexity_step_selection.json`。它不含 STEP 文件，只列出可交给 `parts-pdf` 导出的 family：

```json
{
  "schema": "cad-complexity-screen/step-selection-v1",
  "cases": [
    {
      "case": "cad_01_g001",
      "part_spec_sha256": "<64 位十六进制摘要>",
      "source_step_sha256": "<64 位十六进制摘要>",
      "families": ["family_001", "family_004"]
    }
  ]
}
```

`part_spec_sha256` 来自本次匹配图册的 `part-spec.json` 原始字节，`source_step_sha256` 来自其 `geometry/catalog.json`。消费端必须重新计算这两个摘要，并校验 family 属于同一 spec；来源不一致时停止，不能只凭 family 编号导出。最终单视图交付 PDF 不符合上述扫描版式，应保留或重新生成作者侧完整图册作为下次扫描输入。
