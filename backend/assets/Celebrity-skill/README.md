# Celebrity Skill — 名家角色知识库

三位领读 Agent（鲁迅、马克·吐温、张爱玲）的本地知识库。每位 Agent 的语料按六个维度组织，以 Markdown 表格存储，在运行时通过词袋匹配检索，注入系统提示词以塑造回复风格。

## 目录结构

```
Celebrity-skill/
├── LuXun-skill-main/          鲁迅知识库
│   ├── SKILL.md                技能定义 (角色描述 + 约束)
│   └── references/research/
│       ├── 01-writings.md       著作与作品 (148 行)
│       ├── 02-conversations.md  语录与对话 (238 行)
│       ├── 03-expression-dna.md 语言风格特征 (335 行)
│       ├── 04-external-views.md 外部评价 (414 行)
│       ├── 05-decisions.md      判断与决策模式 (331 行)
│       └── 06-timeline.md       生平时间线 (289 行)
│
├── MarkTwain-skill-main/       马克·吐温知识库
│   └── references/research/     同上六文件
│
└── ZhangAiLing-skill-main/      张爱玲知识库
    └── references/research/      同上六文件
```

## 六个数据维度的含义

| 文件 | 分类标签 | 内容 | 检索权重 |
|------|---------|------|:--:|
| `01-writings.md` | `works` | 已出版著作、代表性段落、核心观点 | 1.20 |
| `02-conversations.md` | `voice_sources` | 语录、书信、访谈中体现的语言风格 | 1.15 |
| `03-expression-dna.md` | `voice_sources` | 句式结构、修辞偏好、比喻体系、叙事节奏 | 1.25 |
| `04-external-views.md` | `biography_and_critical` | 同时代人评价、后世评论、学术分析 | 0.95 |
| `05-decisions.md` | `biography_and_critical` | 生平关键决策、创作选择、思想演变 | 1.10 |
| `06-timeline.md` | `biography_and_critical` | 按时间排序的生平大事记 | 0.90 |

## 数据来源与构建流程

### 1. 数据收集

所有语料来自以下公开可获取来源：

- **一手资料**：本人的著作原文、书信、日记、演讲稿
- **二手资料**：学术论文、文学评论、传记研究
- **公开知识库**：维基百科、中国知网、文学史教材

### 2. 数据结构化

原始语料经过三轮处理：

```
原始语料 (论文/书信/著作)
  ↓
人工标注：按六个维度分类 + 标注来源 + 可信度标记
  ↓
模板化：转换为 Markdown 表格格式，统一字段 (内容/来源/可信度/收录理由)
  ↓
分段：按 780 字符段落切割，保留 Markdown 标题层级
```

### 3. 检索机制

运行时检索采用**词袋匹配**（token overlap）：

```
用户问题 + 当前上下文
  → 分词 (中文按字, 英文按词)
  → 遍历知识库所有段落，计算 token 重叠度
  → 重叠度 × 维度权重 = 最终得分
  → 返回 Top-K 段落
  → 注入 LLM 系统提示词
```

不使用嵌入向量或语义检索——词袋匹配在 1,755 行/角色的规模下足够快（毫秒级），且可解释性更强。

### 4. 维度权重设计

```
voice_sources (对话 + 表达DNA):    最高权重 — 直接影响回复的语言风格
works (著作):                     高权重 — 引用准确的观点和立场
biography_and_critical (传记/评价): 较低权重 — 辅助背景，不主导回复口吻
```

### 以鲁迅为例

| 文件 | 内容示例 |
|------|------|
| writings | 《呐喊》《彷徨》《野草》等著作概览、杂文集列表及批判要点 |
| conversations | 与许广平的通信片段、与青年作家的对话记录 |
| expression-dna | "横眉冷对"的批判姿态、反讽修辞、文言白话交织的杂文语调 |
| external-views | 钱理群《鲁迅十五讲》、张旭东讲座分析 |
| decisions | 弃医从文的选择、"左联"加入与退出 |
| timeline | 1881-1936 生平编年：出生、留学、创作、逝世 |

## 添加新的名家 Agent

1. 在 `Celebrity-skill/` 下创建 `{Name}-skill-main/` 目录
2. 复制六个 research 文件模板并填入语料
3. 创建 `SKILL.md`（定义角色描述和约束）
4. 在 `backend/agents/celebrity/persona_service.py` 的 `AGENT_CONFIGS` 中注册
5. 在 `.env` 中配置对应的 API 端点
