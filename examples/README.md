# 示例

先在项目根目录运行 `uv sync`，配置 `.env` 后使用 `uv run --env-file .env ...`。

## OCR 和两条抽取链的完整调用

```bash
uv run --env-file .env python examples/pipeline.py /path/to/pdfs \
  --output runs/demo --domain schemas/domain-config.example.json
```

此命令会调用真实服务。去掉 --domain 只运行 OCR 与五模块分析。重复相同命令续跑，成功的 OCR/模块/领域阶段不再次提交。超过 200 个 PDF 先自行分批；同一批次不允许重名文件。summary.json 只记录运行状态，不包含密钥。

## 单接口调用

```bash
uv run --env-file .env python examples/science_client.py \
  --module future --file /path/to/full.md --output runs/future.json
```

--module 使用外部 endpoint 名；Python SDK 的 extract 方法和主 CLI 使用 research_question 等聚合模块键。示例是 SDK 的调用方，没有复制一套 HTTP 实现。

## 离线格式示例

article.example.json / article.example.md、grouped.response.example.json、experiment.response.example.json、stage1.example.json、stage2.example.json、extraction_result.example.json 均为虚构内容，不是实际服务抽取结果。JSON 的图片路径仅展示字段格式，没有附图。

```bash
uv run python examples/read_results.py examples/extraction_result.example.json
```

read_results.py 不请求网络，展示如何递归读取分组与实验对象。schema 描述结构，不代表科学语义已验证。

## 跨论文队列与延后补跑

已适配文档可用 `examples/batch.py documents.json --output runs/batch`，五个模块同等轮转并自动识别疑似阻塞。增加 `--fast-first` 将慢模块未发出的任务延后，随后去掉该参数显式补缺。必需模块缺失时返回 partial，不隐瞒缺项。清单格式与调度规则见 [批量调度文档](../docs/10-批量调度与慢模块.md)。端到端 `pipeline.py` 支持 `--blocked-action defer`。
