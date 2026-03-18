"""Benchmark runner for smartmcp search accuracy.

Loads tool schemas (from snapshot or live servers), builds the embedding index,
runs test cases, and reports recall@1, recall@k, MRR, and per-query details.

Usage:
    # From snapshot (fast, offline):
    python benchmarks/benchmark.py --snapshot benchmarks/tools_snapshot.json

    # From live servers:
    python benchmarks/benchmark.py --config smartmcp.json

    # Save results to JSON:
    python benchmarks/benchmark.py --snapshot benchmarks/tools_snapshot.json --output benchmarks/results/baseline.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import click
from mcp import types

from smartmcp.config import load_config
from smartmcp.embedding import EmbeddingIndex
from smartmcp.upstream import UpstreamManager

logging.basicConfig(level=logging.WARNING, format="%(name)s — %(message)s")
logger = logging.getLogger(__name__)

TEST_CASES_PATH = Path(__file__).parent / "test_cases.json"


@dataclass
class QueryResult:
    query: str
    expected: str
    top_k_results: list[str]
    top_k_scores: list[float]
    rank: int | None  # 1-indexed rank of expected tool, None if not found
    recall_at_1: bool
    recall_at_k: bool
    latency_ms: float


@dataclass
class BenchmarkResults:
    total_queries: int = 0
    recall_at_1: float = 0.0
    recall_at_k: float = 0.0
    mrr: float = 0.0
    avg_latency_ms: float = 0.0
    top_k: int = 5
    tool_count: int = 0
    embedding_model: str = ""
    index_build_time_ms: float = 0.0
    query_results: list[QueryResult] = field(default_factory=list)
    namespace_results: dict[str, "NamespaceResult"] = field(default_factory=dict)


@dataclass
class NamespaceResult:
    total_queries: int = 0
    recall_at_1: float = 0.0
    recall_at_k: float = 0.0
    mrr: float = 0.0
    avg_latency_ms: float = 0.0


def load_tools_from_snapshot(path: str) -> list[types.Tool]:
    """Load tool schemas from a JSON snapshot file."""
    raw = json.loads(Path(path).read_text())
    tools = []
    for item in raw:
        tools.append(types.Tool(
            name=item["name"],
            description=item.get("description", ""),
            inputSchema=item.get("inputSchema", {}),
        ))
    return tools


async def load_tools_from_live(config_path: str) -> list[types.Tool]:
    """Connect to live servers and collect tool schemas."""
    config = load_config(config_path)
    upstream = UpstreamManager()
    failed = await upstream.connect_all(config)
    if failed:
        logger.warning("Failed: %s", ", ".join(failed))
    raw_tools = await upstream.collect_tools()
    await upstream.close()
    return [tool for _, tool in raw_tools]


def load_test_cases(path: Path | None = None) -> list[dict]:
    """Load test cases from JSON file."""
    p = path or TEST_CASES_PATH
    if not p.exists():
        raise FileNotFoundError(f"Test cases not found: {p}")
    return json.loads(p.read_text())


def find_invalid_expected_cases(test_cases: list[dict], tools: list[types.Tool]) -> list[dict]:
    """Return test cases whose expected tool name is not in the loaded catalog."""
    tool_names = {tool.name for tool in tools}
    return [case for case in test_cases if case.get("expected") not in tool_names]


def _namespace_from_tool_name(tool_name: str) -> str:
    if "__" in tool_name:
        return tool_name.split("__", 1)[0]
    return "(unprefixed)"


def run_benchmark(tools: list[types.Tool], test_cases: list[dict],
                  top_k: int = 5, model: str = "all-MiniLM-L6-v2") -> BenchmarkResults:
    """Run the full benchmark suite."""
    # Build index
    index = EmbeddingIndex(model)
    build_start = time.perf_counter()
    index.build_index(tools)
    build_time = (time.perf_counter() - build_start) * 1000

    results = BenchmarkResults(
        top_k=top_k,
        tool_count=len(tools),
        embedding_model=model,
        index_build_time_ms=round(build_time, 2),
    )

    recall_1_hits = 0
    recall_k_hits = 0
    reciprocal_ranks = []
    latencies = []
    namespace_stats: dict[str, dict[str, float]] = {}

    for case in test_cases:
        query = case["query"]
        expected = case["expected"]

        start = time.perf_counter()
        search_results = index.search(query, top_k=top_k)
        latency = (time.perf_counter() - start) * 1000

        top_names = [tool.name for tool, _ in search_results]
        top_scores = [round(score, 4) for _, score in search_results]

        rank = None
        if expected in top_names:
            rank = top_names.index(expected) + 1

        is_recall_1 = rank == 1
        is_recall_k = rank is not None

        if is_recall_1:
            recall_1_hits += 1
        if is_recall_k:
            recall_k_hits += 1
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        latencies.append(latency)

        namespace = _namespace_from_tool_name(expected)
        if namespace not in namespace_stats:
            namespace_stats[namespace] = {
                "total": 0.0,
                "recall_1_hits": 0.0,
                "recall_k_hits": 0.0,
                "rr_sum": 0.0,
                "latency_sum": 0.0,
            }
        namespace_stats[namespace]["total"] += 1
        namespace_stats[namespace]["recall_1_hits"] += 1 if is_recall_1 else 0
        namespace_stats[namespace]["recall_k_hits"] += 1 if is_recall_k else 0
        namespace_stats[namespace]["rr_sum"] += 1.0 / rank if rank else 0.0
        namespace_stats[namespace]["latency_sum"] += latency

        results.query_results.append(QueryResult(
            query=query,
            expected=expected,
            top_k_results=top_names,
            top_k_scores=top_scores,
            rank=rank,
            recall_at_1=is_recall_1,
            recall_at_k=is_recall_k,
            latency_ms=round(latency, 2),
        ))

    n = len(test_cases)
    results.total_queries = n
    results.recall_at_1 = round(recall_1_hits / n, 4) if n else 0
    results.recall_at_k = round(recall_k_hits / n, 4) if n else 0
    results.mrr = round(sum(reciprocal_ranks) / n, 4) if n else 0
    results.avg_latency_ms = round(sum(latencies) / n, 2) if n else 0
    for namespace, stats in namespace_stats.items():
        total = int(stats["total"])
        if total == 0:
            continue
        results.namespace_results[namespace] = NamespaceResult(
            total_queries=total,
            recall_at_1=round(stats["recall_1_hits"] / total, 4),
            recall_at_k=round(stats["recall_k_hits"] / total, 4),
            mrr=round(stats["rr_sum"] / total, 4),
            avg_latency_ms=round(stats["latency_sum"] / total, 2),
        )

    return results


def print_results(results: BenchmarkResults) -> None:
    """Print formatted benchmark results to stdout."""
    print(f"\n{'='*70}")
    print(f"  smartmcp Benchmark Results")
    print(f"{'='*70}")
    print(f"  Model:        {results.embedding_model}")
    print(f"  Tools:        {results.tool_count}")
    print(f"  Top-K:        {results.top_k}")
    print(f"  Test cases:   {results.total_queries}")
    print(f"  Index build:  {results.index_build_time_ms:.0f}ms")
    print(f"{'='*70}\n")

    for qr in results.query_results:
        icon = "✅" if qr.recall_at_k else "❌"
        rank_str = f"rank #{qr.rank}" if qr.rank else "NOT FOUND"
        print(f"{icon} \"{qr.query}\"")
        print(f"   Expected: {qr.expected} → {rank_str}")
        print(f"   Top {results.top_k}: {', '.join(qr.top_k_results)}")
        print(f"   Scores:   {', '.join(str(s) for s in qr.top_k_scores)}")
        print(f"   Latency:  {qr.latency_ms:.1f}ms")
        print()

    print(f"{'='*70}")
    print(f"  Recall@1:     {results.recall_at_1:.1%} ({int(results.recall_at_1 * results.total_queries)}/{results.total_queries})")
    print(f"  Recall@{results.top_k}:     {results.recall_at_k:.1%} ({int(results.recall_at_k * results.total_queries)}/{results.total_queries})")
    print(f"  MRR:          {results.mrr:.4f}")
    print(f"  Avg latency:  {results.avg_latency_ms:.1f}ms/query")
    print(f"{'='*70}\n")


def save_results(results: BenchmarkResults, path: str) -> None:
    """Save results to JSON for cross-run comparison."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(results)
    data["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out.write_text(json.dumps(data, indent=2))
    print(f"Results saved to {path}")


@click.command()
@click.option("--snapshot", "snapshot_path", type=click.Path(exists=True),
              help="Path to tools_snapshot.json (offline mode).")
@click.option("--config", "config_path", type=click.Path(exists=True),
              help="Path to smartmcp.json (live mode).")
@click.option("--test-cases", "test_cases_path", type=click.Path(exists=True),
              help="Path to test_cases.json (default: benchmarks/test_cases.json).")
@click.option("--top-k", "top_k", default=5, type=int,
              help="Number of results to return per query.")
@click.option("--model", default="all-MiniLM-L6-v2",
              help="Sentence-transformers model to use.")
@click.option("--output", "output_path", type=click.Path(),
              help="Save results to JSON file.")
@click.option("--allow-invalid-expected", is_flag=True,
              help="Skip test cases whose expected tool is missing from loaded tools.")
def main(snapshot_path: str | None, config_path: str | None,
         test_cases_path: str | None, top_k: int, model: str,
         output_path: str | None, allow_invalid_expected: bool) -> None:
    """Run smartmcp search accuracy benchmarks."""
    if not snapshot_path and not config_path:
        raise click.UsageError("Provide either --snapshot or --config to load tools.")

    # Load tools
    if snapshot_path:
        print(f"Loading tools from snapshot: {snapshot_path}")
        tools = load_tools_from_snapshot(snapshot_path)
    else:
        print(f"Loading tools from live servers: {config_path}")
        tools = asyncio.run(load_tools_from_live(config_path))

    print(f"Loaded {len(tools)} tools")

    # Load test cases
    cases_path = Path(test_cases_path) if test_cases_path else None
    test_cases = load_test_cases(cases_path)
    print(f"Loaded {len(test_cases)} test cases")

    invalid_cases = find_invalid_expected_cases(test_cases, tools)
    if invalid_cases:
        print(f"Found {len(invalid_cases)} invalid test case(s) with unknown expected tools:")
        for case in invalid_cases:
            print(f"  - expected={case['expected']} | query=\"{case['query']}\"")
        if not allow_invalid_expected:
            raise click.UsageError(
                "Invalid expected tool names detected. "
                "Fix test cases or rerun with --allow-invalid-expected."
            )
        invalid_expected = {case["expected"] for case in invalid_cases}
        test_cases = [case for case in test_cases if case["expected"] not in invalid_expected]
        print(f"Proceeding after skipping invalid cases. Remaining test cases: {len(test_cases)}")

    if not test_cases:
        raise click.UsageError("No valid test cases to run.")

    # Run benchmark
    results = run_benchmark(tools, test_cases, top_k=top_k, model=model)
    print_results(results)

    if output_path:
        save_results(results, output_path)


if __name__ == "__main__":
    main()
