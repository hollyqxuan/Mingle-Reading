# 数据骨架

此目录是面向 Mingle Reading 的最小化项目对齐数据骨架。

## 布局

- `raw/books/`：经过来源登记的原始书籍级资产。
- `raw/persona_sources/`：原始作者、角色、传记和评论来源。
- `processed/books/`：归一化的书籍文本、段落映射和检索 chunk。
- `processed/personas/`：供 prompt 或 RAG 使用的结构化角色包。
- `annotations/highlight_qa/`：以高亮为中心的交互金标数据。
- `annotations/chapter_evolution/`：章节摘要和理解演化标注。
- `eval/retrieval/`：检索基准测试和查询-文档相关性标签。
- `eval/persona_consistency/`：角色一致性评测集。
- `eval/anti_spoiler/`：面向 SANQA/ERE/CME 的对抗式评测集。
- `manifests/`：许可、来源、划分和发布清单。

## 应放在此处的内容

- 元数据清单
- 归一化的 JSON / JSONL
- 标注导出
- 评测包

## 不应放在此处的内容

- 代码
- 与数据打包无关的 notebook
- 模型检查点
- 向量数据库运行时文件

## 发布说明

如果书籍不属于公版或未获得明确的再分发许可，在公开发布中仅在此处存储元数据清单。
