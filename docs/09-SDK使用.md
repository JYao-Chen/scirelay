# 9. 可复用 SDK 与命令行（已实现）

本章描述仓库当前可运行代码，而非第 7 章拟议的 Web API。包名 scienceengine-client，导入名 scienceengine，命令行名 scienceengine。Python >=3.12，运行时仅依赖 requests。

## 9.1 公共 API

| 入口 | 参数 | 返回 / 行为 |
| --- | --- | --- |
| ModelConfig(model, base_url, api_key) | 显式模型与凭据 | 不隐式选择模型；repr 隐藏密钥 |
| ModelConfig.from_env() | LLM_MODEL、LLM_BASE_URL、LLM_API_KEY | 配置对象；缺变量抛 KeyError |
| MinerUClient(api_key=None, base_url=..., timeout=60, upload_timeout=600) | 不传 key 则读 MINERU_API_KEY | OCR 客户端 |
| create_batch(files, model_version='vlm', language='en') | 1–200 个唯一文件名 PDF | batch_id、file_urls |
| get_batch(batch_id) | 批次 ID | 单篇状态对象列表 |
| run(files, output, workers=3, poll_interval=15, max_wait=1800, ...) | 文件列表、独立输出目录 | 批次状态字典，complete/partial |
| prepare_document(ocr_dir, output) | 单篇 OCR 目录 | article、blocks、markdown 文件路径 |
| ScienceClient(config, base_url=..., timeout=600) | ModelConfig、服务参数 | 通用分析客户端 |
| extract(module, file) | 聚合模块键、正确格式文件 | 原始模块 JSON；不保存缓存 |
| run(document_dir, output, modules=None, workers=3) | 适配目录、模块选择 | 聚合结果；成功缓存复用，失败下次补跑 |
| DomainExtractor(config, timeout=600, stage1_tokens=7000, stage2_tokens=8192) | 模型配置及预算 | 领域抽取器 |
| run(blocks_file, domain, output, workers=3) | 内部块文件、领域 dict | 字段、区域、错误及 complete/partial/failed/skipped |

输出目录不能由两个进程同时写入。类内部线程使用独立请求，不共享可变 Session；主线程汇总状态。没有自动无限重试，也没有自动切换模型。

## 9.2 OCR 行为细节

提交响应先保存，再执行上传。每个成功上传立即记录 uploaded；每个成功下载记录 downloaded。单次轮询等待到期抛 PollingTimeout，原 batch_id 保留，下次 run 续查，不创建新收费任务。

PDF 文件名在一个批次内必须唯一，data_id 使用客户端生成的 ASCII ID；本地结果目录使用 doc_0000 等 ID，run.json 保存原文件名映射，避免文件名编码与路径耦合。ZIP 完整性检查后在临时目录解压，找到唯一非空 full.md，再提升为正式目录。未下载完成的 done 项续跑时重新查询，以取得当前下载链接。

max_wait 是本次轮询阶段等待预算，不包括上传和下载，不是任务总时长硬保证；单个请求另有连接/读取超时。服务已标 failed 不会自动重提；需重新提交时指定新输出目录。请求超时发生在创建批次之后但响应尚未收到的情形，客户端无法断言服务是否已经创建任务，不能承诺远端 exactly-once。

## 9.3 输入适配与约束

prepare_document 要求 OCR 根目录有 full.md 与恰好一个 *_content_list.json。内部 blocks.json 所有 paragraph 为字符串；服务 article.json 的表格/图片 paragraph 保留说明数组，表格正文保留 html。块 ID 沿用原列表下标，过滤空块后不连续是正常情况。

该函数不是完整书目解析器。摘要和参考文献仅识别明确章节，作者等无法解析的元数据留空；参考编号为本地顺序，不保证与印刷编号一致。图片复制供查阅，没有执行曲线读数。重做适配会更新输入文件修改时间，因此依赖它的后续任务应使用新运行目录；端到端示例续跑时不重复适配已有输入。

## 9.4 通用抽取、领域抽取与缓存

ScienceClient.run 只要求本次所选模块齐全，不固定必须五个。错误落入 errors，成功模块缓存为 stages/<module>.json。future 和 experiment 永远从准备目录读取 full.md。ScienceClient.extract 接受任意 .md 文件名。

DomainExtractor.run 使用 schemas/domain-config.example.json 的通用结构。类别 ID 为英文字母开头，后续允许英文字母、数字、下划线和连字符；类别下 fields 为非空对象。第一阶段要求每个类别出现一次并引用真实块。out_of_scope 返回 skipped；无区域类别不请求模型。第二阶段仅输入选中块，保存输入与原始模型响应。

引用定位当前采用去空白后的连续文本匹配，表格证据需引用原始 HTML。没有沿用领域专属脚本里的复杂 OCR 归一化或特例修正。issues 和 quote_matched 是本地定位结果，不是远端评价；异常记录保存在 flagged_records。

run.json 记录输入路径、大小、mtime_ns、公开模型参数与完整领域配置。相同目录遇到不同配置会拒绝运行，提示选择新 output；不写入 API Key，也不添加文件哈希。修改提示词实现应更新 PROMPT_VERSION，确保新运行不会沿用旧提示结果。

## 9.5 异常与退出码

- ScienceEngineError：业务、格式、缓存配置或抽取调用失败。
- PollingTimeout：OCR 仍未终态，可续跑同一目录。
- requests.RequestException：OCR 网络调用未完成，调用者决定何时续跑；异常可能携带签名 URL，不直接记入公开日志。
- ValueError：无效参数或输入。
- KeyError：缺少环境配置，或响应缺少预期字段。

CLI 捕获上述常见失败，成功/skipped 退出 0，partial/failed 退出 1，缺配置退出 2。单模块返回 partial 时，成功部分仍可读。库调用方应检查 result['status']，不要把“没有抛异常”理解为所有模块完成。

## 9.6 代码结构与维护

```text
src/scienceengine/
  config.py      # 模型配置
  ocr.py         # MinerU 批次完整生命周期
  document.py    # OCR 到两种 article 的适配
  science.py     # 五模块调用、并发与续跑
  domain.py      # 通用两阶段、提示词与来源绑定
  storage.py     # JSON 状态与输入配置一致性
  errors.py      # 错误上下文与已知凭据脱敏
  cli.py         # 命令行适配，不复制业务逻辑
```

最小开发验证见根 README。测试不调用真实模型；本地 HTTP 集成测试覆盖 multipart 之外的 MinerU 上传/轮询/ZIP 下载实际 requests 路径。构建 wheel 后可安装到其他应用；私有仓库访问权限由 GitHub 管理。
