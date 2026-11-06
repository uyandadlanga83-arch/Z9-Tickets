"""Ticket analytics command and formatting."""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from cogs.registry import ticket_group
from utils.embeds import brand_embed
from utils.permissions import is_staff


def _duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    minutes = max(0, int(seconds // 60))
    days, remainder = divmod(minutes, 1440)
    hours, minutes = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


@ticket_group.command(name="stats", description="Show ticket volume, resolution, category, and staff statistics.")
async def ticket_stats(interaction: discord.Interaction) -> None:
    bot: Any = interaction.client
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    if not await is_staff(interaction.user, bot):
        await interaction.response.send_message("You need an authorized staff role to view ticket statistics.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    stats = await bot.db.dashboard_stats(interaction.guild.id)
    embed = brand_embed("TICKET OPERATIONS", "Z9 support activity at a glance.")
    embed.add_field(name="Total", value=str(stats["total"] or 0), inline=True)
    embed.add_field(name="Open", value=str(stats["open_count"] or 0), inline=True)
    embed.add_field(name="Closed", value=str(stats["closed_count"] or 0), inline=True)
    embed.add_field(name="Today", value=str(stats["today_count"] or 0), inline=True)
    embed.add_field(name="Last 7 days", value=str(stats["week_count"] or 0), inline=True)
    embed.add_field(name="Avg. resolution", value=_duration(stats["avg_resolution_seconds"]), inline=True)
    embed.add_field(name="Top category", value=str(stats["popular_category"]), inline=True)
    average = stats["average_rating"]
    rating = f"{float(average):.2f}/5 ({stats['feedback_count']} ratings)" if average is not None else "No ratings yet"
    embed.add_field(name="Feedback", value=rating, inline=True)
    activity = stats["staff_activity"]
    if activity:
        lines = [
            f"<@{row['staff_id']}> — {row['resolutions'] or 0} resolved · {row['claims'] or 0} claimed"
            for row in activity
        ]
        embed.add_field(name="Staff activity", value="\n".join(lines)[:1024], inline=False)
    else:
        embed.add_field(name="Staff activity", value="No staff actions have been recorded yet.", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: Any) -> None:
    # The shared group is registered by ticket_admin after all extensions load.
    del bot
