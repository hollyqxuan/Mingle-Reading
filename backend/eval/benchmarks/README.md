# 基准测试数据

此目录包含对 MVP 评测流程进行冒烟测试所需的最小基准数据。

## 文件

- `highlight_qa/demo/*.jsonl`
  - 与 `backend/assets/schemas/highlight_qa.schema.json` 对齐的逐行样本
- `anti_spoiler/demo/*.jsonl`
  - 与 `backend/assets/schemas/anti_spoiler_eval.schema.json` 对齐的逐行样本
- `chapter_summary/demo/*.jsonl`
  - `backend/eval/run_eval.py` 使用的轻量级摘要检查

## 范围

这些测试数据刻意保持小规模和合成化：

- 它们针对内置的 `backend/assets/examples/mingle_demo_book.txt`
- 用于回归检查，而非模型排名
- 仅断言直接 `eval` 运行所需的最小行为

## 运行

```bash
python backend/eval/run_eval.py
```
