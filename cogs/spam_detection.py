"""
cogs/spam_detection.py
──────────────────────
Hybrid AI + local spam detection for Malta SMP.

Fast local detection handles:
  - Repeated messages (copypasta)
  - Character spam (aaaaaaa, !!!!!!)
  - Emoji spam
  - Flooding (too many messages)
  - Advertisement spam patterns

AI review is triggered for edge cases: messages that score above a
threshold but aren't caught by simple rules.

Actions: delete, warn, timeout.
Thresholds are all configurable per guild via DB config.
"""

import logging
import re
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import utils.embeds as E
from utils.ai_service import moderation_analysis
from utils.permissions import require_staff

log = logging.getLogger("MaltaSMP.SpamDetect")

# ── Regex patterns ────────────────────────────────────────────────────────────
EMOJI_RE = re.compile(r"(<a?:\w+:\d+>|[\U00010000-\U0010ffff])", re.UNICODE)
CHAR_REPEAT_RE = re.compile(r"(.)\1{9,}")          # same char 10+ times in a row
AD_PATTERNS = [
    re.compile(r"\b(discord\.gg|discord\.com/invite)/[\w-]+\b", re.I),
    re.compile(r"\b(join\s+my|come\s+to\s+my)\s+server\b", re.I),
    re.compile(r"\bfree\s+(nitro|robux|vbucks)\b", re.I),
    re.compile(r"\bsubscribe\s+to\s+my\b", re.I),
]

# ── Default thresholds (overridden by guild config) ───────────────────────────
DEFAULTS = {
    "flood_msg_count": 7,     # messages in window before action
    "flood_window_sec": 5,    # window in seconds
    "repeat_threshold": 3,    # same message repeated N times
    "emoji_threshold": 12,    # emojis per message
    "char_spam_threshold": 1, # how many char-repeat blocks trigger action
    "spam_timeout_min": 5,    # timeout duration in minutes on repeated violations
}


class UserTracker:
    """Per-user message tracking for spam analysis."""
    __slots__ = ("timestamps", "last_content", "repeat_count")

    def __init__(self):
        self.timestamps: deque = deque(maxlen=30)
        self.last_content: str = ""
        self.repeat_count: int = 0

    def record(self, content: str) -> None:
        now = time.monotonic()
        self.timestamps.append(now)
        if content.strip() == self.last_content.strip() and content.strip():
            self.repeat_count += 1
        else:
            self.repeat_count = 1
            self.last_content = content.strip()

    def count_in_window(self, seconds: float) -> int:
        now = time.monotonic()
        return sum(1 for t in self.timestamps if now - t <= seconds)


