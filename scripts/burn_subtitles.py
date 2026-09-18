#!/usr/bin/env python3
"""
Burn subtitles onto an exported video using ffmpeg.
Converts transcript.json (from local_transcribe.py or another ASR) → ASS subtitle file → burned video.

Default chaochun layout: letterbox (black bars), chapter progress in the top
bar, white bilingual captions (Chinese above English) in the bottom bar.
"""

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from subtitle_text import add_cjk_spacing
from user_config import resolve_glossary_path as resolve_user_glossary_path
from user_config import resolve_progress_enabled

_DISPLAY_REPLACEMENTS: list[tuple[re.Pattern, str]] = []
_SUBTITLE_BOX_MAX_WIDTH_RATIO = 0.92
_UPSTREAM_PROGRESS_FONT_1080P = 22
# Compact chapter strip: larger labels, tighter top pad than font*2.10 + 80px floor.
_PROGRESS_FONT_MIN = 36
_PROGRESS_FONT_HEIGHT_RATIO_LANDSCAPE = 0.040
_PROGRESS_FONT_HEIGHT_RATIO_PORTRAIT = 0.034
_PROGRESS_TOP_PAD_MIN = 52
_PROGRESS_TOP_PAD_FONT_RATIO = 1.36
_PROGRESS_LABEL_COLOUR = "&H00B4B4B4&"
_PROGRESS_FILL_COLOUR = "&H00C8C8C8&"
_PROGRESS_FILL_ALPHA = "&H8C&"
_CAPTION_PRIMARY_COLOUR = "&H00FFFFFF&"
# F3 bottom captions: larger Chinese, smaller English, packed toward the video.
_CAPTION_ZH_FONT_MIN = 40
_CAPTION_ZH_HEIGHT_RATIO_LANDSCAPE = 0.062
_CAPTION_ZH_HEIGHT_RATIO_PORTRAIT = 0.050
_CAPTION_EN_FONT_MIN = 20
_CAPTION_EN_FONT_RATIO = 0.48
_CAPTION_ZH_LINE_RATIO = 1.05
_CAPTION_EN_LINE_RATIO = 1.08
_CAPTION_STACK_GAP_RATIO = 0.04
_CAPTION_STACK_TOP_INSET_RATIO = 0.08
_CAPTION_BOTTOM_BREATHING_RATIO = 0.20
_CAPTION_BOTTOM_PAD_MIN = 72
_CAPTION_BOTTOM_PAD_MIN_BILINGUAL = 96
# Active long chapter titles: native ASS \move+\clip marquee (Scheme A).
_MARQUEE_HOLD_S = 1.0
_MARQUEE_PX_PER_S = 48.0
_MARQUEE_MIN_SCROLL_S = 1.25
_MARQUEE_MIN_EVENT_S = 0.03
_MARQUEE_WIDTH_SAFETY = 1.04
_DEFAULT_BEAUTY_STRENGTH_PERCENT = 10.0
_DEFAULT_BRIGHTEN_STRENGTH_PERCENT = 10.0
_BRIGHTEN_LAYER_BRIGHTNESS = 0.08
_BRIGHTEN_LAYER_GAMMA = 1.04
_FACE_SAMPLE_COUNT = 18


def log(msg):
    print(f"[burn-subtitles] {msg}", flush=True)


def resolve_ffmpeg() -> str:
    ffmpeg = os.environ.get("CHAOCHUN_SUBTITLE_FFMPEG", "ffmpeg")
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-filters"],
                             capture_output=True, text=True).stdout
    except FileNotFoundError:
        out = ""
    if " ass " not in out:
        sys.exit(
            f"❌ {ffmpeg} 缺少 libass（无 ass 滤镜）；若 ffmpeg 报 "
            f"\"No option name near '...ass'\"，是二进制问题而非路径转义。"
            f"请用 CHAOCHUN_SUBTITLE_FFMPEG 指定带 libass 的 ffmpeg 后重跑，"
            f"中间产物均可复用。"
        )
    return ffmpeg


def set_display_replacements(entries: list[dict]):
    """Configure case-insensitive text replacements applied to final captions.

    Patterns are whitespace-tolerant: spacing drifts at every stage (ASR
    tokens, CJK/Latin spacing), so "GPT55" must also match "GPT 55" and
    "cloud call" must match "cloudcall".
    """
    global _DISPLAY_REPLACEMENTS
    replacements = []
    for entry in entries:
        wrong = (entry.get("wrong") or "").strip()
        correct = entry.get("correct")
        if wrong and isinstance(correct, str):
            parts = [re.escape(ch) for ch in wrong if not ch.isspace()]
            replacements.append((re.compile(r"\s*".join(parts), re.IGNORECASE), correct))
    _DISPLAY_REPLACEMENTS = replacements


def resolve_glossary_path(override: str | None = None) -> Path | None:
    """Resolve the same personal glossary used by ASR and manual learning."""
    return resolve_user_glossary_path(override)


