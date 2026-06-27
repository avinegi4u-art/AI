from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from copresenter.models import DocumentRecord, SourceCitation


TOKEN_RE = re.compile(r"[a-zA-Z0-9']+")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}


@dataclass(frozen=True)
class TextChunk:
    document_id: str
    document_title: str
    index: int
    text: str
    tokens: Counter[str]


def tokenize(text: str) -> list[str]:
    return [
        token
        for token in (match.group(0).lower() for match in TOKEN_RE.finditer(text))
        if token not in STOP_WORDS and len(token) > 1
    ]


def chunk_documents(documents: list[DocumentRecord], max_words: int = 120) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for document in documents:
        words = document.text.split()
        if not words:
            continue
        chunk_index = 0
        for start in range(0, len(words), max_words):
            text = " ".join(words[start : start + max_words]).strip()
            if not text:
                continue
            chunks.append(
                TextChunk(
                    document_id=document.id,
                    document_title=document.title,
                    index=chunk_index,
                    text=text,
                    tokens=Counter(tokenize(text)),
                )
            )
            chunk_index += 1
    return chunks


def search(query: str, documents: list[DocumentRecord], limit: int = 4) -> list[SourceCitation]:
    query_tokens = Counter(tokenize(query))
    if not query_tokens:
        return []

    chunks = chunk_documents(documents)
    scored: list[SourceCitation] = []
    for chunk in chunks:
        score = _score_chunk(query_tokens, chunk)
        if score <= 0:
            continue
        scored.append(
            SourceCitation(
                document_id=chunk.document_id,
                document_title=chunk.document_title,
                chunk_index=chunk.index,
                text=chunk.text,
                score=round(score, 4),
            )
        )

    return sorted(scored, key=lambda citation: citation.score, reverse=True)[:limit]


def answer_question(question: str, documents: list[DocumentRecord]) -> tuple[str, list[SourceCitation]]:
    citations = search(question, documents)
    if not citations:
        return (
            "I do not know based on the uploaded materials. Please add more source documents "
            "or ask a question covered by the presentation files.",
            [],
        )

    snippets = [_shorten(citation.text) for citation in citations[:2]]
    answer = "Based on the uploaded materials: " + " ".join(snippets)
    return answer, citations


def _score_chunk(query_tokens: Counter[str], chunk: TextChunk) -> float:
    score = 0.0
    token_total = sum(chunk.tokens.values()) or 1
    for token, query_count in query_tokens.items():
        if token not in chunk.tokens:
            continue
        term_frequency = chunk.tokens[token] / token_total
        overlap_bonus = 1.0 + math.log1p(query_count)
        score += overlap_bonus + term_frequency
    return score


def _shorten(text: str, max_length: int = 420) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 3].rstrip() + "..."
