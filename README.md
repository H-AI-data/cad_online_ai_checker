# CAD 检测工具：交接与快速开始

这里是 `cad_online_ai_checker` 源码仓库（原交接包名为 `aichecker_1015`），包含三个模块、八个维度。本页负责“下载代码、装好环境、配好 API、启动检查”；各维度的判定规则、参数和排错方法见 [详细使用手册](project/aichecker_new/README.md)。两份 README 说明的是同一套程序。

## 先明确：STEP 前置检查会自动执行

**无需手动先运行 `step检查.py`。** 下面的总入口、模块入口，以及内层手册中的单维度入口，都会自动先调用交接包根目录的 `step检查.py`。它是原始脚本的原样副本，不是另写的检查逻辑，也不依赖原电脑上的绝对路径。

- 模块一：检查输入的零件 STEP/STP。
- 模块二：检查总装和非标准件目录中的全部 STEP/STP。
- 模块三：检查输入的总装 STEP/STP。

全部前置通过，才执行后续检查；任一文件不通过、报错或超时，后续检查停止，不调用 AI，也不入库。完整模块的全部维度同轮通过后，才进行最终查重入库；单维度不入库。详见 [前置检查与报告](project/aichecker_new/README.md#强制-step-前置检查)。

## 0. 下载代码并进入仓库根目录

以下两种方式选一种，不要重复执行：

**方式一：使用 Git。** 已安装 Git 时，在希望保存项目的目录中打开终端，执行：

```bash
git clone https://github.com/H-AI-data/cad_online_ai_checker.git
cd cad_online_ai_checker
```

**方式二：下载 ZIP。** 在 GitHub 仓库页面选择 `Code → Download ZIP`，解压后，在终端进入解压得到的目录。下载 `main` 分支时，目录通常名为 `cad_online_ai_checker-main`。将下面的路径替换为实际路径：

```bash
cd "/实际解压位置/cad_online_ai_checker-main"
```

此时所在位置应同时包含本页 `README.md`、`step检查.py`、`step_sha256_dedup.py`、`step查重.json` 和 `project/`。这就是下文所说的“仓库根目录”。目录名称可以不同（旧交接包可能仍叫 `aichecker_1015`），但内部层级必须保持完整。

## 1. 安装运行环境

本包不是免安装软件。当前完整流程按 macOS 验证：模块一原复杂特征 OCR 依赖 Swift/Vision，查重锁使用 POSIX `fcntl`，不能直接当作 Windows 发行版；Linux 也不能直接替代 macOS 的原 OCR。

先安装 Conda，并完成上一步进入仓库根目录，再执行：

```bash
cd project/aichecker_new
conda env create -f environment.yml
conda activate aichecker_new
export AI_CHECKER_PYTHON="$(command -v python)"
python - <<'PY'
import sys
from pathlib import Path

# Conda 的 FreeCAD 模块位于环境的 lib 目录，需显式加入搜索路径。
sys.path.insert(0, str(Path(sys.prefix) / "lib"))
import FreeCAD
import Part

print("FreeCAD 环境可用")
PY
```

看到“FreeCAD 环境可用”表示自检通过。Conda 安装的 FreeCAD 不一定在 Python 默认搜索路径中，因此不要把上面的自检简化为直接 `import FreeCAD, Part`；程序正式入口会自动传入当前环境的 `lib` 路径，无需手动修改系统环境变量或先执行 `step检查.py`。

同名环境已存在时先核对依赖，不要覆盖。后续命令默认都在 `project/aichecker_new` 中运行。重新打开终端时需要重新进入该目录、激活环境并设置 `AI_CHECKER_PYTHON`。FreeCAD 必须能在该 Python 中按上述方式导入，不能只安装 PyPI 同名包代替。

完整运行模块一还需要安装 Blender 和可用的 Swift/Vision；包内不含 Blender，已用 Blender 4.5.7 验证。按实际安装位置设置：

```bash
export BLENDER_BIN="/Applications/Blender.app/Contents/MacOS/Blender"
swift --version
```

## 2. 配置 API

模块一、二的视觉项需要 API；模块三完全本地运行，可跳过本节。仍在 `project/aichecker_new` 中，复制模板（不覆盖已有配置）：

```bash
cp -n ../key.example.txt ../key.txt
```

用文本编辑器打开 `../key.txt`，保存为 UTF-8 纯文本，填入：

```json
{
  "base_url": "https://api.example.com/v1",
  "api_key": "YOUR_API_KEY"
}
```

地址和密钥都是占位值，必须替换；不要把 Markdown 代码围栏复制进去。密钥只填本身，不加 `Bearer `。

接口必须支持 HTTPS、兼容 Chat Completions 和图片输入。示例中的 `/v1` 会被追加为 `/v1/chat/completions`，也可以填完整接口地址；不要填网页聊天地址，程序不会自动补 `/v1`。

默认模型为 `volcengine/deepseek-v4-1-flash-260910`，需确认服务支持该视觉模型标识；换模型用 `--model "实际模型标识"`，不要把 `model` 写入配置文件。包外配置用 `--llm-config "/你的配置目录/key.txt"` 指定。

开启 AI 会向配置服务发送 CAD 视图、图纸图片及证据，可能产生费用；请使用有权处理这些资料的服务，不要交付真实密钥。接口协议、所有参数、单维度 API 试跑和报错排查统一见 [API 配置与常见问题](project/aichecker_new/README.md#api-配置与常见问题)。

## 3. 准备输入并运行

每次提交一套资料，输入目录可以有多层子目录：

| 模块 | 需要准备的资料 |
| --- | --- |
| 1 | 一个单零件 STEP/STP、至少一份 SolidWorks（扩展名以 `.sld` 开头）及对应 PDF/DXF |
| 2 | 一个输入目录，下设总装目录和非标准件目录；两边各自提供 STEP/STP 及对应 PDF/DXF |
| 3 | 一个多零件总装 STEP/STP，不要求工程图或 SolidWorks |

详细的数量、配对和几何规则见 [输入与检查规则](project/aichecker_new/README.md#自动分类与检查规则)。

首次试跑先创建测试库副本，避免改动随包交付的库。以下仍在 `project/aichecker_new` 中执行：

```bash
mkdir -p ./test_state
cp -n ../../step查重.json ./test_state/step查重.json
```

下面四条命令按需选一条，替换输入路径即可；**每条都已自动包含 STEP 前置检查，不需要另外运行前置脚本**：

```bash
# 自动判断输入属于哪个模块
bash run.sh --input-dir "/一套待检资料" --llm auto \
  --dedup-registry "./test_state/step查重.json"

# 明确运行模块一
bash modules/module_01/run.sh --input-dir "/模块一资料" --dimension all --llm auto \
  --dedup-registry "./test_state/step查重.json"

# 明确运行模块二
bash modules/module_02/run.sh --input-dir "/模块二资料" --dimension all --llm auto --max-llm-calls 160 \
  --dedup-registry "./test_state/step查重.json"

# 明确运行模块三：不需要 API
bash modules/module_03/run.sh --input-dir "/模块三资料" --dimension all --llm off \
  --dedup-registry "./test_state/step查重.json"
```

`--max-llm-calls` 默认40，160是请求次数上限，不是固定消耗。模块一、二需要 AI 的项不能用 `--llm off` 代替通过；模块三的 `auto/off` 均不调用 AI，但完整通过后仍会查重入库。

这些命令完整通过后只登记到指定的测试副本；重复提交相同文件仍会被拒绝。正式验收请把 `--dedup-registry` 改为统一正式库的实际路径。只想测一个维度且不入库，见 [单维度运行方法](project/aichecker_new/README.md#单独运行模块或维度)。

## 4. 查看结果

终端会给出本次报告路径，默认在项目 `outputs/run_时间_随机编号/` 中。先看 `summary.md`，再按问题查看：

- `preflight/summary.json`：STEP 前置是否通过。
- `module_0N/dimension_*/report.md`：各维度的中文结论和原因。
- `finalization/report.md`：完整验收后的查重、登记或拒绝原因。

退出码0表示本次选定检查成功，1表示不通过、无法判定、未执行或重复，2表示运行错误；单维度成功不代表完整验收通过。详见 [输出与退出码](project/aichecker_new/README.md#输出与退出码)。

## 交付目录与注意事项

```text
cad_online_ai_checker/        ← ZIP 解压时通常为 cad_online_ai_checker-main
  README.md                  ← 本页：安装与快速开始
  MANIFEST.sha256            ← 文件校验清单
  step检查.py                ← 自动调用的原始前置脚本
  step_sha256_dedup.py        ← 最终查重脚本
  step查重.json              ← 历史库快照，不是空库
  project/
    key.example.txt          ← 空配置模板
    aichecker_new/
      README.md              ← 详细规则、参数和排错
      run.sh                 ← 总入口
      modules/               ← 三个模块及独立维度
    scale_cad_v2/            ← 四个原始依赖组件，不是整个旧项目
```

请整体移动交付包，保持相对层级，不要只拷走 `project/aichecker_new`。日常运行用 `run.sh`；不要无参数运行 `step_sha256_dedup.py`，其旧模式仍保留历史扫描路径。

随包查重库含历史路径元数据，请勿公开上传。各人副本不会自动同步；缺库、坏库不会自动重建放行。再次打包时排除自己的 `key.txt`、`outputs/`、`test_state/`、缓存、环境和软件安装包。

需要检查交付文件完整性时，在交付包根目录执行 `shasum -a 256 -c MANIFEST.sha256`。正常运行产生的新文件、后续库记录及运行后变化不属于初始交付状态。维护人员请看 [代码入口与测试](project/aichecker_new/README.md#代码入口与测试)。
