"""Language-aware normalisation for the memory full-text index.

FTS5's ``porter ascii`` tokenizer neither case-folds non-ASCII letters
("Äpfel" ≠ "äpfel") nor knows German morphology ("Häuser" ≠ "Haus").
Memory text is therefore indexed through a normalised shadow column: each
token is case-folded and reduced with CISTEM (Weissweiler & Fraser, 2017,
"Developing a Stemmer for German Based on a Comparative Analysis of
Publicly Available Stemmers", GSCL), then FTS5 applies ``porter`` on top for
English inflection. Index and query run through the same function, so the
mapping only has to be consistent, not linguistically perfect.

Every step is linear in the input; tokens are capped so the suffix loop
stays bounded.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Optional

# Bump when the normalisation changes: stored shadow text is then rebuilt.
NORMALIZATION_VERSION = "1"

_MAX_TOKEN_CHARS = 64
_MAX_QUERY_TOKENS = 32
_TOKEN = re.compile(r"[^\W_]+")

_STRIP_GE = re.compile(r"^ge(.{4,})")
_DOUBLE = re.compile(r"(.)\1")
_DOUBLE_BACK = re.compile(r"(.)\*")


def cistem(word: str) -> str:
    """CISTEM stem of one lower-cased token (case-insensitive variant)."""

    word = word.replace("ü", "u").replace("ö", "o").replace("ä", "a").replace("ß", "ss")
    word = _STRIP_GE.sub(r"\1", word)
    # Placeholders keep multi-letter graphemes intact while suffixes go.
    word = word.replace("sch", "$").replace("ei", "%").replace("ie", "&")
    word = _DOUBLE.sub(r"\1*", word)
    while len(word) > 3:
        if len(word) > 5 and word[-2:] in ("em", "er", "nd"):
            word = word[:-2]
            continue
        if word[-1] in "tesn":
            word = word[:-1]
            continue
        break
    word = _DOUBLE_BACK.sub(r"\1\1", word)
    return word.replace("%", "ei").replace("&", "ie").replace("$", "sch")


def _fold(token: str) -> str:
    # casefold: ß→ss, final sigma, etc.; NFKC first so compatibility forms
    # (ligatures, full-width letters) fold to the same token.
    return unicodedata.normalize("NFKC", token).casefold()[:_MAX_TOKEN_CHARS]


def _umlaut_fold(token: str) -> str:
    return _fold(token).replace("ü", "u").replace("ö", "o").replace("ä", "a")


def tokens(text: str) -> List[str]:
    return [cistem(_fold(t)) for t in _TOKEN.findall(unicodedata.normalize("NFKC", text))]


def normalize_for_index(text: str) -> str:
    return " ".join(t for t in tokens(text) if t)


def build_match_query(query: str) -> Optional[str]:
    """FTS5 MATCH expression for a free-text query, or ``None`` if empty.

    Terms are quoted (no FTS syntax injection) and AND-ed; a bare ``OR``
    between terms becomes the FTS operator; ``term*`` is a prefix query on
    the folded, unstemmed term (stemming a prefix would cut it further).
    """

    parts: List[str] = []
    pending_or = False
    for raw in query.split()[: _MAX_QUERY_TOKENS * 2]:
        if raw == "OR":
            pending_or = bool(parts)
            continue
        prefix = raw.endswith("*")
        found = _TOKEN.findall(unicodedata.normalize("NFKC", raw))
        if not found:
            continue
        stems = [cistem(_fold(t)) for t in (found[:-1] if prefix else found)]
        terms = [f'"{stem}"' for stem in stems if stem]
        if prefix:
            # Prefixes match index stems, so only the character mapping is
            # applied ("Häus*" → "haus*"); suffix stripping would cut it.
            terms.append(f'"{_umlaut_fold(found[-1])}"*')
        if not terms:
            continue
        group = terms[0] if len(terms) == 1 else "(" + " ".join(terms) + ")"
        if pending_or and parts:
            parts[-1] = f"{parts[-1]} OR {group}"
        else:
            parts.append(group)
        pending_or = False
        if len(parts) >= _MAX_QUERY_TOKENS:
            break
    if not parts:
        return None
    return " ".join(p if " OR " not in p else f"({p})" for p in parts)
