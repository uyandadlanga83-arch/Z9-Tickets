"""Shared application-command group; cogs add their commands here."""

from discord import app_commands

ticket_group = app_commands.Group(
    name="ticket",
    description="Open and manage Z9 support tickets.",
    guild_only=True,
)

staff_group = app_commands.Group(
    name="staff",
    description="Z9 support staff activity and performance.",
    guild_only=True,
)
