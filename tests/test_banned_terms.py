import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import banned_terms as BANNED  # noqa: E402
import burn_subtitles as BURN  # noqa: E402


def _lexicon(entries: list[dict]) -> BANNED.Lexicon:
    payload = {
        "version": 1,
        "strategy": "generic-paraphrase",
        "entries": entries,
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "banned_terms.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return BANNED.load_lexicon(path)


class BannedTermLexiconTests(unittest.TestCase):
    def test_shipped_table_matches_curated_initial_terms(self):
        payload = json.loads(BANNED.shipped_lexicon_path().read_text(encoding="utf-8"))
        terms = [
            spec["text"] if isinstance(spec, dict) else spec
            for entry in payload["entries"]
            for spec in entry["terms"]
        ]
        self.assertEqual(
            terms,
            ["Codex", "Twitter", "推特", "X平台", "YouTube", "油管", "TikTok", "淘宝"],
        )
        self.assertNotIn("X", terms)
        self.assertTrue(all(entry.get("status") == "confirmed" for entry in payload["entries"]))
        lexicon = BANNED.load_lexicon(BANNED.shipped_lexicon_path())
        self.assertEqual(lexicon.strategy, "generic-paraphrase")
        self.assertEqual(
            lexicon.replacements["codex"],
            {"zh": "AI编程工具", "en": "AI coding tool"},
        )
        self.assertEqual(
            lexicon.replacements["twitter"],
            {"zh": "社交平台", "en": "social platform"},
        )
        self.assertEqual(
            lexicon.replacements["youtube"],
            {"zh": "视频网站", "en": "video site"},
        )
        self.assertEqual(
            lexicon.replacements["tiktok"],
            {"zh": "短视频平台", "en": "short-video platform"},
        )
        self.assertEqual(
            lexicon.replacements["taobao"],
            {"zh": "电商平台", "en": "e-commerce platform"},
        )

    def test_rejects_homoglyph_strategy_and_mask_replacements(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "banned_terms.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "strategy": "homoglyph",
                        "entries": [
                            {
                                "id": "example",
                                "replace": {"zh": "例子", "en": "example"},
                                "terms": ["Example"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "generic-paraphrase"):
                BANNED.load_lexicon(path)
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "strategy": "generic-paraphrase",
                        "entries": [
                            {
                                "id": "example",
                                "replace": {"zh": "***", "en": "example"},
                                "terms": ["Example"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "paraphrase"):
                BANNED.load_lexicon(path)

    def test_bilingual_table_match_leaves_unlisted_words(self):
        lexicon = BANNED.load_lexicon(BANNED.shipped_lexicon_path())
        lines, hits = BANNED.paraphrase_lines(
            [
                {
                    "text": "用 Claude Code 和 Codex 把视频发到油管和推特，再看 TikTok 与淘宝",
                    "en": "Use Claude Code and Codex, then post on YouTube and Twitter, plus TikTok",
                }
            ],
            lexicon,
        )
        self.assertEqual(
            lines[0]["text"],
            "用 Claude Code 和 AI编程工具 把视频发到视频网站和社交平台，再看 短视频平台 与电商平台",
        )
        self.assertEqual(
            lines[0]["en"],
            "Use Claude Code and AI coding tool, then post on video site and social platform, plus short-video platform",
        )
        self.assertTrue(hits)
        self.assertTrue(all(not hit["ambiguous"] for hit in hits))
        again, second_hits = BANNED.paraphrase_lines(lines, lexicon)
        self.assertEqual(again[0]["text"], lines[0]["text"])
        self.assertEqual(again[0]["en"], lines[0]["en"])
        self.assertEqual(second_hits, [])

    def test_shipped_table_matches_x_platform_not_bare_x(self):
        lexicon = BANNED.load_lexicon(BANNED.shipped_lexicon_path())
        lines, hits = BANNED.paraphrase_lines(
            [
                {
                    "text": "去 X 看看，也去X平台。别改 next、OSX、抖音、加微信或 ChatGPT",
                    "en": "Post on X today. Also X平台 and Twitter. Keep OS X, Claude Code, and ChatGPT.",
                }
            ],
            lexicon,
        )
        self.assertEqual(
            lines[0]["text"],
            "去 X 看看，也去社交平台。别改 next、OSX、抖音、加微信或 ChatGPT",
        )
        self.assertEqual(
            lines[0]["en"],
            "Post on X today. Also social platform and social platform. Keep OS X, Claude Code, and ChatGPT.",
        )
        self.assertTrue(hits)
        self.assertTrue(all(hit["term"] == "X平台" or hit["term"] == "Twitter" for hit in hits))
        self.assertTrue(all(not hit["ambiguous"] for hit in hits))
        report = BANNED.build_report(lexicon, hits)
        self.assertEqual(report["ambiguous_hits"], [])
        self.assertIn("口播", report["audio_reminder"])

    def test_single_letter_token_defaults_to_case_sensitive_review(self):
        lexicon = _lexicon(
            [
                {
                    "id": "mark",
                    "replace": {"zh": "标记", "en": "mark"},
                    "terms": [{"text": "Q", "match": "token"}],
                }
            ]
        )
        lines, hits = BANNED.paraphrase_lines(
            [{"text": "按下 Q 和 q", "en": "Press Q and q"}],
            lexicon,
        )
        self.assertEqual(lines[0]["text"], "按下 标记 和 q")
        self.assertEqual(lines[0]["en"], "Press mark and q")
        self.assertTrue(all(hit["ambiguous"] for hit in hits))

    def test_longer_phrase_wins_and_chapter_titles_use_chinese(self):
        lexicon = _lexicon(
            [
                {
                    "id": "short",
                    "replace": {"zh": "短", "en": "short"},
                    "terms": ["Code"],
                },
                {
                    "id": "long",
                    "replace": {"zh": "编程工具", "en": "coding tool"},
                    "terms": ["Claude Code"],
                },
            ]
        )
        lines, _hits = BANNED.paraphrase_lines(
            [{"text": "打开 Claude Code", "en": "Open Claude Code"}],
            lexicon,
        )
        self.assertEqual(lines[0]["text"], "打开 编程工具")
        self.assertEqual(lines[0]["en"], "Open coding tool")
        chapters, chapter_hits = BANNED.paraphrase_chapters(
            [{"title": "在 YouTube 演示", "start": 0, "end": 1}],
            BANNED.load_lexicon(BANNED.shipped_lexicon_path()),
        )
        self.assertEqual(chapters[0]["title"], "在 视频网站 演示")
        self.assertEqual(chapter_hits[0]["surface"], "chapter")
        self.assertEqual(chapter_hits[0]["track"], "zh")

    def test_burn_helper_updates_both_tracks_before_ass(self):
        BURN.set_display_replacements([])
        lexicon = BANNED.load_lexicon(BANNED.shipped_lexicon_path())
        lines, chapters, report = BURN.apply_banned_term_paraphrase(
            [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "把 Codex 发到 YouTube",
                    "en": "Ship Codex to YouTube",
                }
            ],
            [{"title": "YouTube 发布", "start": 0.0, "end": 2.0}],
            lexicon,
        )
        self.assertEqual(lines[0]["text"], "把 AI编程工具 发到 视频网站")
        self.assertEqual(lines[0]["en"], "Ship AI coding tool to video site")
        self.assertEqual(chapters[0]["title"], "视频网站 发布")
        self.assertGreater(report["hit_count"], 0)
        self.assertEqual(report["strategy"], "generic-paraphrase")
        with tempfile.TemporaryDirectory() as tmp:
            ass_path = Path(tmp) / "out.ass"
            BURN.generate_ass(
                lines,
                ass_path,
                video_width=1920,
                video_height=1080,
                max_chars=40,
                preserve_text=True,
                chapters=[],
                bilingual=True,
            )
            content = ass_path.read_text(encoding="utf-8")
            report_path = BURN.emit_paraphrase_report(report, ass_path)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertIn("AI编程工具", content)
        self.assertIn("视频网站", content)
        self.assertIn("AI coding tool", content)
        self.assertIn("video site", content)
        self.assertNotIn("Codex", content)
        self.assertNotIn("YouTube", content)
        self.assertEqual(saved["hit_count"], report["hit_count"])
        self.assertIn("audio_reminder", saved)


if __name__ == "__main__":
    unittest.main()
