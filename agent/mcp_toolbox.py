"""
MCP Toolbox hybrid client.

Routes database calls:
- PostgreSQL, MongoDB, SQLite -> HTTP to Google MCP Toolbox binary
- DuckDB -> direct duckdb Python driver
"""

from __future__ import annotations

import json
import os
import itertools
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    def load_dotenv() -> bool:
        return False


load_dotenv()

TOOLBOX_URL = os.getenv("TOOLBOX_URL", "http://127.0.0.1:5000")
DUCKDB_PATH = os.getenv("DUCKDB_PATH", "./data/dataset.duckdb")

HTTP_SOURCE_TYPES = {"postgres", "mongodb", "sqlite"}
DUCKDB_SOURCE_TYPES = {"duckdb"}


@dataclass
class ToolResult:
    """Result from any tool call."""

    success: bool
    data: Any
    error: Optional[str] = None
    execution_time: float = 0.0
    source_type: str = ""
    tool_name: str = ""


class MCPToolbox:
    """
    Hybrid MCP client routing to HTTP toolbox or direct DuckDB driver.
    """

    def __init__(self, toolbox_url: str = TOOLBOX_URL):
        self.toolbox_url = toolbox_url
        self._duckdb_conn = None
        self._request_ids = itertools.count(1)
        self._tool_source_map: Dict[str, str] = {}
        self._default_source_map = {
            "run_query": "postgres",
            "list_tables": "postgres",
            "describe_books_info": "postgres",
            "preview_books_info": "postgres",
            "find_yelp_businesses": "mongodb",
            "find_yelp_checkins": "mongodb",
            "sqlite_query": "sqlite",
            "duckdb_query": "duckdb",
        }

    def call_tool(self, tool_name: str, parameters: Dict[str, Any]) -> ToolResult:
        """
        Invoke a named tool. Routes to HTTP toolbox or direct DuckDB driver.
        """
        source_type = self._resolve_source_type(tool_name)
        start = time.time()

        if source_type in DUCKDB_SOURCE_TYPES:
            result = self._call_duckdb(parameters.get("query", ""))
        else:
            result = self._call_http(tool_name, parameters)

        result.execution_time = round(time.time() - start, 3)
        result.tool_name = tool_name
        result.source_type = source_type
        return result

    def verify_connections(self) -> Dict[str, bool]:
        """
        Verify all database sources are reachable.
        """
        results: Dict[str, bool] = {}

        try:
            self.list_tools()
            results["toolbox_http"] = True
        except Exception as exc:
            results["toolbox_http"] = False
            results["toolbox_error"] = str(exc)

        try:
            import duckdb

            conn = duckdb.connect(DUCKDB_PATH)
            conn.execute("SELECT 1").fetchone()
            conn.close()
            results["duckdb"] = True
        except Exception as exc:
            results["duckdb"] = False
            results["duckdb_error"] = str(exc)

        return results

    def list_tools(self) -> List[Dict[str, Any]]:
        """
        List all tools registered in the running toolbox binary.
        """
        try:
            payload = self._post_mcp("tools/list", {})
            data = payload.get("result", {}).get("tools", [])
            for tool in data:
                name = tool.get("name", "")
                if name:
                    self._tool_source_map[name] = self._default_source_map.get(name, "")
            return data
        except Exception as exc:
            return [{"error": str(exc)}]

    def _call_http(self, tool_name: str, parameters: Dict[str, Any]) -> ToolResult:
        """Send tool invocation to MCP Toolbox HTTP server."""
        url = f"{self.toolbox_url}/api/tool/{tool_name}/invoke"
        payload = json.dumps(parameters).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                return ToolResult(
                    success=True,
                    data=data.get("result", data),
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode() if exc.fp else str(exc)
            return ToolResult(success=False, data=None, error=f"HTTP {exc.code}: {body}")
        except Exception as exc:
            return ToolResult(success=False, data=None, error=str(exc))

    def _post_mcp(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a JSON-RPC request to the toolbox MCP HTTP endpoint."""
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": next(self._request_ids),
                "method": method,
                "params": params,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.toolbox_url}/mcp",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def _call_duckdb(self, query: str) -> ToolResult:
        """Execute SQL directly against DuckDB."""
        try:
            import duckdb

            conn = duckdb.connect(DUCKDB_PATH, read_only=True)
            rows = conn.execute(query).fetchall()
            cols = [d[0] for d in conn.description] if conn.description else []
            conn.close()
            result_dicts = [dict(zip(cols, row)) for row in rows]
            return ToolResult(success=True, data=result_dicts)
        except Exception as exc:
            return ToolResult(success=False, data=None, error=str(exc))

    def _resolve_source_type(self, tool_name: str) -> str:
        """Determine source type for a tool name."""
        if tool_name in self._tool_source_map:
            return self._tool_source_map[tool_name]
        return self._default_source_map.get(tool_name, "postgres")
