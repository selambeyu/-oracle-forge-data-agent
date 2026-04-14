#!/usr/bin/env python3
"""Run one natural language query against the BookReview database."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict

# Ensure repo root is on sys.path so `from agent...` works when the script is run from scripts/
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
load_dotenv()

from agent.oracle_forge_agent import OracleForgeAgent


def build_db_configs(database_id: str, db_type: str, connection: str) -> Dict[str, dict]:
    db_type = db_type.lower()
    if db_type in {"postgres", "mongodb"}:
        return {
            database_id: {
                "type": db_type,
                "connection_string": connection,
            }
        }
    if db_type in {"sqlite", "duckdb"}:
        return {
            database_id: {
                "type": db_type,
                "path": connection,
            }
        }
    raise ValueError(f"Unsupported db type: {db_type}")


def main() -> None:

    parser = argparse.ArgumentParser(
        description="Run a single natural language query against the BookReview database."
    )
    parser.add_argument("--question", required=True, help="Natural language question to answer.")
    parser.add_argument(
        "--db-type",
        default=os.getenv("BOOKREVIEW_DB_TYPE", "postgres"),
        choices=["postgres", "mongodb", "sqlite", "duckdb"],
        help="Database type for the BookReview database.",
    )
    parser.add_argument(
        "--connection",
        default=os.getenv("BOOKREVIEW_DB_CONN", ""),
        help=(
            "Connection string for postgres/mongodb or file path for sqlite/duckdb. "
            "Optional for postgres/mongodb — query execution is routed through the "
            "MCP Toolbox (MCP_TOOLBOX_URL). If omitted, schema introspection falls "
            "back to KB Layer 2 knowledge. Can also be set via BOOKREVIEW_DB_CONN."
        ),
    )
    parser.add_argument(
        "--db-id",
        default=os.getenv("BOOKREVIEW_DB_ID", "books_database"),
        help="Logical database identifier used by the agent (default: books_database).",
    )
    parser.add_argument("--output", default=None, help="Write JSON result to this file.")
    args = parser.parse_args()

    if not args.connection and args.db_type in {"sqlite", "duckdb"}:
        raise SystemExit(
            f"Error: --connection (file path) is required for --db-type {args.db_type}."
        )

    db_configs = build_db_configs(args.db_id, args.db_type, args.connection)
    agent = OracleForgeAgent(db_configs=db_configs)
    result = agent.answer(
        {
            "question": args.question,
            "available_databases": [args.db_id],
            "schema_info": {},
        }
    )

    output_text = json.dumps(result, indent=2, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_text)
        print(f"Wrote result to {args.output}")
    else:
        print(output_text)


if __name__ == "__main__":
    main()
