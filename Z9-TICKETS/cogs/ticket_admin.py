"""The /ticket command group and administrator settings."""

from __future__ import annotations

import json
from typing import Any

import discord
from discord import app_commands

from cogs.registry import ticket_group
from config import CATEGORIES, SETTING_KEYS
from utils.embeds import brand_embed, ticket_embed
from utils.permissions import can_manage_ticket, is_admin, is_staff, require_ticket_staff
from utils.ticket_actions import (
    add_ticket_member,
    assign_ticket,
    change_priority,
    close_ticket,
    delete_ticket,
    reopen_ticket,
    remove_ticket_member,
    rename_ticket,
    set_ticket_lock,
    unclaim_ticket,
)
from utils.logging import log_ticket_action
from views.ticket_panel import send_ticket_panel, setup_confirmation_embed


def _bot(interaction: discord.Interaction) -> Any:
    return interaction.client


async def _ticket_for_interaction(interaction: discord.Interaction, bot: Any) -> dict[str, Any] | None:
    if not interaction.guild or not interaction.channel_id:
        await interaction.response.send_message("Use this command inside a Z9 ticket channel.", ephemeral=True)
        return None
    ticket = await bot.db.get_ticket_by_channel(interaction.channel_id)
    if not ticket or ticket["status"] == "deleted":
        await interaction.response.send_message("This channel is not an active Z9 ticket.", ephemeral=True)
        return None
    return ticket


