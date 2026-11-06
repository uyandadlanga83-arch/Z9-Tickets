"""Persistent one-to-five-star feedback controls."""

from __future__ import annotations

from typing import Any

import discord

from utils.embeds import brand_embed
from utils.logging import log_ticket_action


class FeedbackButton(discord.ui.Button):
    def __init__(self, ticket_id: int, rating: int):
        super().__init__(
            label=f"{rating} star" if rating == 1 else f"{rating} stars",
            emoji="⭐",
            style=discord.ButtonStyle.secondary,
            custom_id=f"z9:feedback:{ticket_id}:{rating}",
        )
        self.rating = rating

    async def callback(self, interaction: discord.Interaction) -> None:
        view: FeedbackView = self.view  # type: ignore[assignment]
        await view.submit(interaction, self.rating)


class FeedbackView(discord.ui.View):
    def __init__(self, ticket_id: int, guild_id: int, owner_id: int):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id
        self.guild_id = guild_id
        self.owner_id = owner_id
        for rating in range(1, 6):
            self.add_item(FeedbackButton(ticket_id, rating))

    async def submit(self, interaction: discord.Interaction, rating: int) -> None:
        bot: Any = getattr(interaction.client, "bot_instance", interaction.client)
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Only the ticket owner can leave this rating.", ephemeral=True)
            return
        ticket = await bot.db.get_ticket(self.ticket_id)
        if not ticket or ticket["status"] not in {"closed", "deleted"}:
            await interaction.response.send_message("Feedback is available after a ticket closes.", ephemeral=True)
            return
        existing = await bot.db.get_feedback(self.ticket_id)
        if existing:
            await interaction.response.send_message("A rating has already been recorded for this ticket.", ephemeral=True)
            return
        await interaction.response.defer()
        if not await bot.db.save_feedback(self.ticket_id, self.guild_id, self.owner_id, rating):
            await interaction.followup.send("A rating has already been recorded for this ticket.", ephemeral=True)
            return
        guild = bot.get_guild(self.guild_id)
        if guild:
            await log_ticket_action(
                bot, guild, ticket, "feedback", self.owner_id, f"{rating}/5 stars"
            )
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        try:
            await interaction.edit_original_response(
                embed=brand_embed("FEEDBACK RECEIVED", f"Thanks for rating your Z9 support experience **{rating}/5**."),
                view=self,
            )
        except discord.HTTPException:
            await interaction.followup.send("Thank you — your rating was saved.", ephemeral=True)
