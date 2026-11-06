# Z9 Tickets

Z9 Tickets is a private, persistent Discord support bot for the Z9 gaming clan. It uses Python, `discord.py` 2.x, `aiosqlite`, and SQLite. It is designed to run as one always-on Railway worker.

## Features

- Five private ticket categories: General Support, Player Reports, Staff Applications, Partnerships, and Other Issues. There is no clan recruitment category.
- Category-specific intake forms, channel prefixes, and staff role access.
- Persistent category panel and ticket buttons that continue working after restarts.
- Claiming, unclaiming, close/reopen, delete, member access, rename, priority, assignment/transfer, lock/unlock, transcript generation, and ticket information.
- Priority levels: LOW, NORMAL, HIGH, and URGENT.
- Role-checked staff/admin actions, blacklist controls, duplicate prevention, and configurable cooldowns.
- SQLite records for tickets, participants, assignments, staff activity, events, blacklists, feedback, guild settings, and ticket numbering.
- HTML transcripts with authors, IDs, UTC timestamps, attachments, ticket category, creator, assigned staff, closing staff, and reason.
- Transcript files saved locally and optionally uploaded to the configured transcript channel.
- Inactivity reminders, automatic closure, and post-close one-to-five-star feedback.
- `/ticket stats`, staff performance summaries, and `/ticket staff leaderboard`.

## Project layout

```text
Z9-TICKETS/
├── main.py
├── config.py
├── database.py
├── requirements.txt
├── Procfile
├── railway.json
├── .env.example
├── cogs/
├── views/
├── modals/
├── utils/
└── data/
```

## Run locally or in Replit

1. Use Python 3.11 or newer.
2. From this directory, install dependencies with `python -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` for local testing, then set `DISCORD_TOKEN`. Do not commit `.env`.
4. Run the offline validation without a token:

   ```sh
   python main.py --check
   ```

5. Start the bot:

   ```sh
   python main.py
   ```

In Replit, put `DISCORD_TOKEN` in **Tools → Secrets**. The local `.env` file is for local development only.

## Discord Developer Portal setup

1. Open the Discord Developer Portal and create an application for **Z9 Tickets**.
2. In **Bot**, add a bot user and copy its token once. Save it as the `DISCORD_TOKEN` secret in Replit or Railway; never place it in source code, `.env.example`, screenshots, or chat.
3. Under **OAuth2 → URL Generator**, select `bot` and `applications.commands`.
4. Grant the bot these server permissions:
   - View Channels, Send Messages, Read Message History
   - Manage Channels (create, rename, lock, and delete ticket channels)
   - Manage Roles (apply channel permission overwrites)
   - Embed Links and Attach Files
5. Open the generated invite URL and add the bot to the Z9 server. Keep the bot role above any roles it must manage.
6. Copy the server, category, text-channel, and role IDs:
   - Enable **Developer Mode** in Discord settings.
   - Use **Copy Server ID**, **Copy Channel ID**, or **Copy Role ID** from the relevant context menu.
7. Set `GUILD_ID` during initial setup so commands sync quickly to that server. Set category/channel/role IDs using environment variables or `/ticket settings`.
8. Under **Bot → Privileged Gateway Intents**, enable **Server Members Intent** so button-based staff assignment and member access controls can validate current server roles. Message Content Intent is not needed.
9. In the server, run `/ticket setup` in the desired panel channel. The command requires the built-in Administrator permission or a configured `ADMIN_ROLE_IDS` role.

The bot does not need the privileged Message Content intent. Its ticket activity listener only uses message author, channel, and timestamp metadata.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Discord bot token. Save it as a secret. |
| `GUILD_ID` | No | Server to sync slash commands to while setting up; blank syncs globally. |
| `TICKET_CATEGORY_ID` | No | Parent Discord category for ticket channels. |
| `TICKET_PANEL_CHANNEL_ID` | No | Default panel channel used when `/ticket setup` has no channel argument. |
| `TICKET_LOG_CHANNEL_ID` | No | Receives ticket and staff audit events. |
| `TRANSCRIPT_CHANNEL_ID` | No | Receives generated HTML transcripts. |
| `STAFF_ROLE_IDS` | No | Comma-separated staff role IDs used across categories that do not have an override. |
| `ADMIN_ROLE_IDS` | No | Comma-separated admin role IDs; Discord Administrator permission is also accepted. |
| `CATEGORY_STAFF_ROLE_IDS_JSON` | No | Category-to-role map, for example `{"reports":[123456789012345678]}`. |
| `INACTIVITY_TIMEOUT_MINUTES` | No | Delay before a reminder; default is 1440 minutes. |
| `AUTO_CLOSE_TIMEOUT_MINUTES` | No | Inactivity period before automatic closure; default is 10080. Set `0` to disable. |
| `TICKET_COOLDOWN_SECONDS` | No | Minimum wait after closing before the owner can open another ticket; default is 60. |
| `DATA_DIR` | No | Persistent data directory; use `/data` on Railway. |
| `DATABASE_PATH` | No | Optional explicit SQLite path; otherwise defaults to `DATA_DIR/z9_tickets.sqlite3`. |
| `TRANSCRIPT_DIR` | No | Optional transcript path; otherwise defaults to `DATA_DIR/transcripts`. |

