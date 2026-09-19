# ScienceEngine

用于 App 接入的文献 OCR 与知识抽取 Python SDK：将 PDF 转换为可追溯的文档块，调用 ScienceEngine 五类通用分析，并按自定义领域配置执行“富集区定位 → 字段抽取”。

提供可导入的类、命令行、端到端示例、接口文档与流程图。所有示例文献内容均为虚构数据；仓库不包含论文、运行结果或访问密钥。

## 功能

| 能力 | 入口 | 说明 |
| --- | --- | --- |
| PDF OCR | `MinerUClient` / `scienceengine ocr` | 创建批次、上传、轮询、下载、断点续跑 |
| 文档适配 | `prepare_document` / `scienceengine prepare` | MinerU 内容列表 → article.json、blocks.json、Markdown |
| 通用抽取 | `ScienceClient` / `scienceengine extract` | 研究问题、相关工作、研究设计、实验过程、未来展望 |
| 领域抽取 | `DomainExtractor` / `scienceengine domain` | 领域可配置，真实两阶段，来源绑定与分阶段缓存 |
| App 接口设计 | [开发手册](docs/开发手册.md) | 前后端集成与任务 API 设计；尚未部署 HTTP 服务 |

## 安装

要求 Python 3.12+、[uv](https://docs.astral.sh/uv/)。

```bash
git clone git@github.com:JYao-Chen/scienceengine.git
cd scienceengine
uv sync --locked
uv run scienceengine --help
```

在其他 Python 项目中使用：

```bash
uv add "scienceengine-client @ git+ssh://git@github.com/JYao-Chen/scienceengine.git"
```

该仓库为私有仓库，安装者需具备仓库权限。没有发布到 PyPI，不使用 `pip install scienceengine` 代替上述安装。

## 配置

复制 `.env.example` 为本地 `.env`，填写后通过 uv 加载：

```bash
cp .env.example .env
# 编辑 .env，填入自己的凭据与模型配置
uv run --env-file .env scienceengine --help
```

| 变量 | 用途 |
| --- | --- |
| MINERU_API_KEY | OCR，来自 MinerU API 管理 |
| LLM_MODEL | 调用的模型标识，无代码默认模型 |
| LLM_BASE_URL | 模型根地址，不包含 /chat/completions |
| LLM_API_KEY | 模型凭据；领域抽取直接使用，通用分析通过 ScienceEngine 使用 |

`.env.example` 展示 ScienceEngine 当前公开页面的模型配置；使用自有后端时替换模型和地址，并确认 ScienceEngine 服务器可访问该地址。OCR 只需要 MinerU Key；只运行抽取时不需要 OCR Key。Python 类可以显式传参，不必使用环境变量。

## 命令行快速开始

### 1. OCR

```bash
uv run --env-file .env scienceengine ocr /path/to/pdfs \
  --output runs/demo/ocr --model-version vlm --language en \
  --workers 3 --poll-interval 15 --max-wait 1800
```

input 支持单个 PDF 或递归目录。单批 1–200 个 PDF，文件名必须唯一。结果保存在 `runs/demo/ocr/papers/doc_0000/` 等目录，原文件名映射在 `run.json`。`max-wait` 限制本次轮询等待，不包括上传与下载，不是硬实时总期限。

同一命令可续跑已提交批次：已上传/下载项跳过，不隐式重新收费提交。服务返回 failed 时保留错误；如需重提失败文件或改为 pipeline，将失败文件组成新批次并指定新输出目录。

### 2. 适配文档

```bash
uv run scienceengine prepare runs/demo/ocr/papers/doc_0000 \
  --output runs/demo/document
```

生成 `article.json`（通用服务输入）、`blocks.json`（领域模型输入）、`full.md` 和图片。保留内容块 ID 与 0 起始页码；表格 HTML 不丢失。作者、单位等未解析元数据保持空值。

### 3. 五模块抽取

```bash
uv run --env-file .env scienceengine extract runs/demo/document \
  --output runs/demo/science --workers 3 --timeout 600
```

也可只调用选定模块：

```bash
uv run --env-file .env scienceengine extract runs/demo/document \
  --output runs/demo/future-only --modules future_outlook
```

模块名为 `research_question related_work research_idea experiment_process future_outlook`。客户端自动给 experiment/future 上传 Markdown，其余上传 JSON。单模块失败不清空其他结果；重复相同命令只补缺失成功缓存的模块。

### 4. 自定义领域两阶段抽取

```bash
uv run --env-file .env scienceengine domain runs/demo/document/blocks.json \
  --config schemas/domain-config.example.json \
  --output runs/demo/domain --workers 3
```

先编辑领域配置，将虚构的对象/方法/测量字段替换为业务范围。阶段一读取全文并生成对象登记和富集区；阶段二按类别只接收选中块。空区域不请求模型；out_of_scope 跳过阶段二。输入超长不会自动切片；输出截断会记录失败，不当作成功缓存。

## Python 调用

```python
from pathlib import Path
from scienceengine import (
    MinerUClient, ModelConfig, ScienceClient, DomainExtractor, prepare_document,
)
import json

# 从环境变量读取；也可 ModelConfig(model="...", base_url="...", api_key="...")
model = ModelConfig.from_env()
ocr = MinerUClient().run([Path("document.pdf")], Path("runs/python/ocr"))
item = ocr["files"]["document.pdf"]
if item["state"] != "done":
    raise RuntimeError(item.get("error", "OCR failed"))
prepare_document(Path("runs/python/ocr") / item["directory"], "runs/python/document")

client = ScienceClient(model, timeout=600)
# 单接口返回原始模块 JSON，不写缓存
future = client.extract("future_outlook", "runs/python/document/full.md")
# 多接口运行保存阶段缓存与聚合结果
analysis = client.run("runs/python/document", "runs/python/science", workers=3)

domain = json.loads(Path("schemas/domain-config.example.json").read_text())
records = DomainExtractor(model).run(
    "runs/python/document/blocks.json", domain, "runs/python/domain", workers=3,
)
```

完整批量串联示例见 [examples/pipeline.py](examples/pipeline.py)，参数、返回值和异常详见 [SDK 使用文档](docs/09-SDK使用.md)。只运行其中一个环节即可复用已有 OCR 或抽取结果，不要求每次从 PDF 重新开始。

## 输出与续跑规则

```text
runs/demo/
  ocr/run.json                       # 批次、原名映射、上传与解析状态
  ocr/papers/doc_0000/                # OCR 文件
  document/{article.json,blocks.json,full.md,images/}
  science/run.json
  science/extraction_result.json      # science_analysis、errors、status
  science/stages/<module>.json
  domain/run.json
  domain/extraction_result.json       # core_fields、区域、来源、errors
  domain/stages/                      # 阶段输入、解析 JSON 与模型原始响应
```

- 每个 output 只允许一个调用进程写入；线程并发由客户端管理。
- 输入路径/大小/修改时间及非秘密配置保存在 run.json；变更模型、输入、模块集合或领域配置后使用新的输出目录，避免复用旧结果。
- 600 秒为读取等待，不会修改远端内部 LLM deadline。客户端不自动重提付费任务；需要时显式续跑或创建新批次。
- `complete` 是运行完成状态，不表示科学事实正确。原文定位检查由本地生成，不冒充服务质量评级。
- OCR run.json 含临时签名 URL；运行目录不进入 Git，也不要公开分享。API Key 不写入运行配置。
- CLI 成功或合理 skipped 返回 0，partial/failed 或调用异常返回 1，缺少环境配置返回 2。

## 开发与验证

```bash
uv sync --locked --group dev --group docs
uv run pytest -q
uv run ruff check src tests examples scripts
uv run ruff format --check src tests examples scripts
uv run python scripts/build_docs.py
uv build
```

测试使用本地 HTTP 服务和 mock：覆盖 OCR 上传/下载/续跑、轮询到期、服务失败、文件格式路由、业务错误、部分补跑、配置变更、两阶段输入、无效来源与模型截断，不消耗实际接口额度。真实服务联调需自行提供凭据和可处理文档；当前未重新调用远端 OCR/模型。

## 文档

- [完整开发手册 Markdown](docs/开发手册.md) / [离线 HTML](docs/开发手册.html)
- [SDK API 与调用示例](docs/09-SDK使用.md)
- [外部接口 OpenAPI](docs/api/external-services.openapi.json)
- [9 张可编辑流程图](docs/diagrams/README.md)
- [领域配置与数据契约](schemas)

外部依赖：[ScienceEngine](https://sciengine.las.ac.cn/literatureExtraction/)、[MinerU](https://mineru.net/apiManage/docs)。自有 `/v1/extraction-jobs` 等 HTTP 路径仍为设计，SDK 不启动 Web 服务。
