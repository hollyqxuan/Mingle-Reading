# 时间上下文图

Mingle Reading 当前将时间上下文图视为叠加在已分块书籍记录之上的本地图存储，而非独立的外部数据库。

## 图谱存储内容

- `chapter` 节点：章节级时间线锚点和浏览摘要
- `episode` 节点：段落或 chunk 级叙事单元，带来源信息
- `entity` 节点：角色、地点、群体、概念和主题
- `relation` 边：共现、对话和冲突关系，带章节有效性
- `community` 节点：基于实体邻接的连通分量
- `saga` 节点：连续多章节叙事线索

## 时间线层

每个图谱包含一个 `chapter_timeline` 数组。每条记录包含：

- `chapter_index`
- `episode_ids`
- `entity_ids`
- `relation_ids`
- `community_ids`
- `saga_ids`
- `spoiler_level`
- `summary`

这使得 API 使用者可以获得稳定的逐章浏览界面，无需从原始边重建时间线状态。

## 查询层

`backend/knowledge_base/graph/retrieval.py` 支持带以下过滤条件的进度感知检索：

- `max_chapter` 和 `min_chapter`
- `entity_names`
- `entity_types`
- `relation_types`
- `node_types`
- `metadata_filters`
- `min_entity_mentions`
- `min_relation_weight`

检索结果还返回 `hit_type_breakdown`、`applied_filters` 和图谱级统计信息，以便上层查看图谱实际返回了什么。

## 存储层

图谱以 JSON 格式持久化在 `backend/workspace_state/graphs/` 下。存储元数据当前记录：

- `storage_version`
- `saved_at`
- `graph_path`

目前仍是本地文件级图存储。后续工作可在保留相同图谱模型和检索接口的前提下更换存储后端。

## API 接口

图谱层当前通过 FastAPI 应用暴露：

- `GET /api/books/{book_id}/graph`
  - 返回图谱统计信息以及可浏览的 `chapters`、`chapter_timeline`、`episodes`、`entities`、`relations`、`communities` 和 `sagas`
- `GET /api/books/{book_id}/graph/metadata`
  - 仅返回存储元数据和图谱统计信息
- `POST /api/books/{book_id}/graph/query`
  - 使用 `GraphQuery` schema 运行进度感知图谱检索

这保持了图谱存储的本地文件化，同时为前端和评测脚本提供了稳定的数据库式查询接口。