async def _run_action(
    interaction: discord.Interaction,
    action: str,
    *,
    argument: Any = None,
    reason: str | None = None,
) -> None:
    bot = _bot(interaction)
    ticket = await _ticket_for_interaction(interaction, bot)
    if ticket is None or interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return
    member = interaction.user

    if action == "close":
        if not await can_manage_ticket(interaction, bot, ticket, owner_may_close=True):
            return
    elif action == "info":
        participants = await bot.db.get_participants(int(ticket["id"]))
        if member.id not in participants and not await is_staff(member, bot, ticket):
            await interaction.response.send_message("You do not have access to this ticket's information.", ephemeral=True)
            return
    elif action == "unclaim":
        if not await require_ticket_staff(interaction, bot, ticket):
            return
        if ticket.get("claimed_by") != member.id and not await is_admin(member, bot):
            await interaction.response.send_message(
                "Only the claimant or an administrator can unclaim this ticket.", ephemeral=True
            )
            return
    elif not await require_ticket_staff(interaction, bot, ticket):
        return

    if action == "info":
        embed = ticket_embed(ticket, interaction.guild)
        embed.add_field(name="Ticket ID", value=str(ticket["id"]), inline=True)
        embed.add_field(name="Channel ID", value=str(ticket["channel_id"]), inline=True)
        embed.add_field(name="Created", value=f"<t:{int(discord.utils.parse_time(ticket['created_at']).timestamp())}:F>", inline=False)
        if ticket.get("closed_at"):
            closed = discord.utils.parse_time(ticket["closed_at"])
            if closed:
                embed.add_field(name="Closed", value=f"<t:{int(closed.timestamp())}:F>", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        if action == "close":
            path, transcript_error = await close_ticket(
                bot,
                interaction.guild,
                ticket,
                member.id,
                str(reason or "No reason provided"),
                actor_member=member,
            )
            result = "Ticket closed."
            if path:
                result += " Transcript generated and saved."
            if transcript_error:
                result += f" Transcript issue: {transcript_error}"
        elif action == "reopen":
            await reopen_ticket(bot, interaction.guild, ticket, member)
            result = "Ticket reopened."
        elif action == "claim":
            from utils.ticket_actions import claim_ticket

            await claim_ticket(bot, interaction.guild, ticket, member)
            result = "Ticket claimed."
        elif action == "unclaim":
            await unclaim_ticket(bot, interaction.guild, ticket, member)
            result = "Ticket unclaimed."
        elif action == "add":
            await add_ticket_member(bot, interaction.guild, ticket, member, argument)
            result = f"Added {argument.mention} to the ticket."
        elif action == "remove":
            await remove_ticket_member(bot, interaction.guild, ticket, member, argument)
            result = f"Removed {argument.mention} from the ticket."
        elif action in {"assign", "transfer"}:
            await assign_ticket(
                bot,
                interaction.guild,
                ticket,
                member,
                argument,
                action="transfer" if action == "transfer" else "assign",
            )
            result = f"Ticket assigned to {argument.mention}."
        elif action == "rename":
            await rename_ticket(bot, interaction.guild, ticket, member, str(argument))
            result = "Ticket channel renamed."
        elif action == "priority":
            await change_priority(bot, interaction.guild, ticket, member, str(argument))
            result = f"Priority changed to **{argument}**."
        elif action == "lock":
            await set_ticket_lock(bot, interaction.guild, ticket, member, True)
            result = "Ticket locked."
        elif action == "unlock":
            await set_ticket_lock(bot, interaction.guild, ticket, member, False)
            result = "Ticket unlocked."
        elif action == "transcript":
            if not isinstance(interaction.channel, discord.TextChannel):
                raise ValueError("The ticket channel could not be found.")
            from utils.transcripts import generate_transcript
            from utils.ticket_actions import deliver_transcript

            transcript = await generate_transcript(interaction.channel, ticket, bot.config.transcript_dir)
            await deliver_transcript(bot, interaction.guild, ticket, transcript)
            await log_ticket_action(
                bot, interaction.guild, ticket, "transcript", member.id, transcript.name, staff_action=True
            )
            result = f"Transcript generated at `{transcript.name}` and saved to persistent storage."
        else:
            raise ValueError("Unsupported ticket command.")
        await interaction.followup.send(result, ephemeral=True)
    except (ValueError, PermissionError, LookupError) as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
    except discord.HTTPException as exc:
        await interaction.followup.send(f"Discord could not complete that action. ({exc.code})", ephemeral=True)
    except Exception:
        bot.logger.exception("Ticket slash action %s failed", action)
        await interaction.followup.send("That action failed unexpectedly. Staff have been notified.", ephemeral=True)


def _validate_setting(key: str, value: str) -> str:
    if key not in SETTING_KEYS:
        raise ValueError(f"Unknown setting. Allowed keys: {', '.join(sorted(SETTING_KEYS))}.")
    cleaned = value.strip()
    if key.endswith("_id"):
        if cleaned and (not cleaned.isdecimal() or int(cleaned) <= 0):
            raise ValueError(f"{key} must be a positive Discord ID, or blank to clear it.")
    elif key in {"staff_role_ids", "admin_role_ids"}:
        if cleaned:
            for item in cleaned.split(","):
                if not item.strip().isdecimal() or int(item.strip()) <= 0:
                    raise ValueError(f"{key} must be comma-separated positive Discord role IDs.")
    elif key == "category_staff_role_ids":
        if not cleaned:
            return ""
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError('Use JSON such as {"reports":[123456789012345678]}.') from exc
        if not isinstance(parsed, dict):
            raise ValueError("Category staff roles must be a JSON object.")
        for category, ids in parsed.items():
            if category not in CATEGORIES or not isinstance(ids, list):
                raise ValueError(f"Invalid category role entry for {category!r}.")
            if any(not str(role_id).isdecimal() or int(role_id) <= 0 for role_id in ids):
                raise ValueError(f"Every role ID in {category} must be a positive Discord ID.")
        cleaned = json.dumps(parsed)
    else:
        if not cleaned.isdecimal():
            raise ValueError(f"{key} must be a whole number.")
        number = int(cleaned)
        minimum = 1 if key == "inactivity_timeout_minutes" else 0
        if number < minimum:
            raise ValueError(f"{key} must be at least {minimum}.")
    return cleaned


class ResetSettingsView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=90)
        self.guild_id = guild_id

    @discord.ui.button(label="Reset guild settings", style=discord.ButtonStyle.danger, emoji="♻️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        bot = _bot(interaction)
        if interaction.guild_id != self.guild_id or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This confirmation is no longer valid.", ephemeral=True)
            return
        if not await is_admin(interaction.user, bot):
            await interaction.response.send_message("Only configured administrators can reset settings.", ephemeral=True)
            return
        await interaction.response.defer()
        await bot.db.reset_settings(self.guild_id)
        failures = 0
        from utils.ticket_actions import sync_ticket_channel_permissions

        for ticket in await bot.db.list_guild_tickets(self.guild_id):
            try:
                await sync_ticket_channel_permissions(bot, interaction.guild, ticket)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                failures += 1
        for child in self.children:
            child.disabled = True
        detail = "Guild overrides were cleared; environment defaults now apply."
        if failures:
            detail += f" {failures} existing ticket channel(s) could not be updated."
        else:
            detail += " Existing ticket channel access was refreshed."
        await interaction.edit_original_response(
            embed=brand_embed("SETTINGS RESET", detail), view=self
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=brand_embed("RESET CANCELLED", "No settings were changed."), view=self
        )


@ticket_group.command(name="setup", description="Deploy the Z9 ticket panel in a channel.")
@app_commands.describe(channel="Channel where the panel should be posted")
async def ticket_setup(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
) -> None:
    bot = _bot(interaction)
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    if not await is_admin(interaction.user, bot):
        await interaction.response.send_message("Only server administrators or configured admin roles can deploy the panel.", ephemeral=True)
        return
    settings = await bot.db.get_settings(interaction.guild.id)
    configured_channel_id = int(
        settings.get("ticket_panel_channel_id") or bot.config.ticket_panel_channel_id or 0
    )
    configured_channel = interaction.guild.get_channel(configured_channel_id) if configured_channel_id else None
    target = channel or configured_channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        await interaction.response.send_message("Choose a text channel for the panel.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        panel = await send_ticket_panel(bot, target)
        await interaction.followup.send(embed=setup_confirmation_embed(target), ephemeral=True)
        await bot.db.add_event(interaction.guild.id, None, interaction.user.id, "panel_setup", str(panel.id))
    except discord.Forbidden:
        await interaction.followup.send("The bot cannot post in that channel. Check its channel permissions.", ephemeral=True)
    except discord.HTTPException as exc:
        await interaction.followup.send(f"Could not deploy the panel. ({exc.code})", ephemeral=True)


@ticket_group.command(name="close", description="Close the ticket in this channel.")
@app_commands.describe(reason="Short reason recorded in the transcript and ticket log")
async def ticket_close(interaction: discord.Interaction, reason: str = "No reason provided") -> None:
    await _run_action(interaction, "close", reason=reason)


@ticket_group.command(name="reopen", description="Reopen a closed ticket.")
async def ticket_reopen(interaction: discord.Interaction) -> None:
    await _run_action(interaction, "reopen")


@ticket_group.command(name="claim", description="Claim this ticket as the responding staff member.")
async def ticket_claim(interaction: discord.Interaction) -> None:
    await _run_action(interaction, "claim")


@ticket_group.command(name="unclaim", description="Release your claim on this ticket.")
async def ticket_unclaim(interaction: discord.Interaction) -> None:
    await _run_action(interaction, "unclaim")


@ticket_group.command(name="add", description="Give a member access to this ticket.")
async def ticket_add(interaction: discord.Interaction, member: discord.Member) -> None:
    await _run_action(interaction, "add", argument=member)


@ticket_group.command(name="remove", description="Remove a member's access to this ticket.")
async def ticket_remove(interaction: discord.Interaction, member: discord.Member) -> None:
    await _run_action(interaction, "remove", argument=member)


@ticket_group.command(name="rename", description="Rename this ticket channel.")
async def ticket_rename(interaction: discord.Interaction, name: str) -> None:
    await _run_action(interaction, "rename", argument=name)


@ticket_group.command(name="priority", description="Set this ticket's priority.")
@app_commands.choices(
    level=[
        app_commands.Choice(name="LOW", value="LOW"),
        app_commands.Choice(name="NORMAL", value="NORMAL"),
        app_commands.Choice(name="HIGH", value="HIGH"),
        app_commands.Choice(name="URGENT", value="URGENT"),
    ]
)
async def ticket_priority(interaction: discord.Interaction, level: app_commands.Choice[str]) -> None:
    await _run_action(interaction, "priority", argument=level.value)


@ticket_group.command(name="assign", description="Assign this ticket to an authorized staff member.")
async def ticket_assign(interaction: discord.Interaction, member: discord.Member) -> None:
    await _run_action(interaction, "assign", argument=member)


@ticket_group.command(name="transfer", description="Transfer this ticket to another authorized staff member.")
async def ticket_transfer(interaction: discord.Interaction, member: discord.Member) -> None:
    await _run_action(interaction, "transfer", argument=member)


@ticket_group.command(name="lock", description="Prevent ticket participants from sending messages.")
async def ticket_lock(interaction: discord.Interaction) -> None:
    await _run_action(interaction, "lock")


@ticket_group.command(name="unlock", description="Allow ticket participants to send messages again.")
async def ticket_unlock(interaction: discord.Interaction) -> None:
    await _run_action(interaction, "unlock")


@ticket_group.command(name="transcript", description="Generate and upload an HTML transcript.")
async def ticket_transcript(interaction: discord.Interaction) -> None:
    await _run_action(interaction, "transcript")


@ticket_group.command(name="info", description="Show this ticket's details and current status.")
async def ticket_info(interaction: discord.Interaction) -> None:
    await _run_action(interaction, "info")


@ticket_group.command(name="blacklist", description="Block a user from opening Z9 tickets.")
@app_commands.describe(member="Member to block", reason="Why ticket access is being restricted")
async def ticket_blacklist(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided",
) -> None:
    bot = _bot(interaction)
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    if not await is_admin(interaction.user, bot):
        await interaction.response.send_message("Only configured administrators can manage the blacklist.", ephemeral=True)
        return
    await bot.db.blacklist_user(interaction.guild.id, member.id, reason, interaction.user.id)
    await bot.db.add_event(interaction.guild.id, None, interaction.user.id, "blacklist", f"{member.id}: {reason}")
    await interaction.response.send_message(f"{member.mention} is blocked from opening tickets.", ephemeral=True)


@ticket_group.command(name="unblacklist", description="Restore a user's ability to open tickets.")
async def ticket_unblacklist(interaction: discord.Interaction, member: discord.Member) -> None:
    bot = _bot(interaction)
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    if not await is_admin(interaction.user, bot):
        await interaction.response.send_message("Only configured administrators can manage the blacklist.", ephemeral=True)
        return
    removed = await bot.db.unblacklist_user(interaction.guild.id, member.id)
    await bot.db.add_event(interaction.guild.id, None, interaction.user.id, "unblacklist", str(member.id))
    await interaction.response.send_message(
        f"{member.mention} has been removed from the blacklist." if removed else "That member was not blacklisted.",
        ephemeral=True,
    )


@ticket_group.command(name="settings", description="View or update the ticket system settings.")
@app_commands.describe(
    key="Optional setting to update",
    value="New value; use comma-separated role IDs or category JSON where applicable",
)
async def ticket_settings(
    interaction: discord.Interaction,
    key: str | None = None,
    value: str | None = None,
) -> None:
    bot = _bot(interaction)
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    if not await is_admin(interaction.user, bot):
        await interaction.response.send_message("Only configured administrators can view or change settings.", ephemeral=True)
        return
    settings = await bot.db.get_settings(interaction.guild.id)
    if key is None:
        lines = [
            f"`{setting}`: `{settings.get(setting, 'environment default') or 'environment default'}`"
            for setting in sorted(SETTING_KEYS)
        ]
        embed = brand_embed("GUILD SETTINGS", "Run `/ticket settings key value` to save a guild override.\n" + "\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if value is None:
        await interaction.response.send_message("Provide a value to update that setting.", ephemeral=True)
        return
    try:
        normalized_key = key.strip().lower()
        validated = _validate_setting(normalized_key, value)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    await bot.db.set_setting(interaction.guild.id, normalized_key, validated)
    await bot.db.add_event(interaction.guild.id, None, interaction.user.id, "setting_update", normalized_key)
    message = f"Updated `{normalized_key}`. Restart is not required."
    if normalized_key in {"staff_role_ids", "admin_role_ids", "category_staff_role_ids"}:
        from utils.ticket_actions import sync_ticket_channel_permissions

        failures = 0
        for ticket in await bot.db.list_guild_tickets(interaction.guild.id):
            try:
                await sync_ticket_channel_permissions(bot, interaction.guild, ticket)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                failures += 1
        if failures:
            message += f" {failures} existing ticket channel(s) could not be updated; check bot permissions."
        else:
            message += " Existing ticket channel access was refreshed."
    await interaction.followup.send(message, ephemeral=True)


@ticket_group.command(name="reset", description="Reset guild setting overrides without deleting ticket records.")
async def ticket_reset(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    if not await is_admin(interaction.user, bot):
        await interaction.response.send_message("Only configured administrators can reset settings.", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=brand_embed(
            "CONFIRM SETTINGS RESET",
            "This clears guild-level setting overrides and restores environment defaults. "
            "Ticket records, channels, transcripts, and feedback will not be deleted.",
        ),
        view=ResetSettingsView(interaction.guild.id),
        ephemeral=True,
    )


async def setup(bot: Any) -> None:
    await bot.add_cog(TicketAdminCog(bot))
    if bot.tree.get_command("ticket") is None:
        bot.tree.add_command(ticket_group)


from discord.ext import commands


class TicketAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
