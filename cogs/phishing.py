"""
cogs/phishing.py
────────────────
AI-assisted phishing & scam detection for Malta SMP.

Local detection (zero API cost):
  - Known scam domain patterns
  - Discord Nitro scam patterns
  - Token-grabber URL patterns
  - URL shortener abuse

AI analysis for unknown/suspicious URLs.

Commands:
  /security scan   <message>   — manually scan a message
  /security whitelist  <domain> — add a domain to the whitelist
  /security blacklist  <domain> — add a domain to the blacklist
"""

import logging
import re
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands

import utils.embeds as E
from utils.ai_service import phishing_analysis
from utils.permissions import require_staff

log = logging.getLogger("MaltaSMP.Phishing")

# ── Known-bad domain patterns (regex fragments) ───────────────────────────────
SCAM_DOMAIN_PATTERNS = [
    r"free.?nitro",
    r"discord.?gift(?!\.com)",
    r"discord.?promo",
    r"discordnitro",
    r"nitro.?gift",
    r"steam.?community\.ru",
    r"steampowered\.ru",
    r"discordapp\.co(?!m)",
    r"giveaway.?discord",
    r"discord.?hack",
    r"csgo.?skins?.?free",
    r"free.?robux",
    r"roblox.?hack",
    r"minecraft.?hack",
    r"mc.?crack",
    r"ip.?grab(?:ber)?",
    r"token.?grab(?:ber)?",
    r"grabify",
    r"iplogger",
    r"blasze\.tk",
    r"ps3cfw\.com",
    r"2no\.co",
]
_SCAM_RE = [re.compile(p, re.I) for p in SCAM_DOMAIN_PATTERNS]

# URL shorteners often abused in scams
URL_SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly",
    "is.gd", "buff.ly", "adf.ly", "linktr.ee",
}

# Regex to extract all URLs from text
URL_RE = re.compile(r"https?://[^\s<>\"]+", re.I)


def _extract_urls(text: str) -> list[str]:
    return URL_RE.findall(text)


def _get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return url.lower()


def _local_is_malicious(url: str) -> bool:
    domain = _get_domain(url)
    for pat in _SCAM_RE:
        if pat.search(domain) or pat.search(url):
            return True
    return False


def _is_shortener(url: str) -> bool:
    return _get_domain(url) in URL_SHORTENERS