Example category-role configuration:

```json
{
  "general": [111111111111111111],
  "reports": [222222222222222222],
  "applications": [333333333333333333],
  "partnerships": [444444444444444444],
  "other": [555555555555555555]
}
```

Use Discord role IDs, not role names. `CATEGORY_STAFF_ROLE_IDS_JSON` takes precedence for categories it lists; other categories use `STAFF_ROLE_IDS`. Administrator roles are included in every ticket channel's private access list.

Guild-level overrides can be changed by administrators with `/ticket settings key value`. `/ticket reset` clears only those settings overrides after a confirmation; it does not remove tickets or transcripts.

## Railway deployment

1. Push the repository to GitHub.
2. Create a Railway project and deploy the repository as a service.
3. Set the service **Root Directory** to `/Z9-TICKETS`.
4. Set the service **Railway Config File** path to `/Z9-TICKETS/railway.json`. Railway does not resolve the config-file path relative to the Root Directory.
5. Add a Railway **Volume** to the service with mount path **`/data`**. Railway volume mounts are configured in the service settings; they are not created by `railway.json`.
6. Add these Railway variables:

   ```text
   DISCORD_TOKEN=<bot token, saved as a secret>
   DATA_DIR=/data
   DATABASE_PATH=/data/z9_tickets.sqlite3
   TRANSCRIPT_DIR=/data/transcripts
   ```

   Add the Discord server, category, log, transcript, and role IDs as needed. Set `GUILD_ID` to speed up the initial command sync.
7. Deploy. Railway starts `python main.py` from the service root. The process reconnects through `discord.py`; Railway restarts the worker if it exits.
8. Keep **one running replica** for this service. SQLite and a Railway volume are intended for a single bot process, not multiple replicas writing concurrently.
9. To update the bot, push a new commit and redeploy. The database and transcript files remain under `/data` across deploys as long as the volume stays attached.

Without the volume, the bot can still start, but local SQLite and transcript files may be lost during redeploys. Do not use that setup for production.

`railway.json` is included because this project requested that format. Railway's current documentation marks Config as Code deprecated with legacy file support ending December 1, 2026; if deploying after that date, migrate the same start/restart settings through Railway Infrastructure as Code.

## Commands

### Ticket operations

- `/ticket setup [channel]`
- `/ticket close [reason]`
- `/ticket reopen`
- `/ticket claim`
- `/ticket unclaim`
- `/ticket add <member>`
- `/ticket remove <member>`
- `/ticket rename <name>`
- `/ticket priority <level>`
- `/ticket assign <member>`
- `/ticket transfer <member>`
- `/ticket lock`
- `/ticket unlock`
- `/ticket transcript`
- `/ticket info`
- `/ticket stats`
- `/ticket blacklist <member> [reason]`
- `/ticket unblacklist <member>`
- `/ticket settings [key] [value]`
- `/ticket reset`
- `/ticket staff stats [member]`
- `/ticket staff leaderboard`

### Safe defaults and behavior

- Creating a ticket is limited to one open ticket per user per server. Closed-ticket cooldowns are configurable.
- Ticket channels deny `@everyone` access and grant the owner, the category's configured staff roles, admin roles, and the bot the required access.
- Claiming and assignment are recorded separately. Only a staff member authorized for the category can be assigned.
- Ticket owners can close their own tickets; staff can close and reopen tickets. Deletion is restricted to administrators.
- Closing stores a transcript on disk, uploads it when a transcript channel is configured, and asks the owner for feedback by DM (falling back to the ticket channel if DMs are unavailable).
- A transcript is generated even when no transcript upload channel is configured; it remains in the configured transcript directory.
- Discord attachment links in transcripts point to Discord-hosted files and may expire. The HTML transcript itself is retained on the persistent volume.

## GitHub upload from Android

The simplest Android workflow is to use Replit's Git/Version Control panel:

1. Open this Replit project in the Replit Android app or mobile browser.
2. Open **Version Control** and choose **Connect to GitHub** (or **Create repository**).
3. Authorize GitHub, choose a private repository unless you intend the bot source to be public, and create the initial commit.
4. Push the commit. The full project is uploaded; the bot source is inside `Z9-TICKETS/`.
5. In Railway, set the service Root Directory to `/Z9-TICKETS` as described above.

If using GitHub's website instead, open it in Chrome with **Desktop site** enabled, create a repository, then use **Add file → Upload files** and select the extracted `Z9-TICKETS` files while keeping the folders. Do not upload a `.env` file or any bot token. GitHub does not execute the ZIP archive for you; extract it on the phone first if you use this route.

## Offline checks

Run from this directory:

```sh
python main.py --check
python -m compileall -q .
```

`--check` initializes SQLite, imports all extensions, validates the expected slash-command registration, and registers persistent views without logging in to Discord. Live interactions, Discord permissions, actual command sync, and Railway volume mounting require a real bot token and server.
