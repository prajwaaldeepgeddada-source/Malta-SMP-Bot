import discord
from discord.ext import commands
from discord import app_commands
import logging
import re
import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta

import utils.embeds as E
from utils.permissions import is_staff

log = logging.getLogger("MaltaSMP.AutoMod")

# ── Patterns ──────────────────────────────────────────────────────────────────

DISCORD_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord\.com/invite|discordapp\.com/invite)/[\w-]+",
    re.IGNORECASE,
)

SUSPICIOUS_DOMAINS = [
    "free-nitro", "discord-gift", "steamgift", "nitrogift", "discordnitro",
    "steamcommunity.ru", "steampowered.ru", "discordapp.co", "discord.gift",
    "discord-promo", "freegift", "giftcard", "bit.ly", "tinyurl",
]

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)

# ── Message tracking ──────────────────────────────────────────────────────────

class UserMessageTracker:
    """Tracks recent messages per user for spam detection."""
    def __init__(self):
        self.messages: deque = deque()
        self.last_content: str = ""
        self.repeat_count: int = 0

    def add(self, content: str, now: float):
        self.messages.append(now)
        # Remove older than 10 seconds
        while self.messages and self.messages[0] < now - 10:
            self.messages.popleft()
        if content == self.last_content:
            self.repeat_count += 1
        else:
            self.repeat_count = 1
            self.last_content = content

    def message_count_in(self, seconds: float, now: float) -> int:
        return sum(1 for t in self.messages if t >= now - seconds)


