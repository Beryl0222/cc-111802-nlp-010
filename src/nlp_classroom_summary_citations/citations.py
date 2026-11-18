"""Citations anchored to exact fragments of immutable source revisions."""

import hashlib
import uuid
from dataclasses import dataclass

from .text import normalize_text


EXACT_MATCH = "exact_match"
RELOCATED_MATCH = "relocated_unique_match"
QUOTE_MISSING = "quote_missing"
AMBIGUOUS_QUOTE = "ambiguous_quote"
SOURCE_MISSING = "source_missing"
ANCHOR_INTEGRITY_ERROR = "anchor_integrity_error"


@dataclass(frozen=True)
class Evaluation:
    valid: bool
    match: str | None
    reason: str
    current_start: int | None = None
    current_end: int | None = None


def quote_digest(quote):
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


def normalize_quote(quote):
    return normalize_text(str(quote))


def find_occurrences(text, quote):
    """Return every offset at which ``quote`` occurs, including overlaps."""
    if not quote:
        return []
    positions = []
    start = 0
    while True:
        index = text.find(quote, start)
        if index < 0:
            return positions
        positions.append(index)
        start = index + 1


def build_evidence(record, quote, occurrence=None, at=""):
    """Create an immutable anchor into the current revision of ``record``."""
    quote = normalize_quote(quote)
    if not quote:
        raise ValueError("依据引文不能为空")
    positions = find_occurrences(record["text"], quote)
    if not positions:
        raise ValueError(f"来源 {record['record_id']} 的当前修订中找不到该引文")
    if occurrence is None:
        if len(positions) > 1:
            raise ValueError("引文在来源中出现多次，请提供 occurrence 或更长的原文片段")
        position = positions[0]
    else:
        try:
            occurrence = int(occurrence)
        except (TypeError, ValueError) as error:
            raise ValueError("occurrence 必须是从 1 开始的整数") from error
        if occurrence < 1 or occurrence > len(positions):
            raise ValueError("occurrence 超出该引文在来源中的出现次数")
        position = positions[occurrence - 1]
    end = position + len(quote)
    return {
        "evidence_id": uuid.uuid4().hex,
        "record_id": record["record_id"],
        "source": record["source"],
        "revision": record["revision"],
        "start": position,
        "end": end,
        "quote": quote,
        "quote_hash": quote_digest(quote),
        "context_before": record["text"][max(0, position - 30):position],
        "context_after": record["text"][end:end + 30],
        "added_at": at,
    }


def evaluate_evidence(evidence, record):
    """Check an anchor against the currently active revision of its source.

    The stored offsets are only hints. A citation is exact only when the
    quoted text is still at those offsets. If the fragment moved, it is valid
    only when the quote is unique in the new revision; an ambiguous quote is
    never silently resolved to one of several unrelated passages.
    """
    quote = str(evidence.get("quote", ""))
    if not quote:
        return Evaluation(False, None, ANCHOR_INTEGRITY_ERROR)
    stored_digest = evidence.get("quote_hash")
    if stored_digest and stored_digest != quote_digest(quote):
        return Evaluation(False, None, ANCHOR_INTEGRITY_ERROR)
    if record is None:
        return Evaluation(False, None, SOURCE_MISSING)

    text = record.get("text", "")
    start = evidence.get("start")
    end = evidence.get("end")
    if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(text):
        if text[start:end] == quote:
            return Evaluation(True, "exact", EXACT_MATCH, start, end)

    positions = find_occurrences(text, quote)
    if not positions:
        return Evaluation(False, None, QUOTE_MISSING)
    if len(positions) == 1:
        position = positions[0]
        return Evaluation(True, "relocated", RELOCATED_MATCH, position, position + len(quote))
    return Evaluation(False, None, AMBIGUOUS_QUOTE)


def excerpt(text, start, end, window=40):
    """Return a readable excerpt with ellipses, clipped safely to the text."""
    if text is None:
        return None
    start = max(0, min(int(start), len(text)))
    end = max(start, min(int(end), len(text)))
    left = max(0, start - window)
    right = min(len(text), end + window)
    return (
        ("…" if left else "")
        + text[left:right]
        + ("…" if right < len(text) else "")
    )
