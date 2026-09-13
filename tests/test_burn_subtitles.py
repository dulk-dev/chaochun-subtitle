import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "burn_subtitles.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("burn_subtitles", SCRIPT_PATH)
BURN_SUBTITLES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BURN_SUBTITLES)


class ReviewedSrtTests(unittest.TestCase):
    def test_draft_strips_dashes_without_damaging_product_names_or_versions(self):
        BURN_SUBTITLES.set_display_replacements([])
        lines = BURN_SUBTITLES.segments_to_lines([
            {"start": 0.0, "end": 5.0, "text": "先看——oil-html，再看–GPT-5.6。"},
        ], max_chars=60)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["text"], "先看 oil-html 再看 GPT-5.6")
        self.assertEqual((lines[0]["start"], lines[0]["end"]), (0.0, 5.0))

    def test_reviewed_srt_keeps_user_confirmed_dashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviewed.srt"
            path.write_text("1\n00:00:00,000 --> 00:00:05,000\n保留——oil-html 与 GPT-5.6\n", encoding="utf-8")
            lines = BURN_SUBTITLES.read_srt_lines(path)
        self.assertEqual(lines[0]["text"], "保留——oil-html 与 GPT-5.6")

    def test_reviewed_srt_text_is_immutable(self):
        source = """1
00:00:00,000 --> 00:00:01,000
Superpowers gstack grill-me
3:4 和 4:3

2
00:00:01,100 --> 00:00:02,000
oil-html Vibe Coding，保留标点
"""
        BURN_SUBTITLES.set_display_replacements([
            {"wrong": "grill-me", "correct": "grillme"},
            {"wrong": "oil-html", "correct": "oil-HTML"},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviewed.srt"
            path.write_text(source, encoding="utf-8")
            lines = BURN_SUBTITLES.read_srt_lines(path)

        self.assertEqual(
            lines[0]["text"],
            "Superpowers gstack grill-me\n3:4 和 4:3",
        )
        self.assertEqual(lines[1]["text"], "oil-html Vibe Coding，保留标点")
        self.assertEqual(lines[0]["start"], 0.0)
        self.assertEqual(lines[0]["end"], 1.0)

    def test_reviewed_srt_rejects_overlap(self):
        source = """1
00:00:00,000 --> 00:00:02,000
第一条

2
00:00:01,900 --> 00:00:03,000
第二条
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlap.srt"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Overlapping"):
                BURN_SUBTITLES.read_srt_lines(path)

    def test_ass_generation_preserves_reviewed_text(self):
        lines = [{
            "start": 0.0,
            "end": 1.0,
            "text": "grill-me\n3:4 和 4:3 oil-html Vibe Coding",
        }]
        BURN_SUBTITLES.set_display_replacements([
            {"wrong": "grill-me", "correct": "grillme"},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviewed.ass"
            BURN_SUBTITLES.generate_ass(
                lines,
                path,
                video_width=1920,
                video_height=1080,
                max_chars=25,
                preserve_text=True,
            )
            content = path.read_text(encoding="utf-8")

        self.assertIn("grill-me\\N3:4 和 4:3 oil-html Vibe Coding", content)
        self.assertNotIn("grillme\\N", content)
        self.assertIn("Style: CaptionZh,", content)
        self.assertIn("&H00FFFFFF", content)


class ProgressBarTests(unittest.TestCase):
    def test_chapter_titles_stay_fixed_in_their_own_intervals(self):
        payload = {
            "enabled": True,
            "min_progress_duration": 180.0,
            "chapters": [
                {"title": "开场", "start": 0.0, "end": 90.0},
                {"title": "正文", "start": 90.0, "end": 181.0},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapter_path = root / "chapters.json"
            ass_path = root / "subtitles.ass"
            chapter_path.write_text(json.dumps(payload), encoding="utf-8")
            chapters = BURN_SUBTITLES.load_progress_chapters(chapter_path, 181.0)
            BURN_SUBTITLES.generate_ass(
                [{"start": 0.0, "end": 1.0, "text": "测试字幕"}],
                ass_path,
                video_width=1920,
                video_height=1080,
                chapters=chapters,
                duration=181.0,
            )
            content = ass_path.read_text(encoding="utf-8")

        labels = [line for line in content.splitlines() if "ProgressLabel" in line and line.startswith("Dialogue")]
        self.assertEqual(len(labels), 2)
        self.assertTrue(all("0:00:00.00,0:03:01.00" in line for line in labels))
        self.assertIn("开场", labels[0])
        self.assertIn("正文", labels[1])
        layout = BURN_SUBTITLES.letterbox_layout(1920, 1080, bilingual=False, progress=True)
        for line in labels:
            self.assertIn("\\q2", line)
            y = int(line.split("pos(")[1].split(")")[0].split(",")[1])
            self.assertLess(y, layout["top_pad"])
        self.assertNotIn("Style: Timestamp,", content)
        self.assertNotRegex(content, r"Dialogue: 0,.*Timestamp")

    def test_progress_bar_lives_in_the_top_letterbox(self):
        chapters = [
            {"title": "开场", "start": 0.0, "end": 90.0},
            {"title": "正文", "start": 90.0, "end": 181.0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ass_path = Path(tmp) / "subtitles.ass"
            BURN_SUBTITLES.generate_ass(
                [{"start": 0.0, "end": 1.0, "text": "测试字幕"}],
                ass_path,
                video_width=1920,
                video_height=1080,
                chapters=chapters,
                duration=181.0,
            )
            content = ass_path.read_text(encoding="utf-8")

        layout = BURN_SUBTITLES.letterbox_layout(1920, 1080, bilingual=False, progress=True)
        self.assertIn(f"PlayResY: {layout['canvas_height']}", content)
        self.assertGreater(layout["canvas_height"], 1080)
        self.assertNotIn("Style: Timestamp,", content)
        self.assertGreater(layout["progress_font"], BURN_SUBTITLES._UPSTREAM_PROGRESS_FONT_1080P)
        self.assertIn(
            f"Style: ProgressLabel,{BURN_SUBTITLES._caption_font_name()},{layout['progress_font']},"
            f"{BURN_SUBTITLES._PROGRESS_LABEL_COLOUR}",
            content,
        )
        self.assertIn("ProgressFill", content)
        self.assertNotIn("ProgressTrack", content)
        self.assertIn(BURN_SUBTITLES._PROGRESS_FILL_COLOUR, content)
        self.assertIn(BURN_SUBTITLES._PROGRESS_FILL_ALPHA, content)
        fill_lines = [line for line in content.splitlines() if "ProgressFill" in line]
        self.assertTrue(fill_lines)
        fill_y = int(fill_lines[0].split("pos(0,")[1].split(")")[0])
        self.assertEqual(fill_y, 0)
        self.assertEqual(layout["progress_fill_y"], 0)
        self.assertEqual(layout["progress_fill_height"], layout["top_pad"])
        self.assertTrue(fill_lines[0].rstrip().endswith(f"l 0 {layout['top_pad']}"))
        self.assertLess(layout["progress_label_y"], layout["top_pad"])

    def test_three_minute_video_does_not_show_progress(self):
        payload = {
            "enabled": True,
            "min_progress_duration": 180.0,
            "chapters": [
                {"title": "上半段", "start": 0.0, "end": 90.0},
                {"title": "下半段", "start": 90.0, "end": 180.0},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chapters.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            chapters = BURN_SUBTITLES.load_progress_chapters(path, 180.0)

        self.assertEqual(chapters, [])

    def test_progress_can_be_disabled_even_when_chapters_exist(self):
        chapters = BURN_SUBTITLES.load_progress_chapters(
            Path("/does/not/need/to/exist.json"),
            360.0,
            enabled=False,
        )

        self.assertEqual(chapters, [])


class BeautyFilterTests(unittest.TestCase):
    def test_persistent_face_can_be_detected_away_from_top_right(self):
        detections = [
            {
                "sample": sample,
                "x": 0.10 + (sample % 3 - 1) * 0.003,
                "y": 0.70,
                "width": 0.10,
                "height": 0.15,
                "confidence": 0.99,
            }
            for sample in range(18)
        ]
        detections.extend([
            {
                "sample": sample,
                "x": 0.40 + sample * 0.02,
                "y": 0.20,
                "width": 0.08,
                "height": 0.12,
                "confidence": 0.95,
            }
            for sample in range(5)
        ])

        region = BURN_SUBTITLES.derive_camera_region(
            detections, 1920, 1080, sample_count=18
        )

        self.assertIsNotNone(region)
        x, y, width, height = region
        self.assertLess(x, 400)
        self.assertGreater(y, 500)
        self.assertEqual(width, height)
        self.assertEqual((x % 2, y % 2, width % 2), (0, 0, 0))

    def test_region_is_not_guessed_when_face_is_not_persistent(self):
        detections = [{
            "sample": 0,
            "x": 0.7,
            "y": 0.1,
            "width": 0.1,
            "height": 0.1,
            "confidence": 0.99,
        }]
        self.assertIsNone(
            BURN_SUBTITLES.derive_camera_region(
                detections, 1920, 1080, sample_count=18
            )
        )

    def test_default_graph_blends_ten_percent_smoothing_and_brightening(self):
        graph, output = BURN_SUBTITLES.build_beauty_filter_graph(
            "ass='/tmp/subtitles.ass'",
            2880,
            2160,
            (2104, 42, 734, 734),
            0.10,
            0.10,
        )

        self.assertEqual(output, "[video_out]")
        self.assertIn("crop=734:734:2104:42", graph)
        self.assertIn("bilateral=sigmaS=2.5:sigmaR=0.04:planes=1", graph)
        self.assertIn(
            "[beauty_smooth][beauty_original]"
            "blend=all_mode=normal:all_opacity=0.1",
            graph,
        )
        self.assertIn("eq=brightness=0.08:gamma=1.04", graph)
        self.assertIn(
            "[brightness_lifted][brightness_base]"
            "blend=all_mode=normal:all_opacity=0.1",
            graph,
        )
        self.assertTrue(graph.endswith("ass='/tmp/subtitles.ass'[video_out]"))

    def test_graph_keeps_crop_scale_and_letterbox_pad_after_beauty(self):
        pad = BURN_SUBTITLES.letterbox_pad_filter(
            BURN_SUBTITLES.letterbox_layout(720, 720, bilingual=True)
        )
        graph, _ = BURN_SUBTITLES.build_beauty_filter_graph(
            "ass='/tmp/subtitles.ass'",
            1920,
            1080,
            (1300, 20, 500, 500),
            0.10,
            0.10,
            filter_prefix=["crop=1080:1080:420:0"],
            scale_to=(720, 720),
            pad_filter=pad,
        )

        self.assertIn(
            "[beautified]crop=1080:1080:420:0,scale=720:720,"
            f"{pad},ass='/tmp/subtitles.ass'[video_out]",
            graph,
        )

    def test_graph_rejects_invalid_strength(self):
        with self.assertRaisesRegex(ValueError, "At least one"):
            BURN_SUBTITLES.build_beauty_filter_graph(
                "ass='/tmp/subtitles.ass'",
                1920,
                1080,
                (1300, 20, 500, 500),
                0,
                0,
            )


class LetterboxLayoutTests(unittest.TestCase):
    def test_pads_are_even_and_keep_the_picture_in_the_middle(self):
        layout = BURN_SUBTITLES.letterbox_layout(1920, 1080, bilingual=True, progress=True)
        self.assertEqual(layout["top_pad"] % 2, 0)
        self.assertEqual(layout["bottom_pad"] % 2, 0)
        self.assertEqual(layout["canvas_width"] % 2, 0)
        self.assertEqual(layout["canvas_height"] % 2, 0)
        self.assertEqual(
            layout["canvas_height"],
            1080 + layout["top_pad"] + layout["bottom_pad"],
        )
        self.assertGreater(layout["progress_label_y"], 0)
        self.assertLess(layout["progress_label_y"], layout["top_pad"])
        self.assertGreater(layout["zh_y"], 1080 + layout["top_pad"])
        self.assertLess(layout["zh_y"], layout["canvas_height"])
        self.assertGreater(layout["en_y"], layout["zh_y"])
        self.assertLess(layout["en_y"], layout["canvas_height"])
        self.assertEqual(
            BURN_SUBTITLES.letterbox_pad_filter(layout),
            f"pad={layout['canvas_width']}:{layout['canvas_height']}:0:{layout['top_pad']}:black",
        )

    def test_chapter_strip_is_compact_with_larger_labels(self):
        layout = BURN_SUBTITLES.letterbox_layout(1920, 1080, bilingual=True, progress=True)
        captions = BURN_SUBTITLES.letterbox_layout(1920, 1080, bilingual=True, progress=False)
        self.assertGreaterEqual(layout["progress_font"], 40)
        self.assertGreater(layout["progress_font"], BURN_SUBTITLES._UPSTREAM_PROGRESS_FONT_1080P)
        self.assertLessEqual(layout["top_pad"], 64)
        self.assertLess(layout["top_pad"], 80)
        self.assertGreaterEqual(layout["top_pad"], layout["progress_font"] + 8)
        self.assertEqual(layout["bottom_pad"], captions["bottom_pad"])
        self.assertEqual(layout["zh_font"], captions["zh_font"])
        self.assertEqual(layout["en_font"], captions["en_font"])
        self.assertEqual(layout["progress_fill_height"], layout["top_pad"])

    def test_burned_ass_has_no_running_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "no_clock.ass"
            BURN_SUBTITLES.generate_ass(
                [{"start": 0.0, "end": 2.5, "text": "你好", "en": "Hello"}],
                path,
                video_width=1920,
                video_height=1080,
                duration=2.5,
                bilingual=True,
            )
            content = path.read_text(encoding="utf-8")
        self.assertNotIn("Style: Timestamp,", content)
        self.assertNotIn(",Timestamp,", content)
        self.assertIn("你好", content)
        self.assertIn("Hello", content)


class ChapterTitleFitTests(unittest.TestCase):
    def test_short_title_stays_intact(self):
        self.assertEqual(BURN_SUBTITLES.fit_chapter_title("开场", 8), "开场")

    def test_long_title_in_narrow_slot_uses_ellipsis_on_one_line(self):
        title = "这是一段非常非常长的核心方法说明标题"
        fitted = BURN_SUBTITLES.fit_chapter_title(title, 6)
        self.assertIn("…", fitted)
        self.assertNotIn("\n", fitted)
        self.assertLess(len(fitted), len(title))
        self.assertLessEqual(BURN_SUBTITLES._visual_len(fitted), 6.01)

    def test_narrow_chapter_slot_ellipsizes_in_ass(self):
        chapters = [
            {"title": "开头", "start": 0.0, "end": 90.0},
            {"title": "这是一段非常非常长的核心方法说明标题", "start": 90.0, "end": 105.0},
            {"title": "结尾", "start": 105.0, "end": 181.0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ellipsis.ass"
            BURN_SUBTITLES.generate_ass(
                [{"start": 0.0, "end": 1.0, "text": "测试"}],
                path,
                video_width=1920,
                video_height=1080,
                chapters=chapters,
                duration=181.0,
            )
            content = path.read_text(encoding="utf-8")
        labels = [line for line in content.splitlines() if line.startswith("Dialogue") and "ProgressLabel" in line]
        self.assertEqual(len(labels), 3)
        self.assertIn("开头", labels[0])
        self.assertIn("…", labels[1])
        self.assertNotIn("这是一段非常非常长的核心方法说明标题", labels[1])
        self.assertNotIn("\\N", labels[1])
        self.assertIn("结尾", labels[2])
        layout = BURN_SUBTITLES.letterbox_layout(1920, 1080, progress=True)
        slot_px = int(1920 * 15.0 / 181.0)
        fitted = BURN_SUBTITLES.fit_chapter_title(
            chapters[1]["title"],
            BURN_SUBTITLES.chapter_slot_max_visual(slot_px, layout["progress_font"]),
        )
        self.assertEqual(fitted, labels[1].rsplit("}", 1)[-1])
        self.assertLessEqual(
            BURN_SUBTITLES._visual_len(fitted) * layout["progress_font"],
            slot_px,
        )

    def test_newlines_and_spaces_collapse_to_one_line(self):
        self.assertEqual(
            BURN_SUBTITLES.fit_chapter_title("  开场\n说明  ", 12),
            "开场 说明",
        )


class BilingualAssTests(unittest.TestCase):
    def test_chinese_sits_above_english_in_the_bottom_bar(self):
        layout = BURN_SUBTITLES.letterbox_layout(1920, 1080, bilingual=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bilingual.ass"
            BURN_SUBTITLES.generate_ass(
                [{"start": 0.0, "end": 2.0, "text": "欢迎回来", "en": "Welcome back"}],
                path,
                video_width=1920,
                video_height=1080,
                bilingual=True,
                duration=2.0,
            )
            content = path.read_text(encoding="utf-8")
        self.assertIn("&H00FFFFFF", content)
        self.assertIn("欢迎回来", content)
        self.assertIn("Welcome back", content)
        zh_line = next(line for line in content.splitlines() if "CaptionZh" in line and "欢迎回来" in line)
        en_line = next(line for line in content.splitlines() if "CaptionEn" in line and "Welcome back" in line)
        zh_y = int(zh_line.split("pos(")[1].split(")")[0].split(",")[1])
        en_y = int(en_line.split("pos(")[1].split(")")[0].split(",")[1])
        self.assertEqual(zh_y, layout["zh_y"])
        self.assertEqual(en_y, layout["en_y"])
        self.assertGreater(en_y, zh_y)
        self.assertGreater(zh_y, 1080 + layout["top_pad"])

    def test_translate_caption_lines_keeps_existing_english(self):
        lines = [
            {"start": 0.0, "end": 1.0, "text": "你好", "en": "Hi"},
            {"start": 1.0, "end": 2.0, "text": "世界"},
        ]
        translated = BURN_SUBTITLES.translate_caption_lines(
            lines,
            translator=lambda texts: [f"EN:{item}" for item in texts],
        )
        self.assertEqual(translated[0]["en"], "Hi")
        self.assertEqual(translated[1]["en"], "EN:世界")

    def test_merge_english_srt_pairs_by_index(self):
        chinese = [{"start": 0.0, "end": 1.0, "text": "你好"}]
        english = [{"start": 0.0, "end": 1.0, "text": "Hello"}]
        merged = BURN_SUBTITLES.merge_english_srt(chinese, english)
        self.assertEqual(merged[0]["en"], "Hello")


class SingleLineWrapTests(unittest.TestCase):
    def test_long_caption_stays_one_line_within_letterbox_width(self):
        text = "今天我们继续讲解 Claude Code 和 GPT 的实战用法"
        max_chars = BURN_SUBTITLES._resolve_effective_max_chars(0, 1920, 1080, False)
        wrapped = BURN_SUBTITLES._wrap_display_text(text, max_chars)
        self.assertNotIn("\n", wrapped)
        self.assertLessEqual(BURN_SUBTITLES._visual_len(wrapped), max_chars)

    def test_overlong_caption_wraps_only_as_a_last_resort(self):
        text = "这是一句非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常长的中文字幕用于验证限宽"
        max_chars = 16
        wrapped = BURN_SUBTITLES._wrap_display_text(text, max_chars)
        self.assertIn("\n", wrapped)
        for part in wrapped.split("\n"):
            self.assertLessEqual(BURN_SUBTITLES._visual_len(part), max_chars + 0.01)


if __name__ == "__main__":
    unittest.main()
