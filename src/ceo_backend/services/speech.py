"""Spoken replies: the part of an answer worth reading aloud. Gemini voices it in one stream;
the parts let the browser's own voice read it sentence by sentence when Gemini can't."""
import re
from dataclasses import dataclass

from ceo_backend.agents.runtime import CITATION, STRAY_REF

# About 40 seconds of speech. Longer answers end with MORE and stay readable on screen.
BUDGET_CHARS = 600
# Browser-voice parts: the first is one sentence; Chrome cuts off long utterances, so none is long.
PART_CHARS = 300
SAMPLE_RATE = 24_000  # Gemini speech: 16-bit mono PCM
TABLE_NOTE = "I've put a table on screen."
MORE = "The rest is on screen."

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class SpokenText:
    parts: list[str]
    truncated: bool


def _plain(line: str) -> str:
    line = re.sub(r"^#{1,6}\s+", "", line)
    line = re.sub(r"^>\s?", "", line)
    line = re.sub(r"^(?:[-*+•]|\d+[.)])\s+", "", line)
    line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"https?://\S+", "", line)
    line = re.sub(r"[*`~]+|(?<!\w)_+|_+(?!\w)", "", line)
    line = re.sub(r"\s+([.,;:!?])", r"\1", line)
    return re.sub(r"\s{2,}", " ", line).strip()


def sentences(text: str) -> list[str]:
    """The answer as plain sentences: no citations, code, tables or Markdown."""
    text = STRAY_REF.sub("", CITATION.sub("", text or ""))
    result: list[str] = []
    in_code = table_noted = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not line or re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", line):
            continue
        if line.startswith("|"):
            if not table_noted:
                result.append(TABLE_NOTE)
                table_noted = True
            continue
        line = _plain(line)
        if not line:
            continue
        # Headings and list items have no full stop; add one so the voice pauses.
        if not re.search(r"[.!?:;]$", line):
            line += "."
        result.extend(piece for piece in SENTENCE_END.split(line) if piece)
    return result


def spoken_parts(text: str) -> SpokenText:
    pending = sentences(text)
    kept: list[str] = []
    used = 0
    while pending and (not kept or used + len(pending[0]) <= BUDGET_CHARS):
        sentence = pending.pop(0)
        if len(sentence) > BUDGET_CHARS:
            sentence = sentence[:BUDGET_CHARS].rsplit(" ", 1)[0] + "…"
        kept.append(sentence)
        used += len(sentence) + 1
    truncated = bool(pending)
    if truncated:
        kept.append(MORE)

    parts: list[str] = []
    for index, sentence in enumerate(kept):
        if index > 1 and len(parts[-1]) + len(sentence) < PART_CHARS:
            parts[-1] += " " + sentence
        else:
            parts.append(sentence)
    return SpokenText(parts=parts, truncated=truncated)


def raw_pcm(chunk: bytes) -> bytes:
    """Streamed speech is raw PCM. A chunk sent as a WAV file instead loses its header."""
    if chunk[:4] == b"RIFF":
        start = chunk.find(b"data")
        return chunk[start + 8:] if start >= 0 else b""
    return chunk
