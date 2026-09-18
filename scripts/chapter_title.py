#!/usr/bin/env python3
"""Shared chapter-title width and Scheme C marquee helpers.

Preview and burn must agree on “does this title overflow its slot?”.
Visual units match the caption heuristic: CJK = 1.0 em, Latin = 0.55 em,
space = 0.5 em; 1 em maps to ``progress_font`` pixels in ASS PlayRes.
"""

from __future__ import annotations

import re
from typing import Any

CJK_UNIT = 1.0
LATIN_UNIT = 0.55
SPACE_UNIT = 0.5

SLOT_PAD_EM = 0.40
SLOT_PAD_MIN_PX = 10
CLIP_INSET_EM = 0.12
CLIP_INSET_MIN_PX = 2

# Scroll speed in em/s so 720p and 4K stay readable without a px constant.
MARQUEE_EM_PER_SEC = 0.9
MARQUEE_GAP_EM = 2.0
MARQUEE_GAP_CHAR = "\u3000"


def normalize_title(title: str) -> str:
    """Collapse whitespace so preview, ellipsis, and ASS see the same string."""
    return re.sub(r"\s+", " ", str(title or "")).replace("\n", " ").strip()


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
            width += CJK_UNIT
        elif char == " ":
            width += SPACE_UNIT
        else:
            width += LATIN_UNIT
    return width


def chapter_slot_pad_px(font_size: int) -> int:
    return max(SLOT_PAD_MIN_PX, int(font_size * SLOT_PAD_EM))


def chapter_slot_max_visual(slot_width_px: int, font_size: int) -> float:
    """Visual-width budget for a chapter title inside its progress slot.

    ASS CJK glyphs are about one em wide. A 0.72 em factor overflowed short
    slots, so the clip box cut through the label instead of showing a clean
    ellipsis inside the section.
    """
    pad = chapter_slot_pad_px(font_size)
    usable = max(1, int(slot_width_px) - pad * 2)
    return usable / max(1.0, float(font_size))


def title_needs_marquee(title: str, slot_width_px: int, font_size: int) -> bool:
    """True when the full title cannot sit in the slot without clipping."""
    title = normalize_title(title)
    if not title or int(slot_width_px) <= 0 or int(font_size) <= 0:
        return False
    return visual_len(title) > chapter_slot_max_visual(slot_width_px, font_size) + 1e-6


def fit_chapter_title(title: str, max_visual: float) -> str:
    """Keep a chapter label on one line; ellipsize when the slot is too narrow.

    Used for inactive (and never-overflowing) labels. Active overflowing
    chapters scroll the full title instead; see ``marquee_move_cycles``.
    """
    title = normalize_title(title)
    if max_visual <= 0:
        return ""
    if visual_len(title) <= max_visual:
        return title
    ellipsis = "…"
    ellipsis_w = visual_len(ellipsis)
    if max_visual <= ellipsis_w:
        return ellipsis
    budget = max_visual - ellipsis_w
    cut = 0
    width = 0.0
    for index, char in enumerate(title):
        char_w = visual_len(char)
        if width + char_w > budget:
            break
        width += char_w
        cut = index + 1
    if cut <= 0:
        return ellipsis
    return title[:cut].rstrip() + ellipsis


def marquee_cycle_seconds(title: str) -> float:
    """Seconds to shift one title + gap, matching the CSS ``-50%`` loop."""
    title = normalize_title(title)
    if not title:
        return 0.0
    return (visual_len(title) + MARQUEE_GAP_EM) / MARQUEE_EM_PER_SEC


def marquee_loop_text(title: str) -> str:
    """Two copies of the title with a 2 em ideographic-space gap."""
    title = normalize_title(title)
    gap = MARQUEE_GAP_CHAR * int(round(MARQUEE_GAP_EM))
    return f"{title}{gap}{title}"


def preview_marquee_constants() -> dict[str, Any]:
    """Constants injected into the preview layout payload / JS."""
    return {
        "cjk_unit": CJK_UNIT,
        "latin_unit": LATIN_UNIT,
        "space_unit": SPACE_UNIT,
        "slot_pad_em": SLOT_PAD_EM,
        "slot_pad_min_px": SLOT_PAD_MIN_PX,
        "em_per_sec": MARQUEE_EM_PER_SEC,
        "gap_em": MARQUEE_GAP_EM,
    }


def slot_clip_box(
    start_x: int, end_x: int, font_size: int, top_pad: int
) -> tuple[int, int, int, int]:
    """ASS ``\\clip`` rectangle for a chapter slot (x1, y1, x2, y2)."""
    inset = max(CLIP_INSET_MIN_PX, int(font_size * CLIP_INSET_EM))
    clip_left = int(start_x) + inset
    clip_right = max(clip_left + 1, int(end_x) - inset)
    return clip_left, 0, clip_right, int(top_pad)


def marquee_move_cycles(
    title: str,
    *,
    slot_left: int,
    slot_right: int,
    font_size: int,
    label_y: int,
    top_pad: int,
    chapter_start: float,
    chapter_end: float,
) -> list[dict[str, Any]]:
    """One-shot ``\\move`` cycles covering the active chapter window.

    libass has no infinite loop primitive, so each cycle is a Dialogue event
    that shifts left by one title+gap. A partial last cycle still uses the
    same em/s speed. Returns an empty list when the title fits.
    """
    title = normalize_title(title)
    slot_width = max(1, int(slot_right) - int(slot_left))
    if not title_needs_marquee(title, slot_width, font_size):
        return []
    duration = float(chapter_end) - float(chapter_start)
    if duration <= 0.05:
        return []

    pad = chapter_slot_pad_px(font_size)
    x1 = int(slot_left) + pad
    y = int(label_y)
    clip = slot_clip_box(slot_left, slot_right, font_size, top_pad)
    cycle = marquee_cycle_seconds(title)
    if cycle <= 0:
        return []
    distance = (visual_len(title) + MARQUEE_GAP_EM) * float(font_size)
    display = marquee_loop_text(title)

    events: list[dict[str, Any]] = []
    cursor = float(chapter_start)
    end = float(chapter_end)
    while cursor < end - 0.02:
        event_end = min(end, cursor + cycle)
        if event_end <= cursor:
            break
        frac = max(0.0, min(1.0, (event_end - cursor) / cycle))
        x2 = x1 - int(round(distance * frac))
        events.append(
            {
                "start": cursor,
                "end": event_end,
                "x1": x1,
                "y": y,
                "x2": x2,
                "clip": clip,
                "text": display,
            }
        )
        cursor = event_end
    return events


def static_label_windows(
    *,
    chapter_start: float,
    chapter_end: float,
    duration: float,
    overflowing: bool,
) -> list[tuple[float, float]]:
    """Time ranges when the ellipsized/static label should be shown.

    Overflowing titles hide the static label while the chapter is active so
    the marquee is the only text in that slot. Fitting titles stay static
    for the whole video.
    """
    duration = max(float(duration), 0.01)
    start = max(0.0, float(chapter_start))
    end = min(duration, float(chapter_end))
    if not overflowing:
        return [(0.0, duration)]
    windows: list[tuple[float, float]] = []
    if start > 0.02:
        windows.append((0.0, start))
    if end < duration - 0.02:
        windows.append((end, duration))
    return windows
