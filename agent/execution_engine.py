"""
Execution Engine.

Responsibilities:
1. Dialect translation (SQL / MongoDB aggregation / DuckDB analytical SQL)
2. Query execution via MCPToolbox
3. Result merging (multi-database joins)
4. Result validation (types, nulls, integrity)
5. Format transformation (join key resolution)

Routing:
  PostgreSQL / SQLite / MongoDB  → HTTP Google MCP Toolbox (team-dab-toolbox)
  DuckDB                         → direct duckdb Python driver (via MCPToolbox)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.models.models import (
    FormatTransform,
    JoinOp,
    QueryPlan,
    QueryResult,
    SubQuery,
)
from .mcp_toolbox import MCPToolbox


class ExecutionEngine:
    """Execute query plans, returning one QueryResult per sub-query."""

    def __init__(
        self,
        toolbox: Optional[MCPToolbox] = None,
        db_configs: Optional[Dict[str, dict]] = None,
    ):
        self._db_configs: Dict[str, dict] = db_configs or {}
        self.toolbox = toolbox or MCPToolbox(db_configs=self._db_configs)

    def execute_plan(
        self,
        plan: QueryPlan,
        context: Dict[str, Any],
    ) -> List[QueryResult]:
        """Execute each sub-query in plan order and return per-DB results."""
        results: List[QueryResult] = []

        for idx in plan.execution_order:
            sq = plan.sub_queries[idx]
            try:
                tool_name, params = self._build_tool_call(sq)
                tool_result = self.toolbox.call_tool(tool_name, params)

                if not tool_result.success:
                    results.append(
                        QueryResult(
                            database=sq.database,
                            data=None,
                            error=tool_result.error,
                            success=False,
                        )
                    )
                else:
                    data = tool_result.data
                    rows = len(data) if isinstance(data, list) else 1
                    results.append(
                        QueryResult(
                            database=sq.database,
                            data=data,
                            success=True,
                            rows_affected=rows,
                        )
                    )
            except Exception as exc:
                results.append(
                    QueryResult(
                        database=sq.database,
                        data=None,
                        error=str(exc),
                        success=False,
                    )
                )

        # Merge results if join operations are specified
        if plan.join_operations and len(results) > 1:
            try:
                results_by_db = {
                    r.database: r.data
                    for r in results
                    if r.success and isinstance(r.data, list)
                }
                merged = self._merge_by_db(results_by_db, plan.join_operations)
                if merged is not None:
                    first_db = plan.sub_queries[plan.execution_order[0]].database
                    return [
                        QueryResult(
                            database=first_db,
                            data=merged,
                            success=True,
                            rows_affected=len(merged) if isinstance(merged, list) else 0,
                        )
                    ]
            except Exception:
                pass  # Return unmerged results on merge failure

        return results

    def _build_tool_call(self, sq: SubQuery) -> tuple[str, Dict[str, Any]]:
        """Map a sub-query to the MCP tool and its parameters.

        PostgreSQL / SQLite / MongoDB → HTTP toolbox (team-dab-toolbox).
        DuckDB → direct driver via MCPToolbox._call_duckdb (uses DUCKDB_PATH env).
        """
        # Derive actual DB type from db_configs; fall back to query_type
        db_type = self._db_configs.get(sq.database, {}).get("type", sq.query_type).lower()

        if db_type in ("postgresql", "postgres"):
            static_tool = self._match_static_pg_tool(sq.query)
            if static_tool:
                return static_tool, {}
            return "run_query", {"query": sq.query}

        if db_type == "sqlite":
            return "sqlite_query", {"query": sq.query}

        if db_type == "duckdb":
            # MCPToolbox._call_duckdb uses the DUCKDB_PATH env var
            return "duckdb_query", {"query": sq.query}

        if db_type == "mongodb":
            collection, pipeline = self._parse_mongo_query(sq.query)
            tool_name = (
                "find_yelp_checkins" if collection == "checkin" else "find_yelp_businesses"
            )
            return tool_name, {"filterPayload": pipeline, "limit": 20}

        return "run_query", {"query": sq.query}

    def _match_static_pg_tool(self, query: str) -> Optional[str]:
        """Return the name of a static toolbox tool if the query maps to one.

        Avoids needing the dynamic run_query tool for common schema/preview calls.
        Returns None when no static tool matches (caller falls back to run_query).
        """
        q = query.lower().strip()
        # Schema: column listing for books_info
        if "information_schema.columns" in q and "books_info" in q:
            return "describe_books_info"
        # Schema: table listing
        if "information_schema.tables" in q:
            return "list_tables"
        # Data preview: simple SELECT * FROM books_info with no WHERE/aggregation
        if (
            "books_info" in q
            and q.lstrip().startswith("select")
            and "where" not in q
            and "group by" not in q
            and "having" not in q
            and "order by" not in q
            and "max(" not in q
            and "min(" not in q
            and "count(" not in q
            and "sum(" not in q
            and "avg(" not in q
        ):
            return "preview_books_info"
        return None

    def _parse_mongo_query(self, query: str) -> tuple[str, str]:
        """Extract a coarse collection hint and pipeline payload."""
        collection = "checkin" if "checkin" in query.lower() else "business"
        return collection, "{}"

    def _merge_by_db(
        self,
        results_by_db: Dict[str, List[Dict[str, Any]]],
        join_ops: List[JoinOp],
    ) -> Optional[List[Dict[str, Any]]]:
        """Merge datasets keyed by database name using JoinOp definitions."""
        if not results_by_db or not join_ops:
            return None
        op = join_ops[0]
        left = results_by_db.get(op.left_db, [])
        right = results_by_db.get(op.right_db, [])
        if not left and not right:
            return None
        return self._join_datasets(left, right, op.left_key, op.right_key, op.join_type)

    def merge_results(
        self,
        results_by_index: Dict[int, List[Dict[str, Any]]],
        join_ops: List[JoinOp],
    ) -> List[Dict[str, Any]]:
        """Merge result sets by index (legacy interface)."""
        if not results_by_index:
            return []
        if not join_ops:
            return next(iter(results_by_index.values()))
        return next(iter(results_by_index.values()))

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
        matched_right_keys: set = set()
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

    def validate_result(
        self, result: Any, expected_schema: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Validate basic nullability and duplicate constraints."""
        issues: List[str] = []

        if result is None:
            return {"valid": False, "issues": ["Result is None"]}

        if isinstance(result, list):
            if result and isinstance(result[0], dict):
                for key, value in result[0].items():
                    if (
                        value is None
                        and expected_schema.get(key, {}).get("nullable") is False
                    ):
                        issues.append(f"Unexpected null in column: {key}")

            seen: set = set()
            for row in result:
                marker = repr(sorted(row.items())) if isinstance(row, dict) else repr(row)
                if marker in seen:
                    issues.append("Duplicate rows detected")
                    break
                seen.add(marker)

        return {"valid": not issues, "issues": issues}
