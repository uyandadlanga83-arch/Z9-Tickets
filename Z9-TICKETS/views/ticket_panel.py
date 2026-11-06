"""Persistent category selector for the Z9 support panel."""

from __future__ import annotations

from typing import Any

import discord

from config import CATEGORIES
from modals.ticket_forms import TicketFormModal
from utils.embeds import brand_embed, panel_embed


class TicketCategorySelect(discord.ui.Select):
    def __init__(self, bot: Any):
        self.bot = bot
        options = [
            discord.SelectOption(
                label=str(category["title"]),
                value=key,
                description=str(category["description"])[:100],
                emoji=str(category["emoji"]),
            )
            for key, category in CATEGORIES.items()
        ]
        super().__init__(
            placeholder="Select a support category…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="z9:tickets:category",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Open a ticket from a Z9 server.", ephemeral=True)
            return
        blocked = await self.bot.db.is_blacklisted(interaction.guild.id, interaction.user.id)
        if blocked:
            await interaction.response.send_message(
                f"You cannot open tickets here. Reason: {blocked['reason']}", ephemeral=True
            )
            return
        existing = await self.bot.db.get_open_ticket_for_user(interaction.guild.id, interaction.user.id)
        if existing:
            await interaction.response.send_message(
                f"You already have an open ticket: <#{existing['channel_id']}>.", ephemeral=True
            )
            return
        settings = await self.bot.db.get_settings(interaction.guild.id)
        cooldown = int(settings.get("ticket_cooldown_seconds", self.bot.config.ticket_cooldown_seconds))
        remaining = await self.bot.db.cooldown_remaining(interaction.guild.id, interaction.user.id, cooldown)
        if remaining:
            await interaction.response.send_message(
                f"Please wait {remaining} seconds before opening another ticket.", ephemeral=True
            )
            return
        category_key = self.values[0]
        if category_key not in CATEGORIES:
            await interaction.response.send_message("That support category is unavailable.", ephemeral=True)
            return
        await interaction.response.send_modal(TicketFormModal(self.bot, category_key))


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: Any):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect(bot))


async def send_ticket_panel(bot: Any, channel: discord.abc.Messageable) -> discord.Message:
    return await channel.send(embed=panel_embed(), view=TicketPanelView(bot))


def setup_confirmation_embed(channel: discord.abc.Messageable) -> discord.Embed:
    return brand_embed("PANEL DEPLOYED", f"The Z9 ticket panel is now available in {channel.mention}.")
