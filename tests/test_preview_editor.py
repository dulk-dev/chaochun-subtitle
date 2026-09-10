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
        self.assertIn("background: #c8c8c8", fill.group("rules"))
        self.assertIn("color: #b4b4b4", label.group("rules"))
        self.assertIn("text-overflow: ellipsis", label.group("rules"))
        self.assertIn("white-space: nowrap", label.group("rules"))
        self.assertNotIn(".burn-timestamp", PREVIEW_EDITOR.HTML_TEMPLATE)


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
