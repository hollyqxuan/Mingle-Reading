# 领读角色收集

本文档汇总了为 Mingle Reading 收集的首批三位领读角色，并说明这些资产应如何挂接到时间图层。

## 范围

- `persona_lu_xun`
- `persona_mark_twain`
- `persona_zhang_ailing`

## 最低收集要求

每个领读角色现在应满足以下全部要求：

- 至少 `20` 个来源总数
- 至少 `10` 个作品来源
- 至少 `10` 个声音来源

对于 Mingle Reading，`声音来源` 包括访谈、引用、信件、演讲、论文、序言、后记、自传体散文以及其他直接暴露作者声音、方法或立场的材料。

每个角色目前具有：

- 一个符合 schema 的角色包，位于 `backend/assets/data/processed/personas/`
- 多个来源记录，位于 `backend/assets/data/raw/persona_sources/`
- 一个共享的注册表条目，位于 `backend/assets/data/manifests/persona_lead_readers_registry__v001.json`
- 一个大来源目录，位于 `backend/assets/data/raw/persona_sources/catalog_<persona>__v001.json`
- 一个状态摘要，位于 `backend/assets/data/manifests/persona_collection_requirements_summary__v001.json`

## 收集策略

- 优先使用公版原创作品（如可用）。
- 当来源文本受版权保护时，将传记或评论参考仅作为元数据锚点使用。
- 对于张爱玲等现代作者，默认仅存储来源元数据和证据摘要。
- 不在仓库资产中复制长篇受版权保护的段落。

## 时间图映射

推荐图谱结构：

- `persona` 节点
  - 每个领读角色一个
- `persona_source` 节点
  - 每条来源记录一个
- `grounded_in` 边
  - 从角色节点到来源节点
  - 载荷应包含 `source_type`、`time_anchor`、`copyright_status` 和 `redistributable`
- 可选的 `supports_trait` 边
  - 从来源节点到归一化的特征节点，如 `irony`、`social_diagnosis`、`urban_texture`、`vernacular_satire`

推荐检索流程：

1. 按用户选择或系统默认选择角色。
2. 按发布策略和阅读器可见书籍进度过滤角色来源。
3. 分别检索当前书籍 chunk 和角色来源摘要。
4. 在编排中合并，使角色风格有根基，同时书籍回答保持防剧透安全。

## 角色说明

### 鲁迅

- 最适合诊断式、具社会洞察力的精读。
- 强大的来源支撑：公版作者作品加一个仅元数据的传记锚点。
- 当前目录状态：`28 条总计 / 12 条作品 / 12 条声音来源`

### 马克·吐温

- 最适合观察式、讽刺式、轶事风格的领读评论。
- 当前包完全为公版，可在开源仓库中发布。
- 当前目录状态：`30 条总计 / 12 条作品 / 15 条声音来源`

### 张爱玲

- 最适合场景级、质感厚重、情感克制的都市阅读。
- 当前包有意保持以元数据为主，以尊重版权边界。
- 当前目录状态：`30 条总计 / 12 条作品 / 13 条声音来源`

## 后续步骤

- 添加角色来源记录的正式 schema。
- 将来源摘要拆分为更小的证据单元，用于图谱边。
- 添加角色专属评测样本，用于一致性检查。
