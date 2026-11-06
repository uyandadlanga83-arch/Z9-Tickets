"""Ticket message activity listener."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands


class TicketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("z9_tickets")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        ticket = await self.bot.db.get_ticket_by_channel(message.channel.id)
        if ticket and ticket["status"] == "open":
            await self.bot.db.update_ticket(
                int(ticket["id"]),
                last_activity_at=discord.utils.utcnow().isoformat(),
                last_reminder_at=None,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketsCog(bot))
