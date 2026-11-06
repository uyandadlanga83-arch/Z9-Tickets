"""Shared ticket lifecycle and control actions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import discord

from config import CATEGORIES
from database import DuplicateTicketError
from utils.embeds import brand_embed, ticket_embed
from utils.logging import log_ticket_action
from utils.permissions import is_staff, staff_role_ids_for_ticket, admin_role_ids
from utils.transcripts import generate_transcript


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


async def merged_settings(bot: Any, guild_id: int) -> dict[str, str]:
    return await bot.db.get_settings(guild_id)


async def role_overwrites(
    bot: Any,
    guild: discord.Guild,
    category_key: str,
    settings: dict[str, str],
    owner: discord.Member,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
    }
    bot_member = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    if bot_member is None:
        raise RuntimeError("The bot's server membership could not be resolved.")
    overwrites[bot_member] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True,
        manage_channels=True, manage_messages=True, attach_files=True,
    )
    roles = staff_role_ids_for_ticket(bot, settings, category_key) | admin_role_ids(bot, settings)
    for role_id in roles:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
            )
    return overwrites


async def sync_ticket_channel_permissions(
    bot: Any, guild: discord.Guild, ticket: dict[str, Any]
) -> None:
    """Rebuild private-channel overwrites after staff-role settings change."""
    channel = guild.get_channel(int(ticket["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        return
    owner_id = int(ticket["creator_id"])
    owner = guild.get_member(owner_id) or await guild.fetch_member(owner_id)
    settings = await bot.db.get_settings(guild.id)
    overwrites = await role_overwrites(bot, guild, str(ticket["category_key"]), settings, owner)
    overwrites[owner].send_messages = ticket["status"] == "open" and not bool(ticket.get("locked"))

    for user_id in await bot.db.get_participants(int(ticket["id"])):
        if user_id == owner_id:
            continue
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            continue
        if await is_staff(member, bot, ticket, settings):
            continue
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=ticket["status"] == "open" and not bool(ticket.get("locked")),
            read_message_history=True,
        )
    await channel.edit(overwrites=overwrites, reason="Z9 ticket staff access settings updated")


async def create_ticket_from_form(
    bot: Any,
    interaction: discord.Interaction,
    category_key: str,
    answers: dict[str, str],
) -> dict[str, Any]:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        raise ValueError("Tickets can only be opened from a server.")
    if category_key not in CATEGORIES:
        raise ValueError("That ticket category is not available.")
    guild = interaction.guild
    user = interaction.user
    settings = await merged_settings(bot, guild.id)
    blocked = await bot.db.is_blacklisted(guild.id, user.id)
    if blocked:
        raise PermissionError(f"You cannot open tickets in this server. Reason: {blocked['reason']}")
    existing = await bot.db.get_open_ticket_for_user(guild.id, user.id)
    if existing:
        raise DuplicateTicketError
    cooldown = int(settings.get("ticket_cooldown_seconds", bot.config.ticket_cooldown_seconds))
    remaining = await bot.db.cooldown_remaining(guild.id, user.id, cooldown)
    if remaining:
        raise TimeoutError(f"Please wait {remaining} seconds before opening another ticket.")

    category_config = CATEGORIES[category_key]
    number = await bot.db.reserve_ticket_number(guild.id, category_key)
    channel_name = f"z9-{category_config['channel_prefix']}-{number:04d}"
    category_channel_id = int(settings.get("ticket_category_id") or bot.config.ticket_category_id or 0)
    category_channel = guild.get_channel(category_channel_id) if category_channel_id else None
    if category_channel_id and not isinstance(category_channel, discord.CategoryChannel):
        raise ValueError("The configured ticket category ID is not a category in this server.")
    overwrites = await role_overwrites(bot, guild, category_key, settings, user)

    try:
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category_channel,
            overwrites=overwrites,
            topic=f"Z9 ticket #{number:04d} • {category_config['title']} • Owner ID {user.id}",
            reason=f"Z9 ticket opened by {user} ({user.id})",
        )
    except discord.Forbidden as exc:
        raise PermissionError("The bot needs Manage Channels and the ability to view the configured category.") from exc
    except discord.HTTPException as exc:
        raise RuntimeError(f"Discord could not create the ticket channel: {exc}") from exc

    ticket: dict[str, Any] | None = None
    try:
        ticket = await bot.db.create_ticket(
            guild_id=guild.id,
            channel_id=channel.id,
            ticket_number=number,
            category_key=category_key,
            category_title=str(category_config["title"]),
            creator_id=user.id,
            channel_name=channel_name,
            creator_name=user.display_name,
            form_answers=json.dumps(answers, ensure_ascii=False),
        )
        from views.ticket_controls import TicketControlsView

        message = await channel.send(
            content=f"{user.mention} — your Z9 support request is open.",
            embed=ticket_embed(ticket, guild),
            view=TicketControlsView(),
        )
        await bot.db.update_ticket(int(ticket["id"]), ticket_message_id=message.id)
        ticket["ticket_message_id"] = message.id
        await log_ticket_action(bot, guild, ticket, "created", user.id, category_config["title"])
        await notify_staff(bot, guild, ticket, settings)
        return ticket
    except Exception:
        if ticket is not None:
            try:
                await bot.db.mark_ticket_deleted(int(ticket["id"]))
            except Exception:
                bot.logger.exception("Could not mark incomplete ticket %s as deleted", ticket.get("id"))
        try:
            await channel.delete(reason="Ticket setup failed; removing incomplete channel.")
        except discord.HTTPException:
            pass
        raise


async def notify_staff(bot: Any, guild: discord.Guild, ticket: dict[str, Any], settings: dict[str, str]) -> None:
    role_ids = staff_role_ids_for_ticket(bot, settings, str(ticket["category_key"]))
    mentions = " ".join(f"<@&{role_id}>" for role_id in sorted(role_ids) if guild.get_role(role_id))
    channel = guild.get_channel(int(ticket["channel_id"]))
    if channel:
        try:
            await channel.send(
                f"{mentions} New **{ticket['category_title']}** ticket from <@{ticket['creator_id']}>.",
                allowed_mentions=discord.AllowedMentions(roles=True, users=True, everyone=False),
            )
        except discord.HTTPException:
            pass


async def refresh_ticket_message(bot: Any, guild: discord.Guild, ticket: dict[str, Any]) -> None:
    message_id = ticket.get("ticket_message_id")
    if not message_id:
        return
    channel = guild.get_channel(int(ticket["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        message = await channel.fetch_message(int(message_id))
        from views.ticket_controls import TicketControlsView

        await message.edit(embed=ticket_embed(ticket, guild), view=TicketControlsView())
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return


async def claim_ticket(bot: Any, guild: discord.Guild, ticket: dict[str, Any], staff: discord.Member) -> None:
    if ticket["status"] != "open":
        raise ValueError("Only open tickets can be claimed.")
    if not await bot.db.claim_ticket_record(int(ticket["id"]), staff.id):
        current = await bot.db.get_ticket(int(ticket["id"]))
        if current and current.get("claimed_by"):
            raise ValueError(f"This ticket is already claimed by <@{current['claimed_by']}>.")
        raise ValueError("This ticket is no longer open.")
    await bot.db.add_assignment(int(ticket["id"]), staff.id, staff.id, staff.display_name)
    ticket["claimed_by"] = staff.id
    ticket["assigned_staff_id"] = staff.id
    ticket["assigned_staff_name"] = staff.display_name
    await refresh_ticket_message(bot, guild, ticket)
    await log_ticket_action(bot, guild, ticket, "claim", staff.id, staff.display_name, staff_action=True)


async def unclaim_ticket(bot: Any, guild: discord.Guild, ticket: dict[str, Any], staff: discord.Member) -> None:
    if not ticket.get("claimed_by"):
        raise ValueError("This ticket is not claimed.")
    await bot.db.update_ticket(int(ticket["id"]), claimed_by=None)
    if ticket.get("assigned_staff_id") and int(ticket["assigned_staff_id"]) == staff.id:
        await bot.db.unassign(int(ticket["id"]))
        ticket["assigned_staff_id"] = None
    ticket["claimed_by"] = None
    await refresh_ticket_message(bot, guild, ticket)
    await log_ticket_action(bot, guild, ticket, "unclaim", staff.id, staff.display_name, staff_action=True)


async def assign_ticket(
    bot: Any,
    guild: discord.Guild,
    ticket: dict[str, Any],
    actor: discord.Member,
    target: discord.Member,
    action: str = "assign",
) -> None:
    if not await is_staff(target, bot, ticket):
        raise ValueError("The selected member is not authorized for this ticket category.")
    await bot.db.add_assignment(int(ticket["id"]), target.id, actor.id, target.display_name)
    ticket["assigned_staff_id"] = target.id
    ticket["assigned_staff_name"] = target.display_name
    if action == "transfer":
        ticket["claimed_by"] = target.id
        await bot.db.update_ticket(int(ticket["id"]), claimed_by=target.id)
    await refresh_ticket_message(bot, guild, ticket)
    await log_ticket_action(
        bot, guild, ticket, action, actor.id, f"Assigned to {target.mention}.", staff_action=True
    )


async def change_priority(
    bot: Any, guild: discord.Guild, ticket: dict[str, Any], actor: discord.Member, priority: str
) -> None:
    normalized = priority.upper()
    if normalized not in {"LOW", "NORMAL", "HIGH", "URGENT"}:
        raise ValueError("Priority must be LOW, NORMAL, HIGH, or URGENT.")
    await bot.db.update_ticket(int(ticket["id"]), priority=normalized)
    ticket["priority"] = normalized
    await refresh_ticket_message(bot, guild, ticket)
    await log_ticket_action(bot, guild, ticket, "priority", actor.id, normalized, staff_action=True)


async def set_ticket_lock(
    bot: Any, guild: discord.Guild, ticket: dict[str, Any], actor: discord.Member, locked: bool
) -> None:
    if ticket["status"] != "open":
        raise ValueError("Only open tickets can be locked or unlocked.")
    channel = guild.get_channel(int(ticket["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("The ticket channel could not be found.")
    for user_id in await bot.db.get_participants(int(ticket["id"])):
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
        if user_id != int(ticket["creator_id"]) and await is_staff(member, bot, ticket):
            continue
        overwrite = channel.overwrites_for(member)
        overwrite.send_messages = not locked
        await channel.set_permissions(
            member,
            overwrite=overwrite,
            reason=f"Ticket {'locked' if locked else 'unlocked'} by {actor}",
        )
    await bot.db.update_ticket(int(ticket["id"]), locked=int(locked))
    ticket["locked"] = int(locked)
    await log_ticket_action(
        bot, guild, ticket, "lock" if locked else "unlock", actor.id, actor.display_name, staff_action=True
    )


async def add_ticket_member(
    bot: Any, guild: discord.Guild, ticket: dict[str, Any], actor: discord.Member, member: discord.Member
) -> None:
    if member.id == int(ticket["creator_id"]):
        raise ValueError("The ticket owner already has access.")
    channel = guild.get_channel(int(ticket["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("The ticket channel could not be found.")
    await channel.set_permissions(
        member,
        view_channel=True,
        send_messages=ticket["status"] == "open" and not bool(ticket.get("locked")),
        read_message_history=True,
        reason=f"Added to Z9 ticket by {actor}",
    )
    await bot.db.add_participant(int(ticket["id"]), member.id, actor.id)
    await log_ticket_action(bot, guild, ticket, "member_added", actor.id, member.mention, staff_action=True)


async def remove_ticket_member(
    bot: Any, guild: discord.Guild, ticket: dict[str, Any], actor: discord.Member, member: discord.Member
) -> None:
    if member.id == int(ticket["creator_id"]):
        raise ValueError("The ticket owner cannot be removed.")
    channel = guild.get_channel(int(ticket["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("The ticket channel could not be found.")
    await channel.set_permissions(member, overwrite=None, reason=f"Removed from Z9 ticket by {actor}")
    await bot.db.remove_participant(int(ticket["id"]), member.id)
    await log_ticket_action(bot, guild, ticket, "member_removed", actor.id, member.mention, staff_action=True)


async def close_ticket(
    bot: Any,
    guild: discord.Guild,
    ticket: dict[str, Any],
    actor_id: int,
    reason: str = "No reason provided",
    *,
    automatic: bool = False,
    actor_member: discord.Member | None = None,
) -> tuple[Path | None, str]:
    if ticket["status"] != "open":
        raise ValueError("This ticket is already closed.")
    reason = (reason or "No reason provided").strip()[:1000]
    closed_at = _utcnow()
    closing_name = actor_member.display_name if actor_member else "Z9 Tickets Automation"
    if not await bot.db.close_ticket_record(
        int(ticket["id"]), actor_id, closing_name, closed_at, reason
    ):
        raise ValueError("This ticket has already been closed.")
    ticket["status"] = "closed"
    ticket["close_reason"] = reason
    ticket["closed_by"] = actor_id
    ticket["closed_by_name"] = closing_name
    ticket["closed_at"] = closed_at
    transcript_path: Path | None = None
    transcript_error = ""
    channel = guild.get_channel(int(ticket["channel_id"]))
    if channel and isinstance(channel, discord.TextChannel):
        owner = guild.get_member(int(ticket["creator_id"])) or discord.Object(id=int(ticket["creator_id"]))
        try:
            overwrite = channel.overwrites_for(owner)
            overwrite.send_messages = False
            await channel.set_permissions(owner, overwrite=overwrite, reason="Ticket closed")
        except discord.HTTPException:
            pass
        await refresh_ticket_message(bot, guild, ticket)
        try:
            transcript_path = await generate_transcript(
                channel,
                ticket,
                bot.config.transcript_dir,
                closing_staff_id=actor_id,
                closing_reason=reason,
            )
        except Exception as exc:
            bot.logger.exception("Transcript generation failed for ticket %s", ticket["id"])
            transcript_error = str(exc)[:250]
    await log_ticket_action(
        bot,
        guild,
        ticket,
        "auto_close" if automatic else "close",
        actor_id,
        reason,
        staff_action=(
            not automatic
            and actor_member is not None
            and await is_staff(actor_member, bot, ticket)
        ),
    )
    await deliver_transcript(bot, guild, ticket, transcript_path)
    await send_feedback_request(bot, guild, ticket)
    return transcript_path, transcript_error


async def deliver_transcript(
    bot: Any, guild: discord.Guild, ticket: dict[str, Any], transcript_path: Any
) -> None:
    if not transcript_path:
        return
    settings = await bot.db.get_settings(guild.id)
    channel_id = int(settings.get("transcript_channel_id") or bot.config.transcript_channel_id or 0)
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
        await channel.send(
            embed=brand_embed(
                "TRANSCRIPT READY",
                f"Ticket <#{ticket['channel_id']}> • Owner <@{ticket['creator_id']}>",
            ),
            file=discord.File(transcript_path, filename=transcript_path.name),
        )
    except (discord.Forbidden, discord.HTTPException):
        return


async def send_feedback_request(bot: Any, guild: discord.Guild, ticket: dict[str, Any]) -> None:
    if await bot.db.get_feedback(int(ticket["id"])):
        return
    from views.ticket_feedback import FeedbackView

    user = bot.get_user(int(ticket["creator_id"]))
    if user is None:
        try:
            user = await bot.fetch_user(int(ticket["creator_id"]))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            user = None
    destination: discord.abc.Messageable | None = None
    try:
        if user:
            destination = await user.create_dm()
    except discord.HTTPException:
        destination = None
    if destination is None:
        channel = guild.get_channel(int(ticket["channel_id"]))
        if isinstance(channel, discord.TextChannel):
            destination = channel
    if destination is None:
        return
    try:
        message = await destination.send(
            embed=brand_embed("RATE YOUR SUPPORT", "How would you rate the help you received?"),
            view=FeedbackView(int(ticket["id"]), guild.id, int(ticket["creator_id"])),
        )
        await bot.db.update_ticket(
            int(ticket["id"]),
            feedback_channel_id=destination.id,
            feedback_message_id=message.id,
        )
    except discord.HTTPException:
        return


async def reopen_ticket(bot: Any, guild: discord.Guild, ticket: dict[str, Any], actor: discord.Member) -> None:
    if ticket["status"] != "closed":
        raise ValueError("Only closed tickets can be reopened.")
    channel = guild.get_channel(int(ticket["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("The ticket channel no longer exists.")
    owner = guild.get_member(int(ticket["creator_id"])) or discord.Object(id=int(ticket["creator_id"]))
    overwrite = channel.overwrites_for(owner)
    overwrite.view_channel = True
    overwrite.send_messages = True
    overwrite.read_message_history = True
    await channel.set_permissions(owner, overwrite=overwrite, reason=f"Ticket reopened by {actor}")
    reopened_at = _utcnow()
    if not await bot.db.reopen_ticket_record(int(ticket["id"]), reopened_at):
        raise ValueError("This ticket has already been reopened.")
    ticket.update(
        status="open",
        locked=0,
        closed_at=None,
        closed_by=None,
        closed_by_name=None,
        close_reason=None,
        last_activity_at=reopened_at,
        last_reminder_at=None,
    )
    await refresh_ticket_message(bot, guild, ticket)
    await log_ticket_action(bot, guild, ticket, "reopen", actor.id, actor.display_name, staff_action=True)


async def delete_ticket(bot: Any, guild: discord.Guild, ticket: dict[str, Any], actor: discord.Member) -> None:
    channel = guild.get_channel(int(ticket["channel_id"]))
    if ticket["status"] == "open":
        await close_ticket(
            bot,
            guild,
            ticket,
            actor.id,
            "Ticket deleted by staff.",
            actor_member=actor,
        )
    if not await bot.db.mark_ticket_deleted(int(ticket["id"])):
        raise ValueError("This ticket has already been deleted.")
    ticket["status"] = "deleted"
    await log_ticket_action(bot, guild, ticket, "delete", actor.id, "Ticket channel deleted.", staff_action=True)
    if channel:
        await channel.delete(reason=f"Z9 ticket deleted by {actor}")


async def rename_ticket(
    bot: Any, guild: discord.Guild, ticket: dict[str, Any], actor: discord.Member, name: str
) -> None:
    channel = guild.get_channel(int(ticket["channel_id"]))
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("The ticket channel could not be found.")
    cleaned = name.lower().strip().replace(" ", "-")
    cleaned = "".join(character for character in cleaned if character.isalnum() or character in "-_")
    cleaned = cleaned.strip("-_")[:90]
    if not cleaned:
        raise ValueError("Enter a channel name containing letters or numbers.")
    await channel.edit(name=cleaned, reason=f"Ticket renamed by {actor}")
    await bot.db.update_ticket(int(ticket["id"]), channel_name=cleaned)
    ticket["channel_name"] = cleaned
    await log_ticket_action(bot, guild, ticket, "rename", actor.id, cleaned, staff_action=True)
