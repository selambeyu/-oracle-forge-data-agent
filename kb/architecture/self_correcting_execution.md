# Self-Correcting Execution — Closed-Loop Pattern

Sources:
- Claude Code v2.1.88: src/query.ts (main agent loop), StreamingToolExecutor
- OpenAI data agent: closed-loop self-correction pattern

## The core agent loop (from Claude Code source)

From query.ts — the main loop structure:

  while stop_reason != "tool_use":
    call Claude API (streaming)
    if tool_use block returned:
      run StreamingToolExecutor → parallel where safe, serial otherwise
      canUseTool() permission check
      if DENY → append error, continue loop
      if ALLOW → tool.call() → append tool_result → loop back
    else:
      return final text

This is the loop this agent runs. Production harness adds:
  permission checks, streaming, compaction, sub-agents, persistence.

## Self-correction pattern (from OpenAI data agent)

OpenAI describes this as "closed-loop self-correction":
  - Agent evaluates its own progress after each step
  - If query fails or returns suspicious results:
    investigate the error → adjust approach → retry
  - Agent does not surface errors to user
  - User receives either correct answer or honest "could not resolve"

"Overconfidence is the biggest behavioral flaw. The model often
says 'This is the right table' and runs ahead. That is wrong.
Spend more time in the discovery phase first." — Emma Tang, OpenAI

## Execution loop for this agent

Step 1 — PLAN (discovery phase — do not skip)
  Load corrections log FIRST. Know past failures before planning.
  Validate which table to use. Compare candidates in schemas.md.
  Select scoped tools. Identify all databases needed.
  Do NOT execute until table choice is validated.

Step 2 — EXECUTE
  Call scoped tools (one per database type).
  Send results to sandbox for merging/validation.
  For multi-DB questions: call each tool separately, never combined.

Step 3 — CHECK SANDBOX RESPONSE
  Read validation_status field.
  "ok" → proceed to delivery.
  "failed" → diagnose from error_if_any field.

Step 4 — DIAGNOSE AND RETRY (maximum 3 attempts)
  "ID format mismatch" → strip prefix, convert type, retry
  "empty result"       → verify table name in schemas, retry
  "syntax error"       → check query language for this tool, retry
  After 3 failures: return honest error with full trace.
  Never hallucinate an answer. Never guess.

Step 5 — DELIVER
  Package: answer + query_trace + confidence level
  Confidence: high (direct result), medium (inferred), low (partial)

Step 6 — LOG (autoDream)
  Write failures to kb/corrections/log.md using this format:
    [Query]      Natural language question that failed
    [Failure]    What went wrong (symptom)
    [Root Cause] Why it went wrong (diagnosis)
    [Fix]        Exact change applied
    [Outcome]    Result after fix — MUST be verified, not assumed
  Write successful patterns to relevant topic file.
  Update MEMORY.md if new files created.
  Remove one-off entries from corrections log — keep recurring ones.

## Why corrections log loads BEFORE the question (Step 1)

The agent must know past failures before it plans its approach.
If it loads the corrections log after receiving the question,
it plans without knowing what went wrong before — and repeats
the same mistakes across DAB trials.

## Known DAB failure → fix table

  churn window wrong (30 days used instead of 90)
    → always check business_terms.md before time-based queries

  customer_id mismatch across databases
    → normalize: remove "CUST_", cast to int before any join

  SQL sent to query_mongodb returns empty silently
    → always use aggregation pipeline for MongoDB (see tool_scoping.md)

## Injection test question
"The sandbox returns validation_status: failed, error: ID format
mismatch. What are the exact next steps and what happens after
3 retries all fail?"

Expected: strip prefix, convert type, retry. After 3 failures:
return honest error with full query trace. Never guess.
