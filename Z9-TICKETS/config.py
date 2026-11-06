"""Environment-backed configuration for Z9 Tickets."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


CATEGORIES: dict[str, dict[str, object]] = {
    "general": {
        "title": "General Support",
        "emoji": "🛟",
        "description": "Questions, account help, and general clan support.",
        "channel_prefix": "general",
        "questions": (
            ("What do you need help with?", "Briefly describe the issue."),
            ("Which game or platform?", "Game, platform, and relevant username."),
            ("What have you tried so far?", "Any steps already taken or useful context."),
        ),
    },
    "reports": {
        "title": "Player Reports",
        "emoji": "⚠️",
        "description": "Report player conduct or share evidence with staff.",
        "channel_prefix": "report",
        "questions": (
            ("Who are you reporting?", "Player name and, if possible, Discord ID."),
            ("What happened?", "Give a clear, factual description."),
            ("When did it happen and what proves it?", "Time/date, match, and evidence links."),
        ),
    },
    "applications": {
        "title": "Staff Applications",
        "emoji": "📝",
        "description": "Apply to join the Z9 staff team.",
        "channel_prefix": "application",
        "questions": (
            ("What role are you applying for?", "The staff role you are interested in."),
            ("Why would you be a good fit?", "Relevant experience and strengths."),
            ("How active are you, and in what time zone?", "Typical availability and time zone."),
        ),
    },
    "partnerships": {
        "title": "Partnerships",
        "emoji": "🤝",
        "description": "Discuss a community or organization partnership.",
        "channel_prefix": "partner",
        "questions": (
            ("What is your community or organization?", "Name and primary platform."),
            ("What partnership are you proposing?", "Describe the collaboration."),
            ("Share your links and approximate community size.", "Invite or public links and member count."),
        ),
    },
    "other": {
        "title": "Other Issues",
        "emoji": "💬",
        "description": "Anything that does not fit the other categories.",
        "channel_prefix": "other",
        "questions": (
            ("What is this about?", "A short summary of the issue."),
            ("Which game, platform, or area is involved?", "Add relevant context."),
            ("What outcome are you looking for?", "Tell staff how they can help."),
        ),
    },
}

SETTING_KEYS = {
    "ticket_category_id",
    "ticket_log_channel_id",
    "transcript_channel_id",
    "ticket_panel_channel_id",
    "staff_role_ids",
    "admin_role_ids",
    "category_staff_role_ids",
    "inactivity_timeout_minutes",
    "auto_close_timeout_minutes",
    "ticket_cooldown_seconds",
}


def _optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if result <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return result


def _int_value(name: str, default: int, *, minimum: int = 0) -> int:
    value = os.getenv(name, str(default)).strip()
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if result < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return result


def _parse_role_ids(value: str) -> tuple[int, ...]:
    if not value.strip():
        return ()
    role_ids: list[int] = []
    for item in value.split(","):
        cleaned = item.strip()
        if not cleaned:
            continue
        if not cleaned.isdecimal() or int(cleaned) <= 0:
            raise ValueError("Role IDs must be positive Discord IDs separated by commas.")
        role_ids.append(int(cleaned))
    return tuple(dict.fromkeys(role_ids))


def _parse_category_roles(value: str) -> dict[str, tuple[int, ...]]:
    if not value.strip():
        return {}
    try:
        raw = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("CATEGORY_STAFF_ROLE_IDS_JSON must be valid JSON.") from exc
    if not isinstance(raw, dict):
        raise ValueError("CATEGORY_STAFF_ROLE_IDS_JSON must be a JSON object.")
    result: dict[str, tuple[int, ...]] = {}
    for key, role_ids in raw.items():
        if key not in CATEGORIES or not isinstance(role_ids, list):
            raise ValueError(f"Invalid category role entry: {key!r}.")
        converted = _parse_role_ids(",".join(str(role_id) for role_id in role_ids))
        result[key] = converted
    return result


@dataclass(frozen=True)
class Config:
    token: str
    guild_id: int | None
    ticket_category_id: int | None
    ticket_log_channel_id: int | None
    transcript_channel_id: int | None
    ticket_panel_channel_id: int | None
    staff_role_ids: tuple[int, ...]
    admin_role_ids: tuple[int, ...]
    category_staff_role_ids: dict[str, tuple[int, ...]]
    inactivity_timeout_minutes: int
    auto_close_timeout_minutes: int
    ticket_cooldown_seconds: int
    data_dir: Path
    database_path: Path
    transcript_dir: Path


def load_config() -> Config:
    data_dir = Path(os.getenv("DATA_DIR", "data")).expanduser()
    database_value = os.getenv("DATABASE_PATH", "").strip()
    transcript_value = os.getenv("TRANSCRIPT_DIR", "").strip()
    database_path = Path(database_value or data_dir / "z9_tickets.sqlite3").expanduser()
    transcript_dir = Path(transcript_value or data_dir / "transcripts").expanduser()
    return Config(
        token=os.getenv("DISCORD_TOKEN", "").strip(),
        guild_id=_optional_int("GUILD_ID"),
        ticket_category_id=_optional_int("TICKET_CATEGORY_ID"),
        ticket_log_channel_id=_optional_int("TICKET_LOG_CHANNEL_ID"),
        transcript_channel_id=_optional_int("TRANSCRIPT_CHANNEL_ID"),
        ticket_panel_channel_id=_optional_int("TICKET_PANEL_CHANNEL_ID"),
        staff_role_ids=_parse_role_ids(os.getenv("STAFF_ROLE_IDS", "")),
        admin_role_ids=_parse_role_ids(os.getenv("ADMIN_ROLE_IDS", "")),
        category_staff_role_ids=_parse_category_roles(os.getenv("CATEGORY_STAFF_ROLE_IDS_JSON", "")),
        inactivity_timeout_minutes=_int_value("INACTIVITY_TIMEOUT_MINUTES", 1440, minimum=1),
        auto_close_timeout_minutes=_int_value("AUTO_CLOSE_TIMEOUT_MINUTES", 10080),
        ticket_cooldown_seconds=_int_value("TICKET_COOLDOWN_SECONDS", 60),
        data_dir=data_dir,
        database_path=database_path,
        transcript_dir=transcript_dir,
    )
