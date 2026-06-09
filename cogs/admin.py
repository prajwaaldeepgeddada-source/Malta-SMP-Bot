import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime, timezone

import utils.embeds as E
from utils.permissions import require_staff

log = logging.getLogger("MaltaSMP.Admin")


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── /setup ────────────────────────────────────────────────────────────────

    @app_commands.command(name="setup", description="Interactive server setup wizard")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="⚙️ Malta SMP Bot — Setup",
            description=(
                "Use the commands below to configure the bot:\n\n"
                "**Channels**\n"
                "`/setlogs` — Configure all log channels\n"
                "`/settranscripts` — Set transcript channel\n"
                "`/settickets` — Set ticket category\n"
                "`/setwelcome` — Set welcome channel\n"
                "`/setgoodbye` — Set goodbye channel\n\n"
                "**Roles**\n"
                "`/setstaffrole` — Set staff role\n"
                "`/setmodrole` — Set moderator role\n"
                "`/setautorole` — Set auto-role for new members\n\n"
                "**Security**\n"
                "`/setminaccountage` — Set minimum account age\n"
                "`/setraidthreshold` — Configure raid detection\n"
                "`/setspamthreshold` — Configure spam detection\n"
                "`/setlinkwhitelist` — Whitelist domains\n\n"
                "**Tickets**\n"
                "`/ticketpanel` — Send ticket panel to a channel\n\n"
                "**Announcements**\n"
                "`/announce setchannel` — Set default announcement channel\n"
                "`/announce send` — Send a rich embedded announcement\n"
                "`/announce plain` — Send a plain-text announcement\n"
                "`/announce schedule` — Schedule a future announcement\n"
                "`/announce template save/use/list` — Manage templates\n"
            ),
            color=0x3498DB,
        )
        embed.set_footer(text="Malta SMP Bot • All settings stored in database")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /setlogs ──────────────────────────────────────────────────────────────

    @app_commands.command(name="setlogs", description="Configure log channels")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        join_log="Channel for join logs",
        leave_log="Channel for leave logs",
        message_log="Channel for message logs",
        mod_log="Channel for moderation logs",
        voice_log="Channel for voice logs",
        role_log="Channel for role logs",
        nickname_log="Channel for nickname logs",
        channel_log="Channel for channel logs",
        security_log="Channel for security logs",
        automod_log="Channel for automod logs",
    )
    async def setlogs(
        self,
        interaction: discord.Interaction,
        join_log: discord.TextChannel = None,
        leave_log: discord.TextChannel = None,
        message_log: discord.TextChannel = None,
        mod_log: discord.TextChannel = None,
        voice_log: discord.TextChannel = None,
        role_log: discord.TextChannel = None,
        nickname_log: discord.TextChannel = None,
        channel_log: discord.TextChannel = None,
        security_log: discord.TextChannel = None,
        automod_log: discord.TextChannel = None,
    ):
        guild_id = interaction.guild_id
        mapping = {
            "join_log_channel_id": join_log,
            "leave_log_channel_id": leave_log,
            "message_log_channel_id": message_log,
            "mod_log_channel_id": mod_log,
            "voice_log_channel_id": voice_log,
            "role_log_channel_id": role_log,
            "nickname_log_channel_id": nickname_log,
            "channel_log_channel_id": channel_log,
            "security_log_channel_id": security_log,
            "automod_log_channel_id": automod_log,
        }
        updated = []
        for key, channel in mapping.items():
            if channel:
                await self.bot.db.set_config(guild_id, key, str(channel.id))
                updated.append(f"`{key.replace('_channel_id','').replace('_',' ').title()}` → {channel.mention}")

        if not updated:
            await interaction.response.send_message("❌ No channels provided.", ephemeral=True)
            return

        embed = E.success("Log Channels Updated", "\n".join(updated))
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self.bot.db.log_staff_action(guild_id, interaction.user.id, "setlogs", None, str(updated))

    # ── /settranscripts ───────────────────────────────────────────────────────

    @app_commands.command(name="settranscripts", description="Set the transcript channel")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="Channel for ticket transcripts")
    async def set_transcripts(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await self.bot.db.set_config(interaction.guild_id, "transcript_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=E.success("Transcript Channel Set", f"Transcripts will be sent to {channel.mention}."),
            ephemeral=True,
        )

    # ── /settickets ───────────────────────────────────────────────────────────

    @app_commands.command(name="settickets", description="Set the ticket category and log channel")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        category="Category for ticket channels",
        log_channel="Channel for ticket logs",
        inactivity_days="Days before inactivity warning (default: 7)",
    )
    async def set_tickets(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel,
        log_channel: discord.TextChannel = None,
        inactivity_days: int = 7,
    ):
        await self.bot.db.set_config(interaction.guild_id, "ticket_category_id", str(category.id))
        await self.bot.db.set_config(interaction.guild_id, "ticket_inactivity_days", str(inactivity_days))
        if log_channel:
            await self.bot.db.set_config(interaction.guild_id, "ticket_log_channel_id", str(log_channel.id))

        lines = [f"**Category:** {category.name}"]
        if log_channel:
            lines.append(f"**Log Channel:** {log_channel.mention}")
        lines.append(f"**Inactivity Days:** {inactivity_days}")

        await interaction.response.send_message(
            embed=E.success("Ticket System Configured", "\n".join(lines)),
            ephemeral=True,
        )

    # ── /setstaffrole ─────────────────────────────────────────────────────────

    @app_commands.command(name="setstaffrole", description="Set the staff role")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(role="Staff role")
    async def set_staff_role(self, interaction: discord.Interaction, role: discord.Role):
        await self.bot.db.set_config(interaction.guild_id, "staff_role_id", str(role.id))
        await interaction.response.send_message(
            embed=E.success("Staff Role Set", f"Staff role set to {role.mention}."),
            ephemeral=True,
        )

    # ── /setmodrole ───────────────────────────────────────────────────────────

    @app_commands.command(name="setmodrole", description="Set the moderator role")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(role="Moderator role")
    async def set_mod_role(self, interaction: discord.Interaction, role: discord.Role):
        await self.bot.db.set_config(interaction.guild_id, "mod_role_id", str(role.id))
        await interaction.response.send_message(
            embed=E.success("Mod Role Set", f"Moderator role set to {role.mention}."),
            ephemeral=True,
        )

    # ── /viewconfig ───────────────────────────────────────────────────────────

    @app_commands.command(name="viewconfig", description="View current bot configuration")
    @require_staff()
    async def view_config(self, interaction: discord.Interaction):
        guild = interaction.guild
        guild_id = guild.id

        async def ch_name(key):
            cid = await self.bot.db.get_config_int(guild_id, key)
            if not cid:
                return "*Not set*"
            c = guild.get_channel(cid)
            return c.mention if c else f"#{cid} (deleted)"

        async def role_name(key):
            rid = await self.bot.db.get_config_int(guild_id, key)
            if not rid:
                return "*Not set*"
            r = guild.get_role(rid)
            return r.mention if r else f"@{rid} (deleted)"

        embed = discord.Embed(title="⚙️ Bot Configuration", color=0x3498DB)
        embed.add_field(name="Staff Role", value=await role_name("staff_role_id"), inline=True)
        embed.add_field(name="Mod Role", value=await role_name("mod_role_id"), inline=True)
        embed.add_field(name="Auto Role", value=await role_name("autorole_id"), inline=True)
        embed.add_field(name="Welcome Channel", value=await ch_name("welcome_channel_id"), inline=True)
        embed.add_field(name="Goodbye Channel", value=await ch_name("goodbye_channel_id"), inline=True)
        embed.add_field(name="Transcript Channel", value=await ch_name("transcript_channel_id"), inline=True)
        embed.add_field(name="Join Log", value=await ch_name("join_log_channel_id"), inline=True)
        embed.add_field(name="Leave Log", value=await ch_name("leave_log_channel_id"), inline=True)
        embed.add_field(name="Message Log", value=await ch_name("message_log_channel_id"), inline=True)
        embed.add_field(name="Mod Log", value=await ch_name("mod_log_channel_id"), inline=True)
        embed.add_field(name="Voice Log", value=await ch_name("voice_log_channel_id"), inline=True)
        embed.add_field(name="Security Log", value=await ch_name("security_log_channel_id"), inline=True)
        embed.add_field(name="Ticket Log", value=await ch_name("ticket_log_channel_id"), inline=True)
        embed.add_field(name="Role Log", value=await ch_name("role_log_channel_id"), inline=True)
        embed.add_field(name="Nick Log", value=await ch_name("nickname_log_channel_id"), inline=True)

        inactivity = await self.bot.db.get_config(guild_id, "ticket_inactivity_days") or "7"
        min_age = await self.bot.db.get_config(guild_id, "min_account_age_days") or "0"
        embed.add_field(name="Ticket Inactivity", value=f"{inactivity}d", inline=True)
        embed.add_field(name="Min Account Age", value=f"{min_age}d", inline=True)

        embed.timestamp = datetime.now(timezone.utc)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /botstats ─────────────────────────────────────────────────────────────

    @app_commands.command(name="botstats", description="View bot statistics")
    async def bot_stats(self, interaction: discord.Interaction):
        bot = self.bot
        embed = discord.Embed(title="📊 Malta SMP Bot Stats", color=0x3498DB)
        embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
        embed.add_field(name="Users", value=str(sum(g.member_count for g in bot.guilds)), inline=True)
        embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="Commands", value=str(len(bot.tree.get_commands())), inline=True)
        embed.set_thumbnail(url=bot.user.display_avatar.url)
        embed.timestamp = datetime.now(timezone.utc)
        await interaction.response.send_message(embed=embed)

    # ── /ping ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="ping", description="Check bot latency")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            embed=E.info("🏓 Pong!", f"Latency: **{latency}ms**")
        )


async def setup(bot):
    await bot.add_cog(Admin(bot))
