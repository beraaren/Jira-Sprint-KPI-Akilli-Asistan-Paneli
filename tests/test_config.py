"""The active .env project must win over stale process environment values."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config  # noqa: E402


class ConfigReloadTests(unittest.TestCase):
    def test_env_file_project_is_reloaded_and_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            with patch.object(config, "ENV_PATH", env_file), patch.dict(
                os.environ, {"JIRA_PROJECT_KEY": "MS", "JIRA_PAT": "stale-token"}
            ):
                env_file.write_text(
                    "JIRA_PROJECT_KEY=RZN\nJIRA_PAT=current-token\n"
                    "JIRA_FIELD_STORY_POINTS=customfield_10028\n",
                    encoding="utf-8",
                )
                first = config.load_jira_config()
                self.assertEqual(first.project_key, "RZN")
                self.assertEqual(first.token, "current-token")
                self.assertTrue(first.can_fetch_directly)

                env_file.write_text(
                    "JIRA_PROJECT_KEY=TEST\nJIRA_PAT=next-token\n"
                    "JIRA_FIELD_STORY_POINTS=customfield_10028\n",
                    encoding="utf-8",
                )
                self.assertEqual(config.load_jira_config().project_key, "TEST")

    def test_settings_save_preserves_token_and_unrelated_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "# Other application setting\nOTHER_FLAG=keep\nJIRA_PAT=existing-token\n"
                "JIRA_PROJECT_KEY=MS\nJIRA_FIELD_STORY_POINTS=customfield_10028\n",
                encoding="utf-8",
            )
            with patch.object(config, "ENV_PATH", env_file):
                config.save_jira_settings({
                    "JIRA_PROJECT_KEY": "RZN", "JIRA_MONTHS_BACK": "12",
                    "JIRA_FIELD_ANALYST": "", "JIRA_SKIP_SSL": "false",
                })
                saved = config.load_jira_config()
                self.assertEqual(saved.project_key, "RZN")
                self.assertEqual(saved.token, "existing-token")
                self.assertEqual(saved.months_back, 12)
                self.assertIn("# Other application setting", env_file.read_text(encoding="utf-8"))
                self.assertIn("OTHER_FLAG=keep", env_file.read_text(encoding="utf-8"))

                with self.assertRaises(ValueError):
                    config.save_jira_settings({"JIRA_PROJECT_KEY": "RZN\nJIRA_PAT=wrong"})
                self.assertEqual(config.load_jira_config().token, "existing-token")


if __name__ == "__main__":
    unittest.main()
