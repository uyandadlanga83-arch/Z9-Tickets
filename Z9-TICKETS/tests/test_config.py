from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import CATEGORIES, load_config


class ConfigTests(unittest.TestCase):
    def test_empty_optional_paths_fall_back_under_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            values = {
                "DISCORD_TOKEN": "",
                "GUILD_ID": "",
                "TICKET_CATEGORY_ID": "",
                "TICKET_LOG_CHANNEL_ID": "",
                "TRANSCRIPT_CHANNEL_ID": "",
                "TICKET_PANEL_CHANNEL_ID": "",
                "STAFF_ROLE_IDS": "",
                "ADMIN_ROLE_IDS": "",
                "CATEGORY_STAFF_ROLE_IDS_JSON": "",
                "INACTIVITY_TIMEOUT_MINUTES": "1440",
                "AUTO_CLOSE_TIMEOUT_MINUTES": "0",
                "TICKET_COOLDOWN_SECONDS": "0",
                "DATA_DIR": temporary,
                "DATABASE_PATH": "",
                "TRANSCRIPT_DIR": "",
            }
            with patch.dict(os.environ, values):
                config = load_config()
        self.assertEqual(config.database_path, Path(temporary) / "z9_tickets.sqlite3")
        self.assertEqual(config.transcript_dir, Path(temporary) / "transcripts")
        self.assertEqual(config.auto_close_timeout_minutes, 0)

    def test_only_five_requested_categories_are_configured(self) -> None:
        self.assertEqual(
            set(CATEGORIES),
            {"general", "reports", "applications", "partnerships", "other"},
        )
        self.assertTrue(all(len(category["questions"]) == 3 for category in CATEGORIES.values()))


if __name__ == "__main__":
    unittest.main()
