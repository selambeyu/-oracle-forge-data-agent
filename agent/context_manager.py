"""
ContextManager — Three-layer context architecture.

Layer 1: Live schema introspection per connected database.
Layer 2: KB documents (kb/domain/, kb/evaluation/, agent/AGENT.md).
Layer 3: Corrections log (kb/corrections/corrections_log.md).
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from agent.models.models import (
    ContextBundle,
    CorrectionEntry,
    Document,
    SchemaInfo,
)
from utils.schema_introspector import introspect_schema


# Paths relative to repo root
_REPO_ROOT = Path(__file__).parent.parent
_KB_DOMAIN = _REPO_ROOT / "kb" / "domain"
_KB_EVALUATION = _REPO_ROOT / "kb" / "evaluation"
_CORRECTIONS_LOG = _REPO_ROOT / "kb" / "corrections" / "corrections_log.md"
_AGENT_MD = _REPO_ROOT / "agent" / "AGENT.md"

# Loaded once per session, updated after each execution
_BUNDLE: Optional[ContextBundle] = None


class ContextManager:
    """Builds and maintains the three-layer ContextBundle for the agent."""

    def __init__(self, databases: Dict[str, dict]):
        """
        Args:
            databases: mapping of db_name -> connection config dict.
                       Keys: type, connection_string (or path for local DBs)
        """
        self._databases = databases
        self._bundle: Optional[ContextBundle] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def load_all_layers(self) -> ContextBundle:
        """Load all three layers and cache the result."""
        schema = self._load_layer1()
        kb_docs = self._load_layer2()
        corrections = self._load_layer3()
        self._bundle = ContextBundle(
            schema=schema,
            institutional_knowledge=kb_docs,
            corrections=corrections,
        )
        return self._bundle

    def get_bundle(self) -> ContextBundle:
        if self._bundle is None:
            return self.load_all_layers()
        return self._bundle

    def get_schema_for_databases(self, db_names: List[str]) -> Dict[str, SchemaInfo]:
        bundle = self.get_bundle()
        return {k: v for k, v in bundle.schema.items() if k in db_names}

    def get_similar_corrections(self, query: str) -> List[CorrectionEntry]:
        """Return corrections whose query text overlaps with the given query.

        Tokenises on word boundaries so SQL syntax (parentheses, quotes, =)
        does not prevent matching meaningful terms.
        """
        bundle = self.get_bundle()
        query_tokens = set(re.findall(r"[a-z0-9_]+", query.lower()))
        # Remove common SQL stop-words that add noise
        _SQL_STOPS = {"select", "from", "where", "and", "or", "the", "a", "an",
                      "in", "is", "not", "null", "by", "on", "as", "for", "of",
                      "to", "be", "at", "it", "if", "do"}
        query_tokens -= _SQL_STOPS
        results = []
        for entry in bundle.corrections:
            entry_tokens = set(re.findall(r"[a-z0-9_]+", entry.query.lower()))
            entry_tokens -= _SQL_STOPS
            overlap = query_tokens & entry_tokens
            # At least 30 % of the (cleaned) query tokens must match
            if query_tokens and len(overlap) >= max(1, len(query_tokens) * 0.3):
                results.append(entry)
        return results

    def log_correction(
        self,
        query: str,
        failure_cause: str,
        correction: str,
        database: Optional[str] = None,
    ) -> None:
        """Append a new correction entry to the corrections log (append-only)."""
        entry = CorrectionEntry(
            query=query,
            failure_cause=failure_cause,
            correction=correction,
            timestamp=datetime.utcnow(),
            database=database,
        )
        # Persist to disk
        _CORRECTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _CORRECTIONS_LOG.open("a", encoding="utf-8") as f:
            f.write(
                f"\n## {entry.timestamp.isoformat()} | db={database or 'unknown'}\n"
                f"**Query:** {entry.query}\n"
                f"**Failure:** {entry.failure_cause}\n"
                f"**Correction:** {entry.correction}\n"
                f"---\n"
            )
        # Update in-memory bundle
        if self._bundle is not None:
            self._bundle.corrections.append(entry)

    # ── Layer loaders ─────────────────────────────────────────────────────────

    def _load_layer1(self) -> Dict[str, SchemaInfo]:
        schema: Dict[str, SchemaInfo] = {}
        for db_name, config in self._databases.items():
            try:
                schema[db_name] = introspect_schema(db_name, config)
            except Exception as exc:
                # Non-fatal: agent can still work with the databases it can reach
                print(f"[ContextManager] Warning: could not introspect {db_name}: {exc}")
        return schema

    def _load_layer2(self) -> List[Document]:
        docs: List[Document] = []
        sources = [
            _AGENT_MD,
            *sorted(_KB_DOMAIN.glob("*.md")),
            *sorted(_KB_EVALUATION.glob("*.md")),
        ]
        for path in sources:
            if path.exists():
                try:
                    source = str(path.relative_to(_REPO_ROOT))
                except ValueError:
                    source = str(path)
                docs.append(Document(source=source, content=path.read_text(encoding="utf-8")))
        return docs

    def _load_layer3(self) -> List[CorrectionEntry]:
        if not _CORRECTIONS_LOG.exists():
            return []
        return _parse_corrections_log(_CORRECTIONS_LOG.read_text(encoding="utf-8"))


# ── Parser ────────────────────────────────────────────────────────────────────

_ENTRY_RE = re.compile(
    r"## (?P<ts>[^\|]+)\| db=(?P<db>[^\n]+)\n"
    r"\*\*Query:\*\* (?P<query>[^\n]+)\n"
    r"\*\*Failure:\*\* (?P<failure>[^\n]+)\n"
    r"\*\*Correction:\*\* (?P<correction>[^\n]+)",
    re.MULTILINE,
)


def _parse_corrections_log(text: str) -> List[CorrectionEntry]:
    entries = []
    for m in _ENTRY_RE.finditer(text):
        try:
            ts = datetime.fromisoformat(m.group("ts").strip())
        except ValueError:
            ts = datetime.utcnow()
        entries.append(
            CorrectionEntry(
                query=m.group("query").strip(),
                failure_cause=m.group("failure").strip(),
                correction=m.group("correction").strip(),
                timestamp=ts,
                database=m.group("db").strip(),
            )
        )
    return entries