def load_user_glossary(override: str | None = None) -> list[dict]:
    """Load user-specific subtitle corrections from external configuration."""
    glossary_path = resolve_glossary_path(override)
    if glossary_path is None:
        return []
    if not glossary_path.exists():
        return []
    with open(glossary_path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _apply_display_replacements(text: str) -> str:
    for pattern, correct in _DISPLAY_REPLACEMENTS:
        text = pattern.sub(correct, text)
    return text


def _visual_len(text: str) -> float:
    """Visual width estimate: CJK = 1.0, Latin/digits/punct = 0.55, space = 0.5."""
    w = 0.0
    for c in text:
        if (
            '\u4e00' <= c <= '\u9fff'
            or '\u3400' <= c <= '\u4dbf'
            or '\u3000' <= c <= '\u303f'
            or c == '…'
        ):
            w += 1.0
        elif c == ' ':
            w += 0.5
        else:
            w += 0.55
    return w


def _split_text(text: str, max_chars: int) -> list[str]:
    """
    Split text into subtitle-sized chunks.
    Uses visual width (CJK=1.0, Latin=0.55) so mixed lines don't overflow.
    Tries to break at sentence-end punctuation first, then soft punctuation,
    then cuts at word boundaries as a last resort.
    """
    if _visual_len(text) <= max_chars:
        return [text]

    result = []

    def split_at(chunk: str, pattern: str) -> list[str]:
        parts = re.split(pattern, chunk)
        return [p.strip() for p in parts if p.strip()]

    # Pass 1: split at sentence-ending punctuation
    chunks = split_at(text, r'(?<=[。！？!?])\s*')
    if len(chunks) == 1:
        chunks = [text]  # no hard punct found

    # Pass 2: split oversized chunks at soft punctuation
    mid = []
    for c in chunks:
        if _visual_len(c) <= max_chars:
            mid.append(c)
        else:
            sub = split_at(c, r'(?<=[，,、；;])\s*')
            mid.extend(sub if len(sub) > 1 else [c])

    # Pass 3: cut at word boundaries, using visual width to find the split point
    for c in mid:
        while _visual_len(c) > max_chars:
            # Walk forward to find the last space whose prefix fits within max_chars
            cut_at = 0
            best_space = -1
            vw = 0.0
            for i, ch in enumerate(c):
                if ch == ' ' and vw <= max_chars:
                    best_space = i
                vw += _visual_len(ch)
                if vw > max_chars:
                    break
                cut_at = i + 1
            if best_space > len(c) // 4:
                cut_at = best_space
            # cut_at may be 0 if the very first char exceeds budget; force at least 1
            cut_at = max(cut_at, 1)
            result.append(c[:cut_at].rstrip())
            c = c[cut_at:].lstrip()
        if c:
            result.append(c)

    return result or [text]


def _timed_tokens_from_words(words: list[dict]) -> list[dict]:
    """Convert ASR word/character timestamps into timed phrase tokens."""
    units = []
    flat_text = ""
    char_to_unit = []
    for word in words:
        text = word.get("word") or ""
        if not text or word.get("start") is None or word.get("end") is None:
            continue
        unit_idx = len(units)
        units.append({
            "text": text,
            "start": word["start"],
            "end": word["end"],
        })
        flat_text += text
        char_to_unit.extend([unit_idx] * len(text))

    if not flat_text:
        return []

    try:
        import jieba
        jieba.setLogLevel(40)
        raw_tokens = list(jieba.cut(flat_text, HMM=False))
    except ImportError:
        raw_tokens = [u["text"] for u in units]

    tokens = []
    pos = 0
    for raw in raw_tokens:
        if raw == "":
            continue
        start_pos = pos
        end_pos = pos + len(raw)
        pos = end_pos

        visible_positions = [
            i for i in range(start_pos, end_pos)
            if i < len(char_to_unit) and not flat_text[i].isspace()
        ]
        if not visible_positions:
            if tokens:
                tokens[-1]["text"] += raw
            continue

        start_unit = units[char_to_unit[visible_positions[0]]]
        end_unit = units[char_to_unit[visible_positions[-1]]]
        tokens.append({
            "text": raw,
            "start": start_unit["start"],
            "end": end_unit["end"],
            "raw": raw,
        })

    return _merge_spelled_latin_tokens(tokens)


def _merge_spelled_latin_tokens(tokens: list[dict]) -> list[dict]:
    """Merge ASR output like h a t c h into one English token."""
    merged = []
    i = 0
    while i < len(tokens):
        text = tokens[i]["text"].strip()
        if re.fullmatch(r"[A-Za-z]", text):
            j = i
            letters = []
            while j < len(tokens) and re.fullmatch(r"[A-Za-z]", tokens[j]["text"].strip()):
                letters.append(tokens[j]["text"].strip())
                j += 1
            if len(letters) >= 2:
                merged.append({
                    "text": "".join(letters),
                    "start": tokens[i]["start"],
                    "end": tokens[j - 1]["end"],
                    "raw": "".join(letters),
                })
                i = j
                continue
        merged.append(tokens[i])
        i += 1
    return merged


_SOFT_PUNCT = "，,、；;：:"
_HARD_PUNCT = "。！？!?"
_DISPLAY_PUNCT = _SOFT_PUNCT + _HARD_PUNCT + "….—–"


def _strip_display_punctuation(text: str) -> str:
    """Remove punctuation that should guide timing but should not be burned in."""
    text = re.sub(rf"[{re.escape(_DISPLAY_PUNCT.replace('.', ''))}]", "", text)
    return re.sub(r"(?<!\d)\.(?!\d)", "", text)


def _tokens_text(tokens: list[dict]) -> str:
    text = "".join(t["text"] for t in tokens).strip()
    # ASR punctuation is useful for segmentation, but the final burned captions
    # should stay clean and rhythm-driven rather than showing sentence marks.
    text = _strip_display_punctuation(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = add_cjk_spacing(text)
    text = _apply_display_replacements(text)
    return add_cjk_spacing(text).strip()


def _make_line(tokens: list[dict]) -> dict | None:
    text = _tokens_text(tokens)
    if not text:
        return None
    return {
        "start": tokens[0]["start"],
        "end": tokens[-1]["end"],
        "text": text,
    }


def _token_text_for_len(token: dict) -> str:
    return add_cjk_spacing(_strip_display_punctuation(token["text"]))


def _line_duration(tokens: list[dict]) -> float:
    return max(0.0, tokens[-1]["end"] - tokens[0]["start"]) if tokens else 0.0


_WEAK_START_CHARS = {"的", "了", "着", "过", "们", "吗", "呢", "吧", "啊"}
_WEAK_START_WHITELIST = (
    "的确", "了解", "了不起", "着重", "着急", "着手", "着眼",
    "过程", "过去", "过后", "过来", "过于", "过年", "过度", "过滤",
)


def _is_function_particle(text: str) -> bool:
    """
    True when a subtitle must not START with this token: a grammatical particle
    gluing it to the previous phrase ("的一个…"). Tokenizers often attach 的/了
    to the following word, so check the first character, not just 1-char tokens.
    """
    if not text or text[0] not in _WEAK_START_CHARS:
        return False
    return not text.startswith(_WEAK_START_WHITELIST)


def _is_unbalanced_single_char(text: str) -> bool:
    """Avoid ending on a bare CJK character when there is a following token."""
    return len(text) == 1 and bool(re.fullmatch(r"[\u4e00-\u9fff]", text))


def _boundary_score(tokens: list[dict], idx: int, max_chars: int) -> float:
    """Score a boundary after tokens[idx]. Higher means more subtitle-like."""
    line = tokens[:idx + 1]
    text = _tokens_text(line)
    length = _visual_len(text)
    duration = _line_duration(line)
    prev = tokens[idx]
    next_token = tokens[idx + 1] if idx + 1 < len(tokens) else None
    prev_text = prev["text"].strip()
    next_text = (next_token["text"].strip() if next_token else "")
    raw_tail = prev_text[-1] if prev_text else ""
    gap = (next_token["start"] - prev["end"]) if next_token else 0.0

    target_chars = max_chars * 0.78
    target_duration = 2.4
    score = 0.0
    score -= abs(length - target_chars) * 1.5
    score -= abs(duration - target_duration) * 2.0

    if raw_tail in _HARD_PUNCT:
        score += 35
    if gap >= 0.35:
        score += 28
    elif gap >= 0.22:
        score += 18
    elif raw_tail in _SOFT_PUNCT:
        score += 10

    if length < max_chars * 0.42:
        score -= 35
    if duration < 1.0:
        score -= 35
    if length > max_chars:
        score -= 80 + (length - max_chars) * 12
    if duration > 4.2:
        score -= (duration - 4.2) * 18

    if _is_function_particle(next_text):
        score -= 80
    if next_token and _is_unbalanced_single_char(prev_text) and raw_tail not in _HARD_PUNCT + _SOFT_PUNCT:
        score -= 18
    # Never break between Latin/digit characters without punctuation or a real
    # pause — that splits an English word or product name across two subtitles.
    # "." counts as part of the run so version numbers ("GPT5" + "." + "5")
    # are not split either.
    if (
        next_token is not None
        and re.fullmatch(r"[A-Za-z0-9.]", (prev_text or " ")[-1])
        and re.fullmatch(r"[A-Za-z0-9.]", (next_text or " ")[0])
        and raw_tail not in _HARD_PUNCT + _SOFT_PUNCT
        and gap < 0.22
    ):
        score -= 250
    if raw_tail in _SOFT_PUNCT:
        score -= 6
    if next_token is None:
        score += 25
    return score


def _layout_timed_tokens(tokens: list[dict], max_chars: int) -> list[dict]:
    """Lay out phrase tokens into subtitle lines using timing and readability rules."""
    if not tokens:
        return []

    lines = []
    start = 0
    min_duration = 1.1
    max_duration = 3.8
    target_duration = 2.4
    target_chars = max_chars * 0.78

    while start < len(tokens):
        best_idx = start
        best_score = float("-inf")

        for idx in range(start, len(tokens)):
            chunk = tokens[start:idx + 1]
            length = _visual_len(_tokens_text(chunk))
            duration = _line_duration(chunk)

            if idx > start and (length > max_chars * 1.16 or duration > max_duration + 0.8):
                break

            score = _boundary_score(tokens[start:idx + 1] + tokens[idx + 1:idx + 2], len(chunk) - 1, max_chars)
            score -= abs(duration - target_duration) * 3.0
            score -= abs(length - target_chars) * 1.2

            if duration < min_duration and idx + 1 < len(tokens):
                score -= (min_duration - duration) * 40
            if duration > max_duration:
                score -= (duration - max_duration) * 35
            if length > max_chars:
                score -= (length - max_chars) * 25

            if score > best_score:
                best_idx = idx
                best_score = score

        line = _make_line(tokens[start:best_idx + 1])
        if line:
            lines.append(line)
        start = best_idx + 1

    return _filter_noise_lines(_merge_short_lines(_filter_noise_lines(lines), max_chars))


def _merge_short_lines(lines: list[dict], max_chars: int) -> list[dict]:
    """Merge subtitle fragments that are too short to read comfortably."""
    merged = []
    min_chars = max(8, max_chars * 0.38)
    min_duration = 0.95

    for line in lines:
        if not merged:
            merged.append(line)
            continue
        length = _visual_len(line["text"])
        duration = line["end"] - line["start"]
        prev = merged[-1]
        combined_text = (prev["text"] + line["text"]).strip()
        combined_len = _visual_len(combined_text)
        combined_dur = line["end"] - prev["start"]
        gap = line["start"] - prev["end"]

        should_merge = (
            (length < min_chars or duration < min_duration)
            and combined_len <= max_chars * 1.08
            and combined_dur <= 4.2
            and gap <= 0.45
        )

        if should_merge:
            prev["end"] = line["end"]
            prev["text"] = combined_text
        else:
            merged.append(line)

    return merged


def _filter_noise_lines(lines: list[dict]) -> list[dict]:
    """Remove standalone filler/noise subtitles that are not useful on screen."""
    result = []
    for line in lines:
        normalized = re.sub(r"[\s。！？!?，,、；;：:.…]+", "", line["text"]).lower()
        if normalized in {"嗯", "呃", "啊", "额", "em", "um"}:
            continue
        result.append(line)
    return result


def _display_normalized(text: str) -> str:
    """Normalize display text for comparing corrected segment text with ASR words.

    Keep case differences significant. Preview edits often only fix product-name
    casing, such as "skill" -> "Skill"; word-level ASR tokens must not override
    those confirmed display edits.
    """
    text = _apply_display_replacements(text)
    text = _strip_display_punctuation(text)
    return re.sub(r"\s+", "", text)


def _words_to_timed_lines(seg: dict, max_chars: int) -> list[dict]:
    """Build subtitle lines from phrase tokens with timestamps."""
    tokens = _timed_tokens_from_words(seg.get("words", []))
    if tokens and _display_normalized("".join(t["text"] for t in tokens)) != _display_normalized(seg.get("text", "")):
        return []
    return _layout_timed_tokens(tokens, max_chars)


def segments_to_lines(segments: list[dict], max_chars: int = 16) -> list[dict]:
    """
    Convert transcript segments to subtitle lines using segment-level text.

    - Uses seg["text"] directly, so text corrections apply immediately
      (no need to touch the word-level tokens at all)
    - Uses word/character timestamps when available, so local ASR long
      sentences become readable timed subtitle chunks
    - Falls back to segment boundaries for transcripts without word timestamps
    - Long segments are split at punctuation first, then hard-cut;
      timing within a segment is interpolated proportionally by character count
    """
    lines = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue

        timed_word_lines = _words_to_timed_lines(seg, max_chars)
        if timed_word_lines:
            lines.extend(timed_word_lines)
            continue

        # Apply CJK spacing before splitting so word-boundary detection can see
        # spaces at CJK/Latin boundaries, e.g. "这个Screen" becomes "这个 Screen".
        text = add_cjk_spacing(_apply_display_replacements(add_cjk_spacing(_strip_display_punctuation(text)))).strip()

        start = seg["start"]
        end = seg["end"]
        sub_lines = _split_text(text, max_chars)

        if len(sub_lines) == 1:
            lines.append({"start": start, "end": end, "text": sub_lines[0]})
        else:
            # Join as a single multi-line subtitle using \n (converted to \N in ASS)
            # This keeps the original segment timing intact and avoids time-splitting words
            lines.append({"start": start, "end": end, "text": "\n".join(sub_lines)})

    return _filter_noise_lines(_merge_short_lines(_filter_noise_lines(lines), max_chars))


def normalize_line_timing(lines: list[dict], min_gap: float = 0.02) -> list[dict]:
    """Keep subtitle events in order when ASR segment timestamps overlap slightly."""
    if not lines:
        return []
    normalized = []
    for line in lines:
        item = dict(line)
        if normalized and item["start"] < normalized[-1]["end"] + min_gap:
            prev = normalized[-1]
            target_prev_end = item["start"] - min_gap
            if target_prev_end > prev["start"] + 0.08:
                prev["end"] = target_prev_end
            else:
                item["start"] = prev["end"] + min_gap
        if item["end"] <= item["start"]:
            item["end"] = item["start"] + 0.45
        normalized.append(item)
    return normalized


def seconds_to_ass_time(s: float) -> str:
    """Convert seconds to ASS timestamp: H:MM:SS.cc"""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    cs = int((sec - int(sec)) * 100)
    return f"{h}:{m:02d}:{int(sec):02d}.{cs:02d}"


def final_display_text(text: str) -> str:
    """Apply the last display-only cleanup after line merging."""
    return _apply_display_replacements(add_cjk_spacing(_strip_display_punctuation(text)))


def _caption_font_name() -> str:
    """Prefer PingFang on macOS; fall back to a CJK-capable font elsewhere."""
    if sys.platform == "darwin":
        return "PingFang SC"
    return "WenQuanYi Micro Hei"


def letterbox_layout(
    content_width: int,
    content_height: int,
    *,
    bilingual: bool = True,
    progress: bool = False,
) -> dict:
    """Compute even letterbox pads and text anchors for the burned frame.

    Original picture stays unscaled in the middle. Chapter progress (when
    enabled) occupies a compact top black bar with larger directory labels;
    bilingual captions sit in the bottom black bar.
    """
    content_width = _even_dimension(content_width)
    content_height = _even_dimension(content_height)
    is_portrait = content_height > content_width
    zh_font = max(
        _CAPTION_ZH_FONT_MIN,
        int(
            content_height
            * (
                _CAPTION_ZH_HEIGHT_RATIO_PORTRAIT
                if is_portrait
                else _CAPTION_ZH_HEIGHT_RATIO_LANDSCAPE
            )
        ),
    )
    en_font = (
        max(_CAPTION_EN_FONT_MIN, int(round(zh_font * _CAPTION_EN_FONT_RATIO)))
        if bilingual
        else 0
    )
    progress_font = (
        max(
            _PROGRESS_FONT_MIN,
            int(
                content_height
                * (
                    _PROGRESS_FONT_HEIGHT_RATIO_PORTRAIT
                    if is_portrait
                    else _PROGRESS_FONT_HEIGHT_RATIO_LANDSCAPE
                )
            ),
        )
        if progress
        else 12
    )
    if progress:
        top_pad = _even_dimension(
            max(
                _PROGRESS_TOP_PAD_MIN,
                int(progress_font * _PROGRESS_TOP_PAD_FONT_RATIO),
            )
        )
    else:
        top_pad = _even_dimension(max(48, int(content_height * 0.044)))

    zh_line = int(zh_font * _CAPTION_ZH_LINE_RATIO)
    en_line = int(en_font * _CAPTION_EN_LINE_RATIO) if bilingual else 0
    stack_gap = int(zh_font * _CAPTION_STACK_GAP_RATIO) if bilingual else 0
    stack_top_inset = max(0, int(zh_font * _CAPTION_STACK_TOP_INSET_RATIO))
    bottom_pad = _even_dimension(
        max(
            _CAPTION_BOTTOM_PAD_MIN_BILINGUAL if bilingual else _CAPTION_BOTTOM_PAD_MIN,
            zh_line
            + en_line
            + stack_gap
            + stack_top_inset
            + int(zh_font * _CAPTION_BOTTOM_BREATHING_RATIO),
        )
    )
    canvas_height = content_height + top_pad + bottom_pad
    if canvas_height % 2:
        bottom_pad += 1
        canvas_height += 1

    progress_fill_height = top_pad if progress else 0
    progress_fill_y = 0
    progress_label_y = top_pad // 2

    stack_top = content_height + top_pad + stack_top_inset
    zh_y = stack_top + zh_line // 2
    en_y = stack_top + zh_line + stack_gap + max(en_line, 1) // 2 if bilingual else 0

    return {
        "content_width": content_width,
        "content_height": content_height,
        "top_pad": top_pad,
        "bottom_pad": bottom_pad,
        "canvas_width": content_width,
        "canvas_height": canvas_height,
        "zh_font": zh_font,
        "en_font": en_font,
        "progress_font": progress_font,
        "progress_fill_height": progress_fill_height,
        "progress_fill_y": progress_fill_y,
        "progress_label_y": progress_label_y,
        "stack_top_inset": stack_top_inset,
        "stack_gap": stack_gap,
        "zh_y": zh_y,
        "en_y": en_y,
        "zh_line_height": zh_line,
        "en_line_height": en_line,
        "bilingual": bilingual,
        "progress": progress,
    }


def preview_layout_payload(layout: dict) -> dict:
    """JSON for the web preview; keep fonts/bars in lockstep with burn geometry."""
    canvas_h = max(int(layout["canvas_height"]), 1)
    bottom = max(int(layout["bottom_pad"]), 1)
    return {
        "content_width": layout["content_width"],
        "content_height": layout["content_height"],
        "canvas_width": layout["canvas_width"],
        "canvas_height": layout["canvas_height"],
        "top_pad": layout["top_pad"],
        "bottom_pad": layout["bottom_pad"],
        "zh_font": layout["zh_font"],
        "en_font": layout["en_font"],
        "progress_font": layout["progress_font"],
        "stack_top_inset": layout.get("stack_top_inset", 0),
        "stack_gap": layout.get("stack_gap", 0),
        "zh_line_height": layout["zh_line_height"],
        "en_line_height": layout["en_line_height"],
        "bilingual": layout["bilingual"],
        "progress": layout["progress"],
        "top_frac": layout["top_pad"] / canvas_h,
        "bottom_frac": layout["bottom_pad"] / canvas_h,
        "zh_font_frac": layout["zh_font"] / canvas_h,
        "en_font_frac": layout["en_font"] / canvas_h,
        "progress_font_frac": layout["progress_font"] / canvas_h,
        "stack_top_inset_bar_frac": layout.get("stack_top_inset", 0) / bottom,
        "stack_gap_bar_frac": layout.get("stack_gap", 0) / bottom,
    }


def letterbox_pad_filter(layout: dict) -> str:
    """FFmpeg pad filter that extends the original frame with top/bottom bars."""
    return (
        f"pad={layout['canvas_width']}:{layout['canvas_height']}"
        f":0:{layout['top_pad']}:black"
    )


def _safe_max_chars_for_font(video_width: int, font_size: int) -> int:
    pad_x = int(font_size * 0.28)
    usable_width = int(video_width * _SUBTITLE_BOX_MAX_WIDTH_RATIO) - pad_x * 2
    char_width = font_size * 0.72
    return max(4, int(usable_width / char_width))


def _safe_max_chars_for_video(video_width: int, video_height: int) -> int:
    layout = letterbox_layout(video_width, video_height, bilingual=True)
    return _safe_max_chars_for_font(video_width, layout["zh_font"])


def _resolve_effective_max_chars(
    requested: int,
    video_width: int,
    video_height: int,
    square_output: bool,
    *,
    bilingual: bool = True,
) -> int:
    layout = letterbox_layout(video_width, video_height, bilingual=bilingual)
    safe = _safe_max_chars_for_font(video_width, layout["zh_font"])
    if requested > 0:
        return min(requested, safe)
    if square_output:
        return min(22, safe)
    return safe


def _wrap_display_text(text: str, max_chars: int, *, prefer_single_line: bool = True) -> str:
    text = final_display_text(text).strip()
    if max_chars <= 0 or not text:
        return text
    if prefer_single_line and _visual_len(text) <= max_chars:
        return text

    parts = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        if prefer_single_line and _visual_len(raw) <= max_chars:
            parts.append(raw)
        else:
            parts.extend(_split_text(raw, max_chars))
    return "\n".join(part for part in parts if part)


def _rect_path(width: int, height: int) -> str:
    """Return a simple ASS vector rectangle without anti-aliased seams."""
    width = max(1, int(width))
    height = max(1, int(height))
    return f"m 0 0 l {width} 0 l {width} {height} l 0 {height}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def load_progress_chapters(
    path: Path | None, duration: float, *, enabled: bool = True
) -> list[dict]:
    """Load broad progress chapters; videos at or below three minutes stay bar-free."""
    if not enabled or path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    threshold = float(payload.get("min_progress_duration", 180.0))
    if not payload.get("enabled") or duration <= threshold:
        return []
    raw_chapters = payload.get("chapters") or []
    if not 2 <= len(raw_chapters) <= 6:
        raise ValueError("Progress chapters must contain 2 to 6 broad sections")
    chapters = []
    for index, item in enumerate(raw_chapters):
        start = 0.0 if index == 0 else float(item.get("start", 0.0))
        end = float(item.get("end", duration))
        title = final_display_text(str(item.get("title") or "")).strip()
        if not title or start < 0 or end <= start or end > duration + 0.5:
            raise ValueError(f"Invalid progress chapter {index + 1}")
        if chapters and start < chapters[-1]["end"] - 0.05:
            raise ValueError("Progress chapters overlap")
        chapters.append({"title": title, "start": start, "end": end})
    chapters[-1]["end"] = duration
    return chapters


def normalize_chapter_title(title: str) -> str:
    """Collapse chapter titles onto a single visual line."""
    return re.sub(r"\s+", " ", str(title or "")).replace("\n", " ").strip()


def fit_chapter_title(title: str, max_visual: float) -> str:
    """Keep a chapter label on one line; ellipsize when the slot is too narrow.

    Inactive (and non-overflowing) labels stay ellipsized. When the chapter is
    the in-progress section and the full title exceeds the slot, ``_progress_events``
    chains ``\\move`` + ``\\clip`` dialogues instead of this truncated string.
    """
    title = normalize_chapter_title(title)
    if max_visual <= 0:
        return ""
    if _visual_len(title) <= max_visual:
        return title
    ellipsis = "…"
    ellipsis_w = _visual_len(ellipsis)
    if max_visual <= ellipsis_w:
        return ellipsis
    budget = max_visual - ellipsis_w
    cut = 0
    width = 0.0
    for index, char in enumerate(title):
        char_w = _visual_len(char)
        if width + char_w > budget:
            break
        width += char_w
        cut = index + 1
    if cut <= 0:
        return ellipsis
    return title[:cut].rstrip() + ellipsis


def chapter_slot_max_visual(slot_width_px: int, font_size: int) -> float:
    """Visual-width budget for a chapter title inside its progress slot.

    ASS CJK glyphs are about one em wide. A 0.72 em factor overflowed short
    slots, so the clip box cut through the label instead of showing a clean
    ellipsis inside the section.
    """
    pad = max(10, int(font_size * 0.40))
    usable = max(1, int(slot_width_px) - pad * 2)
    return usable / max(1.0, float(font_size))


def chapter_title_needs_scroll(title: str, slot_width_px: int, font_size: int) -> bool:
    """True when the full title exceeds the ellipsis budget for this slot."""
    title = normalize_chapter_title(title)
    return _visual_len(title) > chapter_slot_max_visual(slot_width_px, font_size)


def chapter_title_width_px(title: str, font_size: int) -> float:
    """Estimated rendered width; slight over-estimate so the last glyph stays in clip."""
    return _visual_len(normalize_chapter_title(title)) * float(font_size) * _MARQUEE_WIDTH_SAFETY


def chapter_label_clip_box(
    start_x: int, end_x: int, font_size: int, top_pad: int
) -> tuple[int, int, int, int]:
    clip_left = int(start_x) + max(2, int(font_size * 0.12))
    clip_right = max(clip_left + 1, int(end_x) - max(2, int(font_size * 0.12)))
    return clip_left, 0, clip_right, int(top_pad)


def chapter_marquee_travel(
    title: str, clip_left: int, clip_right: int, font_size: int
) -> tuple[int, int] | None:
    """Left-aligned ``\\an4`` start/end x for a slot-clipped marquee, or None if it fits."""
    text_w = chapter_title_width_px(title, font_size)
    clip_width = max(1, int(clip_right) - int(clip_left))
    if text_w <= clip_width:
        return None
    x_from = int(clip_left)
    x_to = int(round(clip_right - text_w))
    if x_to >= x_from:
        return None
    return x_from, x_to


def plan_chapter_title_marquee(
    start: float,
    end: float,
    x_from: int,
    x_to: int,
    y: int,
    *,
    hold_s: float = _MARQUEE_HOLD_S,
    px_per_s: float = _MARQUEE_PX_PER_S,
) -> list[dict]:
    """Chain hold/scroll segments that ping-pong a title through its clip slot.

    libass has no loop tag, so each segment is a separate Dialogue. A truncated
    last segment interpolates ``x2`` instead of squeezing a full sweep into the
    leftover duration. Short chapters drop extra holds and speed up so one full
    pass still fits when possible.
    """
    start = float(start)
    end = float(end)
    x_from = int(x_from)
    x_to = int(x_to)
    y = int(y)
    if end - start < _MARQUEE_MIN_EVENT_S:
        return []
    if x_from == x_to:
        return [{"start": start, "end": end, "x1": x_from, "x2": x_to, "y": y, "moving": False}]

    duration = end - start
    distance = abs(x_to - x_from)
    base_hold = max(0.0, float(hold_s))
    scroll_s = max(_MARQUEE_MIN_SCROLL_S, distance / max(float(px_per_s), 1.0))
    if duration < base_hold + scroll_s:
        base_hold = min(base_hold, max(0.0, duration * 0.15))
        remain = duration - base_hold
        if remain >= 0.35:
            scroll_s = remain
        else:
            base_hold = 0.0
            scroll_s = duration

    segments: list[dict] = []

    def emit(t0: float, t1: float, xa: int, xb: int) -> None:
        if t1 - t0 < _MARQUEE_MIN_EVENT_S:
            return
        if seconds_to_ass_time(t0) == seconds_to_ass_time(t1):
            return
        segments.append(
            {
                "start": t0,
                "end": t1,
                "x1": int(xa),
                "x2": int(xb),
                "y": y,
                "moving": int(xa) != int(xb),
            }
        )

    t = start
    pos = x_from
    target = x_to
    at_end = True
    full_dist = max(1, distance)
    max_steps = max(8, int(duration / _MARQUEE_MIN_EVENT_S) + 4)
    for _ in range(max_steps):
        remaining = end - t
        if remaining < _MARQUEE_MIN_EVENT_S:
            break
        if at_end and base_hold > 0:
            hold = min(base_hold, remaining)
            emit(t, t + hold, pos, pos)
            t += hold
            at_end = False
            continue
        remain_dist = abs(target - pos)
        needed = scroll_s * (remain_dist / full_dist) if remain_dist else 0.0
        if remain_dist < 1 or needed < _MARQUEE_MIN_EVENT_S:
            pos = target
            at_end = True
            target = x_from if target == x_to else x_to
            if remaining < max(base_hold, _MARQUEE_MIN_EVENT_S):
                emit(t, end, pos, pos)
                break
            continue
        this_scroll = min(needed, remaining)
        frac = this_scroll / needed
        new_pos = int(round(pos + (target - pos) * frac))
        emit(t, t + this_scroll, pos, new_pos)
        t += this_scroll
        pos = new_pos
        if frac >= 1.0 - 1e-6 or pos == target:
            pos = target
            at_end = True
            target = x_from if target == x_to else x_to
        else:
            at_end = False
            target = x_from if target == x_to else x_to
    if not segments:
        emit(start, end, x_from, x_to)
    return segments


def _progress_label_dialogue(
    start: float,
    end: float,
    text: str,
    *,
    clip: tuple[int, int, int, int],
    y: int,
    align: int,
    x: int | None = None,
    x2: int | None = None,
) -> str | None:
    if seconds_to_ass_time(start) == seconds_to_ass_time(end) or end <= start:
        return None
    clip_left, clip_top, clip_right, clip_bottom = clip
    tags = (
        f"\\an{align}\\q2\\clip({clip_left},{clip_top},{clip_right},{clip_bottom})"
    )
    if x2 is not None and x is not None and int(x) != int(x2):
        tags += f"\\move({int(x)},{int(y)},{int(x2)},{int(y)})"
    else:
        tags += f"\\pos({int(x if x is not None else 0)},{int(y)})"
    return (
        f"Dialogue: 3,{seconds_to_ass_time(start)},{seconds_to_ass_time(end)},"
        f"ProgressLabel,,0,0,0,,"
        f"{{{tags}}}{_ass_escape(text)}"
    )


def _progress_events(
    chapters: list[dict], layout: dict, duration: float
) -> tuple[list[str], int, int]:
    """Draw chapter directory and a translucent fill in one top-letterbox band."""
    if not chapters:
        return [], 0, layout.get("progress_font", 12)
    duration = max(duration, 0.01)
    video_width = layout["canvas_width"]
    fill_height = layout["progress_fill_height"] or layout["top_pad"]
    fill_y = layout["progress_fill_y"]
    label_y = layout["progress_label_y"]
    font_size = layout["progress_font"]
    top_pad = layout["top_pad"]
    events = []
    for tick in range(int(math.ceil(duration))):
        start = float(tick)
        end = min(duration, tick + 1.0)
        fill_width = max(1, int(video_width * end / duration))
        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(start)},{seconds_to_ass_time(end)},CaptionBox,ProgressFill,0,0,0,,"
            f"{{\\an7\\pos(0,{fill_y})\\p1\\1c{_PROGRESS_FILL_COLOUR}"
            f"\\1a{_PROGRESS_FILL_ALPHA}\\bord0\\shad0}}"
            f"{_rect_path(fill_width, fill_height)}"
        )
    separator_width = max(2, int(video_width * 0.001))
    marker_height = max(12, layout["top_pad"] - 8)
    marker_y = max(0, (layout["top_pad"] - marker_height) // 2)
    for chapter in chapters[1:]:
        x = int(video_width * float(chapter["start"]) / duration)
        events.append(
            f"Dialogue: 2,{seconds_to_ass_time(0)},{seconds_to_ass_time(duration)},CaptionBox,ProgressMarker,0,0,0,,"
            f"{{\\an7\\pos({x},{marker_y})\\p1\\1c&HB4B4B4&\\1a&H60&\\bord0\\shad0}}"
            f"{_rect_path(separator_width, marker_height)}"
        )
    for chapter in chapters:
        start_x = int(video_width * float(chapter["start"]) / duration)
        end_x = int(video_width * float(chapter["end"]) / duration)
        slot_width = max(1, end_x - start_x)
        center_x = (start_x + end_x) // 2
        full_title = normalize_chapter_title(str(chapter["title"]))
        max_visual = chapter_slot_max_visual(slot_width, font_size)
        fitted = fit_chapter_title(full_title, max_visual)
        clip = chapter_label_clip_box(start_x, end_x, font_size, top_pad)
        chapter_start = float(chapter["start"])
        chapter_end = min(float(chapter["end"]), duration)
        needs_scroll = chapter_title_needs_scroll(full_title, slot_width, font_size)

        def add_static(t0: float, t1: float, text: str, align: int = 5, x_pos: int | None = None) -> None:
            line = _progress_label_dialogue(
                t0,
                t1,
                text,
                clip=clip,
                y=label_y,
                align=align,
                x=center_x if x_pos is None else x_pos,
            )
            if line:
                events.append(line)

        if not needs_scroll:
            add_static(0.0, duration, fitted)
            continue

        if chapter_start > 0:
            add_static(0.0, chapter_start, fitted)
        if chapter_end < duration:
            add_static(chapter_end, duration, fitted)

        travel = chapter_marquee_travel(full_title, clip[0], clip[2], font_size)
        if travel is None:
            add_static(chapter_start, chapter_end, full_title)
            continue
        x_from, x_to = travel
        for segment in plan_chapter_title_marquee(
            chapter_start, chapter_end, x_from, x_to, label_y
        ):
            line = _progress_label_dialogue(
                segment["start"],
                segment["end"],
                full_title,
                clip=clip,
                y=label_y,
                align=4,
                x=segment["x1"],
                x2=segment["x2"] if segment["moving"] else None,
            )
            if line:
                events.append(line)
    return events, layout["top_pad"], font_size


def line_english_text(line: dict) -> str:
    return str(line.get("en") or line.get("text_en") or "").strip()


def merge_english_srt(lines: list[dict], english_lines: list[dict]) -> list[dict]:
    """Attach English captions onto Chinese lines by index, then by start time."""
    merged = [dict(line) for line in lines]
    if len(english_lines) == len(merged):
        for item, english in zip(merged, english_lines):
            item["en"] = english["text"]
        return merged
    remaining = list(english_lines)
    for item in merged:
        match_idx = next(
            (
                index
                for index, english in enumerate(remaining)
                if abs(float(english["start"]) - float(item["start"])) <= 0.12
            ),
            None,
        )
        if match_idx is None:
            continue
        item["en"] = remaining.pop(match_idx)["text"]
    return merged


def _translate_with_qwen(texts: list[str], *, model: str = "qwen-plus") -> list[str]:
    from dashscope_client import call_qwen_json

    translated: list[str] = []
    batch_size = 40
    for start in range(0, len(texts), batch_size):
        chunk = texts[start:start + batch_size]
        numbered = "\n".join(f"{index + 1}. {text}" for index, text in enumerate(chunk))
        payload, _usage = call_qwen_json(
            prompt=(
                "Translate these Chinese video captions into concise English.\n"
                "Keep the same count and order. Do not add commentary, quotes, or numbering.\n"
                "Preserve product names, abbreviations, and first-person voice.\n"
                'Return JSON: {"translations": ["...", "..."]}\n\n'
                f"CAPTIONS\n{numbered}"
            ),
            system="You translate Chinese video captions into English. Return strict JSON only.",
            model=model,
            max_tokens=8192,
            timeout=180,
        )
        batch = payload.get("translations") or []
        if not isinstance(batch, list) or len(batch) != len(chunk):
            raise RuntimeError(
                f"Bilingual translation returned {len(batch) if isinstance(batch, list) else 0} "
                f"lines for a batch of {len(chunk)}"
            )
        translated.extend(str(item or "").strip() for item in batch)
    return translated


def translate_caption_lines(
    lines: list[dict],
    *,
    translator=None,
) -> list[dict]:
    """Fill missing English text on subtitle lines. Existing `en` values are kept."""
    pending_indexes = [
        index for index, line in enumerate(lines) if not line_english_text(line)
    ]
    if not pending_indexes:
        return [dict(line) for line in lines]
    sources = [str(lines[index].get("text") or "").strip() for index in pending_indexes]
    translations = translator(sources) if translator else _translate_with_qwen(sources)
    if len(translations) != len(pending_indexes):
        raise RuntimeError("Bilingual translation count does not match Chinese captions")
    result = [dict(line) for line in lines]
    for index, english in zip(pending_indexes, translations):
        result[index]["en"] = str(english or "").strip()
    return result


def generate_ass(lines: list[dict], output_path: Path, video_width: int = 1920,
                 video_height: int = 1080, max_chars: int = 0,
                 preserve_text: bool = False, chapters: list[dict] | None = None,
                 duration: float = 0.0, bilingual: bool = False):
    """Generate letterbox ASS: top chapter progress, bottom Chinese/English captions."""

    chapters = chapters or []
    duration = duration or max((float(line["end"]) for line in lines), default=0.0)
    layout = letterbox_layout(
        video_width,
        video_height,
        bilingual=bilingual,
        progress=bool(chapters),
    )
    font_name = _caption_font_name()
    zh_max = max_chars if max_chars > 0 else _safe_max_chars_for_font(
        layout["canvas_width"], layout["zh_font"]
    )
    en_max = _safe_max_chars_for_font(layout["canvas_width"], layout["en_font"] or layout["zh_font"])
    progress_events, _progress_height, progress_font = _progress_events(
        chapters, layout, duration
    )

    ass_header = textwrap.dedent(f"""\
        [Script Info]
        ScriptType: v4.00+
        PlayResX: {layout['canvas_width']}
        PlayResY: {layout['canvas_height']}
        WrapStyle: 2
        ScaledBorderAndShadow: yes

        [V4+ Styles]
        Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
        Style: CaptionZh,{font_name},{layout['zh_font']},{_CAPTION_PRIMARY_COLOUR},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,20,20,0,1
        Style: CaptionEn,{font_name},{layout['en_font'] or max(_CAPTION_EN_FONT_MIN, int(round(layout['zh_font'] * _CAPTION_EN_FONT_RATIO)))},{_CAPTION_PRIMARY_COLOUR},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,20,20,0,1
        Style: CaptionBox,Arial,10,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
        Style: ProgressLabel,{font_name},{progress_font},{_PROGRESS_LABEL_COLOUR},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

        [Events]
        Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
    """)

    event_lines = list(progress_events)
    center_x = layout["canvas_width"] // 2
    for line in lines:
        start = seconds_to_ass_time(line["start"])
        end = seconds_to_ass_time(line["end"])
        zh_source = line["text"].strip() if preserve_text else _wrap_display_text(line["text"], zh_max)
        zh_display = _ass_escape(zh_source).replace("\n", r"\N")
        event_lines.append(
            f"Dialogue: 5,{start},{end},CaptionZh,,0,0,0,,"
            f"{{\\an5\\pos({center_x},{layout['zh_y']})}}{zh_display}"
        )
        english = line_english_text(line)
        if bilingual and english:
            en_source = english if preserve_text else _wrap_display_text(english, en_max)
            en_display = _ass_escape(en_source).replace("\n", r"\N")
            event_lines.append(
                f"Dialogue: 5,{start},{end},CaptionEn,,0,0,0,,"
                f"{{\\an5\\pos({center_x},{layout['en_y']})}}{en_display}"
            )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        f.write("\n".join(event_lines))
        f.write("\n")

    log(f"✅ Generated ASS subtitle file: {output_path.name} ({len(event_lines)} lines)")


def seconds_to_srt_time(s: float) -> str:
    """Convert seconds to SRT timestamp: HH:MM:SS,mmm"""
    ms_total = int(round(s * 1000))
    h = ms_total // 3_600_000
    ms_total %= 3_600_000
    m = ms_total // 60_000
    ms_total %= 60_000
    sec = ms_total // 1000
    ms = ms_total % 1000
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def write_subtitle_draft(lines: list[dict], output_path: Path, max_chars: int = 0,
                         preserve_text: bool = False):
    """Write a plain SRT draft for human review before burning."""
    with open(output_path, "w", encoding="utf-8") as f:
        for idx, line in enumerate(lines, 1):
            f.write(f"{idx}\n")
            f.write(f"{seconds_to_srt_time(line['start'])} --> {seconds_to_srt_time(line['end'])}\n")
            text = line["text"] if preserve_text else _wrap_display_text(line["text"], max_chars)
            f.write(text)
            f.write("\n\n")
    log(f"🧾 Wrote subtitle draft: {output_path} ({len(lines)} lines)")


def srt_time_to_seconds(value: str) -> float:
    """Convert SRT timestamp HH:MM:SS,mmm to seconds."""
    hms, ms = value.strip().split(",")
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def read_srt_lines(input_path: Path) -> list[dict]:
    """Read reviewed SRT captions without changing text, line breaks, or timing."""
    content = input_path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError("Reviewed SRT is empty")
    lines = []
    previous_end = None
    for block_number, block in enumerate(re.split(r"\n\s*\n", content), 1):
        rows = block.splitlines()
        if len(rows) < 3 or "-->" not in rows[1]:
            raise ValueError(f"Invalid reviewed SRT block {block_number}")
        start_raw, end_raw = [p.strip() for p in rows[1].split("-->", 1)]
        text = "\n".join(rows[2:]).strip()
        if not text:
            raise ValueError(f"Empty reviewed SRT caption {block_number}")
        start = srt_time_to_seconds(start_raw)
        end = srt_time_to_seconds(end_raw)
        if end <= start:
            raise ValueError(f"Non-positive reviewed SRT timing at block {block_number}")
        if previous_end is not None and start < previous_end:
            raise ValueError(f"Overlapping reviewed SRT timing at block {block_number}")
        lines.append({"start": start, "end": end, "text": text})
        previous_end = end
    return lines


def wrap_reviewed_lines(lines: list[dict], max_chars: int) -> list[dict]:
    """Wrap reviewed SRT text for the target video shape while keeping timings."""
    wrapped = []
    for line in lines:
        item = dict(line)
        parts = []
        for raw in item["text"].splitlines():
            parts.extend(_split_text(raw.strip(), max_chars))
        item["text"] = "\n".join(p for p in parts if p)
        wrapped.append(item)
    return wrapped


def _render_progress(elapsed_us: int, total_s: float, speed: float):
    """Print a single-line progress bar, overwriting the previous one."""
    elapsed_s = elapsed_us / 1_000_000
    pct = min(elapsed_s / total_s, 1.0) if total_s > 0 else 0
    filled = int(pct * 20)
    bar = "█" * filled + "░" * (20 - filled)
    elapsed_fmt = f"{int(elapsed_s // 60)}:{int(elapsed_s % 60):02d}"
    total_fmt   = f"{int(total_s   // 60)}:{int(total_s   % 60):02d}"
    speed_str   = f"{speed:.1f}x" if speed > 0 else "..."
    sys.stdout.write(f"\r  {bar}  {pct:>3.0%}  {elapsed_fmt} / {total_fmt}  {speed_str}  ")
    sys.stdout.flush()


def _even_dimension(value: float, minimum: int = 2) -> int:
    """Round down to an even dimension accepted by common video encoders."""
    result = max(minimum, int(value))
    return result if result % 2 == 0 else result - 1


def _percentile(values: list[float], fraction: float) -> float:
    """Return a simple interpolated percentile without a third-party dependency."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Cannot calculate a percentile of an empty list")
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def derive_camera_region(
    detections: list[dict],
    video_width: int,
    video_height: int,
    sample_count: int,
) -> tuple[int, int, int, int] | None:
    """Find the stable face cluster and turn it into a padded FFmpeg crop.

    Screen content can contain faces too. The camera face is identified by
    persistence at one location across sampled frames, not by an assumed corner.
    """
    valid = []
    for item in detections:
        try:
            width = float(item["width"])
            height = float(item["height"])
            x = float(item["x"])
            y = float(item["y"])
            sample = int(item["sample"])
            confidence = float(item.get("confidence", 1.0))
        except (KeyError, TypeError, ValueError):
            continue
        if (
            confidence < 0.35
            or width < 0.025
            or height < 0.025
            or width > 0.75
            or height > 0.75
            or x < 0
            or y < 0
            or x + width > 1.01
            or y + height > 1.01
        ):
            continue
        valid.append({
            "sample": sample,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "cx": x + width / 2,
            "cy": y + height / 2,
        })

    if not valid or sample_count <= 0:
        return None

    candidates = []
    for anchor in valid:
        radius = max(0.075, min(0.16, max(anchor["width"], anchor["height"]) * 0.75))
        nearby = [
            item for item in valid
            if ((item["cx"] - anchor["cx"]) ** 2 + (item["cy"] - anchor["cy"]) ** 2) ** 0.5
            <= radius
        ]
        unique_samples = len({item["sample"] for item in nearby})
        if unique_samples < max(2, int(sample_count * 0.25 + 0.999)):
            continue
        center_spread = statistics.median(
            ((item["cx"] - anchor["cx"]) ** 2 + (item["cy"] - anchor["cy"]) ** 2) ** 0.5
            for item in nearby
        )
        median_area = statistics.median(item["width"] * item["height"] for item in nearby)
        edge_distance = min(anchor["cx"], 1 - anchor["cx"], anchor["cy"], 1 - anchor["cy"])
        candidates.append((unique_samples, -center_spread, -edge_distance, median_area, nearby))

    if not candidates:
        return None

    selected = max(candidates, key=lambda item: item[:4])[-1]

    # Pad around the face so small head movement stays inside the processed area.
    lefts = [(item["x"] - item["width"] * 0.55) * video_width for item in selected]
    rights = [
        (item["x"] + item["width"] * 1.55) * video_width for item in selected
    ]
    tops = [(item["y"] - item["height"] * 0.45) * video_height for item in selected]
    bottoms = [
        (item["y"] + item["height"] * 1.45) * video_height for item in selected
    ]
    left = _percentile(lefts, 0.10)
    right = _percentile(rights, 0.90)
    top = _percentile(tops, 0.10)
    bottom = _percentile(bottoms, 0.90)

    short_side = min(video_width, video_height)
    side = max(right - left, bottom - top, short_side * 0.16)
    side = min(side, short_side * 0.48)
    side = _even_dimension(side, minimum=64)
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    x = int(round(center_x - side / 2))
    y = int(round(center_y - side / 2))
    x = max(0, min(x, video_width - side))
    y = max(0, min(y, video_height - side))
    x -= x % 2
    y -= y % 2
    return x, y, side, side


def _vision_detector_binary() -> Path:
    """Compile the bundled macOS Vision helper once into the temporary cache."""
    source = Path(__file__).with_name("detect_face_regions.swift")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    binary = Path(tempfile.gettempdir()) / f"screen-studio-face-detector-{digest}"
    if binary.exists():
        return binary
    temporary = binary.with_name(f"{binary.name}-{os.getpid()}.tmp")
    command = [
        "/usr/bin/xcrun", "swiftc", str(source), "-O", "-o", str(temporary),
        "-framework", "AVFoundation", "-framework", "Vision",
        "-framework", "CoreGraphics",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not compile Vision detector")
    os.replace(temporary, binary)
    return binary


def detect_camera_region(
    video_path: Path,
    video_width: int,
    video_height: int,
) -> tuple[int, int, int, int] | None:
    """Sample a video with macOS Vision and return its persistent camera region."""
    if sys.platform != "darwin":
        log("Automatic camera detection requires macOS; beauty skipped")
        return None
    try:
        detector = _vision_detector_binary()
        result = subprocess.run(
            [str(detector), str(video_path), str(_FACE_SAMPLE_COUNT)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Vision detector failed")
        payload = json.loads(result.stdout)
        return derive_camera_region(
            payload.get("detections", []),
            video_width,
            video_height,
            int(payload.get("sampleCount", _FACE_SAMPLE_COUNT)),
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        log(f"Automatic camera detection failed; beauty skipped: {exc}")
        return None


def build_beauty_filter_graph(
    ass_filter: str,
    video_width: int,
    video_height: int,
    camera_region: tuple[int, int, int, int],
    smoothing_strength: float = _DEFAULT_BEAUTY_STRENGTH_PERCENT / 100,
    brighten_strength: float = _DEFAULT_BRIGHTEN_STRENGTH_PERCENT / 100,
    filter_prefix: list[str] | None = None,
    scale_to: tuple[int, int] | None = None,
    pad_filter: str | None = None,
) -> tuple[str, str]:
    """Build one FFmpeg graph for light camera beauty plus subtitles.

    Both strengths are normalized to 0..1. The original camera region remains
    the dominant layer: the defaults mix in 10% edge-preserving smoothing and
    10% of a conservatively brightened layer. Letterbox padding and subtitles
    are rendered last so their edges are never softened or brightened.
    """
    if not 0 <= smoothing_strength <= 1:
        raise ValueError("Smoothing strength must be in the range [0, 1]")
    if not 0 <= brighten_strength <= 1:
        raise ValueError("Brighten strength must be in the range [0, 1]")
    if smoothing_strength == 0 and brighten_strength == 0:
        raise ValueError("At least one camera beauty effect must be enabled")

    x, y, width, height = camera_region
    if width <= 0 or height <= 0 or x < 0 or y < 0:
        raise ValueError("Camera region must contain positive in-frame dimensions")
    if x + width > video_width or y + height > video_height:
        raise ValueError("Camera region must fit inside the video frame")
    smoothing_opacity = f"{smoothing_strength:.4f}".rstrip("0").rstrip(".")
    brighten_opacity = f"{brighten_strength:.4f}".rstrip("0").rstrip(".")
    graph_parts = [
        "[0:v]split=2[beauty_base][beauty_region_source]",
        f"[beauty_region_source]crop={width}:{height}:{x}:{y}[camera_region]",
    ]

    if smoothing_strength > 0:
        graph_parts.extend([
            "[camera_region]split=2[beauty_original][beauty_smooth_source]",
            (
                "[beauty_smooth_source]"
                "bilateral=sigmaS=2.5:sigmaR=0.04:planes=1[beauty_smooth]"
            ),
            (
                "[beauty_smooth][beauty_original]"
                f"blend=all_mode=normal:all_opacity={smoothing_opacity}"
                "[smoothed_region]"
            ),
        ])
    else:
        graph_parts.append("[camera_region]null[smoothed_region]")

    if brighten_strength > 0:
        graph_parts.extend([
            "[smoothed_region]split=2[brightness_base][brightness_source]",
            (
                "[brightness_source]"
                f"eq=brightness={_BRIGHTEN_LAYER_BRIGHTNESS}:"
                f"gamma={_BRIGHTEN_LAYER_GAMMA}[brightness_lifted]"
            ),
            (
                "[brightness_lifted][brightness_base]"
                f"blend=all_mode=normal:all_opacity={brighten_opacity}"
                "[beauty_region]"
            ),
        ])
    else:
        graph_parts.append("[smoothed_region]null[beauty_region]")

    graph_parts.append(
        f"[beauty_base][beauty_region]overlay={x}:{y}[beautified]"
    )

    final_filters = list(filter_prefix or [])
    if scale_to:
        final_filters.append(f"scale={scale_to[0]}:{scale_to[1]}")
    if pad_filter:
        final_filters.append(pad_filter)
    final_filters.append(ass_filter)
    graph_parts.append(f"[beautified]{','.join(final_filters)}[video_out]")
    return ";".join(graph_parts), "[video_out]"


def burn_subtitles(video_path: Path, ass_path: Path, output_path: Path,
                   scale_to: tuple[int, int] | None = None,
                   filter_prefix: list[str] | None = None,
                   total_duration_s: float = 0,
                   encoder: str = "x264",
                   video_size: tuple[int, int] | None = None,
                   camera_region: tuple[int, int, int, int] | None = None,
                   pad_filter: str | None = None):
    """Burn ASS subtitles into video using ffmpeg.

    scale_to: (width, height) to scale before rendering subtitles.
    total_duration_s: video duration for progress display.

    Rotation is handled automatically by ffmpeg's built-in autorotate. It both physically
    corrects the frame orientation AND clears the Display Matrix in the output, so no manual
    transpose filter or metadata patching is needed.
    """
    log(f"Burning subtitles into video...")

    # Escape special chars in path for ffmpeg filter
    ass_str = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

    ass_filter = f"ass='{ass_str}'"
    vf_parts = list(filter_prefix or [])
    if scale_to:
        w, h = scale_to
        vf_parts.append(f"scale={w}:{h}")
    if pad_filter:
        vf_parts.append(pad_filter)
    vf_parts.append(ass_filter)
    vf = ",".join(vf_parts)

    beauty_enabled = camera_region is not None
    if beauty_enabled and not video_size:
        raise ValueError("video_size is required when beauty smoothing is enabled")
    if beauty_enabled:
        graph, video_map = build_beauty_filter_graph(
            ass_filter,
            video_size[0],
            video_size[1],
            camera_region,
            filter_prefix=filter_prefix,
            scale_to=scale_to,
            pad_filter=pad_filter,
        )
        x, y, width, height = camera_region
        log(
            f"Light beauty enabled: {_DEFAULT_BEAUTY_STRENGTH_PERCENT:g}% smoothing, "
            f"{_DEFAULT_BRIGHTEN_STRENGTH_PERCENT:g}% brightening "
            f"in detected camera region {width}x{height}+{x}+{y}"
        )
    else:
        graph, video_map = "", ""
        log("Beauty smoothing disabled")
        if pad_filter:
            log("Letterbox padding enabled")

    if scale_to:
        log(f"Scaling to {scale_to[0]}x{scale_to[1]}")
    if encoder == "videotoolbox":
        if scale_to or filter_prefix or beauty_enabled or pad_filter:
            # H264 for filtered square/downscaled output (smaller file)
            video_codec = ["-c:v", "h264_videotoolbox", "-b:v", "8M"]
        else:
            # HEVC quality-based encoding — matches original iPhone/Screen Studio quality
            # -q:v 65 on hevc_videotoolbox ≈ visually lossless for 4K source
            # -tag:v hvc1 ensures broad player compatibility (hev1 tag breaks QPlayer etc.)
            video_codec = ["-c:v", "hevc_videotoolbox", "-q:v", "65", "-tag:v", "hvc1"]
        log("VideoToolbox hardware encoding")
    else:
        # Software x264 outruns the M-series media engine on wall clock (~2.3x on
        # M5 at 2880x2160) with equal-or-better SSIM and smaller files, and H264
        # is what video platforms prefer for uploads. veryfast+crf20 is the
        # measured sweet spot; the media engine is a fixed-throughput bottleneck
        # that parallel sessions and quality settings cannot speed up.
        video_codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                       "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        log("x264 software encoding (veryfast, crf 20)")

    progress_path = Path("/tmp/ffmpeg_burn_progress.txt")
    progress_path.unlink(missing_ok=True)

    cmd = [os.environ.get("CHAOCHUN_SUBTITLE_FFMPEG", "ffmpeg"), "-i", str(video_path)]
    if beauty_enabled:
        cmd.extend([
            "-filter_complex", graph,
            "-map", video_map,
            "-map", "0:a?",
        ])
    else:
        cmd.extend(["-vf", vf])
    cmd.extend([
        "-c:a", "copy",
        *video_codec,
        "-progress", str(progress_path),
        "-loglevel", "error",
        str(output_path),
        "-y",
    ])

    import time
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)

    while proc.poll() is None:
        time.sleep(0.5)
        if not progress_path.exists():
            continue
        data = {}
        for line in progress_path.read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip()
        raw_us = data.get("out_time_us", "0")
        elapsed_us = int(raw_us) if raw_us and raw_us.lstrip("-").isdigit() else 0
        speed_str = data.get("speed", "0x").replace("x", "")
        try:
            speed = float(speed_str)
        except ValueError:
            speed = 0.0
        _render_progress(elapsed_us, total_duration_s, speed)

    sys.stdout.write("\n")

    if proc.returncode != 0:
        err = (proc.stderr.read() if proc.stderr else "")
        raise RuntimeError(f"ffmpeg failed:\n{err[-1000:]}")

    size_mb = output_path.stat().st_size / 1024 / 1024
    log(f"Output: {output_path.name} ({size_mb:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description="Burn subtitles onto exported Screen Studio video")
    parser.add_argument("--video", required=True, help="Path to exported video file (.mp4)")
    parser.add_argument("--transcript", default=None, help="Path to transcript.json from local_transcribe.py or another ASR")
    parser.add_argument("--srt-input", default=None, help="Burn a reviewed SRT directly without re-laying out transcript text")
    parser.add_argument("--output", default=None, help="Output video path (default: input_subtitled.mp4)")
    parser.add_argument("--glossary", default=None,
                        help="Glossary JSON path; overrides environment and user config")
    parser.add_argument(
        "--chapters",
        default=None,
        help="Optional broad chapter JSON; rendered only when video duration is over three minutes.",
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable chapter progress when chapters are available (default: enabled). "
            "Use --no-progress to hide it for this run."
        ),
    )
    parser.add_argument("--max-chars", type=int, default=0,
                        help="Max visual chars per subtitle line. Default 0 = fill the letterbox width as a single line.")
    parser.add_argument(
        "--bilingual",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Burn Chinese and English together (default: enabled). "
            "Use --no-bilingual for Chinese-only captions."
        ),
    )
    parser.add_argument(
        "--en-srt",
        default=None,
        help="Optional English SRT aligned to the Chinese captions. If omitted, Qwen translates before burn.",
    )
    parser.add_argument("--draft-output", default=None, help="Write an SRT draft for review")
    parser.add_argument("--draft-only", action="store_true", help="Only write the draft; do not generate ASS or burn")
    parser.add_argument("--ass-only", action="store_true", help="Only generate .ass file, don't burn")
    parser.add_argument("--square-output", action="store_true",
                        help="Crop the video to a centered 1:1 square before rendering subtitles.")
    parser.add_argument("--output-height", type=int, default=0,
                        help="Scale output: landscape uses height, portrait uses width, square-output uses side length. Default 0 = keep/crop at source size.")
    parser.add_argument("--encoder", choices=["x264", "videotoolbox"], default="x264",
                        help="Video encoder. Default x264 (software, ~2.3x faster than the "
                             "media engine on Apple Silicon with equal quality). Use "
                             "videotoolbox for the previous hardware HEVC/H264 behavior.")
    parser.add_argument(
        "--no-beauty",
        action="store_true",
        help="Disable automatic face detection and the default camera beauty pass.",
    )
    args = parser.parse_args()

    try:
        progress_requested = resolve_progress_enabled(args.progress)
    except RuntimeError as exc:
        print(f"❌ Invalid progress setting: {exc}")
        sys.exit(1)

    video_path = Path(args.video)
    transcript_path = Path(args.transcript) if args.transcript else None
    srt_input_path = Path(args.srt_input) if args.srt_input else None
    chapters_path = Path(args.chapters) if args.chapters else None

    if not video_path.exists():
        print(f"❌ Video not found: {video_path}")
        sys.exit(1)
    if not transcript_path and not srt_input_path:
        print("❌ Provide either --transcript or --srt-input")
        sys.exit(1)
    if transcript_path and not transcript_path.exists():
        print(f"❌ Transcript not found: {transcript_path}")
        sys.exit(1)
    if srt_input_path and not srt_input_path.exists():
        print(f"❌ SRT not found: {srt_input_path}")
        sys.exit(1)
    if progress_requested and chapters_path and not chapters_path.exists():
        print(f"❌ Chapters not found: {chapters_path}")
        sys.exit(1)

    if not (args.draft_only or args.ass_only):
        resolve_ffmpeg()

    # Output paths
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = video_path.with_name(video_path.stem + "_subtitled.mp4")
    ass_path = output_path.with_suffix(".ass")

    glossary = load_user_glossary(args.glossary) if not srt_input_path else []
    if glossary:
        set_display_replacements(glossary)
        log(f"📚 Loaded {len(glossary)} glossary replacements")

    # Get video dimensions
    probe = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", str(video_path),
        ],
        capture_output=True, text=True
    )
    video_w, video_h, video_duration, video_rotation = 1920, 1080, 0.0, 0
    if probe.returncode == 0:
        for stream in json.loads(probe.stdout).get("streams", []):
            if stream.get("codec_type") == "video":
                video_w = stream.get("width", 1920)
                video_h = stream.get("height", 1080)
                video_duration = float(stream.get("duration", 0) or 0)
                # Detect rotation metadata (e.g. iPhone portrait stored as landscape + rotate)
                for sd in stream.get("side_data_list", []):
                    if "rotation" in sd:
                        try:
                            video_rotation = int(sd["rotation"])
                        except (ValueError, TypeError):
                            pass
                        break
                # Swap to display dimensions so portrait detection and ASS layout are correct
                if abs(video_rotation) in (90, 270):
                    video_w, video_h = video_h, video_w
                break
        if video_duration <= 0:
            video_duration = float((json.loads(probe.stdout).get("format") or {}).get("duration", 0) or 0)
    log(f"📐 Video display resolution: {video_w}x{video_h}"
        + (f" (stored rotated {video_rotation}°)" if video_rotation else ""))

    # Compute output resolution (scale/crop if requested)
    # For portrait video (height > width), scale by width to avoid tiny output.
    # --output-height 1440 on landscape 3840x2160 → 2560x1440 (2K)
    # --output-height 1440 on portrait  2160x3840 → would be 810x1440 (blurry)
    # So for portrait, treat output_height as the target for the SHORT side (width).
    is_portrait = video_h > video_w
    scale_to = None
    filter_prefix = []
    if args.square_output:
        crop_side = min(video_w, video_h)
        crop_side = crop_side if crop_side % 2 == 0 else crop_side - 1
        crop_x = max(0, (video_w - crop_side) // 2)
        crop_y = max(0, (video_h - crop_side) // 2)
        filter_prefix.append(f"crop={crop_side}:{crop_side}:{crop_x}:{crop_y}")
        target_side = args.output_height if args.output_height and args.output_height > 0 else crop_side
        target_side = target_side if target_side % 2 == 0 else target_side + 1
        if target_side != crop_side:
            scale_to = (target_side, target_side)
        ass_w = ass_h = target_side
        log(f"Square output enabled — cropping to {crop_side}x{crop_side}"
            + (f", scaling to {target_side}x{target_side}" if scale_to else ""))
    elif args.output_height and args.output_height > 0:
        if is_portrait:
            # Scale by width: output_height arg acts as target width
            target_w = args.output_height
            if target_w < video_w:
                out_w = target_w if target_w % 2 == 0 else target_w + 1
                out_h = round(video_h * out_w / video_w)
                out_h = out_h if out_h % 2 == 0 else out_h + 1
                scale_to = (out_w, out_h)
                log(f"Portrait video detected — scaling by width to {out_w}x{out_h}")
        else:
            if args.output_height < video_h:
                out_h = args.output_height
                out_w = round(video_w * out_h / video_h)
                out_w = out_w if out_w % 2 == 0 else out_w + 1
                scale_to = (out_w, out_h)
        ass_w = scale_to[0] if scale_to else video_w
        ass_h = scale_to[1] if scale_to else video_h
    else:
        ass_w = video_w
        ass_h = video_h

    if srt_input_path:
        try:
            lines = read_srt_lines(srt_input_path)
        except ValueError as exc:
            print(f"❌ {exc}")
            sys.exit(1)
        log(f"🧾 Loaded {len(lines)} reviewed SRT lines")
        effective_max_chars = _resolve_effective_max_chars(
            args.max_chars, ass_w, ass_h, args.square_output, bilingual=args.bilingual
        )
        log("🔒 Preserving reviewed SRT text, line breaks, and timing exactly")
    else:
        # Load transcript (supports both plain array and {"segments": [...]} from preview editor)
        with open(transcript_path, encoding="utf-8") as f:
            raw = json.load(f)
        segments = raw.get("segments", raw) if isinstance(raw, dict) else raw
        log(f"📝 Loaded {len(segments)} transcript segments")

        # Convert segments to subtitle lines
        effective_max_chars = _resolve_effective_max_chars(
            args.max_chars, ass_w, ass_h, args.square_output, bilingual=args.bilingual
        )
        log(f"🔠 Subtitle line width: {effective_max_chars} visual chars")
        lines = segments_to_lines(segments, effective_max_chars)
        if len(lines) == len(segments):
            for line, segment in zip(lines, segments):
                english = str(segment.get("en") or segment.get("text_en") or "").strip()
                if english:
                    line["en"] = english
        log(f"🔤 Generated {len(lines)} subtitle lines")

    if not srt_input_path:
        lines = normalize_line_timing(lines)

    if args.bilingual:
        en_srt_path = Path(args.en_srt) if args.en_srt else None
        if en_srt_path:
            if not en_srt_path.exists():
                print(f"❌ English SRT not found: {en_srt_path}")
                sys.exit(1)
            try:
                lines = merge_english_srt(lines, read_srt_lines(en_srt_path))
            except ValueError as exc:
                print(f"❌ {exc}")
                sys.exit(1)
            log(f"🌐 Loaded English captions from {en_srt_path.name}")
        log("🌐 Bilingual burn enabled (Chinese above English)")
    else:
        log("Chinese-only burn")

    try:
        chapters = load_progress_chapters(
            chapters_path, video_duration, enabled=progress_requested
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"❌ Invalid chapters: {exc}")
        sys.exit(1)
    log(
        "Progress bar enabled with broad content chapters"
        if chapters
        else (
            "Progress bar disabled by user/configuration"
            if not progress_requested
            else "Progress bar disabled because the video is at most three minutes or has no chapters"
        )
    )
    layout = letterbox_layout(
        ass_w,
        ass_h,
        bilingual=args.bilingual,
        progress=bool(chapters),
    )
    pad_filter = letterbox_pad_filter(layout)
    log(
        f"⬛ Letterbox canvas {layout['canvas_width']}x{layout['canvas_height']} "
        f"(top {layout['top_pad']}px, bottom {layout['bottom_pad']}px)"
    )

    if args.draft_output:
        write_subtitle_draft(
            lines,
            Path(args.draft_output),
            effective_max_chars,
            preserve_text=bool(srt_input_path),
        )
    if args.draft_only:
        return

    if args.bilingual:
        missing_english = [line for line in lines if not line_english_text(line)]
        if missing_english:
            log(f"🌐 Translating {len(missing_english)} Chinese captions into English")
            try:
                lines = translate_caption_lines(lines)
            except Exception as exc:
                print(f"❌ Bilingual translation failed: {exc}")
                sys.exit(1)

    # Generate ASS (at output resolution so font size is correct)
    generate_ass(
        lines,
        ass_path,
        ass_w,
        ass_h,
        effective_max_chars,
        preserve_text=bool(srt_input_path),
        chapters=chapters,
        duration=video_duration,
        bilingual=args.bilingual,
    )
    if args.bilingual:
        english_draft = [
            {
                "start": line["start"],
                "end": line["end"],
                "text": line_english_text(line),
            }
            for line in lines
            if line_english_text(line)
        ]
        if english_draft:
            write_subtitle_draft(
                english_draft,
                ass_path.with_name(ass_path.stem + "_en.srt"),
                0,
                preserve_text=True,
            )

    if args.ass_only:
        log(f"Done. ASS file: {ass_path}")
        return

    camera_region = None
    if not args.no_beauty:
        log("Detecting the persistent camera face with macOS Vision...")
        camera_region = detect_camera_region(video_path, video_w, video_h)
        if camera_region is None:
            log("No stable camera face found; continuing without beauty")

    # Burn subtitles
    burn_subtitles(video_path, ass_path, output_path, scale_to=scale_to,
                   filter_prefix=filter_prefix,
                   total_duration_s=video_duration,
                   encoder=args.encoder,
                   video_size=(video_w, video_h),
                   camera_region=camera_region,
                   pad_filter=pad_filter)

    log("")
    log("=" * 50)
    log("✅ Done!")
    log(f"   Output: {output_path}")
    log(f"   Subtitle file: {ass_path}")
    log("")
    log("Tip: Edit the .ass file to tweak font/size/position, then re-run with --ass-only skipped.")


if __name__ == "__main__":
    main()
