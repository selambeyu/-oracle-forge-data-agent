import json
import unittest
from unittest.mock import patch

from agent.mcp_toolbox import MCPToolbox


class FakeResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class MCPToolboxTests(unittest.TestCase):
    def test_list_tools_reads_mcp_tools_list(self) -> None:
        client = MCPToolbox("http://127.0.0.1:5000")
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": [{"name": "list_tables", "description": "List tables"}]},
        }

        with patch("agent.mcp_toolbox.urllib.request.urlopen", return_value=FakeResponse(payload)) as urlopen_mock:
            result = client.list_tools()

        self.assertEqual(result, payload["result"]["tools"])
        self.assertEqual(client._tool_source_map["list_tables"], "postgres")
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:5000/mcp")
        self.assertEqual(request.get_method(), "POST")

    def test_call_tool_uses_http_for_non_duckdb_sources(self) -> None:
        client = MCPToolbox("http://127.0.0.1:5000")
        payload = {"result": [{"table_name": "books_info"}]}

        with patch("agent.mcp_toolbox.urllib.request.urlopen", return_value=FakeResponse(payload)):
            result = client.call_tool("list_tables", {})

        self.assertTrue(result.success)
        self.assertEqual(result.data, [{"table_name": "books_info"}])
        self.assertEqual(result.source_type, "postgres")
        self.assertEqual(result.tool_name, "list_tables")

    def test_call_tool_routes_duckdb_queries_to_duckdb_mcp(self) -> None:
        client = MCPToolbox("http://127.0.0.1:5000", duckdb_mcp_url="http://127.0.0.1:8001")
        payload = {"result": [{"id": 1, "name": "book"}]}

        with patch("agent.mcp_toolbox.urllib.request.urlopen", return_value=FakeResponse(payload)) as urlopen_mock:
            result = client.call_tool("duckdb_query", {"query": "select 1 as id, 'book' as name"})

        self.assertTrue(result.success)
        self.assertEqual(result.data, [{"id": 1, "name": "book"}])
        self.assertEqual(result.source_type, "duckdb")
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8001/api/tool/duckdb_query/invoke")

    def test_call_tool_routes_mongodb_queries_to_direct_client(self) -> None:
        client = MCPToolbox("http://127.0.0.1:5000")

        with patch.object(client, "_call_mongodb_direct") as mongo_mock:
            mongo_mock.return_value = type("Result", (), {"success": True, "data": [], "error": None, "execution_time": 0.0, "source_type": "", "tool_name": ""})()
            result = client.call_tool("find_yelp_businesses", {"filterPayload": "{}", "limit": 10})

        mongo_mock.assert_called_once_with("find_yelp_businesses", {"filterPayload": "{}", "limit": 10})
        self.assertTrue(result.success)
        self.assertEqual(result.source_type, "mongodb")
        self.assertEqual(result.tool_name, "find_yelp_businesses")

    def test_call_mongodb_direct_parses_filter_and_sanitizes_object_id(self) -> None:
        client = MCPToolbox("http://127.0.0.1:5000")

        class FakeObjectId:
            def __str__(self) -> str:
                return "oid123"

        class FakeCursor:
            def __init__(self, docs):
                self._docs = docs

            def limit(self, limit_value):
                return self._docs[:limit_value]

        class FakeCollection:
            def find(self, query_filter):
                assert query_filter == {"is_open": 1}
                return FakeCursor([{"_id": FakeObjectId(), "is_open": 1}])

        class FakeDatabase:
            def __getitem__(self, collection_name):
                assert collection_name == "business"
                return FakeCollection()

        class FakeMongoClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def __getitem__(self, database_name):
                assert database_name == "yelp_db"
                return FakeDatabase()

        with patch("agent.mcp_toolbox.MongoClient", FakeMongoClient):
            result = client._call_mongodb_direct(
                "find_yelp_businesses",
                {"filterPayload": '{"is_open": 1}', "limit": 5},
            )

        self.assertTrue(result.success)
        self.assertEqual(result.data, [{"_id": "oid123", "is_open": 1}])

    def test_verify_connections_reports_http_and_duckdb(self) -> None:
        client = MCPToolbox("http://127.0.0.1:5000")

        mcp_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": [{"name": "list_tables"}]},
        }
        duckdb_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": [{"name": "duckdb_stockmarket_query"}]},
        }

        with patch(
            "agent.mcp_toolbox.urllib.request.urlopen",
            side_effect=[FakeResponse(mcp_payload), FakeResponse(duckdb_payload)],
        ):
            result = client.verify_connections()

        self.assertTrue(result["toolbox_http"])
        self.assertTrue(result["duckdb"])

    def test_list_tools_returns_error_payload_on_failure(self) -> None:
        client = MCPToolbox("http://127.0.0.1:5000")

        with patch("agent.mcp_toolbox.urllib.request.urlopen", side_effect=RuntimeError("boom")):
            result = client.list_tools()

        self.assertEqual(result, [{"error": "boom"}])


if __name__ == "__main__":
    unittest.main()
