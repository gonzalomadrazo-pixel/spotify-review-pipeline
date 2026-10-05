"""Deterministic extraction done by code instead of a model.

- evidence_quote: short reviews use their whole (stripped) text; for long reviews the model proposes a
  verbatim excerpt and code verifies exact substring membership, repairing only by mapping back to an
  exact source span. Unrepairable quotes fall back to the full text and are flagged for review.
- entities: explicit feature terms matched by regex; each entity is the exact surface string found in
  the review, so no entity can be an unsupported addition.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# ---------------------------------------------------------------- quotes


def _normalized_with_map(s: str):
    """Collapse whitespace runs and lowercase; return normalized string + map to original indices."""
    out, idx_map = [], []
    prev_space = False
    for i, ch in enumerate(s):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx_map.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            idx_map.append(i)
            prev_space = False
    return "".join(out), idx_map


def resolve_quote(text: str, proposed: str, needs_model_quote: bool) -> tuple[str, str]:
    """Return (quote, method). The quote is always an exact substring of `text` with nonempty content."""
    whole = text.strip()
    if not needs_model_quote:
        return whole, "full_text_short_review"
    q = (proposed or "").strip()
    if q and q in text:
        return q, "model_exact"
    if q:
        # Remove wrapping quotes/ellipses the model may add.
        trimmed = q.strip("\"'“”‘’ ").rstrip(".…").strip()
        if len(trimmed) >= 8 and trimmed in text:
            return trimmed, "model_trimmed"
        norm_text, idx_map = _normalized_with_map(text)
        norm_q, _ = _normalized_with_map(trimmed or q)
        norm_q = norm_q.strip()
        pos = norm_text.find(norm_q) if norm_q else -1
        if pos >= 0 and len(norm_q) >= 8:
            start = idx_map[pos]
            end = idx_map[pos + len(norm_q) - 1] + 1
            span = text[start:end].strip()
            if span:
                return span, "model_normalized"
        sm = SequenceMatcher(None, text, q, autojunk=False)
        m = sm.find_longest_match(0, len(text), 0, len(q))
        if m.size >= max(12, int(0.7 * len(q))):
            span = text[m.a:m.a + m.size].strip()
            if span:
                return span, "model_longest_match"
    return whole, "fallback_full_text"


# ---------------------------------------------------------------- entities

_TERMS = [
    "premium", "free version", "free tier", "free plan", "family plan", "duo", "student plan", "subscription",
    "price", "payment", "refund", "charged", "trial",
    "ads?", "advertisements?", "commercials?",
    "shuffle", "smart shuffle", "skips?", "repeat", "seek", "queue", "playlists?", "liked songs", "library",
    "downloads?", "downloaded", "offline", "lyrics", "podcasts?", "audiobooks?", "search", "recommendations?",
    "algorithm", "dj", "daylist", "wrapped", "radio", "autoplay", "canvas",
    "log ?in", "login", "sign ?in", "logged out", "log ?out", "password", "account", "email",
    "facebook", "google", "bluetooth", "android auto", "car", "chromecast", "spotify connect", "smart ?watch",
    "wear ?os", "widget", "lock ?screen", "notifications?", "equali[sz]er", "volume", "sound quality",
    "audio quality", "battery", "storage", "data usage", "cache", "crash(?:es|ed|ing)?", "lag(?:s|gy|ging)?",
    "update", "home ?(?:screen|page|feed)", "interface", "layout", "dark mode", "sleep timer",
    "customer (?:service|support)", "support",
]
_ENTITY_RE = re.compile(r"(?<![\w])(" + "|".join(_TERMS) + r")(?![\w])", re.IGNORECASE)


def extract_entities(text: str, limit: int = 6) -> list[str]:
    found, seen = [], set()
    for m in _ENTITY_RE.finditer(text):
        surface = m.group(0).strip()
        key = surface.lower()
        if surface and key not in seen:
            seen.add(key)
            found.append(surface)
        if len(found) >= limit:
            break
    return found
