# 6. App 前后端集成与调用流程

## 6.1 页面和职责

| 页面 | 用户操作 | 后端支持 |
| --- | --- | --- |
| 文档列表 | 上传文件、选择文档 | 登记 document_id、文件位置与解析状态 |
| 创建抽取任务 | 选择通用模块、领域配置、模型配置 | 创建 job_id、保存任务配置 |
| 任务详情 | 查看每篇每模块状态、补跑失败项 | 查询任务、返回错误、精确重试 |
| 论文结果 | 阅读通用分析、领域记录及来源 | 返回聚合结果及文档块 |
| 导出 | 选择报告或结构化文件 | 只从已保存结果构建产物 |

当前这些是集成设计，不是已经上线的 App 页面。前端不实现提示词拼接与领域抽取，也不暴露模型密钥。

## 6.2 通用调用顺序

![App 调用序列](diagrams/09-platform-sequence.svg)

上传文件后由后端返回文档 ID；前端提交抽取任务，立即得到 job_id。页面定期查询任务，展示各模块而非单一旋转图标。后端对远端接口等待较长时，前端查询仍应快速返回已有状态。

同一文档可以有多个运行，例如采用不同领域定义或模型。结果查询携带运行标识，避免界面把不同配置的结果拼在一起。成功模块可以提前阅读，失败模块显示保存的接口错误摘要与重试入口。

## 6.3 结果解析伪代码

```text
for each selected module:
    if module failed:
        show saved error and retry action
    elif module is experiment_process:
        render protocol/material/equipment/operation/result objects
    else:
        for group in extraction_result:
            render translated group label
            for item in group.extraction_result_list:
                render result_value when present
                render evidence_units and optional explanations
                recursively render children_extraction_result

for category in core_fields:
    render domain-config label
    render records with object/value/unit/conditions/source
```

对子树不能只解析一层。部分条目是容器，即使 result_value 为空，也必须继续解析 children_extraction_result。未识别标签显示原键；未配置的响应字段保存在原始结果中。

## 6.4 来源展示

通用模块使用 evidence_units 的 material_sentence/material_context/source_chapter；领域记录使用 source_block_ids 与 source_locations。两套定位体系不保证直接对应，不将服务 context_id 强制视为本地块 ID。

结果卡片展示字段/结论、原文证据和条件。原文跳转失败时展示已有上下文，不生成虚假的页码。图片只提供阅读来源；没有图像抽取功能时，不暗示图中数值已经读取。

## 6.5 报告和文件导出

文件结构建议由导出模板决定，而不是把所有运行中间件打包。一个最小通用交付结构可以是：

```text
export/
  report.docx                   # 用户选择时生成
  documents/
    <document_id>/
      article.md
      article.json
      extraction_result.json
      images/
```

这是通用模板建议，不是外部服务返回 ZIP 结构。阶段响应、内部索引和调试日志保留在运行存储，不默认混入用户导出。是否附 CSV 由使用目的决定；不能因为内部生成了 CSV 就全部交付。

报告记录任务配置、已返回模块、未返回模块及实际错误。没有依据时不把字段缺失归因为原文没有，不添加自动质量评级。图表、页码和单位保持可追溯。

## 6.6 新领域接入步骤

1. 定义领域范围与排除条件，明确希望得到的对象和字段。
2. 配置类别与字段，给出值类型、单位规则和缺失规则。
3. 将领域定义注入第一阶段和第二阶段提示。
4. 用有代表性的文档核对选区与对象登记，再检查字段输出。
5. 确定结果展示词典和导出模板，记录领域版本。
6. 批量运行时固定配置，变更后用新的运行记录。

字段 ID、显示名、提取指令是不同职责。为新领域修改 LABEL_FIELD_MAP 只能改变中文显示，不能让远端自动抽取全新领域量化字段；后者由领域链实现。
