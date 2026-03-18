# smartmcp Benchmarks

Measures search accuracy of smartmcp's semantic tool routing.

## Quick Start

### 1. Snapshot your tools (one-time, or when servers change)

```bash
python benchmarks/snapshot_tools.py --config smartmcp.json
```

Saves all tool schemas to `benchmarks/tools_snapshot.json`.

### 2. Run the benchmark

```bash
python benchmarks/benchmark.py --snapshot benchmarks/tools_snapshot.json
```

Or from live servers:

```bash
python benchmarks/benchmark.py --config smartmcp.json
```

### 3. Save and compare results

```bash
# Save baseline
python benchmarks/benchmark.py --snapshot benchmarks/tools_snapshot.json --output benchmarks/results/baseline.json

# After making changes
python benchmarks/benchmark.py --snapshot benchmarks/tools_snapshot.json --output benchmarks/results/v2.json

# Compare
python benchmarks/compare.py benchmarks/results/baseline.json benchmarks/results/v2.json
```

## Metrics

| Metric | What it measures |
|--------|-----------------|
| **Recall@1** | Was the correct tool the #1 result? |
| **Recall@K** | Was the correct tool anywhere in the top K? |
| **MRR** | Mean Reciprocal Rank — rewards higher placement |
| **Latency** | Time per search query (ms) |

## Test Cases

Edit `test_cases.json` to add or modify queries. Each entry:

```json
{
  "query": "what an LLM would say when it needs this tool",
  "expected": "server__tool_name"
}
```

## Options

```
--snapshot PATH    Load tools from snapshot (offline, fast)
--config PATH      Load tools from live MCP servers
--test-cases PATH  Custom test cases file
--top-k N          Results per query (default: 5)
--model NAME       Sentence-transformers model
--output PATH      Save results as JSON
```
