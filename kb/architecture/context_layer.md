# OpenAI Data Agent — Six-Layer Context Architecture

Source: OpenAI engineering blog "Inside our in-house data agent"
January 29, 2026. Agent runs over 70,000 datasets, 600 petabytes.
Performance gain: 22 minutes → 90 seconds on the same query.

## Why context beats raw intelligence

Without context, GPT-5.2 vastly misestimates user counts and
misinterprets internal terminology. The same model with 6 context
layers cuts analysis time from 22 minutes to 90 seconds on the
same query. The bottleneck is never query generation — it is context.

In DAB, failures occur because the agent:
- selects the wrong table
- misunderstands a business term
- cannot reconcile entities across databases
- repeats the same mistake across trials

## The six layers (Layers 1, 2, 4, 5 implemented — 3 and 6 optional)

Layer 1 — Schema metadata and query history  [MANDATORY for this project → kb/domain/schemas.md]
  Column names, data types, table lineage (upstream/downstream).
  Historical queries showing which tables are joined together.
  This tells the agent WHERE data lives and HOW it is used.

  Cross-database entity mapping MUST be included:
    PostgreSQL: customer_id = "CUST_00123"
    MongoDB:    customerId  = "123"
    SQLite:     cust_id     = "000123"
  Document casing differences, prefixes, and zero-padding.

Layer 2 — Curated expert descriptions  [MANDATORY for this project → kb/domain/schemas.md]
  Human-written descriptions of key tables and their purpose.
  Captures semantics, business meaning, known limitations.
  What the column names do not tell you.

Layer 3 — Codex Enrichment (table enrichment)  [OPTIONAL — skip for DAB]
  Daily async process: Codex inspects pipeline code for each table.
  Derives: upstream/downstream deps, ownership, granularity,
  join keys, similar tables, filter assumptions.
  Reveals what is INSIDE a table, not just its schema.
  Too heavy for DAB scale. Implement only if join_keys.md is insufficient.

Layer 4 — Institutional knowledge  [MANDATORY for this project → kb/domain/business_terms.md]
  In OpenAI's system: Slack, Docs, Notion for metric definitions.
  In this project: kb/domain/business_terms.md serves this role.
  Contains dataset-specific definitions the schema does not capture.

  Definitions MUST include:
    revenue          = total_price - refunds
    repeat_purchase  = count(orders per customer) > 1
    churn            = no transactions in last 90 days (NOT 30)
  Also define authoritative tables vs deprecated tables.

Layer 5 — Self-learning memory  [MANDATORY for this project → kb/corrections/log.md (KB v3)]
  Corrections and nuances from previous conversations stored.
  Applied to future requests automatically.
  Stateless agents repeat the same mistakes. This prevents it.

  Corrections log entry format:
    [Query]      Natural language question that failed
    [Failure]    What went wrong (symptom)
    [Root Cause] Why it went wrong (diagnosis)
    [Fix]        Exact change applied
    [Outcome]    Result after fix — MUST be verified, not assumed

  Consolidation rule: keep recurring failures and high-impact fixes.
  Remove one-off issues. A log that only grows becomes noise.

Layer 6 — Live runtime queries  [OPTIONAL — only if schema is stale]
  When no prior info exists or data is stale: query live.
  MCP connections to data warehouse for real-time schema inspection.
  Adds infrastructure overhead — do not implement unless schemas.md lags.

## How this maps to this agent's KB

Layer 1+2 = kb/architecture/ + kb/domain/schemas.md    → MANDATORY for this project (KB v1+v2)
Layer 3   = kb/domain/join_keys.md                     → OPTIONAL (Codex Enrichment — heavy, skip unless needed)
Layer 4   = kb/domain/business_terms.md                → MANDATORY for this project (institutional knowledge)
Layer 5   = kb/corrections/log.md                      → MANDATORY for this project (self-learning memory, KB v3)
Layer 6   = MCP tools live queries (tools.yaml)        → OPTIONAL (live runtime — only if schema is stale)

We do NOT implement full 6 layers. Implemented: 1, 2, 4, 5.
Layer 3 and Layer 6 are optional due to complexity and infrastructure cost.
This covers the failures that matter most in DAB: wrong table, wrong metric,
wrong join key, repeated mistakes.

## Key finding from OpenAI: discovery phase

"The more time the agent spends in the discovery phase —
comparing which table to use — the better the results."
Prompt the agent to validate table choice BEFORE running analysis.
Do not run ahead. Spend time in discovery. Then execute.

Overconfidence is the biggest behavioral flaw. The model often
says "This is the right table" and runs ahead. That is wrong.
— Emma Tang, OpenAI

## What Claude architecture adds (gap-fill)

OpenAI layers define WHAT context to load. Claude architecture
defines HOW to manage it across sessions:
  Working memory  → orchestration space for current query
  Tool scoping    → one tool per database type (see tool_scoping.md)
  autoDream       → end-of-session write to corrections log

Neither architecture alone is sufficient. This agent uses both.

## Injection test question
"What is Codex Enrichment and which of the six layers is it?"

Expected: Layer 3, daily async process where Codex inspects
pipeline code to derive what a table actually contains —
upstream/downstream deps, join keys, filter assumptions.
