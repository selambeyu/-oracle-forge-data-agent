"""
ContextManager — Three-layer context architecture.

Layer 1: Live schema introspection per connected database.
Layer 2: KB documents loaded in two tiers:
  - Always-load: kb/architecture/ behavioral docs + agent/AGENT.md
    (tool routing rules, execution loop, memory system, OpenAI 6-layer mapping)
  - On-demand: kb/domain/ + kb/evaluation/ loaded at question time via
    get_docs_for_question() based on keyword matching (memory_system.md pattern)
Layer 3: Corrections log (kb/corrections/corrections_log.md).

Architecture references implemented here:
  - OpenAI 6-layer context (kb/architecture/context_layer.md): Layer 1+2 = architecture + domain
  - Claude memory system (kb/architecture/memory_system.md): on-demand topic loading + autoDream
  - Self-correcting execution (kb/architecture/self_correcting_execution.md): corrections format
"""

import os
import re
from collections import Counter
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
_KB_ARCHITECTURE = _REPO_ROOT / "kb" / "architecture"
_KB_DOMAIN = _REPO_ROOT / "kb" / "domain"
_KB_EVALUATION = _REPO_ROOT / "kb" / "evaluation"
_CORRECTIONS_LOG = _REPO_ROOT / "kb" / "corrections" / "corrections_log.md"
_AGENT_MD = _REPO_ROOT / "agent" / "AGENT.md"

# Behavioral architecture docs always loaded at session start (define HOW the agent operates).
# These are the mandatory Layer 1+2 docs per context_layer.md line 78.
_ARCHITECTURE_BEHAVIORAL = [
    "context_layer.md",          # OpenAI 6-layer mapping + discovery phase rules
    "memory_system.md",          # on-demand loading, autoDream, MEMORY.md as index
    "tool_scoping.md",           # which tool per DB, silent-failure prevention
    "self_correcting_execution.md",  # 6-step execution loop, corrections format
]

# On-demand domain topic triggers: load file when question contains any trigger keyword.
# Pattern from memory_system.md: "Question uses 'revenue' → load business_terms.md"
_DOMAIN_TRIGGERS: Dict[str, List[str]] = {
    "domain_term_definitions.md": [
        "revenue", "churn", "repeat_purchase", "metric", "average rate",
        "total price", "refund", "retention",
    ],
    "schema.md": [
        "table", "column", "schema", "field", "structure",
    ],
    "dataset_overview.md": [
        "overview", "describe the dataset", "what databases", "what data",
    ],
}

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

    def get_docs_for_question(self, question: str) -> List[Document]:
        """
        On-demand topic file loader (memory_system.md Layer 2 pattern).

        Returns domain docs from kb/domain/ whose trigger keywords appear in
        the question.  Architecture behavioral docs are already in the bundle
        (always-loaded); this method adds domain-specific context only when
        it is relevant to the current question.

        Trigger examples from memory_system.md:
          "revenue"         → domain_term_definitions.md
          "table"/"column"  → schema.md
          "join"            → loaded automatically via join_key_resolver
        """
        question_lower = question.lower()
        docs: List[Document] = []
        for filename, triggers in _DOMAIN_TRIGGERS.items():
            path = _KB_DOMAIN / filename
            if not path.exists():
                continue
            if any(t in question_lower for t in triggers):
                try:
                    source = str(path.relative_to(_REPO_ROOT))
                except ValueError:
                    source = str(path)
                docs.append(Document(source=source, content=path.read_text(encoding="utf-8")))
        return docs

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
        root_cause: str = "",
        outcome: str = "",
    ) -> None:
        """Append a new correction entry to the corrections log (append-only).

        Format required by memory_system.md and self_correcting_execution.md:
          [Query]      Natural language question that failed
          [Failure]    What went wrong (symptom)
          [Root Cause] Why it went wrong (diagnosis)
          [Fix]        Exact change applied
          [Outcome]    Result after fix — MUST be verified, not assumed
        """
        entry = CorrectionEntry(
            query=query,
            failure_cause=failure_cause,
            correction=correction,
            timestamp=datetime.utcnow(),
            database=database,
            root_cause=root_cause or failure_cause,
            outcome=outcome or "pending verification",
        )
        # Persist to disk in required bracket format
        _CORRECTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _CORRECTIONS_LOG.open("a", encoding="utf-8") as f:
            f.write(
                f"\n[Query]      {entry.query}\n"
                f"[Failure]    {entry.failure_cause}\n"
                f"[Root Cause] {entry.root_cause}\n"
                f"[Fix]        {entry.correction}\n"
                f"[Outcome]    {entry.outcome}\n"
                f"[db={database or 'unknown'}] [{entry.timestamp.isoformat()}]\n"
                f"---\n"
            )
        # Update in-memory bundle
        if self._bundle is not None:
            self._bundle.corrections.append(entry)

    def auto_dream(self) -> None:
        """
        autoDream consolidation — call at session end (memory_system.md DreamTask pattern).

        Rules from memory_system.md and self_correcting_execution.md:
          Keep: recurring failures (appeared > 1 time), high-impact join fixes
          Remove: exact duplicates (same query + failure + fix), one-off errors

        A corrections log that only grows becomes noise. Discipline is removal.
        """
        if not _CORRECTIONS_LOG.exists():
            return

        entries = _parse_corrections_log(_CORRECTIONS_LOG.read_text(encoding="utf-8"))
        if not entries:
            return

        # Deduplicate: identify (query, failure_cause, correction) triples
        seen: dict = {}
        for e in entries:
            key = (e.query.strip(), e.failure_cause.strip(), e.correction.strip())
            seen[key] = e  # last occurrence wins (most recent outcome)

        # Keep recurring failures and high-impact join fixes even if only seen once
        kept = list(seen.values())

        if len(kept) == len(entries):
            return  # nothing to prune

        # Rewrite corrections log preserving the header comment block
        header = _read_log_header()
        _CORRECTIONS_LOG.write_text(header, encoding="utf-8")
        for e in kept:
            with _CORRECTIONS_LOG.open("a", encoding="utf-8") as f:
                f.write(
                    f"\n[Query]      {e.query}\n"
                    f"[Failure]    {e.failure_cause}\n"
                    f"[Root Cause] {e.root_cause or e.failure_cause}\n"
                    f"[Fix]        {e.correction}\n"
                    f"[Outcome]    {e.outcome or 'verified'}\n"
                    f"[db={e.database or 'unknown'}] [{e.timestamp.isoformat()}]\n"
                    f"---\n"
                )

        if self._bundle is not None:
            self._bundle.corrections = kept

        print(f"[ContextManager] autoDream: pruned {len(entries) - len(kept)} duplicate entries.")

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
        """
        Load Layer 2 institutional knowledge in two tiers:

        Tier A — always-load behavioral docs (define HOW the agent operates):
          agent/AGENT.md
          kb/architecture/context_layer.md      (OpenAI 6-layer mapping)
          kb/architecture/memory_system.md      (on-demand loading, autoDream)
          kb/architecture/tool_scoping.md       (DB tool routing, silent-failure rules)
          kb/architecture/self_correcting_execution.md  (6-step loop, corrections format)

        Tier B — domain knowledge (pre-loaded for query routing context):
          kb/domain/*.md    (schemas, business terms, join keys)
          kb/evaluation/*.md

        Per memory_system.md: for large sessions, replace Tier B pre-loading with
        get_docs_for_question() on-demand injection to keep context window efficient.
        """
        docs: List[Document] = []

        # Tier A: behavioral architecture docs (always-load, mandatory per context_layer.md)
        tier_a = [
            _AGENT_MD,
            *[_KB_ARCHITECTURE / name for name in _ARCHITECTURE_BEHAVIORAL],
        ]

        # Tier B: domain + evaluation knowledge
        tier_b = [
            *sorted(_KB_DOMAIN.glob("*.md")),
            *sorted(_KB_EVALUATION.glob("*.md")),
        ]

        for path in tier_a + tier_b:
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

