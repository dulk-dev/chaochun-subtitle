import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "caption_fit.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("caption_fit", SCRIPT_PATH)
CAPTION_FIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTION_FIT)


class MaxVisualTests(unittest.TestCase):
    def test_1080p_budget_is_about_one_em(self):
        self.assertEqual(CAPTION_FIT.max_visual_units(1920, 66), 26)


class SplitBehaviorTests(unittest.TestCase):
    def test_screenshot_sentence_does_not_start_with_particle(self):
        text = "如果你在用别的这种 'agent' 或者其他的 app 呢也可以做类似的一个探索"
        events = CAPTION_FIT.fit_cue(text, 0.0, 4.0, 26)
        self.assertGreaterEqual(len(events), 2)
        for event in events:
            self.assertFalse(CAPTION_FIT.is_weak_start(event["text"]))
            self.assertLessEqual(CAPTION_FIT.visual_len(event["text"]), 26.01)
            self.assertNotIn("\n", event["text"])

    def test_two_segments_repair_leading_particle(self):
        from burn_subtitles import segments_to_lines

        lines = segments_to_lines(
            [
                {"start": 0.0, "end": 2.0, "text": "如果你在用别的这种 agent 或者其他的 app"},
                {"start": 2.0, "end": 4.0, "text": "呢也可以做类似的一个探索"},
            ],
            max_chars=26,
        )
        self.assertGreaterEqual(len(lines), 2)
        self.assertFalse(any(CAPTION_FIT.is_weak_start(line["text"]) for line in lines))
        self.assertTrue(any(line["text"].endswith("呢") for line in lines))

    def test_slight_overflow_shrinks_instead_of_splitting(self):
        text = "一二三四五六七八九十十一十二"
        cap = CAPTION_FIT.visual_len(text) - 1
        events = CAPTION_FIT.fit_cue(text, 0.0, 2.0, cap)
        self.assertEqual(len(events), 1)
        self.assertLess(events[0]["shrink_scale"], 1.0)
        self.assertGreaterEqual(events[0]["shrink_scale"], 0.75)

    def test_existing_english_with_too_few_words_shrinks(self):
        text = "这是一句需要拆开的很长很长很长很长很长的中文字幕内容还要再长一些"
        events = CAPTION_FIT.fit_cue(
            text, 0.0, 4.0, 16, existing_en="Hello"
        )
        self.assertEqual(len(events), 1)
        self.assertLess(events[0]["shrink_scale"], 1.0)
        self.assertEqual(events[0]["en"], "Hello")

    def test_newlines_become_sequential_events(self):
        events = CAPTION_FIT.fit_cue("第一行\n第二行", 0.0, 2.0, 26)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["text"], "第一行")
        self.assertEqual(events[1]["text"], "第二行")
        self.assertLess(events[0]["end"], events[1]["end"])

    def test_latin_merge_inserts_a_space(self):
        self.assertEqual(
            CAPTION_FIT.join_caption_text("let me use claude", "code now"),
            "let me use claude code now",
        )


class EnglishAttachTests(unittest.TestCase):
    def test_overlap_splits_one_english_cue_across_chinese_pieces(self):
        lines = [
            {"start": 0.0, "end": 1.0, "text": "你好世界"},
            {"start": 1.0, "end": 2.0, "text": "继续讲解"},
        ]
        english = [{"start": 0.0, "end": 2.0, "text": "Hello world keep going"}]
        merged = CAPTION_FIT.attach_english_by_overlap(lines, english)
        self.assertTrue(merged[0].get("en"))
        self.assertTrue(merged[1].get("en"))
        self.assertNotEqual(merged[0]["en"], merged[1]["en"])


if __name__ == "__main__":
    unittest.main()
