import discord
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


COLORS = {
    "success": 0x2ECC71,
    "error": 0xE74C3C,
    "warning": 0xF39C12,
    "info": 0x3498DB,
    "moderation": 0xE67E22,
    "ticket": 0x9B59B6,
    "log": 0x95A5A6,
    "security": 0xC0392B,
}


def success(title: str, description: str = None, **kwargs) -> discord.Embed:
    e = discord.Embed(title=f"✅ {title}", description=description, color=COLORS["success"], **kwargs)
    e.timestamp = _now()
    return e


def error(title: str, description: str = None, **kwargs) -> discord.Embed:
    e = discord.Embed(title=f"❌ {title}", description=description, color=COLORS["error"], **kwargs)
    e.timestamp = _now()
    return e


def warning(title: str, description: str = None, **kwargs) -> discord.Embed:
    e = discord.Embed(title=f"⚠️ {title}", description=description, color=COLORS["warning"], **kwargs)
    e.timestamp = _now()
    return e


def info(title: str, description: str = None, **kwargs) -> discord.Embed:
    e = discord.Embed(title=f"ℹ️ {title}", description=description, color=COLORS["info"], **kwargs)
    e.timestamp = _now()
    return e


def moderation_log(action: str, moderator: discord.Member, target: discord.User | discord.Member, reason: str = None, duration: str = None) -> discord.Embed:
    e = discord.Embed(title=f"🔨 Moderation — {action}", color=COLORS["moderation"])
    e.add_field(name="Moderator", value=f"{moderator.mention} (`{moderator.id}`)", inline=True)
    e.add_field(name="Target", value=f"{target.mention} (`{target.id}`)", inline=True)
    if reason:
        e.add_field(name="Reason", value=reason, inline=False)
    if duration:
        e.add_field(name="Duration", value=duration, inline=True)
    e.timestamp = _now()
    e.set_thumbnail(url=target.display_avatar.url)
    return e


def ticket_embed(ticket_id: str, category: str, creator: discord.Member, reason: str = None) -> discord.Embed:
    e = discord.Embed(
        title=f"🎫 Ticket — {category}",
        description=f"Welcome {creator.mention}! Support will be with you shortly.\n\nUse the buttons below to manage this ticket.",
        color=COLORS["ticket"],
    )
    e.add_field(name="Ticket ID", value=ticket_id, inline=True)
    e.add_field(name="Category", value=category, inline=True)
    e.add_field(name="Creator", value=creator.mention, inline=True)
    if reason:
        e.add_field(name="Reason", value=reason, inline=False)
    e.timestamp = _now()
    e.set_footer(text="Malta SMP Support System")
    return e


def log_embed(title: str, color: int = None, **fields) -> discord.Embed:
    e = discord.Embed(title=title, color=color or COLORS["log"])
    for name, value in fields.items():
        e.add_field(name=name.replace("_", " ").title(), value=str(value), inline=True)
    e.timestamp = _now()
    return e
