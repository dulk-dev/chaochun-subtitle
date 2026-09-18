import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "preview_editor.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("preview_editor", SCRIPT_PATH)
PREVIEW_EDITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREVIEW_EDITOR)


class ProgressLayoutTests(unittest.TestCase):
    def test_progress_is_inside_the_video_overlay(self):
        self.assertNotIn('id="burnTimestamp"', PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn('class="letterbox-top"', PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn('class="letterbox-bottom"', PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn('id="contentProgress"', PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertLess(
            PREVIEW_EDITOR.HTML_TEMPLATE.index('class="letterbox-top"'),
            PREVIEW_EDITOR.HTML_TEMPLATE.index('id="vid"'),
        )
        self.assertLess(
            PREVIEW_EDITOR.HTML_TEMPLATE.index('id="vid"'),
            PREVIEW_EDITOR.HTML_TEMPLATE.index('class="letterbox-bottom"'),
        )

    def test_preview_progress_is_light_gray_in_the_top_bar(self):
        progress = re.search(
            r"\.content-progress\s*\{(?P<rules>.*?)\}",
            PREVIEW_EDITOR.HTML_TEMPLATE,
            re.DOTALL,
        )
        fill = re.search(
            r"\.content-progress-fill\s*\{(?P<rules>.*?)\}",
            PREVIEW_EDITOR.HTML_TEMPLATE,
            re.DOTALL,
        )
        label = re.search(
            r"\.content-progress-label\s*\{(?P<rules>.*?)\}",
            PREVIEW_EDITOR.HTML_TEMPLATE,
            re.DOTALL,
        )
        self.assertIsNotNone(progress)
        self.assertIsNotNone(fill)
        self.assertIsNotNone(label)
        self.assertIn("inset: 0", progress.group("rules"))
        self.assertIn("rgba(200, 200, 200, 0.42)", fill.group("rules"))
        self.assertIn("top: 0", fill.group("rules"))
        self.assertIn("bottom: 0", fill.group("rules"))
        self.assertNotIn("height: 6px", fill.group("rules"))
        self.assertNotIn("background: #c8c8c8", fill.group("rules"))
        self.assertIn("color: #b4b4b4", label.group("rules"))
        self.assertIn("text-overflow: ellipsis", label.group("rules"))
        self.assertIn("white-space: nowrap", label.group("rules"))
        self.assertIn(".content-progress-label.is-scrolling", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("chapter-title-marquee", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("progress-label-text", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("syncChapterLabelMarquee", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("--lb-progress-font", label.group("rules"))
        self.assertNotIn("clamp(", label.group("rules"))
        self.assertNotIn("3.0vw", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("--lb-top-frac", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertNotIn("flex-basis: 11%", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertNotIn("flex-basis: 16%", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertNotIn("flex-basis: 12%", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertNotIn("flex-basis: 18%", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertNotIn(".burn-timestamp", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("object-fit: fill", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("video::cue", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("/api/layout", PREVIEW_EDITOR.HTML_TEMPLATE)
        top_bar = re.search(
            r"\.letterbox-top,\s*\.letterbox-bottom\s*\{(?P<rules>.*?)\}",
            PREVIEW_EDITOR.HTML_TEMPLATE,
            re.DOTALL,
        )
        self.assertIsNotNone(top_bar)
        self.assertIn("overflow: hidden", top_bar.group("rules"))

    def test_preview_captions_match_f3_packing(self):
        caption = re.search(
            r"\.current-subtitle\s*\{(?P<rules>.*?)\}",
            PREVIEW_EDITOR.HTML_TEMPLATE,
            re.DOTALL,
        )
        zh = re.search(
            r"\.current-subtitle-zh\s*\{(?P<rules>.*?)\}",
            PREVIEW_EDITOR.HTML_TEMPLATE,
            re.DOTALL,
        )
        en = re.search(
            r"\.current-subtitle-en\s*\{(?P<rules>.*?)\}",
            PREVIEW_EDITOR.HTML_TEMPLATE,
            re.DOTALL,
        )
        self.assertIsNotNone(caption)
        self.assertIsNotNone(zh)
        self.assertIsNotNone(en)
        self.assertIn("justify-content: flex-start", caption.group("rules"))
        self.assertNotIn("justify-content: center", caption.group("rules"))
        self.assertIn("line-height: 1.05", caption.group("rules"))
        self.assertIn("inset: 0", caption.group("rules"))
        self.assertIn("overflow: hidden", caption.group("rules"))
        self.assertIn("--lb-zh-font", caption.group("rules"))
        self.assertIn("--lb-stack-inset-frac", caption.group("rules"))
        self.assertIn("line-height: 1.05", zh.group("rules"))
        self.assertIn("--lb-en-font", en.group("rules"))
        self.assertIn("line-height: 1.08", en.group("rules"))
        self.assertNotIn("font-size: .72em", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertNotIn("font-size: .48em", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertNotIn("inset: 8% 6% auto", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertNotIn("clamp(20px, 3.4vw, 36px)", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("segmentEn", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("seg.en || seg.text_en", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("seg.zh || seg.text", PREVIEW_EDITOR.HTML_TEMPLATE)
        self.assertIn("disableNativeTextTracks", PREVIEW_EDITOR.HTML_TEMPLATE)


class LetterboxApiTests(unittest.TestCase):
    def test_layout_endpoint_matches_burn_letterbox_layout(self):
        response = PREVIEW_EDITOR.app.test_client().get(
            "/api/layout?width=1280&height=720&bilingual=1&progress=1"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        layout = PREVIEW_EDITOR.letterbox_layout(
            1280, 720, bilingual=True, progress=True
        )
        expected = PREVIEW_EDITOR.preview_layout_payload(layout)
        self.assertEqual(payload["top_pad"], expected["top_pad"])
        self.assertEqual(payload["bottom_pad"], expected["bottom_pad"])
        self.assertEqual(payload["zh_font"], expected["zh_font"])
        self.assertEqual(payload["en_font"], expected["en_font"])
        self.assertEqual(payload["progress_font"], expected["progress_font"])
        self.assertAlmostEqual(payload["top_frac"], layout["top_pad"] / layout["canvas_height"])
        self.assertAlmostEqual(
            payload["en_font"] / payload["zh_font"], 0.48, places=2
        )
        self.assertLess(payload["top_frac"], 0.08)
        self.assertGreater(payload["bottom_frac"], payload["top_frac"])


class ManualGlossaryHookTests(unittest.TestCase):
    def test_saving_source_subtitles_records_pending_agent_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "subtitle-transcript.json"
            transcript.write_text(
                json.dumps({"segments": [{"start": 0, "end": 1, "text": "白练"}]}),
                encoding="utf-8",
            )
            old_transcript = PREVIEW_EDITOR.TRANSCRIPT_PATH
            old_manifest = PREVIEW_EDITOR.MANIFEST
            PREVIEW_EDITOR.TRANSCRIPT_PATH = str(transcript)
            PREVIEW_EDITOR.MANIFEST = None
            try:
                with patch.object(
                    PREVIEW_EDITOR,
                    "learn_manual_edits",
                    return_value={
                        "status": "ok",
                        "pending": [{"wrong": "白练", "correct": "百炼"}],
                        "learned": [],
                        "ignored": [],
                        "conflicts": [],
                    },
                ) as learn:
                    response = PREVIEW_EDITOR.app.test_client().post(
                        "/api/transcript",
                        json={
                            "lang": "src",
                            "segments": [{"start": 0, "end": 1, "text": "百炼"}],
                        },
                    )
            finally:
                PREVIEW_EDITOR.TRANSCRIPT_PATH = old_transcript
                PREVIEW_EDITOR.MANIFEST = old_manifest

        self.assertEqual(response.status_code, 200)
        review = response.get_json()["glossary_learning"]
        self.assertEqual(review["pending_count"], 1)
        self.assertEqual(review["learned_count"], 0)
        learn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
