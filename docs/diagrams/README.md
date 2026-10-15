# 流程图源文件与导出

9 张图均有 Mermaid `.mmd` 源码与已渲染 `.svg`，开发手册 HTML 内嵌 SVG，可断网阅读。关系图表示概念关联，不表示已建 SQL 表；07 是拟议 App 任务状态，09 是尚未实现的平台方案。

| 编号 | 内容 |
| --- | --- |
| 01 | 系统上下文与项目边界 |
| 02 | PDF 到交付的端到端流程 |
| 03 | SciEngine 调用与缓存时序 |
| 04 | MinerU 异步上传与轮询时序 |
| 05 | 文献、块、区域、记录的数据关系 |
| 06 | 两阶段抽取及空区域/失败分支 |
| 07 | App 任务状态设计 |
| 08 | 模块文件路由、缓存与重跑 |
| 09 | 拟议本地任务 API 时序 |

渲染使用 Mermaid CLI 11.15.0 和 Chromium，不属于 SDK 运行依赖。可在本机安装渲染工具后执行：

```bash
mmdc -i docs/diagrams/01-context.mmd -o docs/diagrams/01-context.svg \
  -p /path/to/puppeteer-config.json -b white -w 1600
```

puppeteer 配置中的 executablePath 指向本机 Chromium；该文件由开发者在本机配置，不包含在仓库中。修改图后重渲染，再执行 `uv run --with markdown python scripts/build_docs.py`，确保 HTML 与 SVG 同步。
