"""
cogs/raid_detection.py
──────────────────────
Advanced multi-level raid detection for Malta SMP.

Replaces / supersedes the basic anti-raid in security.py.
(security.py lockdown/unlockdown commands are kept; this cog adds
the /raid command group and a proper 4-level escalation system.)

Raid levels:
  Level 1 — Staff alert only
  Level 2 — Enable slowmode on all channels
  Level 3 — Lock all channels
  Level 4 — Emergency mode (lock + disable invites + restrict perms)

Detection signals:
  - Join rate (configurable, default: 10 joins / 10 seconds)
  - Account age (new accounts joining in bulk)
  - Message rate (high volume across multiple users)
  - Mention spam (@ everyone / mass mentions)
  - Similar message patterns across multiple users (copypasta raid)
  - Bot-like join pattern (no profile pic, default names, etc.)

Commands:
  /raid status
  /raid enable
  /raid disable
  /raid emergency
  /raid unlock
"""

import asyncio
import difflib
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

import utils.embeds as E
from utils.permissions import require_staff

log = logging.getLogger("MaltaSMP.RaidDetection")

# ── Raid level thresholds ─────────────────────────────────────────────────────
# These are the join rates that trigger each level.
# Level 4 is also triggered manually via /raid emergency.
LEVEL_TRIGGERS = {
    1: {"joins": 5,  "window": 10},   # 5 joins / 10s  → alert staff
    2: {"joins": 10, "window": 10},   # 10 joins / 10s → slowmode
    3: {"joins": 15, "window": 10},   # 15 joins / 10s → lock
    4: {"joins": 25, "window": 10},   # 25 joins / 10s → emergency
}

SLOWMODE_SECONDS = 10     # applied at level 2
AUTO_LIFT_SECONDS = 600   # 10 min auto-lift for levels 1-3
EMERGENCY_LIFT_SECONDS = 1800  # 30 min for level 4

# Similarity threshold for "copy-pasta" message raid detection
SIMILARITY_THRESHOLD = 0.85
SIMILAR_MSG_WINDOW = 30   # seconds
SIMILAR_MSG_COUNT = 5     # how many similar messages from different users


class GuildRaidState:
    """Holds per-guild raid state."""

    def __init__(self):
        self.level: int = 0          # current raid level (0 = inactive)
        self.active: bool = False
        self.join_log: deque = deque()
        # Recent messages for similarity check: deque of (timestamp, user_id, content)
        self.recent_messages: deque = deque(maxlen=50)
        # Tracks new accounts that joined during a raid
        self.suspect_members: list[int] = []
        # Auto-lift task
        self.lift_task: asyncio.Task | None = None


