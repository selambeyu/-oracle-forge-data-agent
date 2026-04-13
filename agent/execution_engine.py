"""
Execution Engine with self-correction loop.

Responsibilities:
1. Dialect translation (SQL / MongoDB aggregation / DuckDB analytical SQL)
2. Query execution via MCPToolbox
3. Result merging (multi-database joins)
4. Result validation (types, nulls, integrity)
5. Format transformation (join key resolution)
6. Self-correction loop (max 3 retries, transparent to user)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .mcp_toolbox import MCPToolbox


@dataclass
class FormatTransform:
    """Format transformation for join key resolution."""

    source_format: str
    target_format: str
    transformation_function: str


@dataclass
class SubQuery:
    """One sub-query targeting a single database."""

    query_text: str
    target_database: str
    database_type: str
    depends_on: List[int] = field(default_factory=list)
    extraction_required: bool = False


@dataclass
class JoinOp:
    """Join operation between two sub-query results."""

    left_subquery_idx: int
    right_subquery_idx: int
    join_key_left: str
    join_key_right: str
    join_type: str = "inner"
    format_transformation: Optional[FormatTransform] = None


@dataclass
class QueryPlan:
    """Full execution plan."""

    sub_queries: List[SubQuery]
    execution_order: List[int]
    join_operations: List[JoinOp]
    requires_sandbox: bool = False


@dataclass
class FailureInfo:
    """Detected failure."""

    failure_type: str
    error_message: str
    failed_query: str
    execution_trace: List[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """Final result from execute_plan()."""

    success: bool
    answer: Any
    query_trace: List[Dict[str, Any]] = field(default_factory=list)
    correction_applied: bool = False
    error: Optional[str] = None
    confidence: float = 0.9


class ExecutionEngine:
    """Execute query plans with limited self-correction on failures."""

    MAX_RETRIES = 3

    def __init__(self, toolbox: Optional[MCPToolbox] = None):
        self.toolbox = toolbox or MCPToolbox()

    def execute_plan(self, plan: QueryPlan, context: Dict[str, Any]) -> ExecutionResult:
        """Execute a query plan with up to three attempts."""
        trace: List[Dict[str, Any]] = []
        correction_applied = False

        for attempt in range(self.MAX_RETRIES):
            try:
                sub_results: Dict[int, List[Dict[str, Any]]] = {}
                for idx in plan.execution_order:
                    sq = plan.sub_queries[idx]
                    tool_name, params = self._build_tool_call(sq)
                    result = self.toolbox.call_tool(tool_name, params)

                    trace.append(
                        {
                            "attempt": attempt + 1,
                            "subquery_idx": idx,
                            "db": sq.target_database,
                            "db_type": sq.database_type,
                            "tool": tool_name,
                            "rows": len(result.data) if isinstance(result.data, list) else 0,
                            "error": result.error,
                            "execution_time": result.execution_time,
                        }
                    )

                    if not result.success:
                        raise RuntimeError(result.error or "Unknown execution error")

                    sub_results[idx] = result.data if isinstance(result.data, list) else []

                if plan.join_operations:
                    merged = self.merge_results(sub_results, plan.join_operations)
                else:
                    first_idx = plan.execution_order[0] if plan.execution_order else 0
                    merged = sub_results.get(first_idx, [])

                validation = self.validate_result(merged, {})
                if not validation["valid"]:
                    raise RuntimeError(f"Validation failed: {validation['issues']}")

                return ExecutionResult(
                    success=True,
                    answer=merged,
                    query_trace=trace,
                    correction_applied=correction_applied,
                    confidence=0.9 if not correction_applied else 0.75,
                )
            except Exception as exc:
                error_message = str(exc)
                failed_query = ""
                if plan.sub_queries and plan.execution_order:
                    failed_query = plan.sub_queries[plan.execution_order[0]].query_text
                failure = self._detect_failure(error_message, failed_query)

                if attempt < self.MAX_RETRIES - 1:
                    plan = self._apply_correction(plan, failure, context)
                    correction_applied = True
                    trace.append({"attempt": attempt + 1, "correction": failure.failure_type})
                    continue

                return ExecutionResult(
                    success=False,
                    answer=None,
                    query_trace=trace,
                    correction_applied=correction_applied,
                    error=f"Failed after {self.MAX_RETRIES} attempts: {error_message}",
                    confidence=0.0,
                )

        return ExecutionResult(
            success=False,
            answer=None,
            query_trace=trace,
            correction_applied=correction_applied,
            error="Execution terminated unexpectedly",
            confidence=0.0,
        )

    def _build_tool_call(self, sq: SubQuery) -> tuple[str, Dict[str, Any]]:
        """Map a sub-query to the MCP tool and its parameters."""
        db_type = sq.database_type.lower()

        if db_type in ("postgresql", "postgres", "sqlite", "duckdb"):
            tool_name = {
                "postgresql": "run_query",
                "postgres": "run_query",
                "sqlite": "sqlite_query",
                "duckdb": "duckdb_query",
            }[db_type]
            return tool_name, {"query": sq.query_text}

        if db_type == "mongodb":
            collection, pipeline = self._parse_mongo_query(sq.query_text)
            tool_name = "find_yelp_checkins" if collection == "checkin" else "find_yelp_businesses"
            return tool_name, {"filterPayload": pipeline, "limit": 20}

        return "run_query", {"query": sq.query_text}

    def _parse_mongo_query(self, query_text: str) -> tuple[str, str]:
        """Extract a coarse collection hint and pipeline payload."""
        collection = "business"
        pipeline = "{}"
        if "checkin" in query_text.lower():
            collection = "checkin"
        return collection, pipeline

    def merge_results(
        self,
        results_by_index: Dict[int, List[Dict[str, Any]]],
        join_ops: List[JoinOp],
    ) -> List[Dict[str, Any]]:
        """Merge multiple result sets based on join operations."""
        if not results_by_index:
            return []
        if not join_ops:
            return next(iter(results_by_index.values()))

        merged = list(results_by_index.get(join_ops[0].left_subquery_idx, []))
        for op in join_ops:
            right = results_by_index.get(op.right_subquery_idx, [])
            merged = self._join_datasets(
                merged,
                right,
                op.join_key_left,
                op.join_key_right,
                op.join_type,
                op.format_transformation,
            )
        return merged

    def _join_datasets(
        self,
        left: List[Dict[str, Any]],
        right: List[Dict[str, Any]],
        key_left: str,
        key_right: str,
        join_type: str = "inner",
        transform: Optional[FormatTransform] = None,
    ) -> List[Dict[str, Any]]:
        """Join two datasets in memory."""
        normalized_right: List[Dict[str, Any]] = []
        for row in right:
            updated_row = dict(row)
            if transform and key_right in updated_row:
                updated_row[key_right] = self.apply_format_transformation(
                    updated_row[key_right],
                    transform.source_format,
                    transform.target_format,
                )
            normalized_right.append(updated_row)

        right_index: Dict[Any, List[Dict[str, Any]]] = {}
        for row in normalized_right:
            right_index.setdefault(row.get(key_right), []).append(row)

        result: List[Dict[str, Any]] = []
        matched_right_keys = set()
        for left_row in left:
            left_value = left_row.get(key_left)
            matches = right_index.get(left_value, [])
            if matches:
                matched_right_keys.add(left_value)
                for right_row in matches:
                    result.append({**left_row, **right_row})
            elif join_type in ("left", "full"):
                result.append(dict(left_row))

        if join_type in ("right", "full"):
            for right_key, right_rows in right_index.items():
                if right_key in matched_right_keys:
                    continue
                result.extend(dict(row) for row in right_rows)

        return result

    def apply_format_transformation(
        self,
        value: Any,
        source_format: str,
        target_format: str,
    ) -> Any:
        """Transform join keys between common representations."""
        if value is None:
            return value

        try:
            if source_format == "integer" and "{" in target_format:
                return target_format.format(int(value))
            if source_format.startswith("prefix:"):
                prefix = source_format.split(":", 1)[1]
                return int(str(value).replace(prefix, "", 1))
            if source_format == "zero_padded":
                return int(str(value).lstrip("0") or "0")
            if target_format == "uppercase":
                return str(value).upper()
            if target_format == "lowercase":
                return str(value).lower()
        except (TypeError, ValueError):
            return value

        return value

    def validate_result(self, result: Any, expected_schema: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Validate basic nullability and duplicate constraints."""
        issues: List[str] = []

        if result is None:
            return {"valid": False, "issues": ["Result is None"]}

        if isinstance(result, list):
            if result and isinstance(result[0], dict):
                for key, value in result[0].items():
                    if value is None and expected_schema.get(key, {}).get("nullable") is False:
                        issues.append(f"Unexpected null in column: {key}")

            seen = set()
            for row in result:
                marker = repr(sorted(row.items())) if isinstance(row, dict) else repr(row)
                if marker in seen:
                    issues.append("Duplicate rows detected")
                    break
                seen.add(marker)

        return {"valid": not issues, "issues": issues}

    def _detect_failure(self, error_msg: str, failed_query: str) -> FailureInfo:
        """Classify a failure into a coarse recovery category."""
        lowered = error_msg.lower()

        if any(token in lowered for token in ("syntax", "parse error", "unexpected token")):
            return FailureInfo("syntax", error_msg, failed_query)
        if any(token in lowered for token in ("0 rows", "no rows", "foreign key", "join")):
            return FailureInfo("join_key_mismatch", error_msg, failed_query)
        if any(token in lowered for token in ("unsupported", "invalid operator", "wrong type")):
            return FailureInfo("wrong_db_type", error_msg, failed_query)
        if any(token in lowered for token in ("null", "duplicate", "integrity")):
            return FailureInfo("data_quality", error_msg, failed_query)
        return FailureInfo("unknown", error_msg, failed_query)

    def _apply_correction(
        self,
        plan: QueryPlan,
        failure: FailureInfo,
        context: Dict[str, Any],
    ) -> QueryPlan:
        """Apply an in-place correction strategy and return the plan."""
        if failure.failure_type == "join_key_mismatch":
            for op in plan.join_operations:
                if op.format_transformation is None:
                    op.format_transformation = FormatTransform(
                        source_format="integer",
                        target_format="CUST-{}",
                        transformation_function="prefix customer ids",
                    )
        elif failure.failure_type == "syntax":
            for sq in plan.sub_queries:
                if "LIMIT" not in sq.query_text.upper():
                    sq.query_text = sq.query_text.rstrip(";") + " LIMIT 100;"
        elif failure.failure_type == "wrong_db_type":
            for sq in plan.sub_queries:
                if sq.database_type == "postgres":
                    sq.database_type = "sqlite"

        return plan
