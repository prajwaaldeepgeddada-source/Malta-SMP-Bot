"""
cogs/ai_moderation.py
─────────────────────
AI-powered moderation system for Malta SMP.

Uses a hybrid approach:
  1. Fast local heuristics (always run — zero API cost)
  2. AI review via GitHub Models (GPT-4o) for flagged messages (only when suspicious)

This keeps API usage minimal while catching what local rules miss.

Actions available: warn, delete, timeout, escalate to staff.

Commands:
  /automod ai enable
  /automod ai disable
  /automod ai sensitivity  <low|medium|high>
  /automod ai stats
"""

import asyncio
import logging
import re
import time
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands

import utils.embeds as E
from utils.ai_service import moderation_analysis
from utils.permissions import require_staff

log = logging.getLogger("MaltaSMP.AIMod")

# ── Heuristic thresholds (used before the AI) ─────────────────────────────────
CAPS_RATIO_THRESHOLD = 0.75   # fraction of capital letters
MIN_CAPS_LENGTH = 20          # only flag if message is this long
MASS_MENTION_THRESHOLD = 4    # mentions before flagging

# Patterns that are always suspect (even without AI)
LOCAL_THREAT_PATTERNS = [
    r"\bkill\s+your?self\b",
    r"\bkys\b",
    r"\bdox\b",
    r"\bdoxing\b",
    r"\bip\s+grab\b",
    r"\bip\s+logger\b",
]
_LOCAL_COMPILED = [re.compile(p, re.IGNORECASE) for p in LOCAL_THREAT_PATTERNS]

# Sensitivity -> minimum score before AI analysis is called
SENSITIVITY_THRESHOLDS = {
    "low": 3,       # only very suspicious messages
    "medium": 2,    # moderately suspicious
    "high": 1,      # almost everything suspicious gets AI-checked
}


def _local_suspicion_score(message: discord.Message) -> int:
    """
    Cheap heuristic score (0–5) before any API call.
    Higher = more suspicious.
    """
    score = 0
    content = message.content or ""

    # Local threat patterns
    for pat in _LOCAL_COMPILED:
        if pat.search(content):
            score += 3
            break

    # Mass mentions
    total_mentions = len(message.mentions) + len(message.role_mentions)
    if total_mentions >= MASS_MENTION_THRESHOLD:
        score += 2

    # ALL-CAPS messages
    if len(content) >= MIN_CAPS_LENGTH:
        caps = sum(1 for c in content if c.isupper())
        if caps / len(content) >= CAPS_RATIO_THRESHOLD:
            score += 1

    # URLs (can be fine, but worth a point)
    if re.search(r"https?://", content):
        score += 1

    return score