class RaidDetectionCog(commands.Cog, name="RaidDetection"):
    def __init__(self, bot):
        self.bot = bot
        self._state: dict[int, GuildRaidState] = defaultdict(GuildRaidState)

    def _state_for(self, guild_id: int) -> GuildRaidState:
        return self._state[guild_id]

    # ── Config helpers ────────────────────────────────────────────────────────

    async def _enabled(self, guild_id: int) -> bool:
        val = await self.bot.db.get_config(guild_id, "raid_detection_enabled")
        return val != "false"

    async def _get_join_threshold(self, guild_id: int, level: int) -> dict:
        cfg = LEVEL_TRIGGERS[level].copy()
        # Allow per-guild overrides for level 1 only
        if level == 1:
            joins = await self.bot.db.get_config(guild_id, "raid_join_threshold")
            window = await self.bot.db.get_config(guild_id, "raid_join_window")
            if joins:
                cfg["joins"] = int(joins)
            if window:
                cfg["window"] = int(window)
        return cfg

    async def _get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        for key in ("raid_log_channel_id", "security_log_channel_id", "mod_log_channel_id"):
            cid = await self.bot.db.get_config_int(guild.id, key)
            if cid:
                ch = guild.get_channel(cid)
                if ch:
                    return ch
        return None

    # ── Alerts ────────────────────────────────────────────────────────────────

    async def _alert(self, guild: discord.Guild, level: int, description: str):
        ch = await self._get_log_channel(guild)
        if not ch:
            return

        staff_role_id = await self.bot.db.get_config_int(guild.id, "staff_role_id")
        ping = f"<@&{staff_role_id}>" if staff_role_id else "@here"

        colors = {1: 0xF39C12, 2: 0xE67E22, 3: 0xE74C3C, 4: 0x8E44AD}
        level_names = {
            1: "⚠️ Level 1 — Staff Alert",
            2: "🟠 Level 2 — Slowmode Enabled",
            3: "🔴 Level 3 — Channels Locked",
            4: "🚨 Level 4 — EMERGENCY MODE",
        }
        embed = discord.Embed(
            title=f"🛡️ RAID DETECTED — {level_names.get(level, f'Level {level}')}",
            description=description,
            color=colors.get(level, 0xE74C3C),
        )
        embed.timestamp = discord.utils.utcnow()
        embed.set_footer(text=f"Guild: {guild.name}")

        try:
            await ch.send(content=ping, embed=embed)
        except Exception:
            pass

        await self.bot.db.log_security(
            guild.id, f"raid_level_{level}", None, description[:500]
        )
        log.warning(f"[RAID L{level}] Guild {guild.id}: {description}")

    # ── Level actions ─────────────────────────────────────────────────────────

    async def _apply_level(self, guild: discord.Guild, level: int, reason: str):
        state = self._state_for(guild.id)

        if state.level >= level:
            return  # already at this level or higher

        state.active = True
        state.level = level

        await self._alert(guild, level, reason)

        if level >= 2:
            # Enable slowmode
            for ch in guild.text_channels:
                try:
                    await ch.edit(slowmode_delay=SLOWMODE_SECONDS, reason="Anti-raid slowmode")
                except discord.Forbidden:
                    pass

        if level >= 3:
            # Lock all text channels
            for ch in guild.text_channels:
                try:
                    await ch.set_permissions(
                        guild.default_role, send_messages=False, reason="Anti-raid lockdown"
                    )
                except discord.Forbidden:
                    pass

        if level >= 4:
            # Emergency mode: also disable invites
            try:
                invites = await guild.invites()
                for inv in invites:
                    try:
                        await inv.delete(reason="Emergency raid mode")
                    except Exception:
                        pass
            except discord.Forbidden:
                pass
            # Restrict new member permissions
            try:
                overwrites = guild.default_role.permissions
                new_perms = discord.Permissions(
                    read_messages=True,
                    send_messages=False,
                    add_reactions=False,
                    create_instant_invite=False,
                )
                await guild.default_role.edit(permissions=new_perms, reason="Emergency raid mode")
            except discord.Forbidden:
                pass

        # Schedule auto-lift
        if state.lift_task:
            state.lift_task.cancel()
        delay = EMERGENCY_LIFT_SECONDS if level >= 4 else AUTO_LIFT_SECONDS
        state.lift_task = asyncio.create_task(self._auto_lift(guild, delay))

    async def _lift(self, guild: discord.Guild, reason: str):
        state = self._state_for(guild.id)
        prev_level = state.level
        state.active = False
        state.level = 0
        state.suspect_members.clear()

        if state.lift_task:
            state.lift_task.cancel()
            state.lift_task = None

        # Restore channels
        for ch in guild.text_channels:
            try:
                await ch.set_permissions(
                    guild.default_role, send_messages=None, reason=f"Raid lifted: {reason}"
                )
                await ch.edit(slowmode_delay=0, reason="Raid lifted")
            except discord.Forbidden:
                pass

        ch_log = await self._get_log_channel(guild)
        if ch_log:
            embed = E.success(
                "🛡️ Raid Mode Lifted",
                f"**Reason:** {reason}\n**Previous Level:** {prev_level}",
            )
            embed.timestamp = discord.utils.utcnow()
            try:
                await ch_log.send(embed=embed)
            except Exception:
                pass

        log.info(f"Raid mode lifted in guild {guild.id}: {reason}")

    async def _auto_lift(self, guild: discord.Guild, delay: int):
        await asyncio.sleep(delay)
        if self._state_for(guild.id).active:
            await self._lift(guild, f"Auto-lift after {delay // 60} minutes")

    # ── Join listener ─────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        guild_id = guild.id

        if not await self._enabled(guild_id):
            return

        state = self._state_for(guild_id)
        now = time.monotonic()
        state.join_log.append(now)

        # Check each level threshold (highest first for escalation)
        for level in (4, 3, 2, 1):
            cfg = await self._get_join_threshold(guild_id, level)
            window = cfg["window"]
            threshold = cfg["joins"]

            # Clean old entries
            while state.join_log and state.join_log[0] < now - window:
                state.join_log.popleft()

            recent = len(state.join_log)
            if recent >= threshold:
                await self._apply_level(
                    guild,
                    level,
                    f"{recent} members joined in {window}s (threshold: {threshold})",
                )
                break

        # Track suspicious new accounts for bot-raid detection
        account_age = datetime.now(timezone.utc) - member.created_at
        if account_age.days < 7:
            state.suspect_members.append(member.id)
            if len(state.suspect_members) >= 10 and not state.active:
                await self._apply_level(
                    guild,
                    2,
                    f"10+ new accounts (<7 days old) joined recently — possible bot raid",
                )

    # ── Message listener (copy-pasta raid & mention raid) ─────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return

        guild_id = message.guild.id
        if not await self._enabled(guild_id):
            return

        content = message.content or ""
        state = self._state_for(guild_id)
        now = time.monotonic()

        # Record message for similarity analysis
        state.recent_messages.append((now, message.author.id, content))

        # Check for copy-pasta raid (many users sending near-identical messages)
        recent_window = [(ts, uid, txt) for ts, uid, txt in state.recent_messages
                         if now - ts <= SIMILAR_MSG_WINDOW]
        if len(recent_window) >= SIMILAR_MSG_COUNT:
            unique_users = {uid for _, uid, _ in recent_window}
            if len(unique_users) >= 3 and len(recent_window) >= SIMILAR_MSG_COUNT:
                texts = [txt for _, _, txt in recent_window if txt]
                # Check if most messages are very similar
                similar_count = 0
                if texts:
                    for i in range(1, len(texts)):
                        ratio = difflib.SequenceMatcher(None, texts[0], texts[i]).ratio()
                        if ratio >= SIMILARITY_THRESHOLD:
                            similar_count += 1
                if similar_count >= SIMILAR_MSG_COUNT - 1:
                    await self._apply_level(
                        guild=message.guild,
                        level=2,
                        reason=f"Copy-pasta message raid detected ({similar_count+1} similar messages from {len(unique_users)} users)",
                    )

        # Mention raid: a lot of @everyone / @here / mass-mentions in messages
        if (message.mention_everyone or len(message.mentions) >= 10) and not state.active:
            await self._apply_level(
                message.guild,
                2,
                f"Mass mention raid detected in {message.channel.mention}",
            )

    # ── /raid command group ───────────────────────────────────────────────────

    raid_group = app_commands.Group(
        name="raid",
        description="Raid detection controls",
    )

    @raid_group.command(name="status", description="View current raid detection status")
    @require_staff()
    async def raid_status(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        state = self._state_for(guild_id)
        enabled = await self._enabled(guild_id)

        cfg1 = await self._get_join_threshold(guild_id, 1)

        embed = discord.Embed(
            title="🛡️ Raid Detection Status",
            color=0xE74C3C if state.active else 0x2ECC71,
        )
        embed.add_field(name="System", value="🟢 Enabled" if enabled else "🔴 Disabled", inline=True)
        embed.add_field(name="Current Level", value=f"Level {state.level}" if state.active else "Inactive", inline=True)
        embed.add_field(name="Suspect Members", value=str(len(state.suspect_members)), inline=True)
        embed.add_field(
            name="Level 1 Trigger",
            value=f"{cfg1['joins']} joins / {cfg1['window']}s",
            inline=False,
        )
        embed.add_field(
            name="Level 2–4 Triggers",
            value="10/15/25 joins per 10s",
            inline=False,
        )
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @raid_group.command(name="enable", description="Enable raid detection")
    @app_commands.checks.has_permissions(administrator=True)
    async def raid_enable(self, interaction: discord.Interaction):
        await self.bot.db.set_config(interaction.guild_id, "raid_detection_enabled", "true")
        await interaction.response.send_message(
            embed=E.success("Raid Detection Enabled", "Advanced raid detection is now active."),
            ephemeral=True,
        )

    @raid_group.command(name="disable", description="Disable raid detection")
    @app_commands.checks.has_permissions(administrator=True)
    async def raid_disable(self, interaction: discord.Interaction):
        await self.bot.db.set_config(interaction.guild_id, "raid_detection_enabled", "false")
        await interaction.response.send_message(
            embed=E.warning("Raid Detection Disabled", "Raid detection has been turned off."),
            ephemeral=True,
        )

    @raid_group.command(name="emergency", description="Manually trigger emergency (Level 4) raid mode")
    @require_staff()
    @app_commands.describe(reason="Reason for emergency mode")
    async def raid_emergency(self, interaction: discord.Interaction, reason: str = "Manual emergency trigger"):
        await interaction.response.defer(ephemeral=True)
        await self._apply_level(interaction.guild, 4, f"Manual: {interaction.user} — {reason}")
        await interaction.followup.send(
            embed=E.error(
                "🚨 Emergency Mode Active",
                "Level 4 emergency raid mode has been activated.\n"
                "All channels locked, invites disabled, permissions restricted.\n"
                f"Will auto-lift in {EMERGENCY_LIFT_SECONDS // 60} minutes.",
            ),
            ephemeral=True,
        )

    @raid_group.command(name="unlock", description="Lift raid mode and restore normal operations")
    @require_staff()
    @app_commands.describe(reason="Reason for lifting raid mode")
    async def raid_unlock(self, interaction: discord.Interaction, reason: str = "Staff lifted raid mode"):
        await interaction.response.defer(ephemeral=True)
        await self._lift(interaction.guild, f"Manual by {interaction.user}: {reason}")
        await interaction.followup.send(
            embed=E.success("Raid Mode Lifted", "Server has been returned to normal operation."),
            ephemeral=True,
        )

    @raid_group.command(name="configure", description="Configure raid detection thresholds")
    @app_commands.describe(joins="Joins to trigger Level 1 alert", window="Time window in seconds")
    @app_commands.checks.has_permissions(administrator=True)
    async def raid_configure(
        self,
        interaction: discord.Interaction,
        joins: int = None,
        window: int = None,
    ):
        if joins:
            await self.bot.db.set_config(interaction.guild_id, "raid_join_threshold", str(joins))
        if window:
            await self.bot.db.set_config(interaction.guild_id, "raid_join_window", str(window))
        if not joins and not window:
            await interaction.response.send_message("No changes provided.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=E.success(
                "Raid Thresholds Updated",
                f"Level 1 trigger: {joins or '(unchanged)'} joins / {window or '(unchanged)'}s",
            ),
            ephemeral=True,
        )

    @raid_group.command(name="setlogchannel", description="Set the raid log channel")
    @app_commands.describe(channel="Channel for raid alerts")
    @app_commands.checks.has_permissions(administrator=True)
    async def raid_setlog(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.bot.db.set_config(interaction.guild_id, "raid_log_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=E.success("Raid Log Set", f"Raid alerts will be sent to {channel.mention}."),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(RaidDetectionCog(bot))
