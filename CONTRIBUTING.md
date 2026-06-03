# 参与贡献

感谢你帮助改进 Mingle Reading。当前仓库是一个基于以下技术构建的轻量级 Python MVP：

- `FastAPI` 作为 API 层
- `frontend/` 中的静态前端
- `backend/workspace_state/` 下的本地 JSON 文件作为运行时存储

## 开始之前

- 阅读 `README.md` 中的项目概览。
- 保持变更与现有 MVP 范围一致。
- 不要提交 `backend/workspace_state/` 中的生成文件。
- 倾向于小而可审查的 Pull Request。

## 本地环境搭建

1. 创建并激活 Python 环境。
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 启动应用：

```bash
python main.py
```

4. 打开 `http://127.0.0.1:8000`。

## 项目区域

- `backend/api/`：FastAPI 应用和 HTTP 端点
- `backend/common/`：共享配置和 Pydantic 模型
- `backend/data/`：导入和本地存储辅助模块
- `backend/knowledge_base/`：图谱、问答检索和角色逻辑
- `backend/safety/`：防剧透保护
- `backend/llm_memory/`：角色、编排和摘要生成
- `frontend/`：静态 HTML/CSS/JS 阅读器 UI
- `backend/assets/data/`、`backend/assets/schemas/`、`backend/benchmarks/`、`backend/eval/`：数据集、schema 和评测资产
- `backend/scripts/`：构建和数据集工具脚本

## 开发期望

- 尽可能复用当前的数据 schema 和文件命名约定。
- 保持新功能与现有 FastAPI + 静态前端架构兼容。
- 当行为发生变化时，添加或更新测试。
- 如有新的根级环境搭建要求，应在 README 的后续修改中说明。

## 推荐检查项

在发起 Pull Request 之前运行以下命令：

```bash
python backend/eval/run_eval.py
```

如果你的变更涉及数据集构建器，还需运行相关脚本并确认其产生有效的 JSON/JSONL 输出。

## Pull Request 注意事项

发起 PR 时请包含：

- 变更了什么
- 为什么变更
- 如何验证的
- 任何已知局限或后续工作

## 范围与安全

- 除非明确可再分发，否则避免提交受版权保护的原始文本。
- 当来源再分发权限不明确时，优先使用 schema、示例和仅元数据记录。
- 保持角色和评测资产可追溯至其来源。
