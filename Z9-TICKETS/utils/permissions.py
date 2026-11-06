"""Role and ticket permission checks."""

from __future__ import annotations

import json
from typing import Any

import discord

from config import CATEGORIES


def _ids_from_text(value: str | None) -> set[int]:
    if not value:
        return set()
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return {int(item) for item in parsed if str(item).isdecimal() and int(item) > 0}
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item.isdecimal() and int(item) > 0:
            result.add(int(item))
    return result


def category_role_map(bot: Any, settings: dict[str, str]) -> dict[str, set[int]]:
    raw = settings.get("category_staff_role_ids")
    if raw:
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                return {key: _ids_from_text(json.dumps(ids)) for key, ids in value.items() if key in CATEGORIES}
        except (json.JSONDecodeError, TypeError):
            pass
    return {key: set(role_ids) for key, role_ids in bot.config.category_staff_role_ids.items()}


def admin_role_ids(bot: Any, settings: dict[str, str]) -> set[int]:
    return _ids_from_text(settings.get("admin_role_ids")) or set(bot.config.admin_role_ids)


def global_staff_role_ids(bot: Any, settings: dict[str, str]) -> set[int]:
    return _ids_from_text(settings.get("staff_role_ids")) or set(bot.config.staff_role_ids)


def staff_role_ids_for_ticket(
    bot: Any, settings: dict[str, str], category_key: str | None
) -> set[int]:
    category_map = category_role_map(bot, settings)
    if category_key and category_key in category_map:
        return category_map[category_key]
    return global_staff_role_ids(bot, settings)


def has_role(member: discord.Member, role_ids: set[int]) -> bool:
    return any(role.id in role_ids for role in member.roles)


async def is_admin(member: discord.Member, bot: Any, settings: dict[str, str] | None = None) -> bool:
    if member.guild_permissions.administrator:
        return True
    resolved = settings if settings is not None else await bot.db.get_settings(member.guild.id)
    return has_role(member, admin_role_ids(bot, resolved))


async def is_staff(
    member: discord.Member,
    bot: Any,
    ticket: dict[str, Any] | None = None,
    settings: dict[str, str] | None = None,
) -> bool:
    resolved = settings if settings is not None else await bot.db.get_settings(member.guild.id)
    if await is_admin(member, bot, resolved):
        return True
    category_key = ticket.get("category_key") if ticket else None
    if category_key is None:
        role_ids = global_staff_role_ids(bot, resolved)
        for category_roles in category_role_map(bot, resolved).values():
            role_ids.update(category_roles)
        return has_role(member, role_ids)
    return has_role(member, staff_role_ids_for_ticket(bot, resolved, category_key))


async def can_manage_ticket(
    interaction: discord.Interaction,
    bot: Any,
    ticket: dict[str, Any],
    *,
    owner_may_close: bool = False,
) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message("This control is only available inside a server.", ephemeral=True)
        return False
    settings = await bot.db.get_settings(member.guild.id)
    if await is_staff(member, bot, ticket, settings):
        return True
    if owner_may_close and member.id == int(ticket["creator_id"]):
        return True
    message = "You do not have permission to use this ticket control."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
    return False


async def require_ticket_staff(
    interaction: discord.Interaction, bot: Any, ticket: dict[str, Any]
) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        if interaction.response.is_done():
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        else:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return False
    if await is_staff(member, bot, ticket):
        return True
    message = "You need an authorized Z9 staff role for this ticket."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
    return False


async def require_admin(interaction: discord.Interaction, bot: Any) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        if interaction.response.is_done():
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        else:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return False
    if await is_admin(member, bot):
        return True
    message = "You need a configured administrator role to do that."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
    return False
