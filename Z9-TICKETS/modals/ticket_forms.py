"""Category-specific ticket intake and control modals."""

from __future__ import annotations

import re
from typing import Any

import discord

from config import CATEGORIES
from database import DuplicateTicketError
from utils.ticket_actions import (
    add_ticket_member,
    assign_ticket,
    change_priority,
    close_ticket,
    create_ticket_from_form,
    rename_ticket,
    remove_ticket_member,
)
from utils.permissions import can_manage_ticket, is_admin


class TicketFormModal(discord.ui.Modal):
    def __init__(self, bot: Any, category_key: str):
        category = CATEGORIES[category_key]
        super().__init__(title=f"Z9 • {category['title']}"[:45], timeout=300)
        self.bot = bot
        self.category_key = category_key
        self.inputs: list[tuple[str, discord.ui.TextInput]] = []
        for question, placeholder in category["questions"]:
            field = discord.ui.TextInput(
                label=question[:45],
                placeholder=placeholder[:100],
                min_length=3,
                max_length=1000,
                style=discord.TextStyle.paragraph,
                required=True,
            )
            self.inputs.append((question, field))
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        answers = {question: str(field.value).strip() for question, field in self.inputs}
        if any(len(answer) < 3 for answer in answers.values()):
            await interaction.followup.send("Please provide a little more detail in each answer.", ephemeral=True)
            return
        try:
            ticket = await create_ticket_from_form(self.bot, interaction, self.category_key, answers)
        except DuplicateTicketError:
            existing = await self.bot.db.get_open_ticket_for_user(interaction.guild_id, interaction.user.id)
            link = f"<#{existing['channel_id']}>" if existing else "your current ticket"
            await interaction.followup.send(f"You already have an open ticket: {link}.", ephemeral=True)
        except (PermissionError, TimeoutError, ValueError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Discord could not finish opening the ticket. Please try again later. ({exc.code})",
                ephemeral=True,
            )
        except Exception:
            self.bot.logger.exception("Ticket creation failed for user %s", interaction.user.id)
            await interaction.followup.send("Ticket creation failed unexpectedly. Staff have been notified.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"Your private ticket is ready: <#{ticket['channel_id']}>", ephemeral=True
            )


class TicketActionModal(discord.ui.Modal):
    def __init__(self, bot: Any, ticket_id: int, action: str):
        titles = {
            "close": "Close ticket",
            "add": "Add member",
            "remove": "Remove member",
            "rename": "Rename ticket",
            "assign": "Assign staff",
            "transfer": "Transfer ticket",
        }
        super().__init__(title=f"Z9 • {titles[action]}"[:45], timeout=300)
        self.bot = bot
        self.ticket_id = ticket_id
        self.action = action
        if action == "close":
            self.value_input = discord.ui.TextInput(
                label="Closing reason",
                placeholder="Briefly explain why this ticket is closing",
                max_length=1000,
                required=False,
                style=discord.TextStyle.paragraph,
            )
        elif action == "rename":
            self.value_input = discord.ui.TextInput(
                label="New channel name",
                placeholder="Example: z9-report-0042",
                min_length=2,
                max_length=90,
                required=True,
            )
        elif action in {"add", "remove", "assign", "transfer"}:
            self.value_input = discord.ui.TextInput(
                label="Discord member ID",
                placeholder="Paste the member ID or mention",
                min_length=2,
                max_length=32,
                required=True,
            )
        else:
            raise ValueError(f"Unsupported ticket modal action: {action}")
        self.add_item(self.value_input)

    @staticmethod
    def _parse_member_id(value: str) -> int:
        cleaned = re.sub(r"[<@!>]", "", value.strip())
        if not cleaned.isdecimal() or int(cleaned) <= 0:
            raise ValueError("Enter a valid Discord user ID or mention.")
        return int(cleaned)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ticket = await self.bot.db.get_ticket(self.ticket_id)
        if not ticket or interaction.guild is None or int(ticket["channel_id"]) != interaction.channel_id:
            await interaction.response.send_message("This ticket could not be verified.", ephemeral=True)
            return
        if not await can_manage_ticket(
            interaction,
            self.bot,
            ticket,
            owner_may_close=self.action == "close",
        ):
            return
        if self.action in {"add", "remove", "rename", "assign", "transfer"}:
            if not await self._has_staff_permission(interaction, ticket):
                await interaction.response.send_message(
                    "This action is restricted to authorized Z9 staff.", ephemeral=True
                )
                return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if self.action == "close":
                path, transcript_error = await close_ticket(
                    self.bot,
                    interaction.guild,
                    ticket,
                    interaction.user.id,
                    str(self.value_input.value),
                    actor_member=interaction.user,
                )
                message = "Ticket closed."
                if path:
                    message += " Transcript saved."
                if transcript_error:
                    message += f" Transcript generation issue: {transcript_error}"
            elif self.action == "rename":
                await rename_ticket(
                    self.bot, interaction.guild, ticket, interaction.user, str(self.value_input.value)
                )
                message = "Ticket channel renamed."
            else:
                member_id = self._parse_member_id(str(self.value_input.value))
                try:
                    member = await interaction.guild.fetch_member(member_id)
                except discord.NotFound as exc:
                    raise ValueError("That member is not in this server.") from exc
                if self.action == "add":
                    await add_ticket_member(self.bot, interaction.guild, ticket, interaction.user, member)
                    message = f"Added {member.mention} to this ticket."
                elif self.action == "remove":
                    await remove_ticket_member(self.bot, interaction.guild, ticket, interaction.user, member)
                    message = f"Removed {member.mention} from this ticket."
                else:
                    await assign_ticket(
                        self.bot, interaction.guild, ticket, interaction.user, member,
                        action="transfer" if self.action == "transfer" else "assign",
                    )
                    message = f"Ticket assigned to {member.mention}."
            await interaction.followup.send(message, ephemeral=True)
        except (ValueError, PermissionError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Discord could not complete that action. ({exc.code})", ephemeral=True
            )
        except Exception:
            self.bot.logger.exception("Ticket action %s failed", self.action)
            await interaction.followup.send("That action failed unexpectedly. Staff have been notified.", ephemeral=True)

    async def _has_staff_permission(self, interaction: discord.Interaction, ticket: dict[str, Any]) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        settings = await self.bot.db.get_settings(interaction.guild.id)
        from utils.permissions import is_staff

        return await is_staff(interaction.user, self.bot, ticket, settings)
