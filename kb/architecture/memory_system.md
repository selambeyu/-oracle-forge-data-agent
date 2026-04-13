# Claude Code Memory System — 3-Layer Architecture

Source: Claude Code v2.1.88 source leak (March 2026), src/commands/memory/

## How Claude Code actually manages memory

Claude Code uses three distinct memory layers. Each serves a
different purpose. They are not interchangeable.

## Layer 1 — MEMORY.md (the index file)

MEMORY.md is loaded at every session start without exception.
It is an index only — it contains pointers, not content.
Actual knowledge lives in separate topic files.

From source: MEMORY.md files are loaded lazily per directory
(see src/utils/memory/). The agent reads MEMORY.md first, then
decides which topic files to load based on the current task.

Rule for this agent: MEMORY.md stays under 200 words.
If it exceeds 200 words, remove the oldest changelog entries.
Never remove topic file pointers.

In this project, MEMORY.md also acts as working memory entry point:
the agent uses it to orient before receiving a question, loading
corrections log BEFORE planning to avoid repeating past mistakes.

## Layer 2 — Topic files (loaded on demand)

Topic files contain actual knowledge — schemas, domain rules,
correction patterns. They are NOT pre-loaded.

From source: SkillTool + memdir/ handle on-demand injection.
Knowledge is injected via tool_result, not system prompt.
This keeps the context window efficient for long sessions.

Rule for this agent: each topic file maximum 400 words.
Load a topic file on-demand only when the current question requires it.
Never pre-load all files — context window fills up.

Trigger examples:
  Question involves customer join  → load kb/architecture/tool_scoping.md
  Question uses "revenue"          → load kb/domain/business_terms.md
  Question previously failed       → load kb/corrections/log.md
  Question spans multiple DBs      → load kb/domain/join_keys.md

Topic files store ONLY proven patterns. Each entry must include:
  - Natural language question
  - Final working query
  - Databases used
  - Join logic applied
  - Transformations applied

Example query pattern entry:
  NL: "Find customers with declining purchases and high complaints"
  1. Aggregate purchases per quarter (PostgreSQL)
  2. Extract complaint sentiment from notes (MongoDB aggregation)
  3. Normalize customer_id: remove "CUST_", cast to int
  4. Join result sets in memory

## Layer 3 — Session transcripts (searchable, not pre-loaded)

From source: ~/.claude/projects/<hash>/sessions/<session-id>.jsonl
Sessions are stored as append-only JSONL logs.
Resume flow: getLastSessionLog() → parse JSONL → rebuild messages[]
The agent can search transcripts when a new question resembles
a past one.

Rule for this agent: search transcripts only when relevant.
Never load all transcript history into context.

## autoDream consolidation (DreamTask pattern)

From source: tasks/DreamTask/ — background thinking task.
At session end, the agent runs consolidation:
1. Review what was learned
2. Write new corrections to kb/corrections/log.md using this format:
     [Query]      Natural language question that failed
     [Failure]    What went wrong (symptom)
     [Root Cause] Why it went wrong (diagnosis)
     [Fix]        Exact change applied
     [Outcome]    Result after fix — MUST be verified, not assumed
3. Update relevant topic files with successful patterns
4. Update MEMORY.md index if new files were added

Consolidation discipline:
  Keep: recurring failures, high-impact join fixes, business logic corrections
  Remove: one-off issues, environment errors, already-resolved schema issues
A corrections log that only grows becomes noise. Discipline is removal.

This is the self-learning loop. The agent improves across
sessions without retraining. Do not skip this step.

## Why Claude architecture alone is not enough

Claude memory manages HOW knowledge is stored and retrieved.
It does not provide:
  - schema grounding (which table, which DB)
  - domain knowledge (what "revenue" means in this dataset)
  - cross-database entity mapping

OpenAI's 6-layer context fills these gaps (see context_layer.md).
This agent uses both systems together.

## Injection test questions

Q1: "What is MEMORY.md for, what is its word limit, and what
triggers a topic file to be loaded from memory?"
Expected: index only, 200 words, on-demand when question
requires that specific topic.

Q2: "What fields must every corrections log entry contain,
and which field is most often missing?"
Expected: Query, Failure, Root Cause, Fix, Outcome.
Outcome is most often missing — it must be verified, not assumed.

Q3: "What is the consolidation rule for the corrections log?"
Expected: keep recurring failures and high-impact fixes.
Remove one-off issues. A log that only grows becomes noise.

Q4: "Why is Claude memory architecture alone not enough for
this data agent project?"
Expected: lacks schema grounding and domain knowledge —
does not know which table to query or what business terms mean.
OpenAI 6-layer context fills those gaps (see context_layer.md).
