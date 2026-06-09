"""
cogs/announcements.py
─────────────────────
Full-featured announcement system for Malta SMP.

Features:
  - Rich embed announcements with optional image, thumbnail, footer, colour
  - Plain-text announcements (for @everyone pings that need no embed)
  - Optional role pings (@everyone / @here / specific role)
  - Scheduled announcements (send at a future datetime)
  - Announcement templates (save & reuse common formats)
  - Announcement history stored in DB (view last N sent)
  - Cross-post (publish) support for Discord News channels
  - Configurable default announcement channel per guild
  - All actions logged to the mod-log channel

Slash commands:
  /announce send      — send a rich embedded announcement now
  /announce plain     — send a plain-text announcement (good for @everyone)
  /announce schedule  — schedule an announcement for a future time
  /announce cancel    — cancel a pending scheduled announcement
  /announce list      — list pending scheduled announcements
  /announce history   — view recently sent announcements
  /announce template save    — save a reusable template
  /announce template use     — send from a saved template
  /announce template list    — list saved templates
  /announce template delete  — delete a saved template
  /announce setchannel       — set the default announcement channel
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import utils.embeds as E
from utils.permissions import require_staff

log = logging.getLogger("MaltaSMP.Announcements")

# ── Colour presets ────────────────────────────────────────────────────────────
COLOUR_PRESETS = {
    "blue":    0x3498DB,
    "green":   0x2ECC71,
    "red":     0xE74C3C,
    "orange":  0xE67E22,
    "yellow":  0xF1C40F,
    "purple":  0x9B59B6,
    "teal":    0x1ABC9C,
    "gold":    0xF39C12,
    "white":   0xFFFFFF,
    "black":   0x2C2F33,
    "default": 0x3498DB,
}


def _resolve_colour(name: str) -> int:
    """Accept a preset name or a hex string like #FF5500."""
    lower = name.strip().lower()
    if lower in COLOUR_PRESETS:
        return COLOUR_PRESETS[lower]
    hex_str = lower.lstrip("#")
    try:
        return int(hex_str, 16)
    except ValueError:
        return COLOUR_PRESETS["default"]


# ── Modal for long announcement body ─────────────────────────────────────────

class AnnouncementModal(discord.ui.Modal, title="Create Announcement"):
    ann_title = discord.ui.TextInput(
        label="Title",
        placeholder="e.g. Server Update — New Season!",
        max_length=256,
        required=True,
    )
    ann_body = discord.ui.TextInput(
        label="Body",
        style=discord.TextStyle.paragraph,
        placeholder="Write the full announcement here...",
        max_length=3900,
        required=True,
    )
    ann_footer = discord.ui.TextInput(
        label="Footer (optional)",
        placeholder="e.g. — Malta SMP Staff Team",
        required=False,
        max_length=200,
    )

    def __init__(self, cog, channel: discord.TextChannel, ping: str, colour: int, image_url: str):
        super().__init__()
        self._cog = cog
        self._channel = channel
        self._ping = ping
        self._colour = colour
        self._image_url = image_url

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._cog._send_announcement(
            interaction=interaction,
            channel=self._channel,
            title=self.ann_title.value,
            body=self.ann_body.value,
            footer=self.ann_footer.value or None,
            ping=self._ping,
            colour=self._colour,
            image_url=self._image_url or None,
        )


class PlainModal(discord.ui.Modal, title="Plain Announcement"):
    content = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        placeholder="Write your plain-text announcement here...",
        max_length=1900,
        required=True,
    )

    def __init__(self, cog, channel: discord.TextChannel, ping: str):
        super().__init__()
        self._cog = cog
        self._channel = channel
        self._ping = ping

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._cog._send_plain(interaction, self._channel, self.content.value, self._ping)


# ── Main cog ──────────────────────────────────────────────────────────────────

