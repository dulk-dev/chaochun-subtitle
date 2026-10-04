#!/usr/bin/env python3
"""Fit letterbox captions to one visual line: time-split or shrink, never wrap."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from subtitle_text import add_cjk_spacing

_SUBTITLE_BOX_MAX_WIDTH_RATIO = 0.92
_SLIGHT_OVERFLOW_RATIO = 1.08
_MIN_EVENT_S = 0.45
_SHRINK_FLOOR = 0.75
_LATIN_ATOM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._'+-]*")
_WEAK_START_CHARS = {"的", "了", "着", "过", "们", "吗", "呢", "吧", "啊"}
_WEAK_START_WHITELIST = (
    "的确", "了解", "了不起", "着重", "着急", "着手", "着眼",
    "过程", "过去", "过后", "过来", "过于", "过年", "过度", "过滤",
)
_FINAL_PARTICLES = set("呢吧啊吗")


def visual_len(text: str) -> float:
    """Visual width estimate: CJK = 1.0, Latin/digits/punct = 0.55, space = 0.5."""
    width = 0.0
    for char in text:
        if (
            "\u4e00" <= char <= "\u9fff"
            or "\u3400" <= char <= "\u4dbf"
            or "\u3000" <= char <= "\u303f"
            or char == "…"
        ):
            width += 1.0
        elif char == " ":
            width += 0.5
        else:
            width += 0.55
    return width


def max_visual_units(video_width: int, font_size: int) -> int:
    """How many CJK-em units fit in the caption box at this font size."""
    pad_x = int(font_size * 0.28)
    usable_width = int(video_width * _SUBTITLE_BOX_MAX_WIDTH_RATIO) - pad_x * 2
    return max(4, int(usable_width / max(1.0, float(font_size))))


def collapse_english(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def english_words(text: str) -> list[str]:
    collapsed = collapse_english(text)
    return collapsed.split(" ") if collapsed else []


def join_caption_text(left: str, right: str) -> str:
    """Join two caption fragments without gluing Latin words together."""
    left = (left or "").strip()
    right = (right or "").strip()
    if not left:
        return right
    if not right:
        return left
    if re.search(r"[A-Za-z0-9]$", left) and re.search(r"^[A-Za-z0-9]", right):
        glued = f"{left} {right}"
    else:
        glued = f"{left}{right}"
    return add_cjk_spacing(glued).strip()


def is_weak_start(text: str) -> bool:
    text = (text or "").lstrip()
    if not text or text[0] not in _WEAK_START_CHARS:
        return False
    return not text.startswith(_WEAK_START_WHITELIST)


def peel_weak_prefix(text: str) -> tuple[str, str]:
    """Move a leading particle off the start of text. Returns (moved, rest)."""
    text = (text or "").strip()
    if not is_weak_start(text):
        return "", text
    return text[0], text[1:].lstrip()


def shrink_scale(width: float, cap: float) -> float:
    if cap <= 0 or width <= cap:
        return 1.0
    return max(_SHRINK_FLOOR, cap / width)


def align_split_english(english: str, zh_widths: list[float]) -> list[str] | None:
    """Split existing English on whitespace onto N Chinese pieces.

    Returns None when there are not enough English words for every piece.
    """
    words = english_words(english)
    count = len(zh_widths)
    if count <= 0:
        return []
    if count == 1:
        return [collapse_english(english)]
    if len(words) < count:
        return None
    total = sum(max(0.01, width) for width in zh_widths) or 1.0
    remaining = len(words)
    counts: list[int] = []
    for index, width in enumerate(zh_widths):
        left = count - index
        if index == count - 1:
            counts.append(remaining)
            break
        want = max(1, round(len(words) * (max(0.01, width) / total)))
        want = min(want, remaining - (left - 1))
        counts.append(want)
        remaining -= want
    fragments = []
    cursor = 0
    for take in counts:
        fragments.append(" ".join(words[cursor:cursor + take]))
        cursor += take
    return fragments


@dataclass
class _Atom:
    text: str
    start: int
    end: int


def _tokenize(text: str) -> list[_Atom]:
    atoms: list[_Atom] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        match = _LATIN_ATOM.match(text, index)
        if match:
            atoms.append(_Atom(match.group(0), match.start(), match.end()))
            index = match.end()
            continue
        atoms.append(_Atom(text[index], index, index + 1))
        index += 1
    return atoms


def _is_cjk(char: str) -> bool:
    return bool(char) and ("\u4e00" <= char[0] <= "\u9fff" or "\u3400" <= char[0] <= "\u4dbf")


def split_text_dp(text: str, cap: float) -> list[str]:
    """Split display text into the fewest pieces that each fit `cap`."""
    text = (text or "").strip()
    if not text:
        return []
    if visual_len(text) <= cap:
        return [text]
    atoms = _tokenize(text)
    if not atoms:
        return [text]
    count = len(atoms)
    estimate = max(1, math.ceil(visual_len(text) / max(cap, 1)))
    target = visual_len(text) / estimate
    inf = 10**12
    cost = [inf] * (count + 1)
    prev = [-1] * (count + 1)
    cost[0] = 0.0
    for end in range(1, count + 1):
        for start in range(0, end):
            piece = text[atoms[start].start:atoms[end - 1].end].strip()
            width = visual_len(piece)
            single = end - start == 1
            if width > cap and not single:
                continue
            score = cost[start] + 40.0
            score += abs(width - target) * 1.5
            min_piece = max(4.0, cap * 0.25)
            if width < min_piece and not single:
                score += (min_piece - width) * 2.0
            if is_weak_start(piece):
                score += 80.0
            if start > 0 and atoms[start - 1].text == "的" and piece and _is_cjk(piece[0]):
                score += 50.0
            if piece and piece[-1] in _FINAL_PARTICLES and end < count:
                score -= 25.0
            if width > cap:
                score += 200.0
            if score < cost[end]:
                cost[end] = score
                prev[end] = start
    if prev[count] < 0:
        return [text]
    pieces: list[str] = []
    cursor = count
    while cursor > 0:
        start = prev[cursor]
        pieces.append(text[atoms[start].start:atoms[cursor - 1].end].strip())
        cursor = start
    pieces.reverse()
    return [piece for piece in pieces if piece]


def allocate_time(start: float, end: float, pieces: list[str]) -> list[tuple[float, float, str]]:
    start = float(start)
    end = float(end)
    if end <= start:
        end = start + _MIN_EVENT_S
    if not pieces:
        return []
    weights = [max(1.0, visual_len(piece)) for piece in pieces]
    total = sum(weights)
    duration = end - start
    cursor = start
    result: list[tuple[float, float, str]] = []
    for index, (piece, weight) in enumerate(zip(pieces, weights)):
        nxt = end if index == len(pieces) - 1 else cursor + duration * (weight / total)
        if nxt <= cursor:
            nxt = cursor + 0.01
        result.append((cursor, nxt, piece))
        cursor = nxt
    last_start, _, last_text = result[-1]
    result[-1] = (last_start, end, last_text)
    return result


def _event(
    start: float,
    end: float,
    text: str,
    *,
    src_idxs: list[int],
    shrink: float = 1.0,
    english: str | None = None,
    skip_translate: bool = False,
) -> dict:
    item = {
        "start": float(start),
        "end": float(end),
        "text": text,
        "src_idxs": list(src_idxs),
        "shrink_scale": float(shrink),
    }
    if english:
        item["en"] = collapse_english(english)
    if skip_translate:
        item["skip_translate"] = True
    return item


def fit_cue(
    text: str,
    start: float,
    end: float,
    cap: float,
    *,
    existing_en: str | None = None,
    src_idxs: list[int] | None = None,
) -> list[dict]:
    """Fit one cue into one-or-more single-line events."""
    src_idxs = list(src_idxs or [])
    english = collapse_english(existing_en or "")
    text = (text or "").replace("\\N", "\n")
    hard_parts = [part.strip() for part in text.splitlines() if part.strip()]
    if len(hard_parts) > 1:
        timed = allocate_time(start, end, hard_parts)
        events: list[dict] = []
        for t0, t1, part in timed:
            events.extend(fit_cue(part, t0, t1, cap, existing_en=None, src_idxs=src_idxs))
        if english:
            fragments = align_split_english(english, [visual_len(item["text"]) for item in events])
            if fragments is None:
                for item in events:
                    item["skip_translate"] = True
            else:
                for item, fragment in zip(events, fragments):
                    if fragment:
                        item["en"] = fragment
                    else:
                        item["skip_translate"] = True
        return events

    display = hard_parts[0] if hard_parts else ""
    width = visual_len(display)
    if width <= cap:
        return [_event(start, end, display, src_idxs=src_idxs, english=english or None)]

    if width <= cap * _SLIGHT_OVERFLOW_RATIO:
        return [_event(
            start, end, display, src_idxs=src_idxs,
            shrink=shrink_scale(width, cap), english=english or None,
        )]

    pieces = split_text_dp(display, cap)
    if len(pieces) <= 1:
        return [_event(
            start, end, display, src_idxs=src_idxs,
            shrink=shrink_scale(width, cap), english=english or None,
        )]

    if english:
        fragments = align_split_english(english, [visual_len(piece) for piece in pieces])
        if fragments is None:
            return [_event(
                start, end, display, src_idxs=src_idxs,
                shrink=shrink_scale(width, cap), english=english,
            )]
    else:
        fragments = None

    timed = allocate_time(start, end, pieces)
    if any(t1 - t0 < _MIN_EVENT_S - 1e-9 for t0, t1, _ in timed):
        merged = _merge_short_timed(timed, cap)
        if any(t1 - t0 < _MIN_EVENT_S - 1e-9 for t0, t1, _ in merged):
            return [_event(
                start, end, display, src_idxs=src_idxs,
                shrink=shrink_scale(width, cap), english=english or None,
            )]
        timed = merged
        if english:
            fragments = align_split_english(english, [visual_len(piece) for _, _, piece in timed])
            if fragments is None:
                return [_event(
                    start, end, display, src_idxs=src_idxs,
                    shrink=shrink_scale(width, cap), english=english,
                )]

    events = []
    for index, (t0, t1, piece) in enumerate(timed):
        piece_width = visual_len(piece)
        item = _event(
            t0, t1, piece, src_idxs=src_idxs,
            shrink=shrink_scale(piece_width, cap) if piece_width > cap else 1.0,
        )
        if fragments:
            fragment = fragments[index] if index < len(fragments) else ""
            if fragment:
                item["en"] = fragment
            else:
                item["skip_translate"] = True
        events.append(item)
    return events


def _merge_short_timed(
    timed: list[tuple[float, float, str]], cap: float
) -> list[tuple[float, float, str]]:
    merged: list[tuple[float, float, str]] = []
    for t0, t1, piece in timed:
        if not merged:
            merged.append((t0, t1, piece))
            continue
        prev_t0, prev_t1, prev_text = merged[-1]
        duration = t1 - t0
        combined = join_caption_text(prev_text, piece)
        if duration < _MIN_EVENT_S and visual_len(combined) <= cap:
            merged[-1] = (prev_t0, t1, combined)
        else:
            merged.append((t0, t1, piece))
    return merged


def can_merge_english(left: dict, right: dict) -> bool:
    left_en = bool(collapse_english(str(left.get("en") or left.get("text_en") or "")))
    right_en = bool(collapse_english(str(right.get("en") or right.get("text_en") or "")))
    return left_en == right_en


def merge_src_idxs(left: dict, right: dict) -> list[int]:
    seen: list[int] = []
    for value in list(left.get("src_idxs") or []) + list(right.get("src_idxs") or []):
        if value not in seen:
            seen.append(int(value))
    return seen


def merge_english_text(left: dict, right: dict) -> str:
    parts = [
        collapse_english(str(left.get("en") or left.get("text_en") or "")),
        collapse_english(str(right.get("en") or right.get("text_en") or "")),
    ]
    return " ".join(part for part in parts if part)


def repair_weak_starts(lines: list[dict], cap: float) -> list[dict]:
    """Move a leading particle onto the previous cue when it still fits."""
    result = [dict(line) for line in lines]
    index = 0
    while index < len(result) - 1:
        current = result[index]
        nxt = result[index + 1]
        gap = float(nxt["start"]) - float(current["end"])
        if gap > 0.45:
            index += 1
            continue
        moved, rest = peel_weak_prefix(str(nxt.get("text") or ""))
        if not moved:
            index += 1
            continue
        combined = join_caption_text(str(current.get("text") or ""), moved)
        if visual_len(combined) <= cap:
            current["text"] = combined
            if rest:
                nxt["text"] = rest
                index += 1
                continue
            current["end"] = nxt["end"]
            current["src_idxs"] = merge_src_idxs(current, nxt)
            if merge_english_text(current, nxt):
                current["en"] = merge_english_text(current, nxt)
            del result[index + 1]
            continue
        pooled = join_caption_text(str(current.get("text") or ""), str(nxt.get("text") or ""))
        fitted = fit_cue(
            pooled,
            float(current["start"]),
            float(nxt["end"]),
            cap,
            existing_en=merge_english_text(current, nxt) or None,
            src_idxs=merge_src_idxs(current, nxt),
        )
        result[index:index + 2] = fitted
        index += len(fitted)
    return result


def attach_english_by_overlap(lines: list[dict], english_lines: list[dict]) -> list[dict]:
    """Attach English cues onto Chinese events by time overlap, then align-split."""
    result = [dict(line) for line in lines]
    if not english_lines:
        return result
    if len(english_lines) == len(result) and all(
        abs(float(zh["start"]) - float(en["start"])) <= 0.12
        for zh, en in zip(result, english_lines)
    ):
        for zh, english in zip(result, english_lines):
            zh["en"] = collapse_english(english.get("text") or "")
        return result

    assigned: dict[int, list[str]] = {index: [] for index in range(len(result))}
    for english in english_lines:
        en_text = collapse_english(english.get("text") or "")
        if not en_text:
            continue
        en_start = float(english["start"])
        en_end = float(english["end"])
        hits = [
            index for index, zh in enumerate(result)
            if float(zh["start"]) < en_end and en_start < float(zh["end"])
        ]
        if not hits:
            hits = [
                index for index, zh in enumerate(result)
                if abs(float(zh["start"]) - en_start) <= 0.12
            ]
        if not hits:
            continue
        widths = [visual_len(str(result[index].get("text") or "")) for index in hits]
        fragments = align_split_english(en_text, widths)
        if fragments is None:
            assigned[hits[0]].append(en_text)
            for index in hits[1:]:
                result[index]["skip_translate"] = True
        else:
            for index, fragment in zip(hits, fragments):
                if fragment:
                    assigned[index].append(fragment)
                else:
                    result[index]["skip_translate"] = True
    for index, parts in assigned.items():
        if parts:
            result[index]["en"] = " ".join(parts)
    return result


def plan_preview_overflow(segments: list[dict], cap: float) -> list[dict]:
    """Describe how each preview cue would be burned, without rewriting it."""
    items = []
    for index, segment in enumerate(segments):
        text = str(segment.get("zh") or segment.get("text") or "").strip()
        english = str(segment.get("en") or segment.get("text_en") or "").strip()
        fitted = fit_cue(
            text,
            float(segment.get("start") or 0.0),
            float(segment.get("end") or 0.0),
            cap,
            existing_en=english or None,
            src_idxs=[index],
        )
        items.append({
            "index": index,
            "will_split": len(fitted) > 1,
            "will_shrink": any(float(item.get("shrink_scale") or 1.0) < 1.0 for item in fitted),
            "piece_count": len(fitted),
            "pieces": [item["text"] for item in fitted],
        })
    return items
