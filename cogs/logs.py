import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime, timezone

import utils.embeds as E
from utils.permissions import require_staff

log = logging.getLogger("MaltaSMP.Logs")


async def get_log_channel(bot, guild: discord.Guild, key: str) -> discord.TextChannel | None:
    channel_id = await bot.db.get_config_int(guild.id, key)
    if not channel_id:
        return None
    return guild.get_channel(channel_id)


async def send_log(bot, guild: discord.Guild, key: str, embed: discord.Embed):
    ch = await get_log_channel(bot, guild, key)
    if ch:
        try:
            await ch.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass


class Logs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._invite_cache: dict[int, dict[str, discord.Invite]] = {}

    # ── Join / Leave ──────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild

        # Track user in DB
        await self.bot.db.upsert_user(guild.id, member.id, str(member), member.joined_at.isoformat() if member.joined_at else datetime.now(timezone.utc).isoformat())

        account_age = datetime.now(timezone.utc) - member.created_at
        days = account_age.days

        embed = discord.Embed(
            title="📥 Member Joined",
            color=0x2ECC71,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member", value=f"{member.mention} ({member})", inline=False)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Account Age", value=f"{days} days", inline=True)
        embed.add_field(name="Member Count", value=str(guild.member_count), inline=True)

        # Invite tracking - check inviter
        inviter_row = await self.bot.db.get_invited_by(guild.id, member.id)
        if inviter_row:
            inviter = guild.get_member(inviter_row["inviter_id"])
            embed.add_field(name="Invited By", value=f"{inviter.mention if inviter else inviter_row['inviter_id']} (code: {inviter_row['invite_code']})", inline=False)

        embed.timestamp = datetime.now(timezone.utc)
        await send_log(self.bot, guild, "join_log_channel_id", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        await self.bot.db.set_user_left(guild.id, member.id)

        joined = member.joined_at
        if joined:
            duration = datetime.now(timezone.utc) - joined
            days = duration.days
            dur_str = f"{days}d"
        else:
            dur_str = "Unknown"

        embed = discord.Embed(title="📤 Member Left", color=0xE74C3C)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Member", value=f"{member} (`{member.id}`)", inline=False)
        embed.add_field(name="Time in Server", value=dur_str, inline=True)
        embed.add_field(name="Member Count", value=str(guild.member_count), inline=True)
        roles = [r.mention for r in member.roles if r != guild.default_role]
        if roles:
            embed.add_field(name="Roles", value=" ".join(roles[:10]), inline=False)
        embed.timestamp = datetime.now(timezone.utc)
        await send_log(self.bot, guild, "leave_log_channel_id", embed)

    # ── Messages ──────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        embed = discord.Embed(title="🗑️ Message Deleted", color=0xE74C3C)
        embed.add_field(name="Author", value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        if message.content:
            content = message.content[:1000] + "..." if len(message.content) > 1000 else message.content
            embed.add_field(name="Content", value=content, inline=False)
        if message.attachments:
            embed.add_field(name="Attachments", value="\n".join(a.filename for a in message.attachments), inline=False)
        embed.timestamp = datetime.now(timezone.utc)
        await send_log(self.bot, message.guild, "message_log_channel_id", embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not before.guild or before.author.bot or before.content == after.content:
            return

        embed = discord.Embed(title="✏️ Message Edited", color=0xF39C12, url=after.jump_url)
        embed.add_field(name="Author", value=f"{before.author.mention} (`{before.author.id}`)", inline=True)
        embed.add_field(name="Channel", value=before.channel.mention, inline=True)
        before_content = before.content[:500] + "..." if len(before.content) > 500 else before.content
        after_content = after.content[:500] + "..." if len(after.content) > 500 else after.content
        embed.add_field(name="Before", value=before_content or "*empty*", inline=False)
        embed.add_field(name="After", value=after_content or "*empty*", inline=False)
        embed.timestamp = datetime.now(timezone.utc)
        await send_log(self.bot, before.guild, "message_log_channel_id", embed)

    # ── Voice ─────────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        guild = member.guild

        if before.channel is None and after.channel is not None:
            embed = discord.Embed(title="🔊 Joined Voice", color=0x2ECC71)
            embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="Channel", value=after.channel.mention, inline=True)

        elif before.channel is not None and after.channel is None:
            embed = discord.Embed(title="🔇 Left Voice", color=0xE74C3C)
            embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="Channel", value=before.channel.mention, inline=True)

        elif before.channel != after.channel:
            embed = discord.Embed(title="🔀 Moved Voice", color=0xF39C12)
            embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="From", value=before.channel.mention, inline=True)
            embed.add_field(name="To", value=after.channel.mention, inline=True)
        else:
            return

        embed.set_thumbnail(url=member.display_avatar.url)
        embed.timestamp = datetime.now(timezone.utc)
        await send_log(self.bot, guild, "voice_log_channel_id", embed)

    # ── Member Update (roles, nickname) ──────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = before.guild

        # Nickname change
        if before.nick != after.nick:
            embed = discord.Embed(title="📝 Nickname Changed", color=0x3498DB)
            embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=False)
            embed.add_field(name="Before", value=before.nick or "*none*", inline=True)
            embed.add_field(name="After", value=after.nick or "*none*", inline=True)
            embed.set_thumbnail(url=after.display_avatar.url)
            embed.timestamp = datetime.now(timezone.utc)
            await send_log(self.bot, guild, "nickname_log_channel_id", embed)

        # Role changes
        added_roles = [r for r in after.roles if r not in before.roles]
        removed_roles = [r for r in before.roles if r not in after.roles]

        if added_roles:
            embed = discord.Embed(title="➕ Role Added", color=0x2ECC71)
            embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=True)
            embed.add_field(name="Roles Added", value=" ".join(r.mention for r in added_roles), inline=True)
            embed.set_thumbnail(url=after.display_avatar.url)
            embed.timestamp = datetime.now(timezone.utc)
            await send_log(self.bot, guild, "role_log_channel_id", embed)

        if removed_roles:
            embed = discord.Embed(title="➖ Role Removed", color=0xE74C3C)
            embed.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=True)
            embed.add_field(name="Roles Removed", value=" ".join(r.mention for r in removed_roles), inline=True)
            embed.set_thumbnail(url=after.display_avatar.url)
            embed.timestamp = datetime.now(timezone.utc)
            await send_log(self.bot, guild, "role_log_channel_id", embed)

    # ── Channel Events ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        embed = discord.Embed(title="📁 Channel Created", color=0x2ECC71)
        embed.add_field(name="Name", value=channel.mention, inline=True)
        embed.add_field(name="Type", value=str(channel.type).title(), inline=True)
        embed.add_field(name="ID", value=str(channel.id), inline=True)
        embed.timestamp = datetime.now(timezone.utc)
        await send_log(self.bot, channel.guild, "channel_log_channel_id", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        embed = discord.Embed(title="📁 Channel Deleted", color=0xE74C3C)
        embed.add_field(name="Name", value=channel.name, inline=True)
        embed.add_field(name="Type", value=str(channel.type).title(), inline=True)
        embed.add_field(name="ID", value=str(channel.id), inline=True)
        embed.timestamp = datetime.now(timezone.utc)
        await send_log(self.bot, channel.guild, "channel_log_channel_id", embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        if before.name == after.name:
            return
        embed = discord.Embed(title="✏️ Channel Updated", color=0xF39C12)
        embed.add_field(name="Before", value=before.name, inline=True)
        embed.add_field(name="After", value=after.mention, inline=True)
        embed.timestamp = datetime.now(timezone.utc)
        await send_log(self.bot, after.guild, "channel_log_channel_id", embed)


async def setup(bot):
    await bot.add_cog(Logs(bot))
