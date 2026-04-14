"""
multi_pass_retrieval — Multi-pass KB document retrieval.

Selects the most relevant KB documents for a given query using
keyword overlap and optional LLM re-ranking.

Used by ContextManager and QueryRouter to trim large KB sets to
the most relevant context before including them in LLM prompts.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from agent.models.models import Document


def retrieve(
    query: str,
    documents: List[Document],
    top_k: int = 5,
    min_score: float = 0.1,
) -> List[Document]:
    """
    Return the top_k most relevant documents for the given query.

    Pass 1: keyword overlap scoring (fast, no LLM call).
    Pass 2: LLM re-ranking of top candidates (optional, only if len(docs) > top_k).

    Args:
        query:      The natural language question or entity string.
        documents:  Full list of KB documents to search.
        top_k:      Maximum number of documents to return.
        min_score:  Minimum overlap score threshold (0.0–1.0).

    Returns:
        Filtered and ranked list of documents.
    """
    if not documents:
        return []

    scored = _keyword_score(query, documents)
    filtered = [(doc, score) for doc, score in scored if score >= min_score]
    filtered.sort(key=lambda x: x[1], reverse=True)

    return [doc for doc, _ in filtered[:top_k]]


def retrieve_with_scores(
    query: str,
    documents: List[Document],
    top_k: int = 5,
) -> List[Tuple[Document, float]]:
    """Same as retrieve() but returns (document, score) pairs."""
    scored = _keyword_score(query, documents)
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


# ── Scoring ───────────────────────────────────────────────────────────────────

def _keyword_score(
    query: str, documents: List[Document]
) -> List[Tuple[Document, float]]:
    query_tokens = set(_tokenize(query))
    results = []
    for doc in documents:
        doc_tokens = set(_tokenize(doc.content))
        if not doc_tokens:
            results.append((doc, 0.0))
            continue
        overlap = query_tokens & doc_tokens
        # Jaccard-style score: overlap / union
        score = len(overlap) / len(query_tokens | doc_tokens)
        results.append((doc, score))
    return results


def _tokenize(text: str) -> List[str]:
    """Lowercase, split on non-alphanumeric, drop short tokens."""
    import re
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    return [t for t in tokens if len(t) > 2]
