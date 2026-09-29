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

_SCHEMA_VERSION = 1
_STRATEGY = "generic-paraphrase"
_TOP_KEYS = {"$schema", "version", "strategy", "description", "entries"}
_ENTRY_KEYS = {"id", "status", "note", "replace", "terms"}
_TERM_KEYS = {"text", "match", "case_sensitive", "ambiguous"}
_REPLACE_KEYS = {"zh", "en"}
_STATUSES = {"guess", "confirmed"}
_MATCH_MODES = {"phrase", "token"}
_MASK_REPLACEMENT = re.compile(r"^[\*＊★☆×.\-—_~\s]+$")
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


@dataclass(frozen=True)
class _Rule:
    entry_id: str
    term: str
    pattern: re.Pattern[str]
    ambiguous: bool
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
    if not isinstance(payload, dict):
        raise ValueError(f"Banned-term lexicon must be a JSON object: {source}")
    unknown = set(payload) - _TOP_KEYS
    if unknown:
        raise ValueError(f"Unknown banned-term lexicon fields: {', '.join(sorted(unknown))}")
    version = payload.get("version")
    if version != _SCHEMA_VERSION:
        raise ValueError(f"Banned-term lexicon version must be {_SCHEMA_VERSION}: {source}")
    strategy = payload.get("strategy", _STRATEGY)
    if strategy != _STRATEGY:
        raise ValueError(
            "Banned-term lexicon strategy must be generic-paraphrase: "
            f"{source}"
        )
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"Banned-term lexicon needs a non-empty entries array: {source}")

    replacements: dict[str, dict[str, str]] = {}
    rules: list[_Rule] = []
    seen_ids: set[str] = set()
    seen_terms: set[tuple[str, str, bool]] = set()
    for index, entry in enumerate(entries, 1):
        entry_id, replace, compiled = _compile_entry(entry, index, seen_terms)
        if entry_id in seen_ids:
            raise ValueError(f"Duplicate banned-term entry id: {entry_id}")
        seen_ids.add(entry_id)
        replacements[entry_id] = replace
        rules.extend(compiled)
    rules.sort(key=lambda rule: (-rule.length, rule.entry_id, rule.term))
    description = payload.get("description") or ""
    if not isinstance(description, str):
        raise ValueError("Banned-term lexicon description must be a string")
    return Lexicon(
        path=source,
        strategy=strategy,
        description=description,
        entry_count=len(entries),
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


def _compile_entry(
    entry: object,
    index: int,
    seen_terms: set[tuple[str, str, bool]],
) -> tuple[str, dict[str, str], list[_Rule]]:
    if not isinstance(entry, dict):
        raise ValueError(f"Banned-term entry {index} must be an object")
    unknown = set(entry) - _ENTRY_KEYS
    if unknown:
        raise ValueError(
            f"Unknown fields on banned-term entry {index}: {', '.join(sorted(unknown))}"
        )
    entry_id = str(entry.get("id") or "").strip()
    if not entry_id:
        raise ValueError(f"Banned-term entry {index} needs an id")
    status = entry.get("status", "guess")
    if status not in _STATUSES:
        raise ValueError(f"Banned-term entry {entry_id} status must be guess or confirmed")
    note = entry.get("note", "")
    if not isinstance(note, str):
        raise ValueError(f"Banned-term entry {entry_id} note must be a string")
    replace = entry.get("replace")
    if not isinstance(replace, dict):
        raise ValueError(f"Banned-term entry {entry_id} needs a replace object")
    extra_replace = set(replace) - _REPLACE_KEYS
    if extra_replace:
        raise ValueError(
            f"Banned-term entry {entry_id} replace only accepts zh and en"
        )
    wording = {
        "zh": _clean_replacement(replace.get("zh"), entry_id, "zh"),
        "en": _clean_replacement(replace.get("en"), entry_id, "en"),
    }
    terms = entry.get("terms")
    if not isinstance(terms, list) or not terms:
        raise ValueError(f"Banned-term entry {entry_id} needs at least one term")
    rules: list[_Rule] = []
    for term_index, spec in enumerate(terms, 1):
        text, match, case_sensitive, ambiguous = _term_spec(spec, entry_id, term_index)
        identity = (text if case_sensitive else text.casefold(), match, case_sensitive)
        if identity in seen_terms:
            raise ValueError(f"Duplicate banned term: {text}")
        seen_terms.add(identity)
        rules.append(
            _Rule(
                entry_id=entry_id,
                term=text,
                pattern=_compile_pattern(text, case_sensitive),
                ambiguous=ambiguous,
                length=len(re.sub(r"\s+", "", text)),
            )
        )
    return entry_id, wording, rules


def _term_spec(spec: object, entry_id: str, index: int) -> tuple[str, str, bool, bool]:
    if isinstance(spec, str):
        text = spec.strip()
        match = "phrase"
        case_sensitive = False
        ambiguous = False
        explicit_case = False
        explicit_ambiguous = False
    elif isinstance(spec, dict):
        unknown = set(spec) - _TERM_KEYS
        if unknown:
            raise ValueError(
                f"Unknown fields on {entry_id} term {index}: {', '.join(sorted(unknown))}"
            )
        text = str(spec.get("text") or "").strip()
        match = spec.get("match", "phrase")
        explicit_case = "case_sensitive" in spec
        explicit_ambiguous = "ambiguous" in spec
        case_sensitive = bool(spec.get("case_sensitive", False))
        ambiguous = bool(spec.get("ambiguous", False))
    else:
        raise ValueError(f"Banned-term entry {entry_id} term {index} is invalid")
    if not text:
        raise ValueError(f"Banned-term entry {entry_id} has an empty term")
    if match not in _MATCH_MODES:
        raise ValueError(f"Banned-term entry {entry_id} match must be phrase or token")
    if match == "token" and len(text) == 1 and not explicit_case:
        case_sensitive = True
    if match == "token" and len(text) == 1 and not explicit_ambiguous:
        ambiguous = True
    return text, match, case_sensitive, ambiguous


def _clean_replacement(value: object, entry_id: str, track: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Banned-term entry {entry_id} needs replace.{track}")
    wording = value.strip()
    if _MASK_REPLACEMENT.fullmatch(wording):
        raise ValueError(
            f"Banned-term entry {entry_id} replace.{track} must be a paraphrase, not a mask"
        )
    return wording


def _compile_pattern(text: str, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
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
        replacement = lexicon.replacements[matched_rule.entry_id][track]
        hits.append(
            {
                "entry": matched_rule.entry_id,
                "term": matched_rule.term,
                "matched": matched.group(0),
                "replacement": replacement,
                "ambiguous": matched_rule.ambiguous,
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
