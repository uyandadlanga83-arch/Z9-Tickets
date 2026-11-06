"""Z9 Tickets Discord bot entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import CATEGORIES, Config, load_config
from database import Database


EXTENSIONS = (
    "cogs.tickets",
    "cogs.ticket_admin",
    "cogs.statistics",
    "cogs.staff",
)
EXPECTED_TICKET_COMMANDS = {
    "setup", "close", "reopen", "claim", "unclaim", "add", "remove", "rename",
    "priority", "assign", "transfer", "lock", "unlock", "transcript", "info",
    "stats", "blacklist", "unblacklist", "settings", "reset", "staff",
}


class Z9Bot(commands.Bot):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.db = Database(config.database_path)
        self.logger = logging.getLogger("z9_tickets")
        self.bot_instance = self
        self._extensions_loaded = False
        self._persistent_views_registered = False
        self._permissions_synced = False

    async def setup_hook(self) -> None:
        await self.db.initialize()
        await self.load_all_extensions()
        await self.register_persistent_views()
        await self.sync_commands()

    async def load_all_extensions(self) -> None:
        if self._extensions_loaded:
            return
        for extension in EXTENSIONS:
            await self.load_extension(extension)
        self._extensions_loaded = True

    async def register_persistent_views(self) -> None:
        if self._persistent_views_registered:
            return
        from views.ticket_controls import TicketControlsView
        from views.ticket_feedback import FeedbackView
        from views.ticket_panel import TicketPanelView

        self.add_view(TicketPanelView(self))
        self.add_view(TicketControlsView(self))
        for row in await self.db.get_feedback_ticket_ids():
            if row["feedback_message_id"] and row["feedback_channel_id"]:
                self.add_view(
                    FeedbackView(int(row["id"]), int(row["guild_id"]), int(row["creator_id"])),
                    message_id=int(row["feedback_message_id"]),
                )
        self._persistent_views_registered = True

    async def sync_commands(self) -> None:
        if self.config.guild_id:
            guild = discord.Object(id=self.config.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            self.logger.info("Synced %d Z9 slash commands to guild %s", len(synced), self.config.guild_id)
        else:
            synced = await self.tree.sync()
            self.logger.info("Synced %d global Z9 slash commands", len(synced))

    async def close(self) -> None:
        if self.automation.is_running():
            self.automation.cancel()
        await super().close()
        await self.db.close()

    async def on_ready(self) -> None:
        if not self._permissions_synced:
            from utils.ticket_actions import sync_ticket_channel_permissions

            for guild in self.guilds:
                for ticket in await self.db.list_guild_tickets(guild.id):
                    try:
                        await sync_ticket_channel_permissions(self, guild, ticket)
                    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                        self.logger.warning(
                            "Could not reconcile access for ticket %s in guild %s",
                            ticket["id"],
                            guild.id,
                        )
                    except Exception:
                        self.logger.exception("Permission reconciliation failed for ticket %s", ticket["id"])
            self._permissions_synced = True
        if not self.automation.is_running():
            self.automation.start()
        self.logger.info(
            "Z9 Tickets online as %s in %d guild(s).",
            self.user,
            len(self.guilds),
        )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        self.logger.exception("Unhandled slash command error", exc_info=error)
        message = "The command could not be completed. Please try again or contact Z9 staff."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    @tasks.loop(minutes=1)
    async def automation(self) -> None:
        now = datetime.now(timezone.utc)
        for ticket in await self.db.list_open_tickets():
            try:
                await self._process_ticket_automation(ticket, now)
            except Exception:
                self.logger.exception("Automation failed for ticket %s", ticket.get("id"))

    @automation.before_loop
    async def before_automation(self) -> None:
        await self.wait_until_ready()

    async def _process_ticket_automation(self, ticket: dict[str, Any], now: datetime) -> None:
        guild = self.get_guild(int(ticket["guild_id"]))
        if guild is None:
            return
        settings = await self.db.get_settings(guild.id)
        inactivity_minutes = int(
            settings.get("inactivity_timeout_minutes", str(self.config.inactivity_timeout_minutes))
        )
        auto_close_minutes = int(
            settings.get("auto_close_timeout_minutes", str(self.config.auto_close_timeout_minutes))
        )
        last_activity = datetime.fromisoformat(ticket["last_activity_at"])
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)
        inactive_for = now - last_activity

        if auto_close_minutes > 0 and inactive_for >= timedelta(minutes=auto_close_minutes):
            from utils.ticket_actions import close_ticket

            actor_id = self.user.id if self.user else 0
            await close_ticket(
                self,
                guild,
                ticket,
                actor_id,
                "Automatically closed after prolonged inactivity.",
                automatic=True,
            )
            return

        if inactive_for < timedelta(minutes=inactivity_minutes):
            return
        last_reminded = ticket.get("last_reminder_at")
        if last_reminded:
            reminder_time = datetime.fromisoformat(last_reminded)
            if reminder_time.tzinfo is None:
                reminder_time = reminder_time.replace(tzinfo=timezone.utc)
            if now - reminder_time < timedelta(minutes=inactivity_minutes):
                return
        channel = guild.get_channel(int(ticket["channel_id"]))
        if channel is None:
            try:
                channel = await self.fetch_channel(int(ticket["channel_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        if not isinstance(channel, discord.TextChannel):
            return
        await channel.send(
            f"<@{ticket['creator_id']}> This ticket has been inactive. Reply if you still need help; "
            "it may close automatically after the configured timeout.",
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        await self.db.update_ticket(int(ticket["id"]), last_reminder_at=now.isoformat())
        await self.db.add_event(
            guild.id,
            int(ticket["id"]),
            self.user.id if self.user else None,
            "inactivity_reminder",
            f"Inactive for {int(inactive_for.total_seconds() // 60)} minutes.",
        )


def configure_logging() -> None:
    level = getattr(logging, "INFO")
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def run_check(bot: Z9Bot) -> int:
    """Run offline setup checks without Discord credentials or network access."""
    try:
        await bot.db.initialize()
        await bot.load_all_extensions()
        await bot.register_persistent_views()
        expected_categories = {"general", "reports", "applications", "partnerships", "other"}
        if set(CATEGORIES) != expected_categories:
            raise RuntimeError("Ticket category configuration does not match the five required categories.")
        commands_found = bot.tree.get_commands()
        ticket = next((command for command in commands_found if command.name == "ticket"), None)
        if not isinstance(ticket, app_commands.Group):
            raise RuntimeError("The /ticket command group was not registered.")
        registered = {command.name for command in ticket.commands}
        missing = EXPECTED_TICKET_COMMANDS - registered
        if missing:
            raise RuntimeError(f"Missing /ticket commands: {', '.join(sorted(missing))}")
        from views.ticket_controls import TicketControlsView
        from views.ticket_feedback import FeedbackView
        from views.ticket_panel import TicketPanelView

        panel_view = TicketPanelView(bot)
        panel_categories = {option.value for option in panel_view.children[0].options}
        if panel_categories != expected_categories:
            raise RuntimeError("Ticket panel options do not match the configured categories.")
        expected_controls = {
            "claim", "unclaim", "close", "reopen", "delete", "add", "remove", "rename",
            "priority", "assign", "transfer", "lock", "unlock", "transcript",
        }
        control_ids = {item.custom_id for item in TicketControlsView(bot).children}
        if control_ids != {f"z9:ticket:{action}" for action in expected_controls}:
            raise RuntimeError("A ticket control is missing or has an unexpected custom ID.")
        if len(FeedbackView(1, 1, 1).children) != 5:
            raise RuntimeError("Feedback view must offer five ratings.")
        config_path = Path(__file__).with_name("railway.json")
        railway = json.loads(config_path.read_text())
        if railway.get("build", {}).get("builder") != "RAILPACK":
            raise RuntimeError("railway.json must use the current RAILPACK builder.")
        if railway.get("deploy", {}).get("startCommand") != "python main.py":
            raise RuntimeError("railway.json has an unexpected start command.")
        if "worker: python main.py" not in config_path.with_name("Procfile").read_text():
            raise RuntimeError("Procfile must start the persistent worker.")
        print(f"CHECK_OK: SQLite initialized at {bot.config.database_path}")
        print(f"CHECK_OK: {len(registered)} /ticket commands registered")
        print("CHECK_OK: Five panel categories, fourteen ticket controls, and five feedback ratings registered")
        print("CHECK_OK: Railway RAILPACK, worker start command, and Procfile validated")
        return 0
    except Exception:
        bot.logger.exception("Offline validation failed")
        return 1
    finally:
        await bot.close()


async def async_main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Z9 Tickets Discord bot")
    parser.add_argument("--check", action="store_true", help="Validate local database, views, and command registration offline")
    args = parser.parse_args()
    try:
        config = load_config()
    except ValueError as exc:
        logging.getLogger("z9_tickets").error("Invalid configuration: %s", exc)
        return 2
    bot = Z9Bot(config)
    if args.check:
        return await run_check(bot)
    if not config.token:
        logging.getLogger("z9_tickets").error(
            "DISCORD_TOKEN is missing. Add it as an environment secret before starting the bot."
        )
        await bot.close()
        return 2
    loop = asyncio.get_running_loop()
    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                shutdown_signal,
                lambda: asyncio.create_task(bot.close()),
            )
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await bot.start(config.token, reconnect=True)
    except discord.LoginFailure:
        logging.getLogger("z9_tickets").error("Discord rejected the configured bot token.")
        return 2
    except KeyboardInterrupt:
        logging.getLogger("z9_tickets").info("Shutdown requested.")
    finally:
        if not bot.is_closed():
            await bot.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(async_main()))
