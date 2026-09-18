import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "chapter_title", SCRIPT_DIR / "chapter_title.py"
)
CHAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHAPTER)


class VisualWidthTests(unittest.TestCase):
    def test_cjk_latin_and_space_weights(self):
        self.assertEqual(CHAPTER.visual_len("开场"), 2.0)
        self.assertAlmostEqual(CHAPTER.visual_len("A"), 0.55)
        self.assertAlmostEqual(CHAPTER.visual_len("A B"), 1.6)
        self.assertEqual(CHAPTER.visual_len("…"), 1.0)
        self.assertEqual(CHAPTER.visual_len("　"), 1.0)

    def test_normalize_collapses_whitespace(self):
        self.assertEqual(CHAPTER.normalize_title("  开场\n说明  "), "开场 说明")


class MarqueeDecisionTests(unittest.TestCase):
    def test_short_title_does_not_need_marquee(self):
        self.assertFalse(CHAPTER.title_needs_marquee("开场", 400, 43))

    def test_long_title_in_narrow_slot_needs_marquee(self):
        title = "这是一段非常非常长的核心方法说明标题"
        self.assertTrue(CHAPTER.title_needs_marquee(title, 160, 43))
        fitted = CHAPTER.fit_chapter_title(
            title, CHAPTER.chapter_slot_max_visual(160, 43)
        )
        self.assertIn("…", fitted)
        self.assertNotEqual(fitted, title)

    def test_fit_ellipsis_stays_within_budget(self):
        fitted = CHAPTER.fit_chapter_title("这是一段非常非常长的核心方法说明标题", 6)
        self.assertLessEqual(CHAPTER.visual_len(fitted), 6.01)
        self.assertNotIn("\n", fitted)


class MarqueeCycleTests(unittest.TestCase):
    def test_cycle_seconds_scale_with_title_length(self):
        short = CHAPTER.marquee_cycle_seconds("开场")
        long = CHAPTER.marquee_cycle_seconds("这是一段非常非常长的核心方法说明标题")
        self.assertGreater(long, short)
        self.assertAlmostEqual(
            long,
            (CHAPTER.visual_len("这是一段非常非常长的核心方法说明标题") + CHAPTER.MARQUEE_GAP_EM)
            / CHAPTER.MARQUEE_EM_PER_SEC,
        )

    def test_loop_text_is_two_copies_with_ideographic_gap(self):
        text = CHAPTER.marquee_loop_text("核心方法")
        self.assertTrue(text.startswith("核心方法"))
        self.assertEqual(text.count("核心方法"), 2)
        self.assertIn("\u3000\u3000", text)

    def test_no_cycles_when_title_fits(self):
        cycles = CHAPTER.marquee_move_cycles(
            "开场",
            slot_left=0,
            slot_right=600,
            font_size=43,
            label_y=26,
            top_pad=52,
            chapter_start=0.0,
            chapter_end=10.0,
        )
        self.assertEqual(cycles, [])

    def test_cycles_cover_the_active_window_at_constant_speed(self):
        title = "这是一段非常非常长的核心方法说明标题"
        cycles = CHAPTER.marquee_move_cycles(
            title,
            slot_left=900,
            slot_right=1060,
            font_size=43,
            label_y=26,
            top_pad=52,
            chapter_start=90.0,
            chapter_end=105.0,
        )
        self.assertGreaterEqual(len(cycles), 1)
        self.assertAlmostEqual(cycles[0]["start"], 90.0)
        self.assertAlmostEqual(cycles[-1]["end"], 105.0)
        self.assertTrue(all(item["x2"] < item["x1"] for item in cycles))
        self.assertTrue(all(title in item["text"] for item in cycles))
        self.assertEqual(cycles[0]["clip"][0], CHAPTER.slot_clip_box(900, 1060, 43, 52)[0])

    def test_static_windows_hide_ellipsis_while_active_overflows(self):
        windows = CHAPTER.static_label_windows(
            chapter_start=90.0,
            chapter_end=105.0,
            duration=181.0,
            overflowing=True,
        )
        self.assertEqual(windows, [(0.0, 90.0), (105.0, 181.0)])
        whole = CHAPTER.static_label_windows(
            chapter_start=0.0,
            chapter_end=90.0,
            duration=181.0,
            overflowing=False,
        )
        self.assertEqual(whole, [(0.0, 181.0)])

    def test_preview_constants_match_module_values(self):
        payload = CHAPTER.preview_marquee_constants()
        self.assertEqual(payload["em_per_sec"], CHAPTER.MARQUEE_EM_PER_SEC)
        self.assertEqual(payload["gap_em"], CHAPTER.MARQUEE_GAP_EM)
        self.assertEqual(payload["cjk_unit"], CHAPTER.CJK_UNIT)
        self.assertEqual(payload["latin_unit"], CHAPTER.LATIN_UNIT)


if __name__ == "__main__":
    unittest.main()
