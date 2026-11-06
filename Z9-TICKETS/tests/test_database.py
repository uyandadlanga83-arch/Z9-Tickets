from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import Database, DuplicateTicketError


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite3")
        await self.db.initialize()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.temp_dir.cleanup()

    async def _create_ticket(self, *, user_id: int = 100, channel_id: int = 200) -> dict:
        number = await self.db.reserve_ticket_number(1, "general")
        return await self.db.create_ticket(
            guild_id=1,
            channel_id=channel_id,
            ticket_number=number,
            category_key="general",
            category_title="General Support",
            creator_id=user_id,
            channel_name=f"z9-general-{number:04d}",
            creator_name=f"member-{user_id}",
        )

    async def test_initializes_required_tables_and_ticket_counter(self) -> None:
        import sqlite3

        with sqlite3.connect(self.db.path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertTrue(
            {
                "tickets",
                "ticket_participants",
                "staff_assignments",
                "staff_activity",
                "user_blacklists",
                "ticket_feedback",
                "guild_settings",
                "ticket_counters",
                "ticket_events",
            }.issubset(tables)
        )
        await self.db.initialize()
        self.assertEqual(await self.db.reserve_ticket_number(1, "general"), 1)
        self.assertEqual(await self.db.reserve_ticket_number(1, "general"), 2)
        self.assertEqual(await self.db.reserve_ticket_number(1, "reports"), 1)

    async def test_ticket_persistence_duplicate_protection_and_cooldown(self) -> None:
        ticket = await self._create_ticket()
        self.assertEqual((await self.db.get_ticket(int(ticket["id"])))["channel_id"], 200)
        self.assertEqual(ticket["creator_name"], "member-100")
        self.assertEqual(await self.db.get_open_ticket_for_user(1, 100), ticket)
        self.assertTrue(await self.db.claim_ticket_record(int(ticket["id"]), 901))
        self.assertFalse(await self.db.claim_ticket_record(int(ticket["id"]), 902))

        number = await self.db.reserve_ticket_number(1, "reports")
        with self.assertRaises(DuplicateTicketError):
            await self.db.create_ticket(
                guild_id=1,
                channel_id=201,
                ticket_number=number,
                category_key="reports",
                category_title="Player Reports",
                creator_id=100,
                channel_name="z9-report-0001",
                creator_name="member-100",
            )

        closed_at = Database._now()
        self.assertTrue(await self.db.close_ticket_record(
            int(ticket["id"]),
            999,
            "Staff",
            closed_at,
            "Resolved",
        ))
        self.assertFalse(await self.db.close_ticket_record(
            int(ticket["id"]), 999, "Staff", closed_at, "Resolved"
        ))
        self.assertIsNone(await self.db.get_open_ticket_for_user(1, 100))
        self.assertGreater(await self.db.cooldown_remaining(1, 100, 300), 0)
        self.assertTrue(await self.db.reopen_ticket_record(int(ticket["id"]), Database._now()))
        self.assertEqual((await self.db.get_ticket(int(ticket["id"])))["status"], "open")

    async def test_settings_blacklist_feedback_and_staff_statistics(self) -> None:
        ticket = await self._create_ticket()
        await self.db.set_setting(1, "ticket_log_channel_id", "123")
        self.assertEqual((await self.db.get_settings(1))["ticket_log_channel_id"], "123")
        await self.db.reset_settings(1)
        self.assertEqual(await self.db.get_settings(1), {})

        await self.db.blacklist_user(1, 100, "Repeated spam", 900)
        self.assertEqual((await self.db.is_blacklisted(1, 100))["reason"], "Repeated spam")
        self.assertTrue(await self.db.unblacklist_user(1, 100))
        self.assertIsNone(await self.db.is_blacklisted(1, 100))

        await self.db.add_staff_activity(1, int(ticket["id"]), 901, "claim")
        await self.db.add_staff_activity(1, int(ticket["id"]), 901, "close")
        self.assertTrue(await self.db.save_feedback(int(ticket["id"]), 1, 100, 5))
        self.assertFalse(await self.db.save_feedback(int(ticket["id"]), 1, 100, 1))
        self.assertEqual((await self.db.get_feedback(int(ticket["id"])))["rating"], 5)
        self.assertEqual((await self.db.staff_stats(1, 901))["claims"], 1)
        self.assertEqual((await self.db.staff_stats(1, 901))["resolutions"], 1)
        self.assertEqual((await self.db.dashboard_stats(1))["feedback_count"], 1)


if __name__ == "__main__":
    unittest.main()
