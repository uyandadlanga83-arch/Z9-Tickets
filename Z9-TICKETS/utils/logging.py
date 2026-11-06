"""Ticket audit logging to SQLite and the configured Discord log channel."""

from __future__ import annotations

from typing import Any

import discord

from utils.embeds import log_embed


async def log_ticket_action(
    bot: Any,
    guild: discord.Guild,
    ticket: dict[str, Any],
    action: str,
    actor_id: int | None,
    details: str = "",
    *,
    staff_action: bool = False,
) -> None:
    await bot.db.add_event(guild.id, int(ticket["id"]), actor_id, action, details)
    if staff_action and actor_id:
        await bot.db.add_staff_activity(guild.id, int(ticket["id"]), actor_id, action, details)
    settings = await bot.db.get_settings(guild.id)
    channel_id = int(settings.get("ticket_log_channel_id") or bot.config.ticket_log_channel_id or 0)
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
    if not hasattr(channel, "send"):
        return
    try:
        await channel.send(embed=log_embed(action, ticket, actor_id, details))
    except (discord.Forbidden, discord.HTTPException):
        return
