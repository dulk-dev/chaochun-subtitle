#!/usr/bin/env python3
"""Deterministic banned-term paraphrases for optional burn-time display text.

The lexicon maps known terms to generic wording. It does not rewrite captions
freely, and it does not apply star masks or homoglyphs. Callers must opt in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


AUDIO_REMINDER = (
    "只替换画面上的字幕，不会改口播。如果声音里仍是原来的名称，字幕替换可能不够。"
)

_STRATEGY = "generic-paraphrase"
_ROW_KEYS = {"term", "zh", "en"}
_MASK_REPLACEMENT = re.compile(r"^[\*＊★☆×.\-—_~\s]+$")
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_SINGLE_LATIN = re.compile(r"^[A-Za-z]$")


@dataclass(frozen=True)
class _Rule:
    term: str
    pattern: re.Pattern[str]
    length: int


@dataclass(frozen=True)
class Lexicon:
    path: Path
    strategy: str
    description: str
    entry_count: int
    replacements: dict[str, dict[str, str]]
    _rules: tuple[_Rule, ...]


def shipped_lexicon_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "banned_terms.json"


def resolve_lexicon_path(override: str | None = None) -> Path:
    from user_config import resolve_banned_terms_path

    return resolve_banned_terms_path(override, shipped=shipped_lexicon_path())


def load_lexicon(path: str | Path) -> Lexicon:
    """Load and validate a generic-paraphrase lexicon."""
    source = Path(path).expanduser()
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(
            f"Banned-term lexicon not found: {source}. Copy config/banned_terms.json "
            "or set banned_terms in the chaochun-subtitle config."
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Banned-term lexicon is not valid JSON: {source}: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError(
            "Banned-term lexicon must be a non-empty JSON array of "
            f"{{term, zh, en}} rows: {source}"
        )

    replacements: dict[str, dict[str, str]] = {}
    rules: list[_Rule] = []
    seen_terms: set[str] = set()
    for index, entry in enumerate(payload, 1):
        rule, wording = _compile_row(entry, index)
        identity = rule.term.casefold()
        if identity in seen_terms:
            raise ValueError(f"Duplicate banned term: {rule.term}")
        seen_terms.add(identity)
        replacements[rule.term] = wording
        rules.append(rule)
    rules.sort(key=lambda rule: (-rule.length, rule.term))
    return Lexicon(
        path=source,
        strategy=_STRATEGY,
        description="",
        entry_count=len(payload),
        replacements=replacements,
        _rules=tuple(rules),
    )


def paraphrase_lines(
    lines: list[dict], lexicon: Lexicon
) -> tuple[list[dict], list[dict]]:
    """Replace lexicon terms on Chinese text and any English fields."""
    updated: list[dict] = []
    occurrences: list[dict] = []
    for index, line in enumerate(lines, 1):
        item = dict(line)
        item["text"], hits = _apply_track(str(item.get("text") or ""), lexicon, "zh")
        occurrences.extend(
            _decorate(hit, surface="caption", track="zh", line=index) for hit in hits
        )
        english_key = "en" if item.get("en") else "text_en" if item.get("text_en") else ""
        if english_key:
            replaced, hits = _apply_track(str(item.get(english_key) or ""), lexicon, "en")
            item[english_key] = replaced
            occurrences.extend(
                _decorate(hit, surface="caption", track="en", line=index) for hit in hits
            )
        updated.append(item)
    return updated, occurrences


def paraphrase_chapters(
    chapters: list[dict], lexicon: Lexicon
) -> tuple[list[dict], list[dict]]:
    """Replace lexicon terms on burned chapter titles with the Chinese wording."""
    updated: list[dict] = []
    occurrences: list[dict] = []
    for index, chapter in enumerate(chapters, 1):
        item = dict(chapter)
        item["title"], hits = _apply_track(str(item.get("title") or ""), lexicon, "zh")
        occurrences.extend(
            _decorate(hit, surface="chapter", track="zh", line=index) for hit in hits
        )
        updated.append(item)
    return updated, occurrences


def build_report(lexicon: Lexicon, occurrences: list[dict]) -> dict:
    grouped: dict[tuple, dict] = {}
    ambiguous: list[dict] = []
    for hit in occurrences:
        key = (
            hit["entry"],
            hit["term"],
            hit["track"],
            hit["surface"],
            hit["replacement"],
            hit["ambiguous"],
        )
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {
                "entry": hit["entry"],
                "term": hit["term"],
                "track": hit["track"],
                "surface": hit["surface"],
                "replacement": hit["replacement"],
                "ambiguous": hit["ambiguous"],
                "count": 0,
            }
            grouped[key] = bucket
        bucket["count"] += 1
        if hit["ambiguous"]:
            ambiguous.append(
                {
                    "entry": hit["entry"],
                    "term": hit["term"],
                    "track": hit["track"],
                    "surface": hit["surface"],
                    "line": hit["line"],
                    "matched": hit["matched"],
                    "replacement": hit["replacement"],
                    "context": hit["context"],
                }
            )
    replacements = sorted(
        grouped.values(),
        key=lambda item: (item["surface"], item["track"], item["entry"], item["term"]),
    )
    return {
        "enabled": True,
        "strategy": lexicon.strategy,
        "lexicon": str(lexicon.path),
        "audio_reminder": AUDIO_REMINDER,
        "hit_count": sum(item["count"] for item in replacements),
        "replacements": replacements,
        "ambiguous_hits": ambiguous,
    }


def write_report(path: Path, report: dict) -> None:
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def format_report_log(report: dict) -> str:
    replacements = report.get("replacements") or []
    if not replacements:
        summary = "no lexicon matches"
    else:
        parts = []
        for item in replacements:
            track = item["track"] if item["surface"] == "caption" else item["surface"]
            parts.append(f"{item['entry']}:{track}×{item['count']}")
        summary = ", ".join(parts)
    ambiguous = report.get("ambiguous_hits") or []
    if not ambiguous:
        return f"Banned-term paraphrase: {summary}"
    preview = "; ".join(
        f"line {hit['line']} {hit['track']} {hit['context']}" for hit in ambiguous[:8]
    )
    extra = "" if len(ambiguous) <= 8 else f" (+{len(ambiguous) - 8} more)"
    return (
        f"Banned-term paraphrase: {summary}. "
        f"Ambiguous hits to review: {preview}{extra}"
    )


def report_path_for(output: Path) -> Path:
    return output.with_name(f"{output.stem}.banned-term-paraphrase.json")


def _compile_row(entry: object, index: int) -> tuple[_Rule, dict[str, str]]:
    if not isinstance(entry, dict):
        raise ValueError(f"Banned-term row {index} must be an object with term, zh, and en")
    unknown = set(entry) - _ROW_KEYS
    if unknown:
        raise ValueError(
            f"Banned-term row {index} only accepts term, zh, and en"
        )
    term = entry.get("term")
    if not isinstance(term, str) or not term.strip():
        raise ValueError(f"Banned-term row {index} needs term")
    term = term.strip()
    if _SINGLE_LATIN.fullmatch(term):
        raise ValueError(
            f"Banned-term row {index} is a single letter ({term}). "
            "Use a full name, such as X平台."
        )
    wording = {
        "zh": _clean_replacement(entry.get("zh"), term, "zh"),
        "en": _clean_replacement(entry.get("en"), term, "en"),
    }
    return (
        _Rule(
            term=term,
            pattern=_compile_pattern(term),
            length=len(re.sub(r"\s+", "", term)),
        ),
        wording,
    )


def _clean_replacement(value: object, term: str, track: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Banned term {term} needs {track}")
    wording = value.strip()
    if _MASK_REPLACEMENT.fullmatch(wording):
        raise ValueError(
            f"Banned term {term} {track} must be a paraphrase, not a mask"
        )
    return wording


def _compile_pattern(text: str) -> re.Pattern[str]:
    flags = re.IGNORECASE
    if _CJK.search(text):
        return re.compile(re.escape(text), flags)
    parts = [re.escape(part) for part in re.split(r"\s+", text) if part]
    body = r"\s*".join(parts)
    return re.compile(rf"(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_])", flags)


def _apply_track(text: str, lexicon: Lexicon, track: str) -> tuple[str, list[dict]]:
    if not text:
        return text, []
    pieces: list[str] = []
    hits: list[dict] = []
    index = 0
    while index < len(text):
        matched_rule: _Rule | None = None
        matched: re.Match[str] | None = None
        for rule in lexicon._rules:
            found = rule.pattern.match(text, index)
            if found is None:
                continue
            matched_rule = rule
            matched = found
            break
        if matched_rule is None or matched is None:
            pieces.append(text[index])
            index += 1
            continue
        if matched.end() <= index:
            raise RuntimeError(f"Banned-term pattern for {matched_rule.term} made no progress")
        replacement = lexicon.replacements[matched_rule.term][track]
        hits.append(
            {
                "entry": matched_rule.term,
                "term": matched_rule.term,
                "matched": matched.group(0),
                "replacement": replacement,
                "ambiguous": False,
                "context": _context(text, matched.start(), matched.end()),
            }
        )
        pieces.append(replacement)
        index = matched.end()
    return "".join(pieces), hits


def _decorate(hit: dict, *, surface: str, track: str, line: int) -> dict:
    return {**hit, "surface": surface, "track": track, "line": line}


def _context(text: str, start: int, end: int) -> str:
    left = max(0, start - 12)
    right = min(len(text), end + 12)
    return text[left:right]
