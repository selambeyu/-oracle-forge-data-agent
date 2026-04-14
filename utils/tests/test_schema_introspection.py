"""Tests for schema_introspector utilities."""

import pytest
from unittest.mock import MagicMock, patch

from utils.schema_introspector import introspect_schema
from agent.models import SchemaInfo


def test_introspect_schema_unsupported_type():
    with pytest.raises(ValueError, match="Unsupported"):
        introspect_schema("mydb", {"type": "oracle"})


def test_introspect_schema_sqlite_missing_path():
    result = introspect_schema("sqlite", {"type": "sqlite", "path": ""})
    assert isinstance(result, SchemaInfo)
    assert result.tables == {}


def test_introspect_schema_duckdb_in_memory():
    result = introspect_schema("duckdb", {"type": "duckdb", "path": ":memory:"})
    assert isinstance(result, SchemaInfo)
    assert result.db_type == "duckdb"


def test_introspect_schema_postgres_no_connection():
    result = introspect_schema("postgres", {"type": "postgres", "connection_string": ""})
    assert isinstance(result, SchemaInfo)
    assert result.tables == {}


def test_introspect_schema_mongodb_no_connection():
    result = introspect_schema("mongodb", {"type": "mongodb", "connection_string": ""})
    assert isinstance(result, SchemaInfo)
    assert result.tables == {}