class SpamDetectionCog(commands.Cog, name="SpamDetection"):
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> user_id -> UserTracker
        self._trackers: dict[int, dict[int, UserTracker]] = defaultdict(
            lambda: defaultdict(UserTracker)
        )
        # guild_id -> user_id -> violation count (session)
        self._violations: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    # ── Config helpers ────────────────────────────────────────────────────────

    async def _cfg(self, guild_id: int, key: str) -> int:
        val = await self.bot.db.get_config(guild_id, f"spam_{key}")
        return int(val) if val else DEFAULTS[key]

    async def _enabled(self, guild_id: int) -> bool:
        val = await self.bot.db.get_config(guild_id, "spam_detection_enabled")
        return val != "false"

    async def _is_exempt(self, member: discord.Member) -> bool:
        return (
            member.guild_permissions.administrator
            or member.guild_permissions.manage_messages
        )

    # ── Actions ───────────────────────────────────────────────────────────────

    async def _delete(self, message: discord.Message):
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

    async def _warn(self, message: discord.Message, reason: str):
        await self.bot.db.add_warning(
            message.guild.id, message.author.id, self.bot.user.id, f"[Spam] {reason}"
        )
        try:
            await message.channel.send(
                content=message.author.mention,
                embed=E.warning("⚠️ Spam Warning", reason),
                delete_after=10,
            )
        except Exception:
            pass
        await self._log(message.guild, message.author, "Warn", reason)

    async def _timeout(self, message: discord.Message, reason: str):
        minutes = await self._cfg(message.guild.id, "spam_timeout_min")
        try:
            await message.author.timeout(timedelta(minutes=minutes), reason=f"[Spam] {reason}")
        except discord.Forbidden:
            pass
        await self._log(message.guild, message.author, f"Timeout ({minutes}m)", reason)

    async def _log(self, guild: discord.Guild, member: discord.Member, action: str, reason: str):
        for key in ("spam_log_channel_id", "automod_log_channel_id", "mod_log_channel_id"):
            cid = await self.bot.db.get_config_int(guild.id, key)
            if cid:
                ch = guild.get_channel(cid)
                if ch:
                    embed = E.warning(
                        f"🚫 Spam Detected — {action}",
                        f"**Member:** {member.mention} (`{member.id}`)\n**Reason:** {reason}",
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    try:
                        await ch.send(embed=embed)
                    except Exception:
                        pass
                    break
        await self.bot.db.log_security(guild.id, f"spam_{action.lower()}", member.id, reason)

    # ── Main listener ─────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        if await self._is_exempt(message.author):
            return

        guild_id = message.guild.id
        if not await self._enabled(guild_id):
            return

        content = message.content or ""
        tracker = self._trackers[guild_id][message.author.id]
        tracker.record(content)

        # --- Flood check ---
        flood_count = await self._cfg(guild_id, "flood_msg_count")
        flood_window = await self._cfg(guild_id, "flood_window_sec")
        recent = tracker.count_in_window(flood_window)
        if recent >= flood_count:
            await self._delete(message)
            await self._escalate(message, f"Flooding: {recent} messages in {flood_window}s")
            return

        # --- Repeated message ---
        repeat_threshold = await self._cfg(guild_id, "repeat_threshold")
        if tracker.repeat_count >= repeat_threshold:
            await self._delete(message)
            await self._escalate(message, f"Repeated message (×{tracker.repeat_count})")
            tracker.repeat_count = 0
            return

        # --- Character spam ---
        char_threshold = await self._cfg(guild_id, "char_spam_threshold")
        char_spam_blocks = CHAR_REPEAT_RE.findall(content)
        if len(char_spam_blocks) >= max(1, char_threshold):
            await self._delete(message)
            await self._warn(message, "Character spam detected.")
            return

        # --- Emoji spam ---
        emoji_threshold = await self._cfg(guild_id, "emoji_threshold")
        emoji_count = len(EMOJI_RE.findall(content))
        if emoji_count >= emoji_threshold:
            await self._delete(message)
            await self._warn(message, f"Too many emojis ({emoji_count}/{emoji_threshold}).")
            return

        # --- Advertisement patterns ---
        for pat in AD_PATTERNS:
            if pat.search(content):
                await self._delete(message)
                await self._escalate(message, "Advertisement / invite spam detected")
                return

        # --- AI review for borderline cases ---
        # Only if message has some length and no definitive local flag
        if len(content) > 30 and not message.attachments:
            suspicion = _ai_suspicion_score(content)
            if suspicion >= 2:
                result = await moderation_analysis(content, guild_id=guild_id)
                if result and result.get("flagged") and result.get("category") in (
                    "spam", "advertising", "scam"
                ):
                    await self._delete(message)
                    action = result.get("action", "warn")
                    reason = result.get("reason", "AI: spam/advertising detected")
                    if action == "timeout":
                        await self._timeout(message, reason)
                    else:
                        await self._warn(message, reason)

    async def _escalate(self, message: discord.Message, reason: str):
        """Warn or timeout depending on violation history."""
        user_id = message.author.id
        guild_id = message.guild.id
        self._violations[guild_id][user_id] += 1
        count = self._violations[guild_id][user_id]

        if count >= 3:
            await self._timeout(message, f"Repeated spam violations ({count}x): {reason}")
        else:
            await self._warn(message, reason)

    # ── /spamconfig command group ─────────────────────────────────────────────

    spam_group = app_commands.Group(
        name="spamconfig",
        description="Configure spam detection thresholds",
    )

    @spam_group.command(name="enable", description="Enable spam detection")
    @app_commands.checks.has_permissions(administrator=True)
    async def spam_enable(self, interaction: discord.Interaction):
        await self.bot.db.set_config(interaction.guild_id, "spam_detection_enabled", "true")
        await interaction.response.send_message(
            embed=E.success("Spam Detection Enabled", "Spam detection is now active."),
            ephemeral=True,
        )

    @spam_group.command(name="disable", description="Disable spam detection")
    @app_commands.checks.has_permissions(administrator=True)
    async def spam_disable(self, interaction: discord.Interaction):
        await self.bot.db.set_config(interaction.guild_id, "spam_detection_enabled", "false")
        await interaction.response.send_message(
            embed=E.warning("Spam Detection Disabled", "Spam detection has been turned off."),
            ephemeral=True,
        )

    @spam_group.command(name="thresholds", description="Configure spam thresholds")
    @app_commands.describe(
        flood_messages="Messages in window before action (default: 7)",
        flood_window="Window in seconds (default: 5)",
        repeat_count="Same message repeat count before action (default: 3)",
        emoji_count="Max emojis per message (default: 12)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def spam_thresholds(
        self,
        interaction: discord.Interaction,
        flood_messages: int = None,
        flood_window: int = None,
        repeat_count: int = None,
        emoji_count: int = None,
    ):
        updated = []
        if flood_messages:
            await self.bot.db.set_config(interaction.guild_id, "spam_flood_msg_count", str(flood_messages))
            updated.append(f"Flood messages: {flood_messages}")
        if flood_window:
            await self.bot.db.set_config(interaction.guild_id, "spam_flood_window_sec", str(flood_window))
            updated.append(f"Flood window: {flood_window}s")
        if repeat_count:
            await self.bot.db.set_config(interaction.guild_id, "spam_repeat_threshold", str(repeat_count))
            updated.append(f"Repeat threshold: {repeat_count}")
        if emoji_count:
            await self.bot.db.set_config(interaction.guild_id, "spam_emoji_threshold", str(emoji_count))
            updated.append(f"Emoji threshold: {emoji_count}")

        if not updated:
            await interaction.response.send_message("No changes provided.", ephemeral=True)
            return

        await interaction.response.send_message(
            embed=E.success("Thresholds Updated", "\n".join(updated)),
            ephemeral=True,
        )

    @spam_group.command(name="status", description="View spam detection status")
    @require_staff()
    async def spam_status(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        enabled = await self._enabled(guild_id)
        flood_count = await self._cfg(guild_id, "flood_msg_count")
        flood_window = await self._cfg(guild_id, "flood_window_sec")
        repeat = await self._cfg(guild_id, "repeat_threshold")
        emoji = await self._cfg(guild_id, "emoji_threshold")

        embed = discord.Embed(title="🚫 Spam Detection Config", color=0xE67E22)
        embed.add_field(name="Status", value="🟢 Enabled" if enabled else "🔴 Disabled", inline=False)
        embed.add_field(name="Flood Threshold", value=f"{flood_count} msgs/{flood_window}s", inline=True)
        embed.add_field(name="Repeat Threshold", value=f"×{repeat}", inline=True)
        embed.add_field(name="Emoji Threshold", value=str(emoji), inline=True)
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.send_message(embed=embed, ephemeral=True)


def _ai_suspicion_score(content: str) -> int:
    """Quick pre-filter for borderline messages before sending to AI."""
    score = 0
    lower = content.lower()
    if any(w in lower for w in ["free", "nitro", "giveaway", "click", "win", "subscribe", "join my"]):
        score += 1
    if re.search(r"https?://\S+", content):
        score += 1
    if re.search(r"(.)\1{4,}", content):  # repeated chars
        score += 1
    return score


async def setup(bot):
    await bot.add_cog(SpamDetectionCog(bot))
