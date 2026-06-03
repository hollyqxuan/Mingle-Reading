# Mingle Reading 数据设计

## 1. 与现有材料的设计对齐

本文遵循已有报告和幻灯片中设定的当前项目路线，而非替代它：

- 四类核心资产保持不变：`book text corpus`、`persona source corpus`、`annotation data`、`evaluation data`。
- 分块同时保留当前实际工作和未来扩展：
  - 当前可实现的层：段落或滑动窗口检索 chunk；
  - 保留的更高层：章节结构摘要、全局索引、引用/立场层。
- 防剧透评测保持 PBM 方向：
  - `SANQA`：进度感知的可回答性与剧透控制；
  - `ERE`：情感共鸣与回应边界；
  - `CME`：跨阅读进度的累积意义演化。
- 角色构建保持幻灯片中已提出的来源划分：
  - `fact layer`（事实层）
  - `style layer`（风格层）
  - `source layer`（来源层）

## 2. 数据来源分类

### 2.1 书籍文本来源

- `public_domain_book`：公版小说、散文、经典作品。
- `open_license_book`：开放许可的文本语料。
- `licensed_book`：获得明确授权的 EPUB/TXT。
- `project_demo_book`：在完整授权明确之前，仅用于内部原型验证的演示文本。

典型原始格式：

- `epub`
- `txt`
- `docx`
- `pdf` 仅作为临时输入，不作为首选归档格式

### 2.2 角色来源

- `author_work`：原创论文、信件、序言、访谈、日记。
- `character_source`：直接描述角色言语、动机、关系和弧线的书籍段落。
- `biography_reference`：传记、回忆录、百科页面、教育性摘要。
- `critical_reference`：用于提取稳定分析风格的文学评论或公开讲座。

### 2.3 标注来源

- `highlight_qa`：用户高亮触发的问答或评论对。
- `salience_label`：情绪峰值、冲突强度、象征密度、心理复杂度。
- `chapter_evolution`：章节摘要加上跨进度检查点的意义更新。
- `persona_review`：对角色风格一致性和边界控制的人工审查。

### 2.4 评测来源

- `retrieval_eval`
- `persona_consistency_eval`
- `anti_spoiler_eval`
- `user_study_sample`

## 3. 统一目录结构

```text
backend/assets/data/
  raw/
    books/
    persona_sources/
  processed/
    books/
    personas/
  annotations/
    highlight_qa/
    chapter_evolution/
  eval/
    retrieval/
    persona_consistency/
    anti_spoiler/
  manifests/

backend/assets/schemas/
  raw_text.schema.json
  chunk.schema.json
  persona.schema.json
  highlight_qa.schema.json
  chapter_evolution.schema.json
  anti_spoiler_eval.schema.json

backend/assets/examples/backend/assets/data/
  raw_text/
  chunks/
  personas/
  highlight_qa/
  chapter_evolution/
  anti_spoiler_eval/
```

## 4. 命名约定

仅使用小写 ASCII。标识符内部使用下划线 `_`，仅在日期中使用连字符 `-`。

### 4.1 规范 ID

- `book_id`：`book_<title_slug>`
  - 示例：`book_the_pig_like_maverick`
- `chapter_id`：`ch_<3位序号>`
  - 示例：`ch_003`
- `section_id`：`sec_<3位序号>`
- `paragraph_id`：`para_<4位序号>`
- `chunk_id`：`chunk_<book_short>_<chapter>_<local_index>`
  - 示例：`chunk_pig_003_0007`
- `persona_id`：`persona_<name_slug>`
  - 示例：`persona_lu_xun`
- `sample_id`：`<task>_<book_short>_<chapter>_<index>`
  - 示例：`highlight_qa_pig_003_0002`

### 4.2 文件命名

- 原始文本文件：
  - `book_<title_slug>__source_<source_type>__v001.json`
- Chunk 文件：
  - `chunks__book_<title_slug>__ch_<3位序号>__v001.jsonl`
- 角色文件：
  - `persona_<name_slug>__v001.json`
- 标注文件：
  - `<task>__book_<title_slug>__split_<split>__v001.jsonl`
- 评测文件：
  - `<task>__book_<title_slug>__split_<split>__v001.jsonl`

### 4.3 划分命名

- `train`
- `dev`
- `test`
- `gold`
- `demo`

## 5. 核心 schema 意图

### 5.1 `raw_text`

存储分块前但已完成基本法律和来源登记的书籍或源文档。

### 5.2 `chunk`

存储检索就绪的单元，带有严格的位置元数据，用于防剧透过滤。

### 5.3 `persona`

存储作者或角色 Agent 的事实、风格、立场、引用参考和使用约束。

### 5.4 `highlight_qa`

存储以高亮片段及周围上下文为中心的交互样本。

### 5.5 `chapter_evolution`

存储章节摘要和进度感知的理解更新，设计用于支撑章节收尾和 CME 风格的评测。

### 5.6 `anti_spoiler_eval`

存储对抗式进度感知问题、金标标签、泄漏类别和评分数元数据。

## 6. 数据流

```text
来源获取
  -> 法律/来源登记
  -> 原始文本归一化
  -> 章节/节解析
  -> 段落对齐
  -> chunk 生成
  -> 元数据增强
  -> 角色抽取 / 标注创作
  -> 金标集审查
  -> 评测集打包
  -> 开源过滤和发布清单
```

### 6.1 详细交接点

1. `raw/books` 和 `raw/persona_sources`
   - 由导入 / 版权检查负责。
2. `processed/books`
   - 由文本流水线和分块流水线负责。
3. `processed/personas`
   - 由角色抽取和 prompt 设计协作负责。
4. `annotations/*`
   - 由人工标注和 QA 审查负责。
5. `eval/*`
   - 由基准测试设计和评测脚本负责。

## 7. 开源边界

### 可以开源的

- Schema 定义。
- 命名规则和目录约定。
- 标注指南和评分标准。
- 评测 prompt、标签和分数聚合代码。
- 受版权保护书籍的仅元数据清单。
- 不复制大量版权段落的合成或人工重写演示示例。

### 默认不应公开发布的

- 完整的受版权保护 EPUB/TXT 内容。
- 来自授权书籍的大段连续段落。
- 从无再分发权的非开放传记或评论来源汇编的原始角色语料。
- 包含用户阅读痕迹的内部 API 日志。

### 有条件发布

- 如果记录了来源和许可，公版全文可以发布。
- 如果经过权利和长度审查，短摘录可以作为演示上下文发布。

## 8. 当前最小构建策略

本次交付有意只构建最小的稳定骨架：

- schema 优先
- 元数据优先
- 样本优先
- 不假设导入脚本已经存在

这保持了结构的解耦，便于其他 Agent 后续添加：

- EPUB 解析器
- chunk 构建器
- 标注工具
- 评测运行器
