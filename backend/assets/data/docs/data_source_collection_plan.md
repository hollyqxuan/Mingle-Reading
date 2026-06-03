# Mingle Reading 数据来源搜集执行清单

本文在 [backend/docs/data/mingle_reading_data_design.md](/C:/Users/21358/Desktop/MingleReading/backend/docs/data/mingle_reading_data_design.md)、[backend/docs/architecture_alignment.md](/C:/Users/21358/Desktop/MingleReading/backend/docs/architecture_alignment.md) 与 [backend/assets/data/README.md](/C:/Users/21358/Desktop/MingleReading/backend/assets/data/README.md) 基础上整理，目标是给团队一份可以直接分工执行的数据来源搜集计划。

## 1. 执行目标

- 只围绕四类核心资产展开：`book text`、`persona source`、`annotation`、`evaluation`。
- 优先支撑当前 MVP 主线：`progress-aware retrieval`、`Lead Reader Agent`、`Book Character Agent`、`anti-spoiler evaluation`。
- 尽量复用现有材料中已经反复出现的方向：
  - 书目方向：`《红楼梦》`、`《三体》`、`《一只特立独行的猪》`
  - persona 方向：`鲁迅`、`张爱玲`、`王尔德`、`罗辑`
- 所有数据源都必须先过 `来源记录 -> 可公开性判断 -> 入库优先级` 三步，再进入后续清洗或标注。

## 2. 执行原则

- `P0`：直接支撑 MVP 闭环，且版权风险低、搜集难度低。
- `P1`：支撑 persona 深度或评测质量，但需要更多人工整理。
- `P2`：有价值，但依赖授权、较重标注或后续工程能力，暂不抢前排。

公开性边界统一按以下三档记录：

- `open`：可随开源仓库发布原文或完整结构化结果。
- `internal_only`：仅内部使用，开源时只发 metadata、schema、guideline 或重写后的 demo。
- `review_required`：是否可公开暂不预设，必须由版权/项目负责人复核后再决定。

## 3. 四类数据来源清单

### 3.1 Book Text

| 子类 | 建议来源 | 适用场景 | 可公开性边界 | 优先级 |
| --- | --- | --- | --- | --- |
| 公版文学原文 | Project Gutenberg、Internet Archive、Wikisource、校内已整理公版 TXT/EPUB | 首版 `book ingestion`、chunking、progress-aware retrieval、公开 demo | `open`，但仍需保留来源链接与版本记录 | `P0` |
| 开源许可文本 | 明确带开放许可的文学语料、教学文本集、可再分发 EPUB/TXT | 补充英文或实验性样本，验证多书接入流程 | `open` 或 `review_required`，取决于许可证 | `P1` |
| 现代版权书 metadata-only | 团队已讨论书目但暂无明确再分发权限的现代作品，如 `《三体》`、`《一只特立独行的猪》` 候选 | 内部原型验证、角色检索、反剧透评测设计 | 原文默认 `internal_only`，开源仅发 manifest、字段样例、重写 demo | `P0` |
| 授权书籍正文 | 获得明确许可的 EPUB/TXT/PDF 转文本 | 正式产品级书库、真实阅读场景验证 | 默认 `review_required`，以授权协议为准 | `P2` |

推荐首批书目方向：

- `P0`：先用 1 到 2 本公版长篇小说跑通全链路，优先考虑与既有方案一致、章节结构清晰、角色关系复杂的文本。
- `P0`：保留 `《红楼梦》` 作为重点候选，用于章节切分、角色共现、象征密度和情绪峰值标注。
- `P0`：把 `《三体》`、`《一只特立独行的猪》` 记为内部验证候选，只先收 `书目信息 + 章节目录 + 权限状态 + 样例字段`，不默认进入公开语料。

落地动作：

1. 为每本候选书建立一条 `book manifest`，至少记录 `book_id`、标题、作者、来源 URL、格式、语言、版权状态、计划用途。
2. 优先搜集 `章节结构稳定`、`OCR 压力低`、`角色关系明显` 的文本。
3. 现代版权书若未拿到授权，不进入公开 `raw/books` 原文池，只保留 metadata 和内部跟踪状态。

### 3.2 Persona Source

