"""Safe HTML transcript generation for ticket channels."""

from __future__ import annotations

import html
import re
from datetime import timezone
from pathlib import Path
from typing import Any

import discord


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _timestamp(value: Any) -> str:
    if not isinstance(value, discord.utils.utcnow().__class__):
        return _escape(value)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _message_body(message: discord.Message) -> str:
    parts: list[str] = []
    if message.content:
        parts.append(f"<div class=\"content\">{_escape(message.content).replace(chr(10), '<br>')}</div>")
    for attachment in message.attachments:
        filename = _escape(attachment.filename)
        url = _escape(attachment.url)
        parts.append(
            f'<div class="attachment">Attachment: <a href="{url}" rel="noreferrer noopener">{filename}</a>'
            f" ({attachment.size:,} bytes)</div>"
        )
    for embed in message.embeds:
        if embed.title:
            parts.append(f'<div class="embed-title">{_escape(embed.title)}</div>')
        if embed.description:
            parts.append(f'<div class="embed-desc">{_escape(embed.description)}</div>')
        for field in embed.fields:
            parts.append(
                '<div class="embed-field">'
                f"<strong>{_escape(field.name)}</strong><br>{_escape(field.value).replace(chr(10), '<br>')}"
                "</div>"
            )
    if not parts:
        parts.append('<div class="content muted">[No text content]</div>')
    return "\n".join(parts)


async def generate_transcript(
    channel: discord.TextChannel,
    ticket: dict[str, Any],
    transcript_dir: Path,
    *,
    closing_staff_id: int | None = None,
    closing_reason: str = "",
) -> Path:
    transcript_dir.mkdir(parents=True, exist_ok=True)
    messages: list[discord.Message] = []
    async for message in channel.history(limit=None, oldest_first=True):
        messages.append(message)

    transcript_name = (
        f"z9-ticket-{int(ticket['guild_id'])}-{int(ticket['id'])}-{int(ticket['ticket_number']):04d}.html"
    )
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "-", transcript_name)
    output_path = transcript_dir / safe_name

    message_rows: list[str] = []
    for message in messages:
        author = message.author
        display_name = getattr(author, "display_name", author.name)
        avatar = author.display_avatar.url if hasattr(author, "display_avatar") else ""
        message_rows.append(
            '<article class="message">'
            '<div class="author">'
            f'<img src="{_escape(avatar)}" alt="" class="avatar">'
            f'<strong>{_escape(display_name)}</strong> '
            f'<span class="muted">ID {_escape(author.id)}</span>'
            f'<time>{_timestamp(message.created_at)}</time>'
            "</div>"
            f"<div class=\"body\">{_message_body(message)}</div>"
            "</article>"
        )

    title = _escape(f"Z9 Ticket #{int(ticket['ticket_number']):04d} — {ticket['category_title']}")
    owner = f"{_escape(ticket.get('creator_name') or 'Unknown')} (ID {_escape(ticket['creator_id'])})"
    assigned = (
        f"{_escape(ticket.get('assigned_staff_name') or 'Staff member')} "
        f"(ID {_escape(ticket['assigned_staff_id'])})"
        if ticket.get("assigned_staff_id")
        else "Unassigned"
    )
    closer_id = closing_staff_id or ticket.get("closed_by")
    closer_name = ticket.get("closed_by_name") or ("Z9 Tickets Automation" if not closing_staff_id else "Staff member")
    closer = (
        f"{_escape(closer_name)} (ID {_escape(closer_id)})" if closer_id else "Not recorded"
    )
    details = (
        f"<div><b>Category:</b> {_escape(ticket['category_title'])}</div>"
        f"<div><b>Ticket creator:</b> {owner}</div>"
        f"<div><b>Assigned staff:</b> {_escape(assigned)}</div>"
        f"<div><b>Closing staff:</b> {_escape(closer)}</div>"
        f"<div><b>Closing reason:</b> {_escape(closing_reason or ticket.get('close_reason') or 'Not provided')}</div>"
        f"<div><b>Created:</b> {_timestamp(discord.utils.parse_time(ticket['created_at']))}</div>"
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: data:; style-src 'unsafe-inline';">
<title>{title}</title><style>
:root{{color-scheme:dark;--bg:#0c0d10;--panel:#17191f;--red:#dc2637;--text:#f2f3f5;--muted:#a1a5af}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}}
header{{padding:28px clamp(16px,5vw,64px);border-bottom:1px solid #2a2d35;background:linear-gradient(120deg,#201014,#101115)}}
h1{{margin:0 0 12px;font-size:clamp(22px,4vw,34px)}}.brand{{color:var(--red);font-weight:800;letter-spacing:.12em;font-size:12px}}
.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;color:var(--muted);font-size:13px}}
main{{max-width:1000px;margin:24px auto;padding:0 16px}}.message{{background:var(--panel);border:1px solid #292c34;border-radius:12px;padding:14px 16px;margin:10px 0}}
.author{{display:flex;align-items:center;gap:8px;font-size:13px}}.avatar{{width:28px;height:28px;border-radius:50%;object-fit:cover}}
time{{margin-left:auto;color:var(--muted);font-size:12px}}.muted{{color:var(--muted)}}.body{{margin:10px 0 0 36px;overflow-wrap:anywhere}}
a{{color:#ff6f7c}}.attachment,.embed-title,.embed-desc,.embed-field{{margin-top:8px;padding:8px 10px;background:#20232a;border-left:3px solid var(--red);border-radius:4px}}
footer{{padding:30px;text-align:center;color:var(--muted);font-size:12px}}
</style></head><body><header><div class="brand">Z9 TICKETS / TRANSCRIPT</div><h1>{title}</h1><div class="summary">{details}</div></header>
<main>{''.join(message_rows) or '<p class="muted">No messages were captured.</p>'}</main>
<footer>Generated by Z9 Tickets. Attachment URLs are hosted by Discord and may expire.</footer></body></html>"""
    output_path.write_text(document, encoding="utf-8")
    return output_path
