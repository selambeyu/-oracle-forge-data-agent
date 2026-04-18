# Evaluation Harness

This directory contains the Oracle Forge evaluation harness source code and its
baseline artifacts.

## What Is Here

- `harness.py` — trace logging, per-query records, score progression
- `run_benchmark.py` — rerunnable benchmark entrypoint
- `score.py` — summary scorer for saved benchmark output
- `score_log.json` — baseline score progression log
- `trace_log.jsonl` — append-only per-query and per-tool-call trace log

## Rerun From The Repo

From the repository root:

```bash
PYTHONPATH=. uv run python -m eval.run_benchmark \
  --agent agent.oracle_forge_agent \
  --trials 1 \
  --output eval/baseline.json
```

This uses the built-in sample benchmark set unless you provide `--questions`.

To score a saved result file:

```bash
PYTHONPATH=. uv run python -m eval.score --results eval/baseline.json
```

## Per-query Record Minimums

Each query record written through the harness includes:

- `query_id`
- `query_text`
- `correct`
- `tool_call_ids`
- `tool_call_trace`

This gives a direct link from a scored query to the tool calls that produced
it.
