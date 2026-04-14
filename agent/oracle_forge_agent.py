"""
OracleForgeAgent — Top-level orchestrator.

Receives DAB wire-format input, coordinates all components,
and returns DAB wire-format output.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from agent.context_manager import ContextManager
from agent.execution_engine import ExecutionEngine
from agent.llm_client import LLMClient
from agent.models.models import QueryPlan, QueryResult
from agent.query_router import QueryRouter
from agent.self_correction import SelfCorrectionLoop
from eval.harness import EvaluationHarness

load_dotenv()

# Default database configurations (overridden by env vars)
_DEFAULT_DB_CONFIGS = {
    "postgres": {
        "type": "postgres",
        "connection_string": os.getenv("POSTGRES_URL", ""),
    },
    "mongodb": {
        "type": "mongodb",
        "connection_string": os.getenv("MONGODB_URL", ""),
    },
    "sqlite": {
        "type": "sqlite",
        "path": os.getenv("SQLITE_PATH", ""),
    },
    "duckdb": {
        "type": "duckdb",
        "path": os.getenv("DUCKDB_PATH", ":memory:"),
    },
}


class OracleForgeAgent:
    """
    Main entry point for the Oracle Forge Data Agent.

    Usage:
        agent = OracleForgeAgent()
        result = agent.answer({
            "question": "What is the average rating for businesses in Las Vegas?",
            "available_databases": ["postgres", "mongodb"],
            "schema_info": {}
        })

        # Or via the typed interface:
        result = agent.process_query(
            question="What is the average rating for businesses in Las Vegas?",
            available_databases=["postgres", "mongodb"],
            schema_info={},
        )
    """

    def __init__(
        self,
        db_configs: Optional[Dict[str, dict]] = None,
        session_id: Optional[str] = None,
    ):
        self._session_id = session_id or str(uuid.uuid4())
        self._db_configs = db_configs or _DEFAULT_DB_CONFIGS
        self._client = LLMClient()

        # Session state: per-session interaction history (multi-turn support)
        # Maps session_id -> list of {question, answer, confidence, ...} dicts
        self._sessions: Dict[str, List[Dict[str, Any]]] = {
            self._session_id: []
        }

        # Initialise components
        self._ctx_manager = ContextManager(self._db_configs)
        self._router = QueryRouter(client=self._client)
        self._engine = ExecutionEngine(db_configs=self._db_configs)
        self._correction_loop = SelfCorrectionLoop(
            execution_engine=self._engine,
            context_manager=self._ctx_manager,
            client=self._client,
        )

        # Load all context layers once at session start
        self._ctx_manager.load_all_layers()

        # Evaluation harness — trace tool calls and record query outcomes
        self._harness = EvaluationHarness()
        self._harness_session_id = self._harness.start_session()

    # ── Typed DAB interface (task 10.1) ───────────────────────────────────────

    def process_query(
        self,
        question: str,
        available_databases: List[str],
        schema_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Process a natural language query against available databases.

        Accepts individual DAB format arguments and returns the DAB output
        format dict: {"answer": Any, "query_trace": List[dict], "confidence": float}.

        Args:
            question: Natural language query text.
            available_databases: List of database identifiers accessible for this query.
            schema_info: Schema metadata for available databases.

        Returns:
            DAB result dict containing answer, query_trace, and confidence score.
        """
        return self.answer(
            {
                "question": question,
                "available_databases": available_databases,
                "schema_info": schema_info,
            }
        )

    # ── Session management (task 10.3) ────────────────────────────────────────

    def load_session_context(self, session_id: str) -> None:
        """
        Load context layers for a session, switching the active session.

        Creates a new interaction history slot for the session_id if it has
        not been seen before, then reloads all three context layers so the
        agent picks up any corrections written during a previous session.

        Args:
            session_id: Identifier for the session to load.
        """
        self._session_id = session_id
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        # Reload all layers so Layer-3 corrections from prior sessions are visible
        self._ctx_manager.load_all_layers()

    def update_interaction_memory(
        self,
        query: str,
        correction: str,
        pattern: str,
    ) -> None:
        """
        Update Layer 3 with a user-supplied correction or successful pattern.

        Appends an entry to the corrections log so the agent can apply the
        fix proactively the next time it encounters a similar query.

        Args:
            query: The original query that was problematic or observed.
            correction: The corrected query or response.
            pattern: Description of the failure cause or pattern observed.
        """
        self._ctx_manager.log_correction(
            query=query,
            failure_cause=pattern,
            correction=correction,
        )

    # ── DAB wire-format interface ──────────────────────────────────────────────

    def answer(self, dab_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a DAB benchmark question and return the DAB output format.

        Args:
            dab_input: {"question": str, "available_databases": List[str], "schema_info": dict}

        Returns:
            {"answer": Any, "query_trace": List[dict], "confidence": float}
        """
        question = dab_input["question"]
        available_databases = dab_input.get("available_databases", list(self._db_configs.keys()))

        # Layer 3: check for proactive corrections before first attempt
        context = self._ctx_manager.get_bundle()
        prior_corrections = self._ctx_manager.get_similar_corrections(question)
        correction_applied_proactively = len(prior_corrections) > 0

        # Route: produce a QueryPlan
        plan = self._router.route(question, context, available_databases)

        # Execute with self-correction
        execution_result = self._correction_loop.execute_with_correction(plan, question)

        # Synthesise final answer with confidence score
        answer, confidence = self._synthesise_answer(
            question=question,
            execution_result=execution_result,
            prior_corrections=prior_corrections,
            plan=plan,
        )

        # Build trace for DAB output
        query_trace = self._build_trace(plan, execution_result)

        # Adjust confidence for proactive correction outcome
        if correction_applied_proactively and not execution_result["success"]:
            confidence = max(0.2, confidence - 0.1)
        elif correction_applied_proactively:
            confidence = min(1.0, confidence + 0.05)

        final_correction_applied = (
            correction_applied_proactively or execution_result["correction_applied"]
        )

        # Trace each sub-query as a tool call in the evaluation harness
        tool_call_ids: List[str] = []
        results_by_db = {r.database: r for r in execution_result["results"]}
        for sq in plan.sub_queries:
            result = results_by_db.get(sq.database)
            tool_name = "duckdb_query" if sq.query_type == "duckdb" else "run_query"
            tool_call_ids.append(
                self._harness.trace_tool_call(
                    session_id=self._harness_session_id,
                    tool_name=tool_name,
                    parameters={"query": sq.query, "database": sq.database},
                    result=result.data if result and result.success else None,
                    execution_time=0.0,
                    error=result.error if result else None,
                )
            )

        # Record the query outcome (expected_answer unknown at query time; filled in by run_benchmark)
        self._harness.record_query_outcome(
            session_id=self._harness_session_id,
            query=question,
            answer=answer,
            expected=None,
            tool_call_ids=tool_call_ids,
            available_databases=available_databases,
            confidence=confidence,
            correction_applied=final_correction_applied,
        )

        # Update session interaction history (multi-turn support)
        self._sessions.setdefault(self._session_id, []).append(
            {
                "question": question,
                "answer": answer,
                "confidence": confidence,
                "correction_applied": final_correction_applied,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

        return {
            "answer": answer,
            "query_trace": query_trace,
            "confidence": confidence,
            "tool_call_ids": tool_call_ids,
            "correction_applied": final_correction_applied,
        }

    # ── Answer synthesis (task 10.2) ──────────────────────────────────────────

    def _synthesise_answer(
        self,
        question: str,
        execution_result: Dict[str, Any],
        prior_corrections: list,
        plan: Optional[QueryPlan] = None,
    ) -> tuple[Any, float]:
        """Synthesise a final answer from execution results and compute confidence."""
        results = execution_result["results"]
        success = execution_result["success"]

        if not results:
            return "Unable to retrieve data.", 0.1

        result_summaries = []
        for r in results:
            if r.success:
                result_summaries.append(f"[{r.database}] rows={r.rows_affected}: {r.data}")
            else:
                result_summaries.append(f"[{r.database}] ERROR: {r.error}")

        correction_note = ""
        if prior_corrections:
            correction_note = (
                f"Note: {len(prior_corrections)} prior correction(s) were applied.\n"
            )

        prompt = (
            "Based on the following query results, answer this question concisely.\n\n"
            f"Question: {question}\n\n"
            f"{correction_note}"
            "Results:\n" + "\n".join(result_summaries) + "\n\n"
            "Return only the final answer value (number, string, list, or dict). "
            "No explanation."
        )

        response = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_answer = response.content[0].text.strip()

        # Try to parse as JSON for structured answers
        try:
            parsed = json.loads(raw_answer)
            answer = parsed
        except json.JSONDecodeError:
            answer = raw_answer

        confidence = self._calculate_confidence(
            success=success,
            correction_applied=execution_result["correction_applied"],
            retries_used=execution_result.get("retries_used", 0),
            plan=plan,
            results=results,
        )

        return answer, confidence

    def _calculate_confidence(
        self,
        success: bool,
        correction_applied: bool,
        retries_used: int,
        plan: Optional[QueryPlan] = None,
        results: Optional[List[QueryResult]] = None,
    ) -> float:
        """
        Calculate confidence score based on validation outcomes and query complexity.

        Factors:
        - Base confidence from success/failure
        - Penalty for retries needed (instability indicator)
        - Penalty for correction being applied (required rework)
        - Complexity penalty for multi-database queries
        - Penalty if any individual sub-result failed (partial failure)

        Returns:
            Float in [0.1, 1.0].
        """
        if not success:
            return 0.2  # Non-zero: partial results may still be useful

        confidence = 0.9

        # Each retry penalises confidence (instability)
        confidence -= retries_used * 0.1

        # Correction means rework was needed
        if correction_applied:
            confidence -= 0.1

        # Cross-database queries are more complex → lower baseline certainty
        if plan is not None:
            n_databases = len({sq.database for sq in plan.sub_queries})
            if n_databases > 1:
                confidence -= (n_databases - 1) * 0.05

        # Partial failures within an otherwise successful run
        if results:
            failed_count = sum(1 for r in results if not r.success)
            if failed_count > 0:
                confidence -= failed_count * 0.05

        return max(0.1, min(1.0, confidence))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_trace(self, plan: QueryPlan, execution_result: Dict[str, Any]) -> List[Dict]:
        trace = []
        results_by_db = {r.database: r for r in execution_result["results"]}
        for i, sq in enumerate(plan.sub_queries):
            result = results_by_db.get(sq.database)
            trace.append(
                {
                    "step": i + 1,
                    "db": sq.database,
                    "query": sq.query,
                    "result": str(result.data)[:500] if result and result.success else None,
                    "error": result.error if result else None,
                    "correction_applied": execution_result["correction_applied"],
                }
            )
        return trace

    def get_harness(self) -> EvaluationHarness:
        """Return the evaluation harness for external callers (e.g. run_benchmark)."""
        return self._harness


# ── CLI entry point ────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Oracle Forge Data Agent")
    parser.add_argument("--question", required=True, help="Natural language question")
    parser.add_argument(
        "--databases",
        nargs="+",
        default=["postgres"],
        help="Available database IDs",
    )
    parser.add_argument("--output", default=None, help="Output JSON file path")
    args = parser.parse_args()

    agent = OracleForgeAgent()
    result = agent.answer(
        {
            "question": args.question,
            "available_databases": args.databases,
            "schema_info": {},
        }
    )

    output_json = json.dumps(result, indent=2, default=str)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
        print(f"Result written to {args.output}")
    else:
        print(output_json)


if __name__ == "__main__":
    main()