class AIModerationCog(commands.Cog, name="AIModeration"):
    def __init__(self, bot):
        self.bot = bot
        # Per-guild violation counters for auto-escalation
        # guild_id -> user_id -> count
        self._ai_violations: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        # Simple per-user rate limit so we don't AI-check every message from one person
        # guild_id -> user_id -> last_ai_check_timestamp
        self._last_ai_check: dict[int, dict[int, float]] = defaultdict(dict)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _ai_mod_enabled(self, guild_id: int) -> bool:
        val = await self.bot.db.get_config(guild_id, "ai_mod_enabled")
        return val == "true"

    async def _get_sensitivity(self, guild_id: int) -> str:
        val = await self.bot.db.get_config(guild_id, "ai_mod_sensitivity")
        return val if val in ("low", "medium", "high") else "medium"

    def _can_ai_check(self, guild_id: int, user_id: int) -> bool:
        """Prevent checking the same user more than once every 30 seconds."""
        last = self._last_ai_check[guild_id].get(user_id, 0)
        return (time.monotonic() - last) > 30

    def _mark_ai_check(self, guild_id: int, user_id: int):
        self._last_ai_check[guild_id][user_id] = time.monotonic()

    async def _get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        for key in ("ai_mod_log_channel_id", "automod_log_channel_id", "mod_log_channel_id"):
            cid = await self.bot.db.get_config_int(guild.id, key)
            if cid:
                ch = guild.get_channel(cid)
                if ch:
                    return ch
        return None

    async def _log_ai_mod(
        self,
        guild: discord.Guild,
        member: discord.Member,
        action: str,
        category: str,
        reason: str,
        severity: str,
    ):
        ch = await self._get_log_channel(guild)
        if not ch:
            return

        color_map = {"low": 0xF39C12, "medium": 0xE67E22, "high": 0xE74C3C}
        color = color_map.get(severity, 0xE67E22)

        embed = discord.Embed(
            title=f"🤖 AI Moderation — {action.title()}",
            color=color,
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="Category", value=category.title(), inline=True)
        embed.add_field(name="Severity", value=severity.title(), inline=True)
        embed.add_field(name="Action", value=action.title(), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"User ID: {member.id}")
        embed.timestamp = discord.utils.utcnow()

        staff_role_id = await self.bot.db.get_config_int(guild.id, "staff_role_id")
        ping = f"<@&{staff_role_id}> " if staff_role_id and action == "escalate" else ""

        try:
            await ch.send(content=ping or None, embed=embed)
        except Exception:
            pass

        # Also write to DB
        await self.bot.db.log_security(
            guild.id,
            f"ai_mod_{action}",
            member.id,
            f"[{category}|{severity}] {reason}",
        )

    # ── Main listener ─────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        if message.author.guild_permissions.administrator:
            return
        if message.author.guild_permissions.manage_messages:
            return

        guild_id = message.guild.id

        if not await self._ai_mod_enabled(guild_id):
            return

        content = message.content or ""
        if not content:
            return

        # Step 1: fast local heuristics
        score = _local_suspicion_score(message)
        sensitivity = await self._get_sensitivity(guild_id)
        threshold = SENSITIVITY_THRESHOLDS.get(sensitivity, 2)

        # Check for always-block local patterns first
        for pat in _LOCAL_COMPILED:
            if pat.search(content):
                await self._take_action(message, "delete", "threats", "high", "Detected threatening language")
                await self._warn_user(message, "threats", "Threatening language is not allowed.")
                return

        # Step 2: AI check (only if score meets threshold and not rate-limited)
        if score >= threshold and self._can_ai_check(guild_id, message.author.id):
            self._mark_ai_check(guild_id, message.author.id)
            result = await moderation_analysis(content, guild_id=guild_id)

            if result and result.get("flagged"):
                category = result.get("category", "unknown")
                severity = result.get("severity", "medium")
                action = result.get("action", "warn")
                reason = result.get("reason", "AI flagged this message")

                # Escalate repeat offenders
                self._ai_violations[guild_id][message.author.id] += 1
                vcount = self._ai_violations[guild_id][message.author.id]
                if vcount >= 3 and action != "escalate":
                    action = "escalate"
                    reason += f" (repeat offender: {vcount} violations)"

                await self._take_action(message, action, category, severity, reason)

    async def _take_action(
        self,
        message: discord.Message,
        action: str,
        category: str,
        severity: str,
        reason: str,
    ):
        guild = message.guild
        member = message.author

        if action in ("delete", "timeout", "escalate"):
            try:
                await message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        if action == "timeout":
            minutes = {"low": 5, "medium": 15, "high": 60}.get(severity, 15)
            try:
                from datetime import timedelta
                await member.timeout(timedelta(minutes=minutes), reason=f"[AI Mod] {reason}")
            except discord.Forbidden:
                pass

        if action == "warn":
            await self._warn_user(message, category, reason)

        await self._log_ai_mod(guild, member, action, category, reason, severity)
        log.info(
            f"AI Mod action={action} category={category} severity={severity} "
            f"user={member.id} guild={guild.id}"
        )

    async def _warn_user(self, message: discord.Message, category: str, reason: str):
        try:
            await message.channel.send(
                content=message.author.mention,
                embed=E.warning(
                    "⚠️ AutoMod Warning",
                    f"Your message was flagged for **{category}**.\n{reason}",
                ),
                delete_after=15,
            )
        except Exception:
            pass
        await self.bot.db.add_warning(
            message.guild.id,
            message.author.id,
            self.bot.user.id,
            f"[AI Mod] {category}: {reason}",
        )

    # ── /automod ai commands ──────────────────────────────────────────────────

    automod_group = app_commands.Group(
        name="automodai",
        description="Configure AI-powered moderation",
    )

    @automod_group.command(name="enable", description="Enable AI moderation")
    @app_commands.checks.has_permissions(administrator=True)
    async def ai_mod_enable(self, interaction: discord.Interaction):
        await self.bot.db.set_config(interaction.guild_id, "ai_mod_enabled", "true")
        await interaction.response.send_message(
            embed=E.success("AI Moderation Enabled", "AI-powered moderation is now active."),
            ephemeral=True,
        )

    @automod_group.command(name="disable", description="Disable AI moderation")
    @app_commands.checks.has_permissions(administrator=True)
    async def ai_mod_disable(self, interaction: discord.Interaction):
        await self.bot.db.set_config(interaction.guild_id, "ai_mod_enabled", "false")
        await interaction.response.send_message(
            embed=E.warning("AI Moderation Disabled", "AI moderation has been turned off."),
            ephemeral=True,
        )

    @automod_group.command(name="sensitivity", description="Set AI moderation sensitivity")
    @app_commands.describe(level="Sensitivity level: low, medium, or high")
    @app_commands.choices(level=[
        app_commands.Choice(name="Low (fewer false positives)", value="low"),
        app_commands.Choice(name="Medium (balanced, recommended)", value="medium"),
        app_commands.Choice(name="High (catches more, may have false positives)", value="high"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def ai_mod_sensitivity(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        await self.bot.db.set_config(interaction.guild_id, "ai_mod_sensitivity", level.value)
        await interaction.response.send_message(
            embed=E.success("Sensitivity Set", f"AI moderation sensitivity set to **{level.name}**."),
            ephemeral=True,
        )

    @automod_group.command(name="setlogchannel", description="Set channel for AI moderation logs")
    @app_commands.describe(channel="Log channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def ai_mod_setlog(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.bot.db.set_config(interaction.guild_id, "ai_mod_log_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=E.success("Log Channel Set", f"AI moderation logs will go to {channel.mention}."),
            ephemeral=True,
        )

    @automod_group.command(name="stats", description="View AI moderation statistics")
    @require_staff()
    async def ai_mod_stats(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        enabled = await self._ai_mod_enabled(guild_id)
        sensitivity = await self._get_sensitivity(guild_id)
        violations = sum(self._ai_violations[guild_id].values())
        top_offenders = sorted(
            self._ai_violations[guild_id].items(), key=lambda x: x[1], reverse=True
        )[:5]

        embed = discord.Embed(title="🤖 AI Moderation Stats", color=0xE67E22)
        embed.add_field(name="Status", value="🟢 Enabled" if enabled else "🔴 Disabled", inline=True)
        embed.add_field(name="Sensitivity", value=sensitivity.title(), inline=True)
        embed.add_field(name="Session Violations", value=str(violations), inline=True)

        if top_offenders:
            top_str = "\n".join(f"<@{uid}>: {count}" for uid, count in top_offenders)
            embed.add_field(name="Top Offenders (Session)", value=top_str, inline=False)

        embed.timestamp = discord.utils.utcnow()
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(AIModerationCog(bot))
