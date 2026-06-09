import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime, timezone

import utils.embeds as E
from utils.permissions import require_staff

log = logging.getLogger("MaltaSMP.Welcome")


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Member join ───────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild

        # Auto-role
        autorole_id = await self.bot.db.get_config_int(guild.id, "autorole_id")
        if autorole_id:
            role = guild.get_role(autorole_id)
            if role:
                try:
                    await member.add_roles(role, reason="Auto-role on join")
                except discord.Forbidden:
                    log.warning(f"Cannot assign autorole in {guild.name}")

        # Welcome channel
        welcome_channel_id = await self.bot.db.get_config_int(guild.id, "welcome_channel_id")
        if not welcome_channel_id:
            return

        ch = guild.get_channel(welcome_channel_id)
        if not ch:
            return

        # Custom message
        custom_msg = await self.bot.db.get_config(guild.id, "welcome_message")
        if custom_msg:
            msg = custom_msg.replace("{user}", member.mention).replace("{server}", guild.name).replace("{count}", str(guild.member_count))
        else:
            msg = None

        embed = discord.Embed(
            title=f"👋 Welcome to {guild.name}!",
            description=msg or f"Welcome {member.mention}! You are member **#{guild.member_count}**.",
            color=0x2ECC71,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        embed.set_footer(text=f"ID: {member.id}")
        embed.timestamp = datetime.now(timezone.utc)

        try:
            await ch.send(content=member.mention, embed=embed)
        except discord.Forbidden:
            pass

    # ── Member leave ──────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild

        goodbye_channel_id = await self.bot.db.get_config_int(guild.id, "goodbye_channel_id")
        if not goodbye_channel_id:
            return

        ch = guild.get_channel(goodbye_channel_id)
        if not ch:
            return

        custom_msg = await self.bot.db.get_config(guild.id, "goodbye_message")
        if custom_msg:
            msg = custom_msg.replace("{user}", str(member)).replace("{server}", guild.name).replace("{count}", str(guild.member_count))
        else:
            msg = None

        embed = discord.Embed(
            title=f"👋 Goodbye!",
            description=msg or f"**{member}** has left the server. We now have **{guild.member_count}** members.",
            color=0xE74C3C,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"ID: {member.id}")
        embed.timestamp = datetime.now(timezone.utc)

        try:
            await ch.send(embed=embed)
        except discord.Forbidden:
            pass

    # ── Commands ──────────────────────────────────────────────────────────────

    @app_commands.command(name="setwelcome", description="Set the welcome channel and message")
    @require_staff()
    @app_commands.describe(channel="Welcome channel", message="Message (use {user}, {server}, {count})")
    async def set_welcome(self, interaction: discord.Interaction, channel: discord.TextChannel, message: str = None):
        await self.bot.db.set_config(interaction.guild_id, "welcome_channel_id", str(channel.id))
        if message:
            await self.bot.db.set_config(interaction.guild_id, "welcome_message", message)
        await interaction.response.send_message(
            embed=E.success("Welcome Set", f"Welcome channel set to {channel.mention}." + (f"\nMessage: {message}" if message else "")),
            ephemeral=True,
        )

    @app_commands.command(name="setgoodbye", description="Set the goodbye channel and message")
    @require_staff()
    @app_commands.describe(channel="Goodbye channel", message="Message (use {user}, {server}, {count})")
    async def set_goodbye(self, interaction: discord.Interaction, channel: discord.TextChannel, message: str = None):
        await self.bot.db.set_config(interaction.guild_id, "goodbye_channel_id", str(channel.id))
        if message:
            await self.bot.db.set_config(interaction.guild_id, "goodbye_message", message)
        await interaction.response.send_message(
            embed=E.success("Goodbye Set", f"Goodbye channel set to {channel.mention}."),
            ephemeral=True,
        )

    @app_commands.command(name="setautorole", description="Set the auto-role for new members")
    @require_staff()
    @app_commands.describe(role="Role to assign to new members")
    async def set_autorole(self, interaction: discord.Interaction, role: discord.Role):
        await self.bot.db.set_config(interaction.guild_id, "autorole_id", str(role.id))
        await interaction.response.send_message(
            embed=E.success("Auto-Role Set", f"New members will receive {role.mention}."),
            ephemeral=True,
        )

    @app_commands.command(name="testwelcome", description="Test the welcome message")
    @require_staff()
    async def test_welcome(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(interaction.user.id)
        if not member:
            await interaction.response.send_message("❌ Could not resolve you as a guild member.", ephemeral=True)
            return
        await self.on_member_join(member)
        await interaction.response.send_message(embed=E.success("Test Sent", "Welcome message sent!"), ephemeral=True)

    @app_commands.command(name="testgoodbye", description="Test the goodbye message")
    @require_staff()
    async def test_goodbye(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(interaction.user.id)
        if not member:
            await interaction.response.send_message("❌ Could not resolve you as a guild member.", ephemeral=True)
            return
        await self.on_member_remove(member)
        await interaction.response.send_message(embed=E.success("Test Sent", "Goodbye message sent!"), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Welcome(bot))
