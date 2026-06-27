from __future__ import annotations

import re
from collections import Counter

from copresenter.models import DocumentRecord, ScriptSection
from copresenter.policy import DISCLOSURE_TEXT
from copresenter.retrieval import STOP_WORDS, tokenize


SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def build_script(documents: list[DocumentRecord]) -> tuple[str, list[ScriptSection], str]:
    sections: list[ScriptSection] = []
    for document in documents:
        talking_points = _top_talking_points(document.text)
        if talking_points:
            sections.append(ScriptSection(heading=document.title, talking_points=talking_points))

    closing = (
        "That concludes the prepared overview. I can answer questions using the uploaded "
        "materials, and I will say when the materials do not contain an answer."
    )
    return DISCLOSURE_TEXT, sections, closing


def build_speech_preview(opening: str, sections: list[ScriptSection], closing: str) -> str:
    spoken_sections: list[str] = [opening]
    for section in sections:
        spoken_sections.append(f"Now covering {section.heading}.")
        spoken_sections.extend(section.talking_points)
    spoken_sections.append(closing)
    return "\n\n".join(spoken_sections)


def _top_talking_points(text: str, limit: int = 5) -> list[str]:
    sentences = [sentence.strip() for sentence in SENTENCE_RE.split(text) if sentence.strip()]
    if not sentences:
        return []

    corpus_counts = Counter(token for token in tokenize(text) if token not in STOP_WORDS)
    scored: list[tuple[float, str]] = []
    for sentence in sentences:
        tokens = tokenize(sentence)
        if not tokens:
            continue
        score = sum(corpus_counts[token] for token in tokens) / len(tokens)
        scored.append((score, _ensure_sentence(sentence)))

    if not scored:
        return [_ensure_sentence(" ".join(text.split())[:240])]

    ordered = sorted(scored, key=lambda item: item[0], reverse=True)
    points: list[str] = []
    seen: set[str] = set()
    for _, sentence in ordered:
        normalized = sentence.lower()
        if normalized in seen:
            continue
        points.append(sentence)
        seen.add(normalized)
        if len(points) >= limit:
            break
    return points


def _ensure_sentence(text: str) -> str:
    clean = " ".join(text.split())
    if not clean:
        return clean
    if clean[-1] in ".!?":
        return clean
    return clean + "."
