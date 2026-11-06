from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from utils.transcripts import generate_transcript


class FakeChannel:
    def __init__(self, message: object):
        self.message = message

    async def _messages(self):
        yield self.message

    def history(self, **kwargs):
        del kwargs
        return self._messages()


class TranscriptTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcript_includes_ticket_metadata_and_escapes_user_content(self) -> None:
        author = SimpleNamespace(
            display_name="<script>alert(1)</script>",
            name="player",
            id=123,
            display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/avatar.png"),
        )
        message = SimpleNamespace(
            author=author,
            content="<img src=x onerror=alert(1)>",
            attachments=[
                SimpleNamespace(filename="evidence <one>.png", url="https://cdn.discordapp.com/file.png", size=123)
            ],
            embeds=[
                SimpleNamespace(
                    title="Ticket Intake",
                    description=None,
                    fields=[SimpleNamespace(name="Report details", value="Account issue")],
                )
            ],
            created_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
        )
        ticket = {
            "id": 5,
            "guild_id": 1,
            "ticket_number": 17,
            "category_title": "Player Reports",
            "creator_id": 10,
            "creator_name": "Clan member",
            "assigned_staff_id": 11,
            "assigned_staff_name": "Z9 Mod",
            "closed_by": 12,
            "closed_by_name": "Z9 Lead",
            "close_reason": "Resolved",
            "created_at": "2026-11-06T00:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = await generate_transcript(
                FakeChannel(message),  # type: ignore[arg-type]
                ticket,
                Path(temporary),
                closing_staff_id=12,
                closing_reason="Resolved",
            )
            html = path.read_text(encoding="utf-8")
        self.assertIn("Player Reports", html)
        self.assertIn("Clan member (ID 10)", html)
        self.assertIn("Z9 Mod (ID 11)", html)
        self.assertIn("Z9 Lead (ID 12)", html)
        self.assertIn("Closing reason:</b> Resolved", html)
        self.assertIn("Report details", html)
        self.assertIn("Account issue", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertNotIn("<img src=x onerror=alert(1)>", html)
        self.assertIn("evidence &lt;one&gt;.png", html)


if __name__ == "__main__":
    unittest.main()
