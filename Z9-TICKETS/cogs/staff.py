"""Staff performance and leaderboard commands."""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from cogs.registry import staff_group, ticket_group
from utils.embeds import brand_embed
from utils.permissions import is_staff

ticket_group.add_command(staff_group)


@staff_group.command(name="stats", description="Show ticket activity for a staff member.")
@app_commands.describe(member="Staff member to inspect (defaults to you)")
async def staff_stats_command(
    interaction: discord.Interaction,
    member: discord.Member | None = None,
) -> None:
    bot: Any = interaction.client
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    target = member or interaction.user
    if not await is_staff(interaction.user, bot) or not await is_staff(target, bot):
        await interaction.response.send_message("Staff statistics are only available to authorized staff.", ephemeral=True)
        return
    stats = await bot.db.staff_stats(interaction.guild.id, target.id)
    embed = brand_embed("STAFF PERFORMANCE", f"Activity summary for {target.mention}.")
    embed.add_field(name="Claims", value=str(stats["claims"]), inline=True)
    embed.add_field(name="Resolved", value=str(stats["resolutions"]), inline=True)
    embed.add_field(name="Assignments", value=str(stats["assignments"]), inline=True)
    embed.add_field(name="Recorded actions", value=str(stats["actions"]), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@staff_group.command(name="leaderboard", description="Show the Z9 ticket staff leaderboard.")
async def staff_leaderboard_command(interaction: discord.Interaction) -> None:
    bot: Any = interaction.client
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    if not await is_staff(interaction.user, bot):
        await interaction.response.send_message("You need an authorized staff role to view the leaderboard.", ephemeral=True)
        return
    rows = await bot.db.leaderboard(interaction.guild.id)
    embed = brand_embed("STAFF LEADERBOARD", "Ranked by recorded ticket resolutions.")
    if rows:
        lines = [
            f"**{index}.** <@{row['staff_id']}> — {row['resolutions'] or 0} resolved · "
            f"{row['claims'] or 0} claimed"
            for index, row in enumerate(rows, start=1)
        ]
        embed.description = "\n".join(lines)
    else:
        embed.description = "No staff activity has been recorded yet."
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: Any) -> None:
    del bot