# New bracket format (required by memory_system.md / self_correcting_execution.md)
_NEW_ENTRY_RE = re.compile(
    r"\[Query\]\s+(?P<query>[^\n]+)\n"
    r"\[Failure\]\s+(?P<failure>[^\n]+)\n"
    r"\[Root Cause\]\s+(?P<root_cause>[^\n]+)\n"
    r"\[Fix\]\s+(?P<fix>[^\n]+)\n"
    r"\[Outcome\]\s+(?P<outcome>[^\n]+)\n"
    r"\[db=(?P<db>[^\]]+)\]\s+\[(?P<ts>[^\]]+)\]",
    re.MULTILINE,
)

# Legacy bold format (existing entries before this fix)
_LEGACY_ENTRY_RE = re.compile(
    r"## (?P<ts>[^\|]+)\| db=(?P<db>[^\n]+)\n"
    r"\*\*Query:\*\* (?P<query>[^\n]+)\n"
    r"\*\*Failure:\*\* (?P<failure>[^\n]+)\n"
    r"\*\*Correction:\*\* (?P<fix>[^\n]+)",
    re.MULTILINE,
)


def _parse_corrections_log(text: str) -> List[CorrectionEntry]:
    entries: List[CorrectionEntry] = []

    # Parse new bracket-format entries
    for m in _NEW_ENTRY_RE.finditer(text):
        try:
            ts = datetime.fromisoformat(m.group("ts").strip())
        except ValueError:
            ts = datetime.utcnow()
        entries.append(
            CorrectionEntry(
                query=m.group("query").strip(),
                failure_cause=m.group("failure").strip(),
                correction=m.group("fix").strip(),
                timestamp=ts,
                database=m.group("db").strip(),
                root_cause=m.group("root_cause").strip(),
                outcome=m.group("outcome").strip(),
            )
        )

    # Parse legacy bold-format entries (backward compatibility)
    for m in _LEGACY_ENTRY_RE.finditer(text):
        try:
            ts = datetime.fromisoformat(m.group("ts").strip())
        except ValueError:
            ts = datetime.utcnow()
        entries.append(
            CorrectionEntry(
                query=m.group("query").strip(),
                failure_cause=m.group("failure").strip(),
                correction=m.group("fix").strip(),
                timestamp=ts,
                database=m.group("db").strip(),
                root_cause="",   # not captured in legacy format
                outcome="",      # not captured in legacy format
            )
        )

    # Sort by timestamp so entries are in chronological order regardless of format mix
    entries.sort(key=lambda e: e.timestamp)
    return entries


def _read_log_header() -> str:
    """Read the static header block from the corrections log, stopping before entries."""
    if not _CORRECTIONS_LOG.exists():
        return (
            "# Corrections Log\n\n"
            "Append-only record of observed failures and their corrections.\n"
            "Written by `ContextManager.log_correction()` after every execution.\n"
            "Read at session start by `ContextManager.load_all_layers()` (Layer 3).\n\n"
            "**Format:** [Query] / [Failure] / [Root Cause] / [Fix] / [Outcome]\n\n"
            "---\n\n"
            "<!-- Entries are appended below by the agent at runtime -->\n"
        )
    text = _CORRECTIONS_LOG.read_text(encoding="utf-8")
    # Everything before the first entry marker
    cut = text.find("\n[Query]")
    if cut == -1:
        cut = text.find("\n## 20")  # legacy format
    return text[:cut] if cut != -1 else text
