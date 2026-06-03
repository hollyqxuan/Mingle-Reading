#!/usr/bin/env python3
"""Compare knowledge graph builds across disambiguation methods."""
import json, sys
from pathlib import Path
from collections import Counter

GRAPHS_DIR = Path("backend/runtime/graphs")
BASE_NAME = "百年孤独-根据马尔克斯指定版本翻译-未做任何增删-加西亚-马尔克斯-范晔-z-lib-org"
METHODS = {
    0: ("baseline", f"{BASE_NAME}-exp-baseline"),
    1: ("description-cache", f"{BASE_NAME}-exp-description-cache"),
    2: ("cot-description", f"{BASE_NAME}-exp-cot-description"),
    3: ("description-generation", f"{BASE_NAME}-exp-description-generation"),
}


def analyze(path: Path) -> dict:
    with open(path) as f:
        g = json.load(f)

    entities = g['entities']
    relations = g['relations']
    mc = Counter(e['mention_count'] for e in entities.values())
    total = len(entities)
    chars = sum(1 for e in entities.values() if e['entity_type'] == 'character')

    # Check for "何塞·阿尔卡蒂オ" entities specifically
    jose_entities = {}
    for eid, e in entities.items():
        name = e['canonical_name']
        if '何塞·阿尔卡蒂奥' in name or '阿尔卡ティオ' in name:
            jose_entities[name] = e['mention_count']

    return {
        'total': total,
        'chars': chars,
        'relations': len(relations),
        'mc1': mc.get(1, 0),
        'mc1pct': round(mc.get(1, 0) / total * 100, 1) if total else 0,
        'mc4plus': sum(v for k, v in mc.items() if k >= 4),
        'mc10plus': sum(v for k, v in mc.items() if k >= 10),
        'llm_calls': g.get('metadata', {}).get('llm_calls', '?'),
        'jose_entities': jose_entities,
    }


print("=" * 80)
print("  Disambiguation Method Comparison — 百年孤独")
print("=" * 80)
print(f"\n{'Metric':<30s}", end="")
for method, (label, _) in METHODS.items():
    print(f" {'M'+str(method)+' '+label:<22s}", end="")
print()

results = {}
for method, (label, book_id) in METHODS.items():
    path = GRAPHS_DIR / f"{book_id}.graph.json"
    if not path.exists():
        results[method] = None
        continue
    results[method] = analyze(path)

print("-" * 80)
for metric, fmt, key in [
    ("Total Entities", ">5d", "total"),
    ("Character Entities", ">5d", "chars"),
    ("Total Relations", ">5d", "relations"),
    ("mc=1 Fragments", ">5d", "mc1"),
    ("mc>=4 Core", ">5d", "mc4plus"),
    ("mc>=10 Core+", ">5d", "mc10plus"),
    ("LLM Calls", ">5d", "llm_calls"),
]:
    print(f"  {metric:<28s}", end="")
    for method in METHODS:
        r = results.get(method)
        if r:
            print(f" {r[key]:{fmt}}", end="  ")
        else:
            print(f" {'—':>5s}", end="  ")
    print()

# Fragmentation rate
print(f"  {'Fragmentation Rate':<28s}", end="")
for method in METHODS:
    r = results.get(method)
    if r:
        print(f" {r['mc1pct']:>4.1f}%", end="  ")
    else:
        print(f" {'—':>5s}", end="  ")
print()

# José Arcadio specific check
print(f"\n{'—'*80}")
print("  José Arcadio 家族实体检查")
print(f"{'—'*80}")
for method in METHODS:
    r = results.get(method)
    if not r:
        print(f"\n  M{method}: No graph yet")
        continue
    label = METHODS[method][1]
    print(f"\n  M{method} ({label}):")
    for name, mc in sorted(r['jose_entities'].items(), key=lambda x: -x[1]):
        marker = ""
        if '布恩迪亚' not in name and '第二' not in name and '叔父' not in name and '孪生' not in name:
            marker = " ← 可能是二代长子?"
        print(f"    mc={mc:>4}  {name}{marker}")
