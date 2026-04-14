"""
join_key_resolver — Detect and reconcile cross-database join keys.

Used by QueryRouter when a plan involves multiple databases.
Reads join key hints from kb/domain/join_key_glossary.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from agent.models.models import JoinOp, SchemaInfo, SubQuery

_REPO_ROOT = Path(__file__).parent.parent
_JOIN_KEY_GLOSSARY = _REPO_ROOT / "kb" / "domain" / "join_key_glossary.md"

# Parsed once at import time
_GLOSSARY: Optional[Dict[str, Dict]] = None


def resolve_join_keys(
    sub_queries: List[SubQuery],
    schema: Dict[str, SchemaInfo],
) -> List[JoinOp]:
    """
    Detect join operations required across the given sub-queries.

    Returns a list of JoinOp instances with left_key, right_key, and
    cast information sourced from the KB glossary.
    """
    glossary = _load_glossary()
    join_ops: List[JoinOp] = []

    for i, left_sq in enumerate(sub_queries):
        for right_sq in sub_queries[i + 1:]:
            join = _find_join(left_sq, right_sq, schema, glossary)
            if join:
                join_ops.append(join)

    return join_ops


def normalize_key_value(value: str, from_type: str, to_type: str) -> str:
    """
    Convert a join key value between type formats.

    Example: normalize_key_value("12345", "int", "string") -> "12345"
             normalize_key_value("  CUS-001 ", "string", "string") -> "CUS-001"
    """
    value = str(value).strip()
    if to_type == "int":
        return str(int(float(value)))
    return value


# ── Internal helpers ──────────────────────────────────────────────────────────

def _find_join(
    left: SubQuery,
    right: SubQuery,
    schema: Dict[str, SchemaInfo],
    glossary: Dict[str, Dict],
) -> Optional[JoinOp]:
    left_cols = _all_columns(left.database, schema)
    right_cols = _all_columns(right.database, schema)

    for key_name, key_info in glossary.items():
        left_present = any(key_name in c.lower() for c in left_cols)
        right_present = any(key_name in c.lower() for c in right_cols)
        if left_present and right_present:
            return JoinOp(
                left_db=left.database,
                right_db=right.database,
                left_key=key_info.get("left_column", key_name),
                right_key=key_info.get("right_column", key_name),
            )

    return None


def _all_columns(db_name: str, schema: Dict[str, SchemaInfo]) -> List[str]:
    info = schema.get(db_name)
    if not info:
        return []
    return [col for cols in info.tables.values() for col in cols]


def _load_glossary() -> Dict[str, Dict]:
    global _GLOSSARY
    if _GLOSSARY is not None:
        return _GLOSSARY

    _GLOSSARY = {}
    if not _JOIN_KEY_GLOSSARY.exists():
        return _GLOSSARY

    text = _JOIN_KEY_GLOSSARY.read_text(encoding="utf-8")
    # Parse markdown table rows:  | key | left_col | right_col | note |
    for line in text.splitlines():
        if not line.startswith("|") or "---" in line or "key" in line.lower():
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) >= 3:
            key_name = parts[0].lower()
            _GLOSSARY[key_name] = {
                "left_column": parts[1] if len(parts) > 1 else key_name,
                "right_column": parts[2] if len(parts) > 2 else key_name,
                "note": parts[3] if len(parts) > 3 else "",
            }

    return _GLOSSARY