class AnnouncementsCog(commands.Cog, name="Announcements"):
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> { job_id: asyncio.Task }
        self._scheduled: dict[int, dict[int, asyncio.Task]] = {}

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _get_default_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        cid = await self.bot.db.get_config_int(guild.id, "announcement_channel_id")
        return guild.get_channel(cid) if cid else None

    async def _save_announcement(
        self,
        guild_id: int,
        channel_id: int,
        author_id: int,
        title: str,
        body: str,
        ping: str,
        scheduled: bool = False,
        scheduled_for: str = None,
        status: str = "sent",
    ) -> int:
        return await self.bot.db.execute(
            """
            INSERT INTO announcements
              (guild_id, channel_id, author_id, title, body, ping, scheduled, scheduled_for, status)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (guild_id, channel_id, author_id, title, body, ping,
             1 if scheduled else 0, scheduled_for, status),
        )

    async def _update_ann_status(self, ann_id: int, status: str):
        await self.bot.db.execute(
            "UPDATE announcements SET status=? WHERE id=?",
            (status, ann_id),
        )

    async def _get_templates(self, guild_id: int) -> list:
        return await self.bot.db.fetchall(
            "SELECT * FROM announcement_templates WHERE guild_id=? ORDER BY name",
            (guild_id,),
        )

    async def _get_template(self, guild_id: int, name: str):
        return await self.bot.db.fetchone(
            "SELECT * FROM announcement_templates WHERE guild_id=? AND name=?",
            (guild_id, name.lower()),
        )

    # ── Core send helpers ─────────────────────────────────────────────────────

    async def _build_ping_content(self, guild: discord.Guild, ping: str) -> str:
        """Resolve ping string to a mention."""
        if not ping or ping == "none":
            return ""
        if ping == "everyone":
            return "@everyone"
        if ping == "here":
            return "@here"
        # Try to resolve as a role name or ID
        if ping.isdigit():
            role = guild.get_role(int(ping))
            return role.mention if role else ""
        role = discord.utils.get(guild.roles, name=ping)
        return role.mention if role else ""

    async def _send_announcement(
        self,
        *,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        title: str,
        body: str,
        footer: str = None,
        ping: str = "none",
        colour: int = 0x3498DB,
        image_url: str = None,
        thumbnail_url: str = None,
    ) -> discord.Message | None:
        """Build and send the embed announcement."""
        embed = discord.Embed(
            title=title,
            description=body,
            color=colour,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(
            name=interaction.guild.name,
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None,
        )
        if footer:
            embed.set_footer(text=footer)
        else:
            embed.set_footer(text=f"Malta SMP • Announced by {interaction.user.display_name}")
        if image_url:
            embed.set_image(url=image_url)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        ping_content = await self._build_ping_content(interaction.guild, ping)

        try:
            msg = await channel.send(content=ping_content or None, embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=E.error("Permission Denied", f"I don't have permission to send messages in {channel.mention}."),
                ephemeral=True,
            )
            return None
        except discord.HTTPException as exc:
            await interaction.followup.send(
                embed=E.error("Send Failed", f"Failed to send announcement: {exc}"),
                ephemeral=True,
            )
            return None

        # Auto-publish if it's a news channel
        if channel.type == discord.ChannelType.news:
            try:
                await msg.publish()
            except Exception:
                pass

        # Save to DB
        await self._save_announcement(
            guild_id=interaction.guild_id,
            channel_id=channel.id,
            author_id=interaction.user.id,
            title=title,
            body=body[:500],
            ping=ping,
        )

        # Log the action
        await self._log(interaction.guild, interaction.user, channel, title, "Sent")

        await interaction.followup.send(
            embed=E.success("Announcement Sent", f"Your announcement was posted in {channel.mention}."),
            ephemeral=True,
        )
        return msg

    async def _send_plain(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        content: str,
        ping: str = "none",
    ):
        """Send a plain-text announcement (no embed)."""
        ping_content = await self._build_ping_content(interaction.guild, ping)
        full = f"{ping_content}\n{content}".strip() if ping_content else content

        try:
            msg = await channel.send(content=full)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=E.error("Permission Denied", f"Can't send to {channel.mention}."),
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(
                embed=E.error("Send Failed", str(exc)),
                ephemeral=True,
            )
            return

        if channel.type == discord.ChannelType.news:
            try:
                await msg.publish()
            except Exception:
                pass

        await self._save_announcement(
            guild_id=interaction.guild_id,
            channel_id=channel.id,
            author_id=interaction.user.id,
            title="[Plain]",
            body=content[:500],
            ping=ping,
        )
        await self._log(interaction.guild, interaction.user, channel, content[:80], "Sent (plain)")
        await interaction.followup.send(
            embed=E.success("Announcement Sent", f"Plain announcement posted in {channel.mention}."),
            ephemeral=True,
        )

    async def _log(self, guild: discord.Guild, author: discord.Member, channel: discord.TextChannel, title: str, action: str):
        """Log announcement to mod-log channel."""
        log_cid = await self.bot.db.get_config_int(guild.id, "mod_log_channel_id")
        if not log_cid:
            return
        log_ch = guild.get_channel(log_cid)
        if not log_ch:
            return
        embed = discord.Embed(
            title=f"📢 Announcement — {action}",
            color=0x3498DB,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Author", value=author.mention, inline=True)
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Title", value=title[:100], inline=False)
        try:
            await log_ch.send(embed=embed)
        except Exception:
            pass
        await self.bot.db.log_staff_action(guild.id, author.id, "announcement", None, f"{action}: {title[:100]}")

    # ── Scheduled announcement runner ─────────────────────────────────────────

    async def _run_scheduled(
        self,
        ann_id: int,
        delay: float,
        guild_id: int,
        channel_id: int,
        author_id: int,
        title: str,
        body: str,
        footer: str,
        ping: str,
        colour: int,
        image_url: str,
    ):
        """Background task: wait then send a scheduled announcement."""
        await asyncio.sleep(delay)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = guild.get_channel(channel_id)
        if not channel:
            await self._update_ann_status(ann_id, "failed_no_channel")
            return
        author = guild.get_member(author_id) or guild.me

        embed = discord.Embed(
            title=title,
            description=body,
            color=colour,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(
            name=guild.name,
            icon_url=guild.icon.url if guild.icon else None,
        )
        if footer:
            embed.set_footer(text=footer)
        else:
            embed.set_footer(text="Malta SMP • Scheduled Announcement")
        if image_url:
            embed.set_image(url=image_url)

        # Build ping content manually (no interaction object here)
        ping_content = ""
        if ping == "everyone":
            ping_content = "@everyone"
        elif ping == "here":
            ping_content = "@here"
        elif ping and ping != "none":
            if ping.isdigit():
                role = guild.get_role(int(ping))
                ping_content = role.mention if role else ""
            else:
                role = discord.utils.get(guild.roles, name=ping)
                ping_content = role.mention if role else ""

        try:
            msg = await channel.send(content=ping_content or None, embed=embed)
            if channel.type == discord.ChannelType.news:
                try:
                    await msg.publish()
                except Exception:
                    pass
            await self._update_ann_status(ann_id, "sent")
            log.info(f"Scheduled announcement {ann_id} sent in guild {guild_id}")
        except Exception as exc:
            log.error(f"Scheduled announcement {ann_id} failed: {exc}")
            await self._update_ann_status(ann_id, "failed")

        # Clean up task reference
        guild_tasks = self._scheduled.get(guild_id, {})
        guild_tasks.pop(ann_id, None)

    # ── /announce command group ───────────────────────────────────────────────

    announce_group = app_commands.Group(
        name="announce",
        description="Announcement tools for staff",
    )

    # -- /announce send --

    @announce_group.command(name="send", description="Send a rich embedded announcement")
    @require_staff()
    @app_commands.describe(
        channel="Channel to post in (uses default if not set)",
        ping="Who to ping: none / everyone / here / role name",
        colour="Embed colour: blue/green/red/orange/purple/gold or a hex code",
        image_url="URL of a large image to attach to the embed",
    )
    async def announce_send(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
        ping: str = "none",
        colour: str = "blue",
        image_url: str = None,
    ):
        target = channel or await self._get_default_channel(interaction.guild)
        if not target:
            await interaction.response.send_message(
                embed=E.error(
                    "No Channel Set",
                    "Provide a channel or set a default with `/announce setchannel`.",
                ),
                ephemeral=True,
            )
            return

        colour_int = _resolve_colour(colour)
        modal = AnnouncementModal(self, target, ping, colour_int, image_url or "")
        await interaction.response.send_modal(modal)

    # -- /announce plain --

    @announce_group.command(name="plain", description="Send a plain-text announcement (no embed)")
    @require_staff()
    @app_commands.describe(
        channel="Channel to post in",
        ping="Who to ping: none / everyone / here / role name",
    )
    async def announce_plain(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
        ping: str = "none",
    ):
        target = channel or await self._get_default_channel(interaction.guild)
        if not target:
            await interaction.response.send_message(
                embed=E.error("No Channel Set", "Provide a channel or use `/announce setchannel`."),
                ephemeral=True,
            )
            return
        modal = PlainModal(self, target, ping)
        await interaction.response.send_modal(modal)

    # -- /announce schedule --

    @announce_group.command(name="schedule", description="Schedule an announcement for a future time")
    @require_staff()
    @app_commands.describe(
        title="Announcement title",
        body="Announcement body text",
        send_at="When to send — format: YYYY-MM-DD HH:MM (UTC)",
        channel="Channel to post in",
        ping="Who to ping: none / everyone / here / role name",
        colour="Embed colour preset or hex",
        footer="Optional footer text",
        image_url="Optional image URL",
    )
    async def announce_schedule(
        self,
        interaction: discord.Interaction,
        title: str,
        body: str,
        send_at: str,
        channel: discord.TextChannel = None,
        ping: str = "none",
        colour: str = "blue",
        footer: str = None,
        image_url: str = None,
    ):
        # Parse the datetime
        try:
            send_dt = datetime.strptime(send_at.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            await interaction.response.send_message(
                embed=E.error("Invalid Date", "Use the format `YYYY-MM-DD HH:MM` (UTC), e.g. `2025-12-25 18:00`"),
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc)
        if send_dt <= now:
            await interaction.response.send_message(
                embed=E.error("Invalid Time", "The scheduled time must be in the future."),
                ephemeral=True,
            )
            return

        delay = (send_dt - now).total_seconds()

        target = channel or await self._get_default_channel(interaction.guild)
        if not target:
            await interaction.response.send_message(
                embed=E.error("No Channel Set", "Provide a channel or use `/announce setchannel`."),
                ephemeral=True,
            )
            return

        colour_int = _resolve_colour(colour)

        # Save to DB with status "pending"
        ann_id = await self._save_announcement(
            guild_id=interaction.guild_id,
            channel_id=target.id,
            author_id=interaction.user.id,
            title=title,
            body=body[:500],
            ping=ping,
            scheduled=True,
            scheduled_for=send_dt.isoformat(),
            status="pending",
        )

        # Spawn background task
        task = asyncio.create_task(
            self._run_scheduled(
                ann_id=ann_id,
                delay=delay,
                guild_id=interaction.guild_id,
                channel_id=target.id,
                author_id=interaction.user.id,
                title=title,
                body=body,
                footer=footer,
                ping=ping,
                colour=colour_int,
                image_url=image_url,
            )
        )
        self._scheduled.setdefault(interaction.guild_id, {})[ann_id] = task

        embed = E.success(
            "Announcement Scheduled",
            f"📅 Will be sent in **{target.mention}** at `{send_dt.strftime('%Y-%m-%d %H:%M UTC')}`\n"
            f"**ID:** `{ann_id}` · **Title:** {title[:60]}",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self._log(interaction.guild, interaction.user, target, title, f"Scheduled for {send_at}")

    # -- /announce cancel --

    @announce_group.command(name="cancel", description="Cancel a pending scheduled announcement")
    @require_staff()
    @app_commands.describe(announcement_id="ID from /announce list")
    async def announce_cancel(self, interaction: discord.Interaction, announcement_id: int):
        guild_tasks = self._scheduled.get(interaction.guild_id, {})
        task = guild_tasks.pop(announcement_id, None)

        if task:
            task.cancel()
            await self._update_ann_status(announcement_id, "cancelled")
            await interaction.response.send_message(
                embed=E.success("Announcement Cancelled", f"Scheduled announcement `#{announcement_id}` has been cancelled."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                embed=E.error("Not Found", f"No pending scheduled announcement with ID `{announcement_id}` found."),
                ephemeral=True,
            )

    # -- /announce list --

    @announce_group.command(name="list", description="List pending scheduled announcements")
    @require_staff()
    async def announce_list(self, interaction: discord.Interaction):
        rows = await self.bot.db.fetchall(
            "SELECT * FROM announcements WHERE guild_id=? AND status='pending' ORDER BY scheduled_for",
            (interaction.guild_id,),
        )

        if not rows:
            await interaction.response.send_message(
                embed=E.info("No Pending Announcements", "There are no scheduled announcements waiting to be sent."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="📅 Scheduled Announcements", color=0x3498DB)
        for row in rows[:10]:
            ch = interaction.guild.get_channel(row["channel_id"])
            ch_str = ch.mention if ch else f"#{row['channel_id']}"
            embed.add_field(
                name=f"#{row['id']} — {row['title'][:50]}",
                value=f"**When:** `{row['scheduled_for'][:16]} UTC`\n**Channel:** {ch_str}\n**Ping:** {row['ping']}",
                inline=False,
            )
        embed.set_footer(text=f"Cancel with /announce cancel <id>")
        embed.timestamp = datetime.now(timezone.utc)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- /announce history --

    @announce_group.command(name="history", description="View recently sent announcements")
    @require_staff()
    @app_commands.describe(limit="Number of announcements to show (1–20)")
    async def announce_history(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 20] = 10):
        rows = await self.bot.db.fetchall(
            "SELECT * FROM announcements WHERE guild_id=? AND status='sent' ORDER BY created_at DESC LIMIT ?",
            (interaction.guild_id, limit),
        )

        if not rows:
            await interaction.response.send_message(
                embed=E.info("No History", "No announcements have been sent yet."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="📢 Announcement History", color=0x9B59B6)
        for row in rows:
            ch = interaction.guild.get_channel(row["channel_id"])
            ch_str = ch.mention if ch else f"#{row['channel_id']}"
            author = interaction.guild.get_member(row["author_id"])
            author_str = author.display_name if author else str(row["author_id"])
            embed.add_field(
                name=f"#{row['id']} — {row['title'][:50]}",
                value=f"**Sent:** `{row['created_at'][:16]}`\n**By:** {author_str}\n**Channel:** {ch_str}",
                inline=False,
            )
        embed.timestamp = datetime.now(timezone.utc)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- /announce setchannel --

    @announce_group.command(name="setchannel", description="Set the default announcement channel")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="Default channel for announcements")
    async def announce_setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.bot.db.set_config(interaction.guild_id, "announcement_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=E.success("Announcement Channel Set", f"Default announcement channel set to {channel.mention}."),
            ephemeral=True,
        )

    # ── Template sub-group ────────────────────────────────────────────────────

    template_group = app_commands.Group(
        name="template",
        description="Manage announcement templates",
        parent=announce_group,
    )

    @template_group.command(name="save", description="Save the current announcement as a template")
    @require_staff()
    @app_commands.describe(
        name="Template name (used to recall it later)",
        title="Announcement title",
        body="Announcement body",
        footer="Optional footer",
        colour="Colour preset or hex",
    )
    async def template_save(
        self,
        interaction: discord.Interaction,
        name: str,
        title: str,
        body: str,
        footer: str = None,
        colour: str = "blue",
    ):
        name = name.lower().strip()
        colour_int = _resolve_colour(colour)
        data = json.dumps({"title": title, "body": body, "footer": footer, "colour": colour_int})

        await self.bot.db.execute(
            """
            INSERT INTO announcement_templates (guild_id, name, data, author_id)
            VALUES (?,?,?,?)
            ON CONFLICT(guild_id, name) DO UPDATE SET data=excluded.data, author_id=excluded.author_id
            """,
            (interaction.guild_id, name, data, interaction.user.id),
        )
        await interaction.response.send_message(
            embed=E.success("Template Saved", f"Template **`{name}`** saved. Use `/announce template use {name}` to post it."),
            ephemeral=True,
        )

    @template_group.command(name="use", description="Send an announcement using a saved template")
    @require_staff()
    @app_commands.describe(
        name="Template name",
        channel="Channel to post in (overrides default)",
        ping="Who to ping: none / everyone / here / role name",
    )
    async def template_use(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel = None,
        ping: str = "none",
    ):
        row = await self._get_template(interaction.guild_id, name.lower())
        if not row:
            await interaction.response.send_message(
                embed=E.error("Template Not Found", f"No template named `{name}`. Use `/announce template list` to see all."),
                ephemeral=True,
            )
            return

        target = channel or await self._get_default_channel(interaction.guild)
        if not target:
            await interaction.response.send_message(
                embed=E.error("No Channel Set", "Provide a channel or set a default."),
                ephemeral=True,
            )
            return

        data = json.loads(row["data"])
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title=data["title"],
            description=data["body"],
            color=data.get("colour", 0x3498DB),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(
            name=interaction.guild.name,
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None,
        )
        footer_text = data.get("footer") or f"Malta SMP • Announced by {interaction.user.display_name}"
        embed.set_footer(text=footer_text)

        ping_content = await self._build_ping_content(interaction.guild, ping)

        try:
            msg = await target.send(content=ping_content or None, embed=embed)
            if target.type == discord.ChannelType.news:
                try:
                    await msg.publish()
                except Exception:
                    pass
        except discord.Forbidden:
            await interaction.followup.send(
                embed=E.error("Permission Denied", f"Can't send to {target.mention}."),
                ephemeral=True,
            )
            return

        await self._save_announcement(
            guild_id=interaction.guild_id,
            channel_id=target.id,
            author_id=interaction.user.id,
            title=data["title"],
            body=data["body"][:500],
            ping=ping,
        )
        await self._log(interaction.guild, interaction.user, target, data["title"], f"From template '{name}'")
        await interaction.followup.send(
            embed=E.success("Template Sent", f"Announcement posted in {target.mention}."),
            ephemeral=True,
        )

    @template_group.command(name="list", description="List all saved templates")
    @require_staff()
    async def template_list(self, interaction: discord.Interaction):
        rows = await self._get_templates(interaction.guild_id)
        if not rows:
            await interaction.response.send_message(
                embed=E.info("No Templates", "No templates saved yet. Use `/announce template save` to create one."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="📋 Announcement Templates", color=0x9B59B6)
        for row in rows:
            data = json.loads(row["data"])
            embed.add_field(
                name=f"`{row['name']}`",
                value=f"**Title:** {data['title'][:50]}\n**Body preview:** {data['body'][:60]}...",
                inline=False,
            )
        embed.set_footer(text="Use /announce template use <name> to send")
        embed.timestamp = datetime.now(timezone.utc)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @template_group.command(name="delete", description="Delete a saved template")
    @require_staff()
    @app_commands.describe(name="Template name to delete")
    async def template_delete(self, interaction: discord.Interaction, name: str):
        row = await self._get_template(interaction.guild_id, name.lower())
        if not row:
            await interaction.response.send_message(
                embed=E.error("Not Found", f"No template named `{name}`."),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "DELETE FROM announcement_templates WHERE guild_id=? AND name=?",
            (interaction.guild_id, name.lower()),
        )
        await interaction.response.send_message(
            embed=E.success("Template Deleted", f"Template `{name}` has been deleted."),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(AnnouncementsCog(bot))