| 子类 | 建议来源 | 适用场景 | 可公开性边界 | 优先级 |
| --- | --- | --- | --- | --- |
| 作者本人作品 | 小说、杂文、评论、序跋、随笔等原作 | 构建 `Lead Reader Agent` 的分析路径与表达习惯 | 公版作者可 `open`，其他作者多为 `internal_only` 或 `review_required` | `P0` |
| 书信/日记/访谈/演讲 | 公开信件、日记、采访实录、公开讲座整理 | 补事实层与风格层，减少 persona 只有“语气模仿”没有“思考路径”的问题 | 依具体来源定，常见为 `review_required` | `P1` |
| 传记与百科 | 传记、学术百科、课程资料、博物馆/纪念馆公开资料 | 生平、时代背景、代表主题、关系网络 | 多数只适合 `internal_only` 摘要化使用，少量开放资料可 `open` | `P1` |
| 书内角色证据集 | 角色出场段落、关键对话、动机转折、关系变化片段 | `Book Character Agent`、角色一致性评测、Bubble 触发 | 若书正文受限，则默认 `internal_only` | `P0` |
| 风格脚手架资料 | 团队人工整理的“分析步骤”“常见判断方式”“禁区” | system prompt scaffold、persona review、评测 rubric | 团队自产内容可 `open` | `P0` |

推荐首批 persona 方向：

- `P0`：`鲁迅`
  - 原因：项目材料已明确给出推理脚手架，且适合评论型批注任务。
- `P0`：`王尔德`
  - 原因：与鲁迅形成风格对照，适合做 persona 区分度评测。
- `P1`：`张爱玲`
  - 原因：已是核心示例，但资料公开边界与文本使用边界需要单独核。
- `P0`：`罗辑`
  - 仅作为 `character persona` 候选，数据主体来自书内角色证据集，不单独扩展成作者型知识库。

落地动作：

1. 每个 persona 建一条 `persona manifest`，拆成 `fact layer`、`style layer`、`source layer`。
2. 作者型 persona 与角色型 persona 分库存放，避免检索串扰。
3. 对 `张爱玲` 这类现代作者，先收集 `来源目录 + 摘要卡片 + 不可公开标记`，不要默认归入可发布语料。

### 3.3 Annotation

| 子类 | 建议来源 | 适用场景 | 可公开性边界 | 优先级 |
| --- | --- | --- | --- | --- |
| `highlight_qa` 金标 | 团队成员基于真实阅读片段手工编写 | 侧边问答、选中即问、citation 对齐 | 若含受版权限制长引文则 `internal_only`，可发布脱敏版 | `P0` |
| 情绪/冲突/心理复杂度标注 | 重点章节人工标注，必要时 LLM 辅助初筛、人审定稿 | Bubble 触发、salience score 校准 | 标签和 guideline 可 `open`，原文片段按书目版权决定 | `P0` |
| `chapter_evolution` 标注 | 章节总结、已读理解、误解修正、伏笔跟踪 | 章节总结、CME 评测、阅读进度回放 | 摘要和标签可 `open`，受限原文上下文默认不公开 | `P1` |
| persona review 标注 | 人工判断回答是否符合 persona 的价值观、推理路径、表达边界 | persona fidelity 训练与评测 | rubric 可 `open`，样本视引用情况决定 | `P0` |

推荐首批标注范围：

- 先围绕 1 本公版长篇 + 1 个内部现代书候选做小规模金标。
- 每本先抽 `5-8` 个高价值章节，不追求全书铺开。
- 每章至少覆盖：
  - `2-3` 条 highlight QA
  - `2` 条 Bubble 候选段
  - `1` 条 chapter evolution
  - `1` 条 spoiler-risk 问题

落地动作：

1. 先写标注 guideline，再开始批量打标，避免前后标准漂移。
2. 标注单元必须带 `book_id/chapter_id/paragraph_id/chunk_id`，保证后续可回溯。
3. 所有 LLM 辅助标注都必须留 `human_verified` 字段。

### 3.4 Evaluation

| 子类 | 建议来源 | 适用场景 | 可公开性边界 | 优先级 |
| --- | --- | --- | --- | --- |
| retrieval eval | 人工设计 query + 标准支撑段 + query-doc 对齐 | 验证 `top-k recall`、`MRR`、章节命中率 | query、标签、id 可 `open`；原文证据视版权决定 | `P0` |
| persona consistency eval | 围绕 `鲁迅 / 王尔德 / 张爱玲 / 罗辑` 设计同题异答样本 | 验证风格一致性、分析框架一致性、表达自然度 | rubric 可 `open`，引用样本需分版权处理 | `P0` |
| anti-spoiler eval | `SANQA`、`Spoiler-Trap`、位置化问答、诱导追问 | 验证 Narrative-Aware Gating 与 prompt guard | 样本结构与标签可 `open`，上下文片段按书目权限处理 | `P0` |
| emotion resonance / ERE eval | 中性解释、共情回应、分析反思三类互动样本 | 验证回应是否有边界感、不越界煽情 | rubric 可 `open`，样本文本视版权决定 | `P1` |
| cumulative meaning / CME eval | 多进度 checkpoint 下的意义更新问题 | 验证章节推进中的理解累积能力 | 题目和标签可 `open`，上下文片段视版权决定 | `P1` |
| user study sample | 阅读理解题、回忆题、问卷、停留时长模板 | 后续真实用户实验 | 默认 `review_required` | `P2` |

