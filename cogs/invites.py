import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime, timezone

import utils.embeds as E
from utils.permissions import require_staff

log = logging.getLogger("MaltaSMP.Invites")


class Invites(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._invite_cache: dict[int, dict[str, int]] = {}  # guild_id -> {code: uses}

    async def cog_load(self):
        # Schedule cache population after bot is ready; calling wait_until_ready()
        # directly inside cog_load (which runs inside setup_hook) would deadlock.
        import asyncio
        asyncio.create_task(self._populate_cache_when_ready())

    async def _populate_cache_when_ready(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self._cache_invites(guild)

    async def _cache_invites(self, guild: discord.Guild):
        try:
            invites = await guild.invites()
            self._invite_cache[guild.id] = {inv.code: inv.uses for inv in invites}
            for inv in invites:
                if inv.inviter:
                    await self.bot.db.upsert_invite(guild.id, inv.code, inv.inviter.id, inv.uses or 0, inv.max_uses or 0)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ── Events ────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if not invite.guild:
            return
        guild = invite.guild
        if guild.id not in self._invite_cache:
            self._invite_cache[guild.id] = {}
        self._invite_cache[guild.id][invite.code] = invite.uses or 0
        if invite.inviter:
            await self.bot.db.upsert_invite(guild.id, invite.code, invite.inviter.id, invite.uses or 0, invite.max_uses or 0)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if not invite.guild:
            return
        cache = self._invite_cache.get(invite.guild.id, {})
        cache.pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        old_cache = self._invite_cache.get(guild.id, {})

        try:
            new_invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return

        new_cache = {inv.code: inv.uses for inv in new_invites}

        used_code = None
        inviter_id = None

        for code, uses in new_cache.items():
            old_uses = old_cache.get(code, 0)
            if uses > old_uses:
                used_code = code
                # Find inviter
                for inv in new_invites:
                    if inv.code == code and inv.inviter:
                        inviter_id = inv.inviter.id
                        break
                break

        self._invite_cache[guild.id] = new_cache

        if used_code and inviter_id:
            await self.bot.db.record_invite_use(guild.id, used_code, inviter_id, member.id)
            # Update DB
            for inv in new_invites:
                if inv.code == used_code and inv.inviter:
                    await self.bot.db.upsert_invite(guild.id, inv.code, inv.inviter.id, inv.uses or 0, inv.max_uses or 0)
            log.info(f"{member} joined {guild.name} via invite {used_code} from {inviter_id}")

    # ── Commands ──────────────────────────────────────────────────────────────

    @app_commands.command(name="invites", description="Check how many invites you or a member have")
    @app_commands.describe(member="Member to check (defaults to you)")
    async def invites_cmd(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        count = await self.bot.db.get_user_invites(interaction.guild_id, target.id)
        embed = E.info(
            f"Invites — {target}",
            f"{target.mention} has invited **{count}** member(s) to the server.",
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="inviteleaderboard", description="View the top inviters in this server")
    async def invite_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        leaderboard = await self.bot.db.get_invite_leaderboard(interaction.guild_id, 10)
        if not leaderboard:
            await interaction.followup.send(embed=E.info("Invite Leaderboard", "No invite data yet."))
            return

        embed = discord.Embed(title="🏆 Invite Leaderboard", color=0xF1C40F)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(leaderboard):
            member = interaction.guild.get_member(row["inviter_id"])
            name = str(member) if member else f"User {row['inviter_id']}"
            medal = medals[i] if i < 3 else f"#{i+1}"
            lines.append(f"{medal} **{name}** — {row['total']} invite(s)")
        embed.description = "\n".join(lines)
        embed.timestamp = datetime.now(timezone.utc)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="inviteinfo", description="Check who invited a specific member")
    @require_staff()
    @app_commands.describe(member="Member to look up")
    async def invite_info(self, interaction: discord.Interaction, member: discord.Member):
        row = await self.bot.db.get_invited_by(interaction.guild_id, member.id)
        if not row:
            await interaction.response.send_message(embed=E.info("Invite Info", f"No invite data found for {member.mention}."), ephemeral=True)
            return

        inviter = interaction.guild.get_member(row["inviter_id"])
        embed = E.info(
            f"Invite Info — {member}",
            f"{member.mention} was invited by **{inviter.mention if inviter else row['inviter_id']}** using code `{row['invite_code']}`.",
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Invites(bot))
