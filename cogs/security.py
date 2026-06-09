import discord
from discord.ext import commands
from discord import app_commands
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from collections import deque

import utils.embeds as E
from utils.permissions import require_staff

log = logging.getLogger("MaltaSMP.Security")


class Security(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # guild_id -> deque of join timestamps
        self._join_log: dict[int, deque] = {}
        # guild_id -> lockdown active bool
        self._lockdown: dict[int, bool] = {}

    def _get_join_log(self, guild_id: int) -> deque:
        if guild_id not in self._join_log:
            self._join_log[guild_id] = deque()
        return self._join_log[guild_id]

    async def _get_threshold(self, guild_id: int, key: str, default: int) -> int:
        val = await self.bot.db.get_config(guild_id, key)
        return int(val) if val else default

    async def _security_alert(self, guild: discord.Guild, title: str, description: str):
        channel_id = await self.bot.db.get_config_int(guild.id, "security_log_channel_id")
        if not channel_id:
            channel_id = await self.bot.db.get_config_int(guild.id, "mod_log_channel_id")
        if channel_id:
            ch = guild.get_channel(channel_id)
            if ch:
                embed = E.error(f"🚨 {title}", description)
                # Ping staff role if set
                staff_role_id = await self.bot.db.get_config_int(guild.id, "staff_role_id")
                ping = f"<@&{staff_role_id}>" if staff_role_id else ""
                try:
                    await ch.send(content=ping, embed=embed)
                except Exception:
                    pass

    # ── Anti-Raid: Mass join detection ────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        now = datetime.now(timezone.utc).timestamp()

        join_log = self._get_join_log(guild.id)
        join_log.append(now)

        # Check raid thresholds
        raid_threshold = await self._get_threshold(guild.id, "raid_join_threshold", 10)
        raid_window = await self._get_threshold(guild.id, "raid_join_window", 10)

        # Clean old joins
        while join_log and join_log[0] < now - raid_window:
            join_log.popleft()

        recent_joins = len(join_log)

        if recent_joins >= raid_threshold and not self._lockdown.get(guild.id, False):
            await self._trigger_lockdown(guild, f"{recent_joins} members joined in {raid_window} seconds")

        # New account check
        account_age_days = await self._get_threshold(guild.id, "min_account_age_days", 0)
        if account_age_days > 0:
            account_age = datetime.now(timezone.utc) - member.created_at
            if account_age.days < account_age_days:
                try:
                    dm = E.error(
                        f"Kicked from {guild.name}",
                        f"Your account must be at least {account_age_days} day(s) old to join this server.\n"
                        f"Your account is {account_age.days} day(s) old.",
                    )
                    await member.send(embed=dm)
                except Exception:
                    pass
                try:
                    await member.kick(reason=f"Account too new ({account_age.days}d < {account_age_days}d required)")
                except discord.Forbidden:
                    pass
                await self.bot.db.log_security(guild.id, "new_account_kick", member.id, f"Account age: {account_age.days}d")
                await self._security_alert(
                    guild,
                    "New Account Kicked",
                    f"{member.mention} (`{member.id}`) was kicked — account only {account_age.days} day(s) old.",
                )

    async def _trigger_lockdown(self, guild: discord.Guild, reason: str):
        self._lockdown[guild.id] = True
        log.warning(f"RAID DETECTED in {guild.name}: {reason}")

        await self.bot.db.log_security(guild.id, "raid_detected", None, reason)
        await self._security_alert(
            guild,
            "⚠️ RAID DETECTED — Lockdown Initiated",
            f"**Reason:** {reason}\n\nAll channels are being locked. Staff notified.",
        )

        # Lock all text channels
        failed = 0
        for channel in guild.text_channels:
            try:
                await channel.set_permissions(
                    guild.default_role,
                    send_messages=False,
                    reason="Anti-raid lockdown",
                )
            except discord.Forbidden:
                failed += 1

        log.info(f"Lockdown applied to {guild.name}. {failed} channel(s) failed.")

        # Auto-lift in background so the gateway is not blocked
        import asyncio
        asyncio.create_task(self._auto_lift_after_delay(guild, 600))

    async def _auto_lift_after_delay(self, guild: discord.Guild, delay: int):
        import asyncio
        await asyncio.sleep(delay)
        if self._lockdown.get(guild.id):
            await self._lift_lockdown(guild, "Auto-lift after 10 minutes")

    async def _lift_lockdown(self, guild: discord.Guild, reason: str):
        self._lockdown[guild.id] = False
        for channel in guild.text_channels:
            try:
                await channel.set_permissions(
                    guild.default_role,
                    send_messages=None,
                    reason=f"Lockdown lifted: {reason}",
                )
            except discord.Forbidden:
                pass

        await self._security_alert(guild, "✅ Lockdown Lifted", f"**Reason:** {reason}")
        log.info(f"Lockdown lifted in {guild.name}: {reason}")

    # ── Commands ──────────────────────────────────────────────────────────────

    @app_commands.command(name="lockdown", description="Manually trigger server lockdown")
    @require_staff()
    @app_commands.describe(reason="Reason for lockdown")
    async def lockdown_cmd(self, interaction: discord.Interaction, reason: str = "Manual lockdown"):
        await interaction.response.defer(ephemeral=True)
        await self._trigger_lockdown(interaction.guild, f"Manual by {interaction.user}: {reason}")
        await interaction.followup.send(embed=E.error("Lockdown Active", "Server lockdown has been triggered."), ephemeral=True)

    @app_commands.command(name="unlockdown", description="Lift server lockdown")
    @require_staff()
    @app_commands.describe(reason="Reason for lifting")
    async def unlockdown_cmd(self, interaction: discord.Interaction, reason: str = "Lockdown lifted by staff"):
        await interaction.response.defer(ephemeral=True)
        await self._lift_lockdown(interaction.guild, f"Manual by {interaction.user}: {reason}")
        await interaction.followup.send(embed=E.success("Lockdown Lifted", "Server lockdown has been lifted."), ephemeral=True)

    @app_commands.command(name="setminaccountage", description="Set minimum account age in days (0 = disabled)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(days="Minimum account age in days")
    async def set_min_age(self, interaction: discord.Interaction, days: app_commands.Range[int, 0, 365]):
        await self.bot.db.set_config(interaction.guild_id, "min_account_age_days", str(days))
        if days == 0:
            msg = "Minimum account age requirement disabled."
        else:
            msg = f"New members must have accounts at least **{days}** day(s) old to join."
        await interaction.response.send_message(embed=E.success("Account Age Set", msg), ephemeral=True)

    @app_commands.command(name="setraidthreshold", description="Set raid detection threshold")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(joins="Number of joins to trigger", window="Time window in seconds")
    async def set_raid_threshold(self, interaction: discord.Interaction, joins: int, window: int = 10):
        await self.bot.db.set_config(interaction.guild_id, "raid_join_threshold", str(joins))
        await self.bot.db.set_config(interaction.guild_id, "raid_join_window", str(window))
        await interaction.response.send_message(
            embed=E.success("Raid Threshold Set", f"Lockdown will trigger if {joins} members join within {window} seconds."),
            ephemeral=True,
        )

    @app_commands.command(name="securitystatus", description="View security status")
    @require_staff()
    async def security_status(self, interaction: discord.Interaction):
        guild = interaction.guild
        lockdown = self._lockdown.get(guild.id, False)
        min_age = await self.bot.db.get_config(guild.id, "min_account_age_days") or "0"
        raid_threshold = await self.bot.db.get_config(guild.id, "raid_join_threshold") or "10"
        raid_window = await self.bot.db.get_config(guild.id, "raid_join_window") or "10"
        automod = await self.bot.db.get_config(guild.id, "automod_enabled") or "true"

        embed = discord.Embed(title="🔒 Security Status", color=0xE74C3C if lockdown else 0x2ECC71)
        embed.add_field(name="Lockdown", value="🔴 ACTIVE" if lockdown else "🟢 Inactive", inline=True)
        embed.add_field(name="AutoMod", value="🟢 Enabled" if automod != "false" else "🔴 Disabled", inline=True)
        embed.add_field(name="Min Account Age", value=f"{min_age} day(s)", inline=True)
        embed.add_field(name="Raid Threshold", value=f"{raid_threshold} joins / {raid_window}s", inline=True)
        embed.timestamp = datetime.now(timezone.utc)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Security(bot))
