"""
Serializer / Composer Fix Suite (Task 2 P1B)

Centralized text-formatting and output-linting helpers that
repair the five root-caused sub-bugs plus the CI gate.

Usage:
    from serializer_fixes import (
        normalize_joined_text,
        strip_trailing_permit,
        dedupe_adjacent_words,
        escape_for_html_once,
        lint_output,
        lint_output_as_dict,
    )
"""
from __future__ import annotations

import html
import json
import re
from typing import Any


# ── 2b. List splitter / sentence-join seam normalizer ───────────────────────

def normalize_joined_text(text: str) -> str:
    """
    Fix segment-concatenation seams:
      - Double periods `..` → single period
      - Period followed by lowercase `and` / `save` etc. → strip period, lowercase
      - Trailing period on left segment plus continued sentence on right
    """
    if not isinstance(text, str):
        return text
    # Normalize whitespace
    t = re.sub(r"\s+", " ", text).strip()
    # 2b + 2c: Fix double periods (but allow ellipsis)
    t = re.sub(r"\.{2}(?!\.)", ".", t)
    # 2c: Period followed by space + lowercase word (sentence seam)
    #     → strip the period and keep the lowercase word OR insert conjunction
    # e.g. "...before bidding. and save receipt." → "...before bidding and save receipt."
    def _seam_fix(m: re.Match[str]) -> str:
        punct = m.group(1)
        word = m.group(2)
        # If the left context ends in a typical sentence end, check if word
        # is a conjunction / continuation word
        continuation_words = {"and", "or", "but", "then", "also", "plus", "including", "save", "using", "with", "via", "through", "by", "for", "from", "to"}
        if word.lower() in continuation_words:
            return f" {word}"
        return f"{punct} {word.capitalize()}"
    t = re.sub(r"([.!?])\s+([a-z]{2,})", _seam_fix, t)
    # Clean any remaining double spaces
    t = re.sub(r"\s{2,}", " ", t)
    return t


# ── 2d(i). Strip trailing "Permit" from permit_name before template insert ──

def strip_trailing_permit(name: str) -> str:
    """
    Remove a trailing ' Permit' or ' Permits' token so templates like
    '{permit_name} Permit is Required' don't produce 'Building Permit Permit'.
    """
    if not isinstance(name, str):
        return name or ""
    n = name.strip()
    n = re.sub(r"\s+[Pp]ermits?\s*$", "", n)
    return n


# ── 2d(ii). Dedupe adjacent duplicate words ─────────────────────────────────

def dedupe_adjacent_words(text: str) -> str:
    """
    Collapse adjacent identical words: 'commercial restaurant commercial TI'
    → 'commercial restaurant TI'.
    """
    if not isinstance(text, str):
        return text
    def _dedup(m: re.Match[str]) -> str:
        words = m.group(0).split()
        seen = set()
        out = []
        for w in words:
            w_lower = w.lower()
            if w_lower not in seen:
                out.append(w)
                seen.add(w_lower)
            # else drop duplicate
        return " ".join(out)
    # Match runs of 3+ words where we can check for duplicates
    return re.sub(r"(?:[A-Za-z]+(?:\s+|$)){3,}", _dedup, text)


# ── 2e. HTML entity leak fix ────────────────────────────────────────────────

def escape_for_html_once(text: str) -> str:
    """
    Escape text for HTML, but only if the text does NOT already contain
    escaped entities like &amp; . Prevents double-escaping.
    """
    if not isinstance(text, str):
        return text
    # If already contains HTML entities, unescape first, then escape once
    if re.search(r"&(?:amp|lt|gt|quot|#\d+);", text):
        text = html.unescape(text)
    return html.escape(text)


def decode_html_entities(text: str) -> str:
    """Unescape any HTML entities in text."""
    if not isinstance(text, str):
        return text
    return html.unescape(text)


# ── Unified apply-all serializer fix ────────────────────────────────────────

def apply_all_serializer_fixes(text: str) -> str:
    """Run all text-level serializer fixes in deterministic order."""
    if not isinstance(text, str):
        return text
    text = normalize_joined_text(text)
    text = dedupe_adjacent_words(text)
    # Permit-name stripping is done per-field, not globally on all text
    return text


def apply_all_html_escape_fixes(text: str) -> str:
    """Decode double-escaped entities, then escape once for HTML."""
    if not isinstance(text, str):
        return text
    text = decode_html_entities(text)
    return escape_for_html_once(text)


# ── 2f. Output Linter (CI gate + runtime alert) ─────────────────────────────

# Fail-level patterns (CI build fails)
_FAIL_PATTERNS: dict[str, str] = {
    "broken_multiplier":       r"\b\d+\.\d+×",       # actually spec says \\d+× but the repro is .0× — let's use the actual broken pattern
    "zero_prefix_multiplier":  r"\B\.\d+×",          # the .0× case
    "times_one":               r"×\s*1\b",
    "permit_permit":           r"\b[Pp]ermit\s+[Pp]ermit\b",
    "double_period":           r"\.{2}(?!\.)",
    "sentence_join_seam":      r"[.!?]\s+[a-z]{2,}",  # period followed by lowercase word
    "entity_leak":             r"&(?:amp|lt|gt|quot|#\d+);",
    "dead_wrong_savannah":     r"\(912\)\s*651-6790",
    "wrong_richmond_address":  r"110\s+W\.?\s*State",
    # 2b: "homeowner" on commercial-classified inputs (checked separately)
    # "structured_permit_kind": r"use the structured permit kind",
}

# Flag-only patterns (alert, do not fail CI)
_FLAG_ONLY_PATTERNS: dict[str, str] = {
    "adjacent_duplicate_token": r"\b(\w+)(\s+\w+){0,2}\s+\1\b",
    "orphan_list_item":         r"^\s*\W?(\w+\s?){1,2}[.,]?\s*$",
}

def lint_output(text: str, *, is_commercial: bool = False) -> list[dict[str, Any]]:
    """
    Run linter against raw text or JSON-serialized text.
    Returns list of hits with severity.
    """
    if not isinstance(text, str):
        text = json.dumps(text, sort_keys=True, default=str)
    hits: list[dict[str, Any]] = []
    for code, pattern in _FAIL_PATTERNS.items():
        if re.search(pattern, text, flags=re.I):
            hits.append({"severity": "fail", "code": code, "pattern": pattern})
    if is_commercial and re.search(r"\bhomeowner\b", text, flags=re.I):
        hits.append({"severity": "fail", "code": "homeowner_on_commercial", "pattern": r"\bhomeowner\b"})
    for code, pattern in _FLAG_ONLY_PATTERNS.items():
        if re.search(pattern, text, flags=re.I | re.M):
            hits.append({"severity": "flag", "code": code, "pattern": pattern})
    return hits


def lint_output_as_dict(public: dict[str, Any], *, is_commercial: bool = False) -> list[dict[str, Any]]:
    """Lint an output dict by serializing it and running the linter."""
    text = json.dumps(public, sort_keys=True, default=str, ensure_ascii=False)
    return lint_output(text, is_commercial=is_commercial)


def has_fail_level_hit(public: dict[str, Any], *, is_commercial: bool = False) -> bool:
    """Quick check for any fail-level linter hit."""
    return any(h["severity"] == "fail" for h in lint_output_as_dict(public, is_commercial=is_commercial))
