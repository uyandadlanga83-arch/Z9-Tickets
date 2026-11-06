"""Persistent controls attached to each ticket's opening message."""

from __future__ import annotations

from typing import Any

import discord

from modals.ticket_forms import TicketActionModal
from utils.embeds import brand_embed
from utils.permissions import can_manage_ticket, is_admin, is_staff
from utils.ticket_actions import (
    change_priority,
    claim_ticket,
    close_ticket,
    delete_ticket,
    reopen_ticket,
    set_ticket_lock,
    unclaim_ticket,
)


class TicketActionButton(discord.ui.Button):
    def __init__(self, action: str, label: str, emoji: str, style: discord.ButtonStyle, row: int):
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=f"z9:ticket:{action}",
            row=row,
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        view: TicketControlsView = self.view  # type: ignore[assignment]
        await view.handle(interaction, self.action)


class PrioritySelect(discord.ui.Select):
    def __init__(self, bot: Any, ticket_id: int):
        self.bot = bot
        self.ticket_id = ticket_id
        options = [
            discord.SelectOption(label=priority.title(), value=priority, emoji=emoji)
            for priority, emoji in (
                ("LOW", "🟢"), ("NORMAL", "🔴"), ("HIGH", "🟠"), ("URGENT", "🚨")
            )
        ]
        super().__init__(placeholder="Choose a ticket priority", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This action must be used in a server.", ephemeral=True)
            return
        ticket = await self.bot.db.get_ticket(self.ticket_id)
        if not ticket or not await is_staff(interaction.user, self.bot, ticket):
            await interaction.response.send_message("You cannot change this ticket's priority.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await change_priority(self.bot, interaction.guild, ticket, interaction.user, self.values[0])
        except (ValueError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        else:
            await interaction.followup.send(f"Priority set to **{self.values[0]}**.", ephemeral=True)


class PrioritySelectView(discord.ui.View):
    def __init__(self, bot: Any, ticket_id: int):
        super().__init__(timeout=120)
        self.add_item(PrioritySelect(bot, ticket_id))


class TicketControlsView(discord.ui.View):
    def __init__(self, bot: Any | None = None):
        super().__init__(timeout=None)
        self.bot = bot
        controls = [
            ("claim", "Claim", "🙋", discord.ButtonStyle.success, 0),
            ("unclaim", "Unclaim", "↩️", discord.ButtonStyle.secondary, 0),
            ("close", "Close", "🔒", discord.ButtonStyle.danger, 0),
            ("reopen", "Reopen", "🔓", discord.ButtonStyle.success, 0),
            ("delete", "Delete", "🗑️", discord.ButtonStyle.danger, 0),
            ("add", "Add Member", "➕", discord.ButtonStyle.secondary, 1),
            ("remove", "Remove Member", "➖", discord.ButtonStyle.secondary, 1),
            ("rename", "Rename", "✏️", discord.ButtonStyle.secondary, 1),
            ("priority", "Priority", "🚦", discord.ButtonStyle.primary, 1),
            ("assign", "Assign Staff", "👤", discord.ButtonStyle.primary, 1),
            ("transfer", "Transfer", "🔁", discord.ButtonStyle.primary, 2),
            ("lock", "Lock", "🔐", discord.ButtonStyle.secondary, 2),
            ("unlock", "Unlock", "🔓", discord.ButtonStyle.secondary, 2),
            ("transcript", "Transcript", "📄", discord.ButtonStyle.secondary, 2),
        ]
        for action, label, emoji, style, row in controls:
            self.add_item(TicketActionButton(action, label, emoji, style, row))

    async def handle(self, interaction: discord.Interaction, action: str) -> None:
        bot = self.bot or getattr(interaction.client, "bot_instance", interaction.client)
        if interaction.guild is None:
            await interaction.response.send_message("This control only works inside a server.", ephemeral=True)
            return
        ticket = await bot.db.get_ticket_by_channel(interaction.channel_id or 0)
        if not ticket or ticket["status"] == "deleted":
            await interaction.response.send_message("This ticket is no longer active.", ephemeral=True)
            return
        if action == "close":
            if await can_manage_ticket(interaction, bot, ticket, owner_may_close=True):
                await interaction.response.send_modal(TicketActionModal(bot, int(ticket["id"]), "close"))
            return
        if action == "delete":
            if not isinstance(interaction.user, discord.Member) or not await is_admin(interaction.user, bot):
                await interaction.response.send_message(
                    "Only server administrators or configured admin roles can delete tickets.", ephemeral=True
                )
                return
        elif not await can_manage_ticket(interaction, bot, ticket):
            return
        if action == "unclaim" and ticket.get("claimed_by") != interaction.user.id:
            if not isinstance(interaction.user, discord.Member) or not await is_admin(interaction.user, bot):
                await interaction.response.send_message("Only the claimant or an administrator can unclaim this ticket.", ephemeral=True)
                return
        if action in {"add", "remove", "rename", "assign", "transfer"}:
            await interaction.response.send_modal(TicketActionModal(bot, int(ticket["id"]), action))
            return
        if action == "priority":
            await interaction.response.send_message(
                embed=brand_embed("SET PRIORITY", "Choose the urgency for this ticket."),
                view=PrioritySelectView(bot, int(ticket["id"])),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if not isinstance(interaction.user, discord.Member):
                raise PermissionError("A server member is required.")
            if action == "claim":
                await claim_ticket(bot, interaction.guild, ticket, interaction.user)
                result = "Ticket claimed."
            elif action == "unclaim":
                await unclaim_ticket(bot, interaction.guild, ticket, interaction.user)
                result = "Ticket unclaimed."
            elif action == "reopen":
                await reopen_ticket(bot, interaction.guild, ticket, interaction.user)
                result = "Ticket reopened."
            elif action == "delete":
                await delete_ticket(bot, interaction.guild, ticket, interaction.user)
                result = "Ticket deleted."
            elif action == "lock":
                await set_ticket_lock(bot, interaction.guild, ticket, interaction.user, True)
                result = "Ticket locked."
            elif action == "unlock":
                await set_ticket_lock(bot, interaction.guild, ticket, interaction.user, False)
                result = "Ticket unlocked."
            elif action == "transcript":
                from utils.logging import log_ticket_action
                from utils.ticket_actions import deliver_transcript
                from utils.transcripts import generate_transcript

                if not isinstance(interaction.channel, discord.TextChannel):
                    raise ValueError("The ticket channel could not be found.")
                path = await generate_transcript(interaction.channel, ticket, bot.config.transcript_dir)
                await deliver_transcript(bot, interaction.guild, ticket, path)
                await log_ticket_action(bot, interaction.guild, ticket, "transcript", interaction.user.id, path.name, staff_action=True)
                result = "Transcript generated and saved to persistent storage."
            else:
                raise ValueError("Unknown ticket action.")
            await interaction.followup.send(result, ephemeral=True)
        except (ValueError, PermissionError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except discord.HTTPException as exc:
            await interaction.followup.send(f"Discord could not complete that action. ({exc.code})", ephemeral=True)
        except Exception:
            bot.logger.exception("Ticket control action %s failed", action)
            await interaction.followup.send("That action failed unexpectedly. Staff have been notified.", ephemeral=True)