class AutoMod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> user_id -> tracker
        self._trackers: dict[int, dict[int, UserMessageTracker]] = defaultdict(lambda: defaultdict(UserMessageTracker))
        # guild_id -> user_id -> mention count
        self._mention_counts: dict[int, dict[int, deque]] = defaultdict(lambda: defaultdict(deque))

    # ── Default thresholds ────────────────────────────────────────────────────

    async def _get_threshold(self, guild_id: int, key: str, default: int) -> int:
        val = await self.bot.db.get_config(guild_id, key)
        return int(val) if val else default

    async def _get_whitelist(self, guild_id: int) -> list[str]:
        val = await self.bot.db.get_config(guild_id, "link_whitelist")
        if val:
            return [d.strip() for d in val.split(",")]
        return []

    async def _is_exempt(self, member: discord.Member) -> bool:
        return await is_staff(self.bot, member) or member.guild_permissions.manage_messages

    # ── Log automod action ────────────────────────────────────────────────────

    async def _log_automod(self, guild: discord.Guild, action: str, member: discord.Member, reason: str):
        channel_id = await self.bot.db.get_config_int(guild.id, "automod_log_channel_id")
        if not channel_id:
            channel_id = await self.bot.db.get_config_int(guild.id, "mod_log_channel_id")
        if channel_id:
            ch = guild.get_channel(channel_id)
            if ch:
                embed = E.warning(
                    f"🤖 AutoMod — {action}",
                    f"**Member:** {member.mention} (`{member.id}`)\n**Reason:** {reason}",
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                try:
                    await ch.send(embed=embed)
                except Exception:
                    pass
        await self.bot.db.log_security(guild.id, f"automod_{action.lower()}", member.id, reason)

    # ── Actions ───────────────────────────────────────────────────────────────

    async def _warn_member(self, message: discord.Message, reason: str):
        try:
            warn_embed = E.warning("AutoMod Warning", reason)
            sent = await message.channel.send(content=message.author.mention, embed=warn_embed, delete_after=10)
        except Exception:
            pass
        await self.bot.db.add_warning(message.guild.id, message.author.id, self.bot.user.id, f"[AutoMod] {reason}")
        await self._log_automod(message.guild, "Warn", message.author, reason)

    async def _timeout_member(self, message: discord.Message, reason: str, minutes: int = 5):
        try:
            await message.author.timeout(timedelta(minutes=minutes), reason=f"[AutoMod] {reason}")
        except discord.Forbidden:
            pass
        await self._log_automod(message.guild, f"Timeout ({minutes}m)", message.author, reason)

    # ── Main listener ─────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return

        # Check if automod is enabled
        enabled = await self.bot.db.get_config(message.guild.id, "automod_enabled")
        if enabled == "false":
            return

        if await self._is_exempt(message.author):
            return

        # Run checks in order
        await self._check_spam(message)
        await self._check_mentions(message)
        await self._check_emoji_spam(message)
        await self._check_links(message)

    async def _check_spam(self, message: discord.Message):
        guild_id = message.guild.id
        user_id = message.author.id
        tracker = self._trackers[guild_id][user_id]
        now = message.created_at.timestamp()
        tracker.add(message.content, now)

        # Fast message spam
        msg_threshold = await self._get_threshold(guild_id, "spam_msg_threshold", 7)
        msg_window = await self._get_threshold(guild_id, "spam_msg_window", 5)
        count = tracker.message_count_in(msg_window, now)

        if count >= msg_threshold:
            try:
                await message.channel.purge(limit=min(count, 20), check=lambda m: m.author.id == user_id)
            except Exception:
                pass
            violations = await self.bot.db.increment_violation(guild_id, user_id, "spam")
            if violations >= 3:
                await self._timeout_member(message, "Repeated spam violations", 10)
            else:
                await self._warn_member(message, f"Spam detected — sending too many messages too fast.")
            return

        # Repeated message spam
        repeat_threshold = await self._get_threshold(guild_id, "spam_repeat_threshold", 4)
        if tracker.repeat_count >= repeat_threshold:
            try:
                await message.delete()
            except Exception:
                pass
            await self._warn_member(message, "Please don't repeat the same message.")
            tracker.repeat_count = 0

    async def _check_mentions(self, message: discord.Message):
        if not message.mentions and not message.role_mentions:
            return

        guild_id = message.guild.id
        user_id = message.author.id
        mention_threshold = await self._get_threshold(guild_id, "mention_threshold", 5)
        total_mentions = len(message.mentions) + len(message.role_mentions)

        if total_mentions >= mention_threshold:
            try:
                await message.delete()
            except Exception:
                pass
            violations = await self.bot.db.increment_violation(guild_id, user_id, "mentions")
            if violations >= 2:
                await self._timeout_member(message, "Mass mention spam", 15)
            else:
                await self._warn_member(message, f"Mass mentioning is not allowed ({total_mentions} mentions).")

    async def _check_emoji_spam(self, message: discord.Message):
        if not message.content:
            return

        guild_id = message.guild.id
        emoji_threshold = await self._get_threshold(guild_id, "emoji_threshold", 10)

        # Count standard and custom emojis
        standard_emoji_count = sum(1 for c in message.content if ord(c) > 0x1F300)
        custom_emoji_count = len(re.findall(r"<a?:\w+:\d+>", message.content))
        total = standard_emoji_count + custom_emoji_count

        if total >= emoji_threshold:
            try:
                await message.delete()
            except Exception:
                pass
            await self._warn_member(message, f"Too many emojis ({total}). Please keep it under {emoji_threshold}.")

    async def _check_links(self, message: discord.Message):
        if not message.content:
            return

        guild_id = message.guild.id
        content_lower = message.content.lower()

        # Check Discord invites
        invite_block = await self.bot.db.get_config(guild_id, "block_invites")
        if invite_block != "false":
            whitelist = await self._get_whitelist(guild_id)
            if DISCORD_INVITE_RE.search(content_lower):
                # Check whitelist — allowed invite codes / server IDs
                # Simple whitelist: if any whitelist term appears in the link, allow
                matches = DISCORD_INVITE_RE.findall(content_lower)
                blocked = False
                for match in matches:
                    if not any(w in match for w in whitelist):
                        blocked = True
                        break
                if blocked:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    await self._warn_member(message, "Discord invite links are not allowed here.")
                    return

        # Check suspicious/scam links
        urls = URL_RE.findall(message.content)
        for url in urls:
            url_lower = url.lower()
            if any(domain in url_lower for domain in SUSPICIOUS_DOMAINS):
                try:
                    await message.delete()
                except Exception:
                    pass
                await self._timeout_member(message, "Suspicious/scam link detected", 60)
                await self._log_automod(message.guild, "Scam Link Blocked", message.author, url[:200])
                return

    # ── Config commands ───────────────────────────────────────────────────────

    @app_commands.command(name="automod", description="Enable or disable AutoMod")
    @app_commands.describe(enabled="Enable or disable AutoMod")
    @app_commands.checks.has_permissions(administrator=True)
    async def automod_toggle(self, interaction: discord.Interaction, enabled: bool):
        await self.bot.db.set_config(interaction.guild_id, "automod_enabled", str(enabled).lower())
        state = "enabled" if enabled else "disabled"
        await interaction.response.send_message(embed=E.success(f"AutoMod {state.title()}", f"AutoMod has been {state}."), ephemeral=True)

    @app_commands.command(name="setlinkwhitelist", description="Set whitelisted domains (comma-separated)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(domains="Comma-separated domains to whitelist")
    async def set_whitelist(self, interaction: discord.Interaction, domains: str):
        await self.bot.db.set_config(interaction.guild_id, "link_whitelist", domains)
        await interaction.response.send_message(embed=E.success("Whitelist Set", f"Whitelisted: `{domains}`"), ephemeral=True)

    @app_commands.command(name="setspamthreshold", description="Set spam detection threshold")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(messages="Messages per window before action", window="Time window in seconds")
    async def set_spam_threshold(self, interaction: discord.Interaction, messages: int, window: int = 5):
        await self.bot.db.set_config(interaction.guild_id, "spam_msg_threshold", str(messages))
        await self.bot.db.set_config(interaction.guild_id, "spam_msg_window", str(window))
        await interaction.response.send_message(
            embed=E.success("Threshold Set", f"Spam threshold: {messages} messages in {window} seconds."),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(AutoMod(bot))