推荐首批评测方向：

- `P0`：先建最小三件套
  - `retrieval eval`
  - `persona consistency eval`
  - `anti-spoiler eval`
- `P1`：在最小三件套稳定后，再扩到 `ERE` 与 `CME`。

落地动作：

1. 每类评测先做 `20-30` 条高质量样本，不急着追规模。
2. 防剧透集必须显式记录 `reader_progress_boundary`。
3. persona 评测必须保留“普通模型基线回答”或“非 persona 回答”作为对照。

## 4. 推荐搜集顺序

### Phase 1：两周内跑通最小闭环

- 书本文本：
  - 选定 `1` 本公版书进入公开实验主线。
  - 为 `《红楼梦》` 建 manifest 与章节结构草表。
  - 为 `《三体》`、`《一只特立独行的猪》` 只建 metadata 跟踪卡。
- persona：
  - 先做 `鲁迅`、`王尔德` 两套作者型 persona source。
  - 先做 `罗辑` 的角色证据集模板，不急着扩全书。
- 标注：
  - 写完 `highlight_qa` 与 `bubble salience` guideline。
  - 产出一批小金标样本用于打通 ingestion -> retrieval -> response。
- 评测：
  - 建立最小 `retrieval/persona/anti_spoiler` 三套样本格式。

### Phase 2：扩展到项目示例主线

- 补 `张爱玲` persona source，但严格标记可公开边界。
- 如果 `《红楼梦》` 文本链路稳定，优先在它上面扩 chapter evolution、ERE、CME。
- 对现代书目推动授权或明确内部使用边界。

### Phase 3：为后续产品化准备

- 扩书目池与 persona 池。
- 对接真实用户阅读日志和用户实验样本。
- 形成开源版 release manifest 与内部版 full manifest 两套出口。

## 5. 团队分工建议

| 任务 | 主要产出 | 建议对接角色 |
| --- | --- | --- |
| 书目搜集与权限记录 | `book manifest`、来源链接、权限状态 | 数据 Agent + 项目负责人 |
| persona 资料搜集 | `persona manifest`、事实层/风格层卡片 | 数据 Agent + prompt/model Agent |
| 标注规范制定 | guideline、标签定义、抽检规则 | 数据 Agent + 评测 Agent |
| 原文清洗与切分 | 章节解析、chunk、metadata | 数据工程 / 后端 Agent |
| 评测集设计 | query、rubric、judge 规则 | 评测 Agent + prompt/model Agent |
| 权限复核 | 能否公开、能否入仓、能否发 demo | 项目负责人 / 版权对接同学 |

## 6. 还缺的关键前置物

- 一份统一的 `manifest` 字段模板，至少覆盖书目与 persona 两类来源记录。
- 一份标注 guideline 初稿，至少先覆盖 `highlight_qa`、`salience_label`、`anti_spoiler_eval`。
- 一份“现代版权书处理规则”，明确什么可以入库、什么只能保留 metadata。
- 与模型/后端同学确认首版 ingestion 所需最小字段，避免先采了后面用不上。

## 7. 建议立即开的任务单

1. 建 `book manifest` 模板，并登记 `《红楼梦》`、`《三体》`、`《一只特立独行的猪》`。
2. 建 `persona manifest` 模板，并登记 `鲁迅`、`王尔德`、`张爱玲`、`罗辑`。
3. 选 `1` 本公版书，产出首批 `highlight_qa`、`spoiler_trap`、`persona_consistency` 小样本。
4. 约项目负责人确认现代书目与现代作者资料的公开边界。
5. 约模型/后端 Agent 对齐 `chunk metadata` 最小字段集。

## 8. 本文与现有方案的对齐点

- 保持四类资产不变：`book text`、`persona source`、`annotation`、`evaluation`。
- 保持 persona 的三层结构：`fact layer`、`style layer`、`source layer`。
- 保持评测主线：`retrieval`、`persona consistency`、`anti-spoiler`，并向 `SANQA / ERE / CME` 延展。
- 保持项目当前叙事：以 `鲁迅 / 张爱玲 / 王尔德 / 罗辑` 和 `《红楼梦》 / 《三体》 / 《一只特立独行的猪》` 为优先对齐对象，但严格区分公开数据与内部数据。
