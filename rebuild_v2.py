#!/usr/bin/env python3
"""Rebuild 百年孤独 knowledge graph — sliding window pipeline, v2."""
import sys, json, time
sys.path.insert(0, '.')
import backend.config

from backend.api.schemas import BookRecord, BookChunk
from backend.knowledge_graph.builder import TemporalGraphBuilder
from backend.knowledge_graph.extraction_window import build_extraction_windows

book_path = 'backend/runtime/books/百年孤独-根据马尔克斯指定版本翻译-未做任何增删-加西亚-马尔克斯-范晔-z-lib-org.json'
with open(book_path) as f:
    book_data = json.load(f)

chunks = [BookChunk(**c) for c in book_data['chunks']]
windows = build_extraction_windows(chunks, window_size=800)
print(f"Chunks: {len(chunks)} -> Windows: {len(windows)}", flush=True)

book = BookRecord(**book_data)

def progress(payload):
    stage = payload.get("stage", "")
    if stage == "window-extraction":
        wi = payload.get("processed_snippets", 0)
        total = payload.get("total_snippets", 504)
        print(f"  W[{wi}/{total}] {payload.get('message','')}", flush=True)
    elif stage == "graph-episode-start":
        cur = payload.get("processed_snippets", 0)
        total = payload.get("total_snippets", 838)
        if cur % 100 == 0:
            print(f"  E[{cur}/{total}]", flush=True)

builder = TemporalGraphBuilder(progress_callback=progress)
t0 = time.time()
graph = builder.build(book)
elapsed = time.time() - t0

print(f"DONE {elapsed:.0f}s ({elapsed/60:.1f}m)", flush=True)
print(f"LLM: {graph.metadata.get('llm_calls')}", flush=True)
print(f"Entities: {len(graph.entities)}", flush=True)
print(f"Relations: {len(graph.relations)}", flush=True)
print(f"Episodes: {len(graph.episodes)}", flush=True)
print(f"Communities: {len(graph.communities)}", flush=True)
print(f"Sagas: {len(graph.sagas)}", flush=True)
