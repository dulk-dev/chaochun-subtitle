import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import user_config as USER_CONFIG  # noqa: E402


CONFIG_ENV_KEYS = (
    "DASHSCOPE_API_KEY",
    "CHAOCHUN_SUBTITLE_CONFIG",
    "CHAOCHUN_SUBTITLE_API_KEY_FILE",
    "CHAOCHUN_SUBTITLE_GLOSSARY",
    "CHAOCHUN_SUBTITLE_PROGRESS_ENABLED",
    "CHAOCHUN_SUBTITLE_PROGRESS_MIN_DURATION",
    "OIL_SUBTITLE_CONFIG",
    "OIL_SUBTITLE_API_KEY_FILE",
    "OIL_SUBTITLE_GLOSSARY",
    "OIL_SUBTITLE_PROGRESS_ENABLED",
    "OIL_SUBTITLE_PROGRESS_MIN_DURATION",
    "SCREEN_STUDIO_EDITOR_CONFIG",
    "SCREEN_STUDIO_EDITOR_API_KEY_FILE",
    "SCREEN_STUDIO_EDITOR_GLOSSARY",
    "SCREEN_STUDIO_EDITOR_PROGRESS_ENABLED",
    "SCREEN_STUDIO_EDITOR_PROGRESS_MIN_DURATION",
)


def isolated_env(**overrides: str) -> dict[str, str]:
    env = {key: "" for key in CONFIG_ENV_KEYS}
    env.update(overrides)
    return env


