"""Compare two benchmark result JSON files side-by-side.

Usage:
    python benchmarks/compare.py benchmarks/results/baseline.json benchmarks/results/after_improvement.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_results(path: str) -> dict:
    return json.loads(Path(path).read_text())


def fmt_pct(val: float) -> str:
    return f"{val:.1%}"


def fmt_delta(before: float, after: float, as_pct: bool = True) -> str:
    diff = after - before
    if as_pct:
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff:.1%}"
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.4f}"


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python benchmarks/compare.py <before.json> <after.json>")
        sys.exit(1)

    before = load_results(sys.argv[1])
    after = load_results(sys.argv[2])

    print(f"\n{'='*60}")
    print(f"  Benchmark Comparison")
    print(f"{'='*60}")
    print(f"  Before: {sys.argv[1]} ({before.get('timestamp', '?')})")
    print(f"  After:  {sys.argv[2]} ({after.get('timestamp', '?')})")
    print(f"{'='*60}\n")

    metrics = [
        ("Recall@1", "recall_at_1", True),
        (f"Recall@{before.get('top_k', 5)}", "recall_at_k", True),
        ("MRR", "mrr", False),
        ("Avg latency (ms)", "avg_latency_ms", False),
        ("Index build (ms)", "index_build_time_ms", False),
    ]

    print(f"  {'Metric':<20} {'Before':>10} {'After':>10} {'Delta':>10}")
    print(f"  {'-'*50}")
    for label, key, is_pct in metrics:
        b = before.get(key, 0)
        a = after.get(key, 0)
        if is_pct:
            print(f"  {label:<20} {fmt_pct(b):>10} {fmt_pct(a):>10} {fmt_delta(b, a, True):>10}")
        else:
            print(f"  {label:<20} {b:>10.2f} {a:>10.2f} {fmt_delta(b, a, False):>10}")

    # Show queries that changed
    before_results = {qr["query"]: qr for qr in before.get("query_results", [])}
    after_results = {qr["query"]: qr for qr in after.get("query_results", [])}

    improved = []
    regressed = []

    for query in before_results:
        if query not in after_results:
            continue
        b = before_results[query]
        a = after_results[query]
        b_rank = b.get("rank")
        a_rank = a.get("rank")

        if a_rank is not None and (b_rank is None or a_rank < b_rank):
            improved.append((query, b_rank, a_rank))
        elif b_rank is not None and (a_rank is None or a_rank > b_rank):
            regressed.append((query, b_rank, a_rank))

    if improved:
        print(f"\n  ✅ Improved ({len(improved)}):")
        for query, b_rank, a_rank in improved:
            b_str = f"#{b_rank}" if b_rank else "miss"
            a_str = f"#{a_rank}" if a_rank else "miss"
            print(f"     \"{query}\" — {b_str} → {a_str}")

    if regressed:
        print(f"\n  ❌ Regressed ({len(regressed)}):")
        for query, b_rank, a_rank in regressed:
            b_str = f"#{b_rank}" if b_rank else "miss"
            a_str = f"#{a_rank}" if a_rank else "miss"
            print(f"     \"{query}\" — {b_str} → {a_str}")

    if not improved and not regressed:
        print(f"\n  No individual query rank changes.")

    print()


if __name__ == "__main__":
    main()
