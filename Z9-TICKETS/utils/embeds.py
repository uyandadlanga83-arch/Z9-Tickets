"""Consistent Z9-branded Discord embeds."""

from __future__ import annotations

import json
from typing import Any

import discord

from config import CATEGORIES

Z9_RED = discord.Color.from_rgb(220, 38, 55)
Z9_DARK = discord.Color.from_rgb(22, 24, 29)
PRIORITY_COLORS = {
    "LOW": discord.Color.from_rgb(76, 175, 80),
    "NORMAL": discord.Color.from_rgb(220, 38, 55),
    "HIGH": discord.Color.from_rgb(255, 152, 0),
    "URGENT": discord.Color.from_rgb(183, 28, 28),
}


def brand_embed(title: str, description: str | None = None, *, color: discord.Color = Z9_RED) -> discord.Embed:
    embed = discord.Embed(title=f"Z9 TICKETS  /  {title}", description=description, color=color)
    embed.set_footer(text="Z9 • Support that keeps you moving")
    return embed


def panel_embed() -> discord.Embed:
    embed = brand_embed(
        "SUPPORT CENTER",
        "Choose the category that best matches your request. Z9 staff will respond in a private ticket.",
    )
    for key, category in CATEGORIES.items():
        embed.add_field(
            name=f"{category['emoji']}  {category['title']}",
            value=str(category["description"]),
            inline=False,
        )
    embed.add_field(
        name="Before opening",
        value="Please submit one request at a time and include clear, accurate details.",
        inline=False,
    )
    return embed


def ticket_embed(ticket: dict[str, Any], guild: discord.Guild | None = None) -> discord.Embed:
    priority = str(ticket.get("priority", "NORMAL"))
    category_key = str(ticket.get("category_key", "other"))
    category = CATEGORIES.get(category_key, {})
    state = str(ticket.get("status", "open")).upper()
    embed = brand_embed(
        f"{ticket.get('category_title', 'SUPPORT').upper()}  /  #{int(ticket['ticket_number']):04d}",
        f"**Status:** {state}  •  **Priority:** {priority}\n"
        "Keep replies focused on this request. Staff actions are recorded for moderation and reporting.",
        color=PRIORITY_COLORS.get(priority, Z9_RED),
    )
    owner_name = ticket.get("creator_name") or "Ticket owner"
    embed.add_field(
        name="Ticket owner",
        value=f"{owner_name} • <@{ticket['creator_id']}> (`{ticket['creator_id']}`)",
        inline=True,
    )
    embed.add_field(name="Category", value=str(category.get("title", ticket.get("category_title", "Other"))), inline=True)
    embed.add_field(name="Priority", value=priority, inline=True)
    embed.add_field(name="Channel lock", value="Locked" if ticket.get("locked") else "Open", inline=True)
    claimed_by = ticket.get("claimed_by")
    assigned = ticket.get("assigned_staff_id")
    embed.add_field(name="Claimed by", value=f"<@{claimed_by}>" if claimed_by else "Unclaimed", inline=True)
    assigned_name = ticket.get("assigned_staff_name") or "Staff member"
    embed.add_field(
        name="Assigned staff",
        value=f"{assigned_name} • <@{assigned}>" if assigned else "Unassigned",
        inline=True,
    )
    if ticket.get("closed_by"):
        closed_name = ticket.get("closed_by_name") or "Staff member"
        embed.add_field(name="Closed by", value=f"{closed_name} • <@{ticket['closed_by']}>", inline=True)
    if ticket.get("close_reason"):
        embed.add_field(name="Closing reason", value=str(ticket["close_reason"])[:1024], inline=False)

    try:
        answers = json.loads(ticket.get("form_answers") or "{}")
    except (TypeError, json.JSONDecodeError):
        answers = {}
    if isinstance(answers, dict):
        for index, (question, answer) in enumerate(answers.items()):
            if index >= 3:
                break
            value = str(answer).strip() or "—"
            embed.add_field(name=str(question)[:256], value=value[:1024], inline=False)
    embed.timestamp = discord.utils.parse_time(ticket.get("created_at"))
    return embed


def log_embed(action: str, ticket: dict[str, Any], actor_id: int | None, details: str = "") -> discord.Embed:
    embed = brand_embed(
        f"TICKET {action.upper()}",
        f"Ticket <#{ticket['channel_id']}> • `{ticket['category_key']}` • #{int(ticket['ticket_number']):04d}",
        color=Z9_DARK,
    )
    if actor_id:
        embed.add_field(name="Actor", value=f"<@{actor_id}> (`{actor_id}`)", inline=True)
    if details:
        embed.add_field(name="Details", value=details[:1024], inline=False)
    embed.add_field(name="Owner", value=f"<@{ticket['creator_id']}>", inline=True)
    return embed
