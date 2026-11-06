"""Async SQLite repository for persistent ticket state."""

from __future__ import annotations

import aiosqlite
from pathlib import Path
from typing import Any


class DuplicateTicketError(Exception):
    """Raised when the user already has an active ticket in the guild."""


class _ManagedConnection:
    """Connection context that applies SQLite safety pragmas after opening."""

    def __init__(self, path: Path):
        self.connection = aiosqlite.connect(path, timeout=30)

    async def __aenter__(self) -> aiosqlite.Connection:
        db = await self.connection.__aenter__()
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA busy_timeout = 30000")
        return db

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.connection.__aexit__(exc_type, exc, traceback)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    async def _connect(self) -> _ManagedConnection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return _ManagedConnection(self.path)

    async def initialize(self) -> None:
        async with await self._connect() as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL UNIQUE,
                    ticket_number INTEGER NOT NULL,
                    category_key TEXT NOT NULL,
                    category_title TEXT NOT NULL,
                    creator_id INTEGER NOT NULL,
                    creator_name TEXT NOT NULL DEFAULT 'Unknown',
                    channel_name TEXT NOT NULL,
                    form_answers TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'closed', 'deleted')),
                    locked INTEGER NOT NULL DEFAULT 0,
                    priority TEXT NOT NULL DEFAULT 'NORMAL'
                        CHECK (priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')),
                    claimed_by INTEGER,
                    assigned_staff_id INTEGER,
                    assigned_staff_name TEXT,
                    closed_by_name TEXT,
                    ticket_message_id INTEGER,
                    feedback_channel_id INTEGER,
                    feedback_message_id INTEGER,
                    close_reason TEXT,
                    closed_by INTEGER,
                    created_at TEXT NOT NULL,
                    last_activity_at TEXT NOT NULL,
                    last_reminder_at TEXT,
                    closed_at TEXT,
                    UNIQUE(guild_id, category_key, ticket_number)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_ticket_per_user
                    ON tickets(guild_id, creator_id) WHERE status = 'open';
                CREATE INDEX IF NOT EXISTS idx_tickets_status_activity
                    ON tickets(status, last_activity_at);
                CREATE INDEX IF NOT EXISTS idx_tickets_creator
                    ON tickets(guild_id, creator_id, created_at);

                CREATE TABLE IF NOT EXISTS ticket_participants (
                    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL,
                    added_by INTEGER NOT NULL,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY(ticket_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS staff_assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                    staff_id INTEGER NOT NULL,
                    assigned_by INTEGER NOT NULL,
                    assigned_at TEXT NOT NULL,
                    unassigned_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_assignments_active
                    ON staff_assignments(ticket_id, unassigned_at);

                CREATE TABLE IF NOT EXISTS staff_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    ticket_id INTEGER REFERENCES tickets(id) ON DELETE SET NULL,
                    staff_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_staff_activity_guild
                    ON staff_activity(guild_id, created_at);

                CREATE TABLE IF NOT EXISTS user_blacklists (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    added_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS ticket_feedback (
                    ticket_id INTEGER PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                    comment TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER NOT NULL,
                    setting_key TEXT NOT NULL,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, setting_key)
                );
                CREATE TABLE IF NOT EXISTS ticket_counters (
                    guild_id INTEGER NOT NULL,
                    category_key TEXT NOT NULL,
                    last_number INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(guild_id, category_key)
                );
                CREATE TABLE IF NOT EXISTS ticket_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    ticket_id INTEGER REFERENCES tickets(id) ON DELETE SET NULL,
                    actor_id INTEGER,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_ticket
                    ON ticket_events(ticket_id, created_at);
                """
            )
            columns_cursor = await db.execute("PRAGMA table_info(tickets)")
            columns = {row["name"] for row in await columns_cursor.fetchall()}
            if "form_answers" not in columns:
                await db.execute("ALTER TABLE tickets ADD COLUMN form_answers TEXT NOT NULL DEFAULT '{}'")
            if "locked" not in columns:
                await db.execute("ALTER TABLE tickets ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")
            if "creator_name" not in columns:
                await db.execute("ALTER TABLE tickets ADD COLUMN creator_name TEXT NOT NULL DEFAULT 'Unknown'")
            if "assigned_staff_name" not in columns:
                await db.execute("ALTER TABLE tickets ADD COLUMN assigned_staff_name TEXT")
            if "closed_by_name" not in columns:
                await db.execute("ALTER TABLE tickets ADD COLUMN closed_by_name TEXT")
            await db.commit()

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    async def reserve_ticket_number(self, guild_id: int, category_key: str) -> int:
        async with await self._connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO ticket_counters(guild_id, category_key, last_number) VALUES (?, ?, 0)",
                (guild_id, category_key),
            )
            await db.execute(
                "UPDATE ticket_counters SET last_number = last_number + 1 WHERE guild_id = ? AND category_key = ?",
                (guild_id, category_key),
            )
            cursor = await db.execute(
                "SELECT last_number FROM ticket_counters WHERE guild_id = ? AND category_key = ?",
                (guild_id, category_key),
            )
            row = await cursor.fetchone()
            await db.commit()
            return int(row["last_number"])

    async def create_ticket(
        self,
        *,
        guild_id: int,
        channel_id: int,
        ticket_number: int,
        category_key: str,
        category_title: str,
        creator_id: int,
        channel_name: str,
        creator_name: str,
        form_answers: str = "{}",
        created_at: str | None = None,
    ) -> dict[str, Any]:
        now = created_at or self._now()
        async with await self._connect() as db:
            try:
                async with db.execute("BEGIN IMMEDIATE"):
                    cursor = await db.execute(
                        """
                        INSERT INTO tickets (
                            guild_id, channel_id, ticket_number, category_key, category_title,
                            creator_id, creator_name, channel_name, form_answers, created_at, last_activity_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            guild_id, channel_id, ticket_number, category_key, category_title,
                            creator_id, creator_name[:200], channel_name, form_answers, now, now,
                        ),
                    )
                    ticket_id = cursor.lastrowid
                    await db.execute(
                        "INSERT INTO ticket_participants(ticket_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
                        (ticket_id, creator_id, creator_id, now),
                    )
                    await db.execute(
                        """INSERT INTO ticket_events(guild_id, ticket_id, actor_id, action, details, created_at)
                           VALUES (?, ?, ?, 'created', ?, ?)""",
                        (guild_id, ticket_id, creator_id, creator_name[:500], now),
                    )
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                await db.rollback()
                if "idx_one_open_ticket_per_user" in str(exc) or "UNIQUE constraint failed: tickets.guild_id, tickets.creator_id" in str(exc):
                    raise DuplicateTicketError from exc
                raise
        result = await self.get_ticket(ticket_id)
        if result is None:
            raise RuntimeError("Ticket insert completed but the record could not be read.")
        return result

    async def get_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        async with await self._connect() as db:
            cursor = await db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
            return self._dict(await cursor.fetchone())

    async def get_ticket_by_channel(self, channel_id: int) -> dict[str, Any] | None:
        async with await self._connect() as db:
            cursor = await db.execute("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))
            return self._dict(await cursor.fetchone())

    async def get_open_ticket_for_user(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        async with await self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM tickets WHERE guild_id = ? AND creator_id = ? AND status = 'open' LIMIT 1",
                (guild_id, user_id),
            )
            return self._dict(await cursor.fetchone())

    async def cooldown_remaining(self, guild_id: int, user_id: int, cooldown_seconds: int) -> int:
        if cooldown_seconds <= 0:
            return 0
        async with await self._connect() as db:
            cursor = await db.execute(
                """SELECT closed_at FROM tickets
                   WHERE guild_id = ? AND creator_id = ? AND closed_at IS NOT NULL
                   ORDER BY closed_at DESC LIMIT 1""",
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
        if not row:
            return 0
        from datetime import datetime, timezone

        closed_at = datetime.fromisoformat(row["closed_at"])
        return max(0, cooldown_seconds - int((datetime.now(timezone.utc) - closed_at).total_seconds()))

    async def update_ticket(self, ticket_id: int, **fields: Any) -> None:
        allowed = {
            "status", "locked", "priority", "claimed_by", "assigned_staff_id", "assigned_staff_name",
            "ticket_message_id", "feedback_channel_id", "feedback_message_id", "close_reason", "closed_by",
            "closed_by_name",
            "closed_at", "last_activity_at", "last_reminder_at", "channel_name", "form_answers",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported ticket fields: {', '.join(sorted(invalid))}")
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        async with await self._connect() as db:
            cursor = await db.execute(
                f"UPDATE tickets SET {assignments} WHERE id = ?",
                (*fields.values(), ticket_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("Ticket does not exist.")
            await db.commit()

    async def claim_ticket_record(self, ticket_id: int, staff_id: int) -> bool:
        async with await self._connect() as db:
            cursor = await db.execute(
                """UPDATE tickets SET claimed_by = ?
                   WHERE id = ? AND status = 'open' AND (claimed_by IS NULL OR claimed_by = ?)""",
                (staff_id, ticket_id, staff_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def close_ticket_record(
        self,
        ticket_id: int,
        closed_by: int,
        closed_by_name: str,
        closed_at: str,
        reason: str,
    ) -> bool:
        async with await self._connect() as db:
            cursor = await db.execute(
                """UPDATE tickets SET status = 'closed', closed_by = ?, closed_by_name = ?,
                       closed_at = ?, close_reason = ?
                   WHERE id = ? AND status = 'open'""",
                (closed_by, closed_by_name[:200], closed_at, reason[:1000], ticket_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def reopen_ticket_record(self, ticket_id: int, last_activity_at: str) -> bool:
        async with await self._connect() as db:
            cursor = await db.execute(
                """UPDATE tickets
                   SET status = 'open', locked = 0, closed_at = NULL, closed_by = NULL,
                       closed_by_name = NULL, close_reason = NULL,
                       last_activity_at = ?, last_reminder_at = NULL
                   WHERE id = ? AND status = 'closed'""",
                (last_activity_at, ticket_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def mark_ticket_deleted(self, ticket_id: int) -> bool:
        async with await self._connect() as db:
            cursor = await db.execute(
                "UPDATE tickets SET status = 'deleted' WHERE id = ? AND status != 'deleted'",
                (ticket_id,),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def add_event(
        self,
        guild_id: int,
        ticket_id: int | None,
        actor_id: int | None,
        action: str,
        details: str = "",
    ) -> None:
        async with await self._connect() as db:
            await db.execute(
                """INSERT INTO ticket_events(guild_id, ticket_id, actor_id, action, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (guild_id, ticket_id, actor_id, action[:100], details[:2000], self._now()),
            )
            await db.commit()

    async def add_staff_activity(
        self, guild_id: int, ticket_id: int | None, staff_id: int, action: str, details: str = ""
    ) -> None:
        async with await self._connect() as db:
            await db.execute(
                """INSERT INTO staff_activity(guild_id, ticket_id, staff_id, action, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (guild_id, ticket_id, staff_id, action[:100], details[:2000], self._now()),
            )
            await db.commit()

    async def set_setting(self, guild_id: int, key: str, value: str) -> None:
        async with await self._connect() as db:
            await db.execute(
                """INSERT INTO guild_settings(guild_id, setting_key, setting_value, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(guild_id, setting_key) DO UPDATE SET
                     setting_value = excluded.setting_value, updated_at = excluded.updated_at""",
                (guild_id, key, value, self._now()),
            )
            await db.commit()

    async def get_settings(self, guild_id: int) -> dict[str, str]:
        async with await self._connect() as db:
            cursor = await db.execute(
                "SELECT setting_key, setting_value FROM guild_settings WHERE guild_id = ?",
                (guild_id,),
            )
            return {row["setting_key"]: row["setting_value"] for row in await cursor.fetchall()}

    async def reset_settings(self, guild_id: int) -> None:
        async with await self._connect() as db:
            await db.execute("DELETE FROM guild_settings WHERE guild_id = ?", (guild_id,))
            await db.commit()

    async def add_participant(self, ticket_id: int, user_id: int, added_by: int) -> None:
        async with await self._connect() as db:
            await db.execute(
                """INSERT INTO ticket_participants(ticket_id, user_id, added_by, added_at)
                   VALUES (?, ?, ?, ?) ON CONFLICT(ticket_id, user_id) DO NOTHING""",
                (ticket_id, user_id, added_by, self._now()),
            )
            await db.commit()

    async def remove_participant(self, ticket_id: int, user_id: int) -> bool:
        async with await self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM ticket_participants WHERE ticket_id = ? AND user_id = ?",
                (ticket_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def add_assignment(
        self, ticket_id: int, staff_id: int, assigned_by: int, staff_name: str = ""
    ) -> None:
        now = self._now()
        async with await self._connect() as db:
            await db.execute(
                "UPDATE staff_assignments SET unassigned_at = ? WHERE ticket_id = ? AND unassigned_at IS NULL",
                (now, ticket_id),
            )
            await db.execute(
                """INSERT INTO staff_assignments(ticket_id, staff_id, assigned_by, assigned_at)
                   VALUES (?, ?, ?, ?)""",
                (ticket_id, staff_id, assigned_by, now),
            )
            await db.execute(
                "UPDATE tickets SET assigned_staff_id = ?, assigned_staff_name = ? WHERE id = ?",
                (staff_id, staff_name[:200] or None, ticket_id),
            )
            await db.commit()

    async def unassign(self, ticket_id: int) -> None:
        now = self._now()
        async with await self._connect() as db:
            await db.execute(
                "UPDATE staff_assignments SET unassigned_at = ? WHERE ticket_id = ? AND unassigned_at IS NULL",
                (now, ticket_id),
            )
            await db.execute(
                "UPDATE tickets SET assigned_staff_id = NULL, assigned_staff_name = NULL WHERE id = ?",
                (ticket_id,),
            )
            await db.commit()

    async def get_participants(self, ticket_id: int) -> list[int]:
        async with await self._connect() as db:
            cursor = await db.execute("SELECT user_id FROM ticket_participants WHERE ticket_id = ?", (ticket_id,))
            return [int(row["user_id"]) for row in await cursor.fetchall()]

    async def blacklist_user(self, guild_id: int, user_id: int, reason: str, added_by: int) -> None:
        async with await self._connect() as db:
            await db.execute(
                """INSERT INTO user_blacklists(guild_id, user_id, reason, added_by, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(guild_id, user_id) DO UPDATE SET
                     reason = excluded.reason, added_by = excluded.added_by, created_at = excluded.created_at""",
                (guild_id, user_id, reason[:1000], added_by, self._now()),
            )
            await db.commit()

    async def unblacklist_user(self, guild_id: int, user_id: int) -> bool:
        async with await self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM user_blacklists WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def is_blacklisted(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        async with await self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM user_blacklists WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            return self._dict(await cursor.fetchone())

    async def save_feedback(
        self, ticket_id: int, guild_id: int, user_id: int, rating: int, comment: str = ""
    ) -> bool:
        if rating not in range(1, 6):
            raise ValueError("Feedback rating must be between 1 and 5.")
        async with await self._connect() as db:
            cursor = await db.execute(
                """INSERT INTO ticket_feedback(ticket_id, guild_id, user_id, rating, comment, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ticket_id) DO NOTHING""",
                (ticket_id, guild_id, user_id, rating, comment[:1000], self._now()),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def get_feedback(self, ticket_id: int) -> dict[str, Any] | None:
        async with await self._connect() as db:
            cursor = await db.execute("SELECT * FROM ticket_feedback WHERE ticket_id = ?", (ticket_id,))
            return self._dict(await cursor.fetchone())

    async def get_feedback_ticket_ids(self) -> list[dict[str, Any]]:
        async with await self._connect() as db:
            cursor = await db.execute(
                """SELECT t.id, t.guild_id, t.creator_id, t.feedback_channel_id, t.feedback_message_id
                   FROM tickets t LEFT JOIN ticket_feedback f ON f.ticket_id = t.id
                   WHERE t.status IN ('closed', 'deleted')
                     AND t.feedback_message_id IS NOT NULL AND f.ticket_id IS NULL"""
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def list_open_tickets(self) -> list[dict[str, Any]]:
        async with await self._connect() as db:
            cursor = await db.execute("SELECT * FROM tickets WHERE status = 'open'")
            return [dict(row) for row in await cursor.fetchall()]

    async def list_guild_tickets(self, guild_id: int) -> list[dict[str, Any]]:
        async with await self._connect() as db:
            cursor = await db.execute(
                "SELECT * FROM tickets WHERE guild_id = ? AND status != 'deleted'",
                (guild_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def dashboard_stats(self, guild_id: int) -> dict[str, Any]:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        week = (now - timedelta(days=7)).isoformat()
        async with await self._connect() as db:
            cursor = await db.execute(
                """SELECT
                     COUNT(*) AS total,
                     SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
                     SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed_count,
                     SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS today_count,
                     SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS week_count,
                     AVG(CASE WHEN status = 'closed' AND closed_at IS NOT NULL
                              THEN (julianday(closed_at) - julianday(created_at)) * 86400 END) AS avg_resolution_seconds
                   FROM tickets WHERE guild_id = ? AND status != 'deleted'""",
                (today, week, guild_id),
            )
            totals = dict(await cursor.fetchone())
            cursor = await db.execute(
                """SELECT category_title, COUNT(*) AS count FROM tickets
                   WHERE guild_id = ? AND status != 'deleted'
                   GROUP BY category_key ORDER BY count DESC, category_title LIMIT 1""",
                (guild_id,),
            )
            popular = await cursor.fetchone()
            cursor = await db.execute(
                "SELECT AVG(rating) AS average, COUNT(*) AS count FROM ticket_feedback WHERE guild_id = ?",
                (guild_id,),
            )
            feedback = dict(await cursor.fetchone())
            cursor = await db.execute(
                """SELECT staff_id,
                     SUM(CASE WHEN action = 'claim' THEN 1 ELSE 0 END) AS claims,
                     SUM(CASE WHEN action IN ('close', 'auto_close') THEN 1 ELSE 0 END) AS resolutions,
                     COUNT(*) AS actions
                   FROM staff_activity WHERE guild_id = ?
                   GROUP BY staff_id ORDER BY resolutions DESC, claims DESC, actions DESC LIMIT 5""",
                (guild_id,),
            )
            staff = [dict(row) for row in await cursor.fetchall()]
        return {
            **totals,
            "popular_category": popular["category_title"] if popular else "—",
            "average_rating": feedback["average"],
            "feedback_count": feedback["count"],
            "staff_activity": staff,
        }

    async def staff_stats(self, guild_id: int, staff_id: int) -> dict[str, int]:
        async with await self._connect() as db:
            cursor = await db.execute(
                """SELECT COUNT(*) AS actions,
                     SUM(CASE WHEN action = 'claim' THEN 1 ELSE 0 END) AS claims,
                     SUM(CASE WHEN action IN ('close', 'auto_close') THEN 1 ELSE 0 END) AS resolutions,
                     SUM(CASE WHEN action = 'assign' THEN 1 ELSE 0 END) AS assignments
                   FROM staff_activity WHERE guild_id = ? AND staff_id = ?""",
                (guild_id, staff_id),
            )
            row = dict(await cursor.fetchone())
            return {key: int(value or 0) for key, value in row.items()}

    async def leaderboard(self, guild_id: int, limit: int = 10) -> list[dict[str, Any]]:
        async with await self._connect() as db:
            cursor = await db.execute(
                """SELECT staff_id,
                     SUM(CASE WHEN action IN ('close', 'auto_close') THEN 1 ELSE 0 END) AS resolutions,
                     SUM(CASE WHEN action = 'claim' THEN 1 ELSE 0 END) AS claims,
                     COUNT(*) AS actions
                   FROM staff_activity WHERE guild_id = ?
                   GROUP BY staff_id ORDER BY resolutions DESC, claims DESC, actions DESC LIMIT ?""",
                (guild_id, limit),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def close(self) -> None:
        """Connections are short-lived; retained for clean bot shutdown symmetry."""
