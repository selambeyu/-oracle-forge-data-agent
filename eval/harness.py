"""
Evaluation harness for trace logging, pass@1 scoring, and score progression.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from Levenshtein import distance as levenshtein_distance
except ImportError:
    def levenshtein_distance(a: Any, b: Any) -> int:
        """Fallback Levenshtein implementation."""
        left = str(a)
        right = str(b)
        dp = list(range(len(right) + 1))
        for i, left_char in enumerate(left):
            next_dp = [i + 1]
            for j, right_char in enumerate(right):
                next_dp.append(
                    min(
                        dp[j] + (left_char != right_char),
                        dp[j + 1] + 1,
                        next_dp[-1] + 1,
                    )
                )
            dp = next_dp
        return dp[-1]


EVAL_DIR = Path(__file__).parent
SCORE_LOG_PATH = EVAL_DIR / "score_log.json"
TRACE_LOG_PATH = EVAL_DIR / "trace_log.jsonl"


@dataclass
class ToolCallEvent:
    """Immutable record of one tool call."""

    event_id: str
    timestamp: str
    session_id: str
    tool_name: str
    parameters: Dict[str, Any]
    result_status: str
    execution_time: float
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QueryEvent:
    """Immutable record of one query attempt."""

    event_id: str
    timestamp: str
    session_id: str
    query_text: str
    available_databases: List[str]
    tool_call_ids: List[str]
    answer: Any
    expected_answer: Any
    correct: bool
    confidence: float
    correction_applied: bool
    execution_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScoreLogEntry:
    """One entry in the score progression log."""

    timestamp: str
    run_id: str
    pass_at_1_score: float
    total_queries: int
    correct_first_attempt: int
    corrections_applied: int
    average_execution_time: float
    changes_since_last_run: str = ""


@dataclass
class RegressionResult:
    """Results of a regression test run."""

    test_set_name: str
    total_tests: int
    passed: int
    failed: int
    regressions: List[str]
    improvements: List[str]
    execution_time: float


class EvaluationHarness:
    """Trace tool usage and query outcomes for benchmark evaluation."""

    def __init__(
        self,
        eval_dir: Optional[Path] = None,
        score_log_path: Optional[Path] = None,
        trace_log_path: Optional[Path] = None,
    ):
        self.eval_dir = eval_dir or EVAL_DIR
        self.score_log_path = score_log_path or (self.eval_dir / "score_log.json")
        self.trace_log_path = trace_log_path or (self.eval_dir / "trace_log.jsonl")
        self._tool_call_events: List[ToolCallEvent] = []
        self._query_events: List[QueryEvent] = []
        self.eval_dir.mkdir(parents=True, exist_ok=True)

    def start_session(self) -> str:
        """Generate a short unique session identifier."""
        return str(uuid.uuid4())[:8]

    def trace_tool_call(
        self,
        session_id: str,
        tool_name: str,
        parameters: Dict[str, Any],
        result: Any,
        execution_time: float,
        error: Optional[str] = None,
    ) -> str:
        """Record one tool call and append it to the trace log."""
        result_status = "failure" if error else "success"
        if isinstance(result, dict) and result.get("retry"):
            result_status = "retry"

        event = ToolCallEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
            tool_name=tool_name,
            parameters=parameters,
            result_status=result_status,
            execution_time=execution_time,
            error=error,
        )
        self._tool_call_events.append(event)
        with self.trace_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict()) + "\n")
        return event.event_id

    def record_query_outcome(
        self,
        session_id: str,
        query: str,
        answer: Any,
        expected: Any,
        tool_call_ids: List[str],
        available_databases: Optional[List[str]] = None,
        confidence: float = 0.9,
        correction_applied: bool = False,
        execution_time: float = 0.0,
    ) -> QueryEvent:
        """Record a query outcome and append it to the trace log."""
        event = QueryEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
            query_text=query,
            available_databases=available_databases or [],
            tool_call_ids=tool_call_ids,
            answer=answer,
            expected_answer=expected,
            correct=self._score_answer(answer, expected),
            confidence=confidence,
            correction_applied=correction_applied,
            execution_time=execution_time,
        )
        self._query_events.append(event)
        with self.trace_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict()) + "\n")
        return event

    def calculate_pass_at_1(self, events: Optional[List[QueryEvent]] = None) -> float:
        """Calculate first-attempt accuracy as a percentage."""
        query_events = events or self._query_events
        if not query_events:
            return 0.0
        correct = sum(1 for event in query_events if event.correct and not event.correction_applied)
        return round((correct / len(query_events)) * 100, 2)

    def run_benchmark(
        self,
        agent: Any,
        queries: List[Dict[str, Any]],
        n_trials: int = 5,
        changes_description: str = "",
    ) -> Dict[str, Any]:
        """Run a benchmark suite and log the resulting score."""
        session = self.start_session()
        total_corrections = 0

        for query_spec in queries:
            for _ in range(n_trials):
                started_at = time.time()
                response = agent.process_query(
                    question=query_spec["question"],
                    available_databases=query_spec.get("available_databases", []),
                    schema_info=query_spec.get("schema_info", {}),
                )
                elapsed = round(time.time() - started_at, 3)

                event = self.record_query_outcome(
                    session_id=session,
                    query=query_spec["question"],
                    answer=response.get("answer"),
                    expected=query_spec.get("expected_answer"),
                    tool_call_ids=response.get("tool_call_ids", []),
                    available_databases=query_spec.get("available_databases", []),
                    confidence=response.get("confidence", 0.9),
                    correction_applied=response.get("correction_applied", False),
                    execution_time=elapsed,
                )
                if event.correction_applied:
                    total_corrections += 1

        pass_at_1 = self.calculate_pass_at_1()
        average_execution_time = round(
            sum(event.execution_time for event in self._query_events) / max(len(self._query_events), 1),
            3,
        )
        correct_first_attempt = sum(
            1 for event in self._query_events if event.correct and not event.correction_applied
        )
        score_entry = self.log_score(
            pass_at_1=pass_at_1,
            total_queries=len(queries),
            correct=correct_first_attempt,
            corrections=total_corrections,
            avg_time=average_execution_time,
            changes=changes_description,
        )

        return {
            "pass_at_1": pass_at_1,
            "session_id": session,
            "total_queries": len(queries),
            "score_entry": asdict(score_entry),
        }

    def log_score(
        self,
        pass_at_1: float,
        total_queries: int,
        correct: int,
        corrections: int,
        avg_time: float,
        changes: str = "",
    ) -> ScoreLogEntry:
        """Append a score entry to the score progression log."""
        entry = ScoreLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            run_id=str(uuid.uuid4())[:8],
            pass_at_1_score=pass_at_1,
            total_queries=total_queries,
            correct_first_attempt=correct,
            corrections_applied=corrections,
            average_execution_time=avg_time,
            changes_since_last_run=changes,
        )
        existing_log: List[Dict[str, Any]] = []
        if self.score_log_path.exists():
            try:
                existing_log = json.loads(self.score_log_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing_log = []
        existing_log.append(asdict(entry))
        self.score_log_path.write_text(json.dumps(existing_log, indent=2), encoding="utf-8")
        return entry

    def get_score_progression(self) -> List[Dict[str, Any]]:
        """Read the full score history from disk."""
        if not self.score_log_path.exists():
            return []
        return json.loads(self.score_log_path.read_text(encoding="utf-8"))

    def run_regression_suite(
        self,
        agent: Any,
        test_set: List[Dict[str, Any]],
        baseline_results: Optional[Dict[str, bool]] = None,
    ) -> RegressionResult:
        """Run held-out queries and compare against baseline results."""
        started_at = time.time()
        current_results: Dict[str, bool] = {}

        for query_spec in test_set:
            query_id = query_spec.get("id", query_spec["question"][:30])
            response = agent.process_query(
                question=query_spec["question"],
                available_databases=query_spec.get("available_databases", []),
                schema_info=query_spec.get("schema_info", {}),
            )
            current_results[query_id] = self._score_answer(
                response.get("answer"),
                query_spec.get("expected_answer"),
            )

        regressions: List[str] = []
        improvements: List[str] = []
        if baseline_results:
            for query_id, current_correct in current_results.items():
                baseline_correct = baseline_results.get(query_id, False)
                if baseline_correct and not current_correct:
                    regressions.append(query_id)
                elif not baseline_correct and current_correct:
                    improvements.append(query_id)

        passed = sum(1 for is_correct in current_results.values() if is_correct)
        return RegressionResult(
            test_set_name="held_out",
            total_tests=len(test_set),
            passed=passed,
            failed=len(test_set) - passed,
            regressions=regressions,
            improvements=improvements,
            execution_time=round(time.time() - started_at, 2),
        )

    def parse_trace_log(self) -> List[Dict[str, Any]]:
        """Parse the JSONL trace log into a structured list."""
        if not self.trace_log_path.exists():
            return []

        events: List[Dict[str, Any]] = []
        with self.trace_log_path.open(encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    events.append(json.loads(stripped))
                except json.JSONDecodeError:
                    events.append({"parse_error": stripped})
        return events

    def pretty_print_trace(self, events: Optional[List[Dict[str, Any]]] = None) -> str:
        """Render a human-readable trace report."""
        trace_events = events or self.parse_trace_log()
        lines = ["=" * 60, "QUERY TRACE LOG", "=" * 60]
        for event in trace_events:
            if "tool_name" in event:
                lines.append(
                    f"\n[TOOL] {event.get('tool_name')} | "
                    f"{event.get('result_status', '?').upper()} | "
                    f"{event.get('execution_time', 0):.3f}s"
                )
                lines.append(f"  params: {json.dumps(event.get('parameters', {}), indent=4)}")
                if event.get("error"):
                    lines.append(f"  ERROR: {event['error']}")
            elif "query_text" in event:
                marker = "PASS" if event.get("correct") else "FAIL"
                lines.append(f"\n[QUERY] {marker} {event.get('query_text', '')[:80]}")
                lines.append(f"  answer: {event.get('answer')}")
                lines.append(f"  expected: {event.get('expected_answer')}")
                lines.append(f"  confidence: {event.get('confidence', 0):.2f}")
        return "\n".join(lines)

    def export_dab_results(self, output_path: str, team_name: str = "Team PaLM") -> None:
        """Export the in-memory query results in DAB submission format."""
        results_by_query: Dict[str, List[Dict[str, Any]]] = {}
        for event in self._query_events:
            query_id = event.query_text[:50]
            bucket = results_by_query.setdefault(query_id, [])
            bucket.append(
                {
                    "trial_number": len(bucket) + 1,
                    "answer": event.answer,
                    "correct": event.correct,
                    "execution_time": event.execution_time,
                    "tool_calls": len(event.tool_call_ids),
                    "correction_applied": event.correction_applied,
                }
            )

        submission = {
            "team_name": team_name,
            "submission_date": datetime.now(timezone.utc).isoformat(),
            "agent_version": "1.0.0",
            "results": [
                {"query_id": query_id, "trials": trials}
                for query_id, trials in results_by_query.items()
            ],
            "pass_at_1": self.calculate_pass_at_1(),
            "total_queries": len(results_by_query),
            "trials_per_query": 5,
        }
        Path(output_path).write_text(json.dumps(submission, indent=2), encoding="utf-8")

    def _score_answer(self, got: Any, expected: Any) -> bool:
        """Score answers using exact or near-exact string similarity."""
        if got is None:
            return False
        got_str = str(got).strip().lower()
        expected_str = str(expected).strip().lower()
        if got_str == expected_str:
            return True
        return levenshtein_distance(got_str, expected_str) <= 2