class PhishingCog(commands.Cog, name="Phishing"):
    def __init__(self, bot):
        self.bot = bot

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_whitelist(self, guild_id: int) -> set[str]:
        val = await self.bot.db.get_config(guild_id, "phishing_whitelist")
        if val:
            return {d.strip().lower() for d in val.split(",")}
        return set()

    async def _get_blacklist(self, guild_id: int) -> set[str]:
        val = await self.bot.db.get_config(guild_id, "phishing_blacklist")
        if val:
            return {d.strip().lower() for d in val.split(",")}
        return set()

    async def _phishing_enabled(self, guild_id: int) -> bool:
        val = await self.bot.db.get_config(guild_id, "phishing_enabled")
        return val != "false"  # default on

    async def _get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        for key in ("security_log_channel_id", "mod_log_channel_id"):
            cid = await self.bot.db.get_config_int(guild.id, key)
            if cid:
                ch = guild.get_channel(cid)
                if ch:
                    return ch
        return None

    async def _handle_malicious(
        self,
        message: discord.Message,
        url: str,
        reason: str,
        source: str = "local",
    ):
        """Delete the message, log the incident, notify moderators."""
        guild = message.guild
        member = message.author

        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

        log.warning(
            f"Phishing [{source}] in guild {guild.id} by {member.id}: {url[:100]} — {reason}"
        )
        await self.bot.db.log_security(guild.id, "phishing_detected", member.id, f"{url[:200]} — {reason}")

        ch = await self._get_log_channel(guild)
        if ch:
            staff_role_id = await self.bot.db.get_config_int(guild.id, "staff_role_id")
            ping = f"<@&{staff_role_id}>" if staff_role_id else ""

            embed = discord.Embed(
                title="🚨 Phishing / Scam Detected",
                color=0xC0392B,
            )
            embed.set_author(name=str(member), icon_url=member.display_avatar.url)
            embed.add_field(name="Detection Source", value=source.title(), inline=True)
            embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="Channel", value=message.channel.mention, inline=True)
            embed.add_field(name="URL", value=f"`{url[:300]}`", inline=False)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.timestamp = discord.utils.utcnow()
            embed.set_footer(text="Message has been deleted")

            try:
                await ch.send(content=ping or None, embed=embed)
            except Exception:
                pass

        # Warn the user
        try:
            await message.channel.send(
                content=member.mention,
                embed=E.error(
                    "⚠️ Malicious Link Removed",
                    "Your message contained a link that has been flagged as a scam or phishing attempt. "
                    "If you believe this is a mistake, please contact a moderator.",
                ),
                delete_after=15,
            )
        except Exception:
            pass

    # ── Main listener ─────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        if message.author.guild_permissions.administrator:
            return

        guild_id = message.guild.id
        if not await self._phishing_enabled(guild_id):
            return

        content = message.content or ""
        urls = _extract_urls(content)
        if not urls:
            return

        whitelist = await self._get_whitelist(guild_id)
        blacklist = await self._get_blacklist(guild_id)

        for url in urls:
            domain = _get_domain(url)

            # Whitelist check
            if domain in whitelist or any(domain.endswith(w) for w in whitelist):
                continue

            # Blacklist check
            if domain in blacklist or any(domain.endswith(b) for b in blacklist):
                await self._handle_malicious(message, url, "Domain is blacklisted", "blacklist")
                return

            # Local pattern check
            if _local_is_malicious(url):
                await self._handle_malicious(message, url, "Known scam/phishing domain pattern", "local")
                return

            # URL shortener — send to AI
            if _is_shortener(url):
                result = await phishing_analysis(content, [url], guild_id=guild_id)
                if result and result.get("malicious"):
                    reason = result.get("reason", "AI: malicious short URL")
                    await self._handle_malicious(message, url, reason, "ai")
                    return

        # If multiple URLs and none caught locally, do a bulk AI check for suspicious combos
        if len(urls) >= 2:
            result = await phishing_analysis(content, urls, guild_id=guild_id)
            if result and result.get("malicious") and result.get("confidence") in ("medium", "high"):
                reason = result.get("reason", "AI: suspicious links")
                await self._handle_malicious(message, urls[0], reason, "ai")

    # ── /security commands ────────────────────────────────────────────────────

    security_group = app_commands.Group(
        name="security",
        description="Phishing and security tools",
    )

    @security_group.command(name="scan", description="Manually scan a message for phishing")
    @app_commands.describe(text="Message text to scan")
    @require_staff()
    async def security_scan(self, interaction: discord.Interaction, text: str):
        await interaction.response.defer(ephemeral=True)

        urls = _extract_urls(text)
        if not urls:
            await interaction.followup.send(
                embed=E.info("No URLs Found", "The message contains no URLs to scan."),
                ephemeral=True,
            )
            return

        local_flags = [url for url in urls if _local_is_malicious(url)]
        ai_result = await phishing_analysis(text, urls, guild_id=interaction.guild_id)

        embed = discord.Embed(title="🔍 Security Scan Results", color=0xC0392B if local_flags or (ai_result and ai_result.get("malicious")) else 0x2ECC71)
        embed.add_field(name="URLs Found", value=str(len(urls)), inline=True)
        embed.add_field(name="Local Flags", value=str(len(local_flags)), inline=True)

        if ai_result:
            embed.add_field(
                name="AI Analysis",
                value=f"Malicious: **{ai_result.get('malicious', False)}**\n"
                      f"Type: {ai_result.get('type', 'N/A')}\n"
                      f"Confidence: {ai_result.get('confidence', 'N/A')}\n"
                      f"Reason: {ai_result.get('reason', 'N/A')}",
                inline=False,
            )
        else:
            embed.add_field(name="AI Analysis", value="Unavailable", inline=False)

        if local_flags:
            embed.add_field(
                name="Flagged URLs",
                value="\n".join(f"`{u[:100]}`" for u in local_flags[:5]),
                inline=False,
            )

        embed.timestamp = discord.utils.utcnow()
        await interaction.followup.send(embed=embed, ephemeral=True)

    @security_group.command(name="whitelist", description="Add a domain to the phishing whitelist")
    @app_commands.describe(domain="Domain to whitelist (e.g. minecraft.net)")
    @app_commands.checks.has_permissions(administrator=True)
    async def security_whitelist(self, interaction: discord.Interaction, domain: str):
        domain = domain.strip().lower().lstrip("www.")
        current = await self._get_whitelist(interaction.guild_id)
        current.add(domain)
        await self.bot.db.set_config(interaction.guild_id, "phishing_whitelist", ",".join(current))
        await interaction.response.send_message(
            embed=E.success("Whitelist Updated", f"`{domain}` added to the phishing whitelist."),
            ephemeral=True,
        )

    @security_group.command(name="blacklist", description="Add a domain to the phishing blacklist")
    @app_commands.describe(domain="Domain to blacklist")
    @app_commands.checks.has_permissions(administrator=True)
    async def security_blacklist(self, interaction: discord.Interaction, domain: str):
        domain = domain.strip().lower().lstrip("www.")
        current = await self._get_blacklist(interaction.guild_id)
        current.add(domain)
        await self.bot.db.set_config(interaction.guild_id, "phishing_blacklist", ",".join(current))
        await interaction.response.send_message(
            embed=E.success("Blacklist Updated", f"`{domain}` added to the phishing blacklist."),
            ephemeral=True,
        )

    @security_group.command(name="whitelist_remove", description="Remove a domain from the whitelist")
    @app_commands.describe(domain="Domain to remove")
    @app_commands.checks.has_permissions(administrator=True)
    async def security_whitelist_remove(self, interaction: discord.Interaction, domain: str):
        domain = domain.strip().lower().lstrip("www.")
        current = await self._get_whitelist(interaction.guild_id)
        current.discard(domain)
        await self.bot.db.set_config(interaction.guild_id, "phishing_whitelist", ",".join(current))
        await interaction.response.send_message(
            embed=E.success("Whitelist Updated", f"`{domain}` removed from the whitelist."),
            ephemeral=True,
        )

    @security_group.command(name="lists", description="View current whitelist and blacklist")
    @require_staff()
    async def security_lists(self, interaction: discord.Interaction):
        whitelist = await self._get_whitelist(interaction.guild_id)
        blacklist = await self._get_blacklist(interaction.guild_id)

        embed = discord.Embed(title="🔒 Security Domain Lists", color=0x3498DB)
        embed.add_field(
            name="✅ Whitelist",
            value="\n".join(f"`{d}`" for d in sorted(whitelist)) or "Empty",
            inline=True,
        )
        embed.add_field(
            name="🚫 Blacklist",
            value="\n".join(f"`{d}`" for d in sorted(blacklist)) or "Empty",
            inline=True,
        )
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(PhishingCog(bot))
