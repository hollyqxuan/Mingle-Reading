#!/usr/bin/env python3
"""Rebuild with configurable disambiguation method.

Usage:
    python rebuild_experiment.py --method 0   # Baseline
    python rebuild_experiment.py --method 1   # Character Description Cache
    python rebuild_experiment.py --method 2   # CoT + Description Cache
    python rebuild_experiment.py --method 3   # Description Cache + Generation Info
    python rebuild_experiment.py --all        # Run all sequentially
"""
import sys, json, time, argparse, os
sys.path.insert(0, '.')
import backend.config
# Force MUSE_NEUTRAL (flash model) — pop AFTER config loads .env
for var in ['GRAPHITI_EXTRACTOR_API_KEY', 'GRAPHITI_EXTRACTOR_BASE_URL', 'GRAPHITI_EXTRACTOR_MODEL_NAME']:
    os.environ.pop(var, None)
from backend.api.schemas import BookRecord, BookChunk
from backend.knowledge_graph.builder import TemporalGraphBuilder
from backend.knowledge_graph.extraction_window import build_extraction_windows

BOOK_PATH = 'backend/runtime/books/百年孤独-根据马尔克斯指定版本翻译-未做任何增删-加西亚-马尔克斯-范晔-z-lib-org.json'
BASE_BOOK_ID = "百年孤独-根据马尔克斯指定版本翻译-未做任何增删-加西亚-马尔克斯-范晔-z-lib-org"
METHODS = {
    0: ("baseline", False, False),
    1: ("description-cache", True, False),
    2: ("cot-description", True, False),  # CoT is in base prompt
    3: ("description-generation", True, True),
}

def build(method: int):
    label, use_desc, use_gen = METHODS[method]
    book_id = f"{BASE_BOOK_ID}-exp-{label}"  # All methods use unique IDs
    print(f"\n{'='*60}")
    print(f"  Method {method}: {label}")
    print(f"  DescCache={use_desc}, GenInfo={use_gen}")
    print(f"  Book ID: {book_id}")
    print(f"{'='*60}\n", flush=True)

    with open(BOOK_PATH) as f:
        book_data = json.load(f)
    chunks = [BookChunk(**c) for c in book_data['chunks']]
    windows = build_extraction_windows(chunks, window_size=800)
    print(f"Chunks: {len(chunks)} -> Windows: {len(windows)}", flush=True)

    book_data['book_id'] = book_id
    book = BookRecord(**book_data)

    def progress(payload):
        stage = payload.get('stage', '')
        if stage == 'window-extraction':
            wi = payload.get('processed_snippets', 0)
            total = payload.get('total_snippets', 504)
            if wi % 20 == 0:
                print(f"  W[{wi}/{total}]", flush=True)
        elif stage == 'graph-episode-start':
            cur = payload.get('processed_snippets', 0)
            if cur % 200 == 0:
                print(f"  E[{cur}]", flush=True)

    builder = TemporalGraphBuilder(
        progress_callback=progress,
        use_description_cache=use_desc,
        use_generation_info=use_gen,
    )
    start = time.time()
    graph = builder.build(book)
    elapsed = time.time() - start

    print(f"\nDONE in {elapsed:.0f}s ({elapsed/60:.1f}m)", flush=True)
    print(f"LLM: {graph.metadata.get('llm_calls')}", flush=True)
    print(f"Entities: {len(graph.entities)}", flush=True)
    print(f"Relations: {len(graph.relations)}", flush=True)
    print(f"Dep edges: {sum(len(ep.depends_on) for ep in graph.episodes.values())}", flush=True)
    return graph


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--method', type=int, default=0, choices=[0,1,2,3])
    p.add_argument('--all', action='store_true')
    args = p.parse_args()

    methods = [0, 1, 2, 3] if args.all else [args.method]
    for m in methods:
        build(m)
