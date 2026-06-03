# 层级化数据集与来源注册表构建器

`backend/scripts/` 现在包含两个入口点：

- `backend/scripts/source_registry_manifest_builder.py`
  - 将本地 `txt/json` 来源注册为标准原始记录和来源清单
  - 支持 `books` 和 `persona_sources` 模式
  - 可选择性调用层级化构建器处理 `books`
- `backend/scripts/hierarchical_dataset_builder.py`
  - 从一份归一化的书籍来源构建层级化 Mingle Reading 数据集产物以及图谱中间导出

## 1. 来源注册表入口点

当需要首先将本地来源注册到 `backend/assets/data/` 风格的目录布局和清单结构中时使用此入口。

### 支持的输入

- 纯 `.txt`
- 包含 `content` 或 `text` 的来源 `.json`
- `books` 模式下的序列化 `BookRecord` `.json`
- 单文件、多文件或目录中的 `.txt/.json`

### 处理书籍

```bash
python backend/scripts/source_registry_manifest_builder.py backend/scripts/demo_book.txt --mode books
```

默认写入到 `backend/scripts/registry_output/`：

```text
backend/scripts/registry_output/
  backend/assets/data/
    raw/
      books/
        book_demo_book__source_project_demo_book__v001.json
    processed/
      books/
        book_demo_book/
          v001/
            raw_record.json
            hierarchical_chunks.jsonl
            l0_raw_paragraph.jsonl
            l1_fine_grained.jsonl
            l2_structure_summary.jsonl
            l3_global_index.jsonl
            l4_quote_or_stance.jsonl
            manifest.json
            graph/
              graph.json
              episodes.jsonl
              entities.jsonl
              relations.jsonl
              communities.jsonl
              sagas.jsonl
    manifests/
      manifest__books__book_demo_book__v001.json
      source_registry__books__v001.json
```

### 处理角色来源

```bash
python backend/scripts/source_registry_manifest_builder.py path/to/persona_notes.txt --mode persona_sources --persona-name "Lu Xun" --source-type author_work
```

输出：

```text
backend/scripts/registry_output/
  backend/assets/data/
    raw/
      persona_sources/
        persona_source_persona_notes__source_author_work__v001.json
    manifests/
      manifest__persona_sources__persona_source_persona_notes__v001.json
      source_registry__persona_sources__v001.json
```

### 实用选项

- `--output-root backend/scripts/registry_output_custom`
- `--version v002`
- `--source-type licensed_book`
- `--copyright-status licensed`
- `--skip-hierarchical-build`
  - 仅限 `books` 模式
  - 仅注册原始文件/清单，不生成处理后的 chunk
- `--recursive`
  - 展开目录输入并拾取 `.txt/.json` 文件

### 注册行为

- `books` 模式将 ID 归一化为小写 ASCII 加下划线，例如 `book_demo_book`。
- `persona_sources` 模式生成 `persona_source_<slug>` 和可选的 `persona_<slug>`。
- 原始来源文件名遵循 `backend/docs/data/mingle_reading_data_design.md` 中针对书籍的模式：
  - `book_<title_slug>__source_<source_type>__v001.json`
- 来源清单存储在 `backend/assets/data/manifests/` 下，批量注册表文件按模式分组：
  - `manifest__books__<book_id>__v001.json`
  - `manifest__persona_sources__<source_id>__v001.json`
  - `source_registry__books__v001.json`
  - `source_registry__persona_sources__v001.json`

## 2. 层级化数据集构建器

当归一化的书籍原始记录已存在，只需要层级化处理产物时使用此入口。

### 支持的输入

- 纯 `.txt`
- 包含 `content` 的 `raw_text` 风格 `.json`
- 序列化的 `BookRecord` `.json`

### 运行

```bash
python backend/scripts/hierarchical_dataset_builder.py backend/scripts/demo_book.txt --output-dir backend/scripts/build_output/demo_run
```

### 输出

- `raw_record.json`
- `hierarchical_chunks.jsonl`
- `l0_raw_paragraph.jsonl`
- `l1_fine_grained.jsonl`
- `l2_structure_summary.jsonl`
- `l3_global_index.jsonl`
- `l4_quote_or_stance.jsonl`
- `graph/graph.json`
- `graph/episodes.jsonl`
- `graph/entities.jsonl`
- `graph/relations.jsonl`
- `graph/communities.jsonl`
- `graph/sagas.jsonl`
- `manifest.json`

## 3. 说明

- L1 使用段落窗口生成适合检索的 chunk。
- L2 是章节结构摘要骨架。
- L3 是单个全局路由/索引 chunk。
- L4 是引用/立场占位层，供下游角色或评论工作器使用。
- 图谱导出基于 L0 段落视图生成，因此保持防剧透感知和位置对齐。
- 当来源仍需要来源登记时，新的注册表脚本是推荐入口点。