class UserConfigResolutionTests(unittest.TestCase):
    def _bind_paths(self, tmp: Path) -> dict[str, Path]:
        chaochun = tmp / "chaochun"
        oil = tmp / "oil"
        studio = tmp / "studio"
        bailian = tmp / "bailian" / "config.json"
        return {
            "chaochun": chaochun,
            "oil": oil,
            "studio": studio,
            "bailian": bailian,
            "preferred_config": chaochun / "config.json",
            "preferred_key": chaochun / "dashscope_api_key",
            "preferred_glossary": chaochun / "glossary.json",
            "oil_config": oil / "config.json",
            "oil_key": oil / "dashscope_api_key",
            "oil_glossary": oil / "glossary.json",
            "studio_config": studio / "config.json",
        }

    def _patched(self, paths: dict[str, Path], **env: str) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            patch.object(USER_CONFIG, "PREFERRED_CONFIG", paths["preferred_config"])
        )
        stack.enter_context(
            patch.object(USER_CONFIG, "PREFERRED_API_KEY_FILE", paths["preferred_key"])
        )
        stack.enter_context(
            patch.object(USER_CONFIG, "PREFERRED_GLOSSARY", paths["preferred_glossary"])
        )
        stack.enter_context(
            patch.object(USER_CONFIG, "LEGACY_OIL_CONFIG", paths["oil_config"])
        )
        stack.enter_context(
            patch.object(USER_CONFIG, "LEGACY_OIL_API_KEY_FILE", paths["oil_key"])
        )
        stack.enter_context(
            patch.object(USER_CONFIG, "LEGACY_OIL_GLOSSARY", paths["oil_glossary"])
        )
        stack.enter_context(
            patch.object(USER_CONFIG, "LEGACY_CONFIG", paths["studio_config"])
        )
        stack.enter_context(
            patch.object(USER_CONFIG, "LEGACY_BAILIAN_CONFIG", paths["bailian"])
        )
        stack.enter_context(patch.dict(os.environ, isolated_env(**env), clear=False))
        return stack

    def test_config_path_prefers_new_env_then_new_file_then_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._bind_paths(root)
            for directory in (paths["chaochun"], paths["oil"], paths["studio"]):
                directory.mkdir()
            paths["preferred_config"].write_text("{}", encoding="utf-8")
            paths["oil_config"].write_text("{}", encoding="utf-8")
            paths["studio_config"].write_text("{}", encoding="utf-8")
            new_env = root / "from-chaochun-env.json"
            oil_env = root / "from-oil-env.json"

            with self._patched(paths, CHAOCHUN_SUBTITLE_CONFIG=str(new_env)):
                self.assertEqual(USER_CONFIG.config_path(), new_env)

            with self._patched(paths):
                self.assertEqual(USER_CONFIG.config_path(), paths["preferred_config"])

            paths["preferred_config"].unlink()
            with self._patched(paths, OIL_SUBTITLE_CONFIG=str(oil_env)):
                self.assertEqual(USER_CONFIG.config_path(), oil_env)

            with self._patched(paths):
                self.assertEqual(USER_CONFIG.config_path(), paths["oil_config"])

            paths["oil_config"].unlink()
            with self._patched(paths):
                self.assertEqual(USER_CONFIG.config_path(), paths["studio_config"])

            paths["studio_config"].unlink()
            with self._patched(paths):
                self.assertEqual(USER_CONFIG.config_path(), paths["preferred_config"])

    def test_new_config_file_beats_legacy_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._bind_paths(root)
            paths["chaochun"].mkdir()
            paths["preferred_config"].write_text(
                json.dumps({"source": "chaochun"}), encoding="utf-8"
            )
            oil_env = root / "legacy.json"
            oil_env.write_text(json.dumps({"source": "oil-env"}), encoding="utf-8")
            with self._patched(paths, OIL_SUBTITLE_CONFIG=str(oil_env)):
                self.assertEqual(USER_CONFIG.load_user_config()["source"], "chaochun")

    def test_legacy_oil_config_env_still_works_without_new_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._bind_paths(root)
            config = root / "oil-config.json"
            config.write_text(
                json.dumps({"subtitles": {"progress_enabled": False}}),
                encoding="utf-8",
            )
            with self._patched(paths, OIL_SUBTITLE_CONFIG=str(config)):
                self.assertFalse(USER_CONFIG.resolve_progress_enabled())

    def test_progress_env_prefers_chaochun_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._bind_paths(Path(tmp))
            with self._patched(
                paths,
                CHAOCHUN_SUBTITLE_PROGRESS_ENABLED="0",
                OIL_SUBTITLE_PROGRESS_ENABLED="1",
            ):
                self.assertFalse(USER_CONFIG.resolve_progress_enabled())

    def test_glossary_prefers_new_file_then_legacy_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._bind_paths(Path(tmp))
            paths["chaochun"].mkdir()
            paths["oil"].mkdir()
            paths["preferred_glossary"].write_text("[]", encoding="utf-8")
            paths["oil_glossary"].write_text("[]", encoding="utf-8")
            with self._patched(paths):
                self.assertEqual(
                    USER_CONFIG.resolve_glossary_path(), paths["preferred_glossary"]
                )
            paths["preferred_glossary"].unlink()
            with self._patched(paths):
                self.assertEqual(
                    USER_CONFIG.resolve_glossary_path(), paths["oil_glossary"]
                )

    def test_api_key_reads_legacy_oil_file_when_chaochun_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._bind_paths(Path(tmp))
            paths["oil"].mkdir()
            paths["oil_key"].write_text("oil-secret\n", encoding="utf-8")
            paths["bailian"].parent.mkdir()
            paths["bailian"].write_text(
                json.dumps({"api_key": "bailian"}), encoding="utf-8"
            )
            with self._patched(paths):
                self.assertEqual(USER_CONFIG.load_dashscope_api_key(), "oil-secret")

    def test_save_writes_preferred_chaochun_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._bind_paths(Path(tmp))
            with self._patched(paths):
                saved = USER_CONFIG.save_dashscope_api_key("new-secret")
            self.assertEqual(saved, paths["preferred_key"])
            self.assertEqual(saved.read_text(encoding="utf-8").strip(), "new-secret")
            self.assertEqual(stat.S_IMODE(saved.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(paths["chaochun"].stat().st_mode), 0o700)

    def test_migrate_copies_oil_key_to_chaochun_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._bind_paths(Path(tmp))
            paths["oil"].mkdir()
            paths["oil_key"].write_text("legacy-oil-key\n", encoding="utf-8")
            with self._patched(paths):
                migrated = USER_CONFIG.migrate_legacy_dashscope_api_key_file()
                self.assertEqual(migrated, paths["preferred_key"])
                self.assertEqual(
                    paths["preferred_key"].read_text(encoding="utf-8").strip(),
                    "legacy-oil-key",
                )
                self.assertEqual(
                    stat.S_IMODE(paths["preferred_key"].stat().st_mode), 0o600
                )
                self.assertIsNone(USER_CONFIG.migrate_legacy_dashscope_api_key_file())

    def test_bailian_config_is_last_api_key_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._bind_paths(Path(tmp))
            paths["bailian"].parent.mkdir()
            paths["bailian"].write_text(
                json.dumps({"api_key": "from-bailian"}), encoding="utf-8"
            )
            with self._patched(paths):
                self.assertEqual(USER_CONFIG.load_dashscope_api_key(), "from-bailian")

    def test_vocabulary_cache_falls_back_to_oil_then_prefers_chaochun(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            preferred = cache / "chaochun-subtitle" / "vocabulary-cache.json"
            legacy = cache / "oil-subtitle" / "vocabulary-cache.json"
            with patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache)}, clear=False):
                self.assertEqual(
                    USER_CONFIG.default_vocabulary_cache_path(), preferred
                )
                legacy.parent.mkdir()
                legacy.write_text("{}", encoding="utf-8")
                self.assertEqual(USER_CONFIG.default_vocabulary_cache_path(), legacy)
                preferred.parent.mkdir()
                preferred.write_text("{}", encoding="utf-8")
                self.assertEqual(
                    USER_CONFIG.default_vocabulary_cache_path(), preferred
                )


if __name__ == "__main__":
    unittest.main()
