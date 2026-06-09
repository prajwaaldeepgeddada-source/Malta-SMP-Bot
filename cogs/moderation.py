import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import timedelta

from utils.permissions import require_mod, require_staff, is_staff
import utils.embeds as E

log = logging.getLogger("MaltaSMP.Moderation")


class ConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)
        self.confirmed = False

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        self.stop()
        await interaction.response.defer()


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _log_mod_action(self, guild: discord.Guild, action: str, mod: discord.Member, target, reason: str, duration: str = None):
        await self.bot.db.log_moderation(guild.id, action, mod.id, target.id if hasattr(target, 'id') else target, reason, duration)
        await self.bot.db.log_staff_action(guild.id, mod.id, action, target.id if hasattr(target, 'id') else target, reason)

        channel_id = await self.bot.db.get_config_int(guild.id, "mod_log_channel_id")
        if not channel_id:
            return
        ch = guild.get_channel(channel_id)
        if not ch:
            return
        embed = E.moderation_log(action, mod, target, reason, duration)
        try:
            await ch.send(embed=embed)
        except discord.Forbidden:
            pass

    # ── /warn ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="warn", description="Warn a member")
    @require_mod()
    @app_commands.describe(member="Member to warn", reason="Reason for the warning", evidence="Evidence (URL or description)")
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str, evidence: str = None):
        if member.bot:
            await interaction.response.send_message("❌ Cannot warn bots.", ephemeral=True)
            return
        if member.guild_permissions.administrator:
            await interaction.response.send_message("❌ Cannot warn administrators.", ephemeral=True)
            return

        warn_id = await self.bot.db.add_warning(interaction.guild_id, member.id, interaction.user.id, reason, evidence)
        count = await self.bot.db.count_warnings(interaction.guild_id, member.id)

        embed = E.warning(
            "Member Warned",
            f"{member.mention} has been warned.\n**Reason:** {reason}\n**Warning #{count}** (ID: {warn_id})",
        )
        await interaction.response.send_message(embed=embed)

        # DM
        try:
            dm_embed = E.warning(
                f"Warning Received — {interaction.guild.name}",
                f"You have received a warning.\n**Reason:** {reason}\n**Total Warnings:** {count}",
            )
            await member.send(embed=dm_embed)
        except Exception:
            pass

        await self._log_mod_action(interaction.guild, "Warn", interaction.user, member, reason)

    # ── /warnings ─────────────────────────────────────────────────────────────

    @app_commands.command(name="warnings", description="View a member's warnings")
    @require_mod()
    @app_commands.describe(member="Member to check")
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        warns = await self.bot.db.get_warnings(interaction.guild_id, member.id)
        if not warns:
            await interaction.response.send_message(embed=E.info("No Warnings", f"{member.mention} has no active warnings."), ephemeral=True)
            return

        embed = discord.Embed(
            title=f"⚠️ Warnings — {member}",
            color=0xF39C12,
            description=f"**{len(warns)}** active warning(s)",
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        for w in warns[:10]:
            mod = interaction.guild.get_member(w["moderator_id"])
            mod_str = str(mod) if mod else str(w["moderator_id"])
            embed.add_field(
                name=f"#{w['id']} — {w['created_at'][:10]}",
                value=f"**Reason:** {w['reason']}\n**By:** {mod_str}" + (f"\n**Evidence:** {w['evidence']}" if w["evidence"] else ""),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="delwarn", description="Delete a warning by ID")
    @require_mod()
    @app_commands.describe(warning_id="Warning ID to remove")
    async def delwarn(self, interaction: discord.Interaction, warning_id: int):
        removed = await self.bot.db.remove_warning(warning_id, interaction.guild_id)
        if removed:
            await interaction.response.send_message(embed=E.success("Warning Removed", f"Warning #{warning_id} has been removed."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=E.error("Not Found", f"Warning #{warning_id} not found."), ephemeral=True)

    # ── /clear ────────────────────────────────────────────────────────────────

    @app_commands.command(name="clear", description="Delete messages in this channel")
    @require_mod()
    @app_commands.describe(amount="Number of messages to delete (1-100)")
    async def clear(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(embed=E.success("Messages Cleared", f"Deleted **{len(deleted)}** messages."), ephemeral=True)
        await self._log_mod_action(interaction.guild, "Clear", interaction.user, interaction.user, f"Cleared {len(deleted)} messages in {interaction.channel.mention}")

    # ── /timeout ──────────────────────────────────────────────────────────────

    @app_commands.command(name="timeout", description="Timeout a member")
    @require_mod()
    @app_commands.describe(member="Member to timeout", minutes="Duration in minutes", reason="Reason")
    async def timeout_cmd(self, interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason provided"):
        if member.bot or member.guild_permissions.administrator:
            await interaction.response.send_message("❌ Cannot timeout this member.", ephemeral=True)
            return

        duration = timedelta(minutes=minutes)
        await member.timeout(duration, reason=reason)

        duration_str = f"{minutes} minute(s)"
        embed = E.moderation_log("Timeout", interaction.user, member, reason, duration_str)
        await interaction.response.send_message(embed=embed)

        try:
            dm = E.warning(f"You have been timed out in {interaction.guild.name}", f"**Reason:** {reason}\n**Duration:** {duration_str}")
            await member.send(embed=dm)
        except Exception:
            pass

        await self._log_mod_action(interaction.guild, "Timeout", interaction.user, member, reason, duration_str)

    # ── /untimeout ────────────────────────────────────────────────────────────

    @app_commands.command(name="untimeout", description="Remove a member's timeout")
    @require_mod()
    @app_commands.describe(member="Member to untimeout", reason="Reason")
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await member.timeout(None, reason=reason)
        embed = E.success("Timeout Removed", f"{member.mention}'s timeout has been removed.\n**Reason:** {reason}")
        await interaction.response.send_message(embed=embed)
        await self._log_mod_action(interaction.guild, "Untimeout", interaction.user, member, reason)

    # ── /kick ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="kick", description="Kick a member from the server")
    @require_mod()
    @app_commands.describe(member="Member to kick", reason="Reason")
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        if member.bot or member.guild_permissions.administrator:
            await interaction.response.send_message("❌ Cannot kick this member.", ephemeral=True)
            return

        view = ConfirmView()
        msg = await interaction.response.send_message(
            embed=E.warning("Confirm Kick", f"Are you sure you want to kick {member.mention}?\n**Reason:** {reason}"),
            view=view,
            ephemeral=True,
        )
        await view.wait()

        if not view.confirmed:
            await interaction.edit_original_response(embed=E.info("Cancelled", "Kick cancelled."), view=None)
            return

        try:
            dm = E.error(f"You have been kicked from {interaction.guild.name}", f"**Reason:** {reason}")
            await member.send(embed=dm)
        except Exception:
            pass

        await member.kick(reason=reason)
        embed = E.moderation_log("Kick", interaction.user, member, reason)
        await interaction.edit_original_response(embed=embed, view=None)
        await self._log_mod_action(interaction.guild, "Kick", interaction.user, member, reason)

    # ── /ban ──────────────────────────────────────────────────────────────────

    @app_commands.command(name="ban", description="Ban a member from the server")
    @require_mod()
    @app_commands.describe(member="Member to ban", reason="Reason", delete_days="Days of messages to delete (0-7)")
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided", delete_days: app_commands.Range[int, 0, 7] = 1):
        if member.bot or member.guild_permissions.administrator:
            await interaction.response.send_message("❌ Cannot ban this member.", ephemeral=True)
            return

        view = ConfirmView()
        await interaction.response.send_message(
            embed=E.warning("Confirm Ban", f"Are you sure you want to **ban** {member.mention}?\n**Reason:** {reason}"),
            view=view,
            ephemeral=True,
        )
        await view.wait()

        if not view.confirmed:
            await interaction.edit_original_response(embed=E.info("Cancelled", "Ban cancelled."), view=None)
            return

        try:
            dm = E.error(f"You have been banned from {interaction.guild.name}", f"**Reason:** {reason}")
            await member.send(embed=dm)
        except Exception:
            pass

        await member.ban(reason=reason, delete_message_days=delete_days)
        embed = E.moderation_log("Ban", interaction.user, member, reason)
        await interaction.edit_original_response(embed=embed, view=None)
        await self._log_mod_action(interaction.guild, "Ban", interaction.user, member, reason)

    # ── /unban ────────────────────────────────────────────────────────────────

    @app_commands.command(name="unban", description="Unban a user by ID")
    @require_mod()
    @app_commands.describe(user_id="User ID to unban", reason="Reason")
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)
            return

        try:
            user = await self.bot.fetch_user(uid)
            await interaction.guild.unban(user, reason=reason)
            embed = E.success("User Unbanned", f"**{user}** (`{uid}`) has been unbanned.\n**Reason:** {reason}")
            await interaction.response.send_message(embed=embed)
            await self._log_mod_action(interaction.guild, "Unban", interaction.user, user, reason)
        except discord.NotFound:
            await interaction.response.send_message("❌ User not found or not banned.", ephemeral=True)

    # ── /lock ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="lock", description="Lock this channel")
    @require_mod()
    @app_commands.describe(reason="Reason for locking")
    async def lock(self, interaction: discord.Interaction, reason: str = "No reason provided"):
        await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
        embed = E.error("Channel Locked", f"This channel has been locked.\n**Reason:** {reason}\n**By:** {interaction.user.mention}")
        await interaction.response.send_message(embed=embed)
        await self._log_mod_action(interaction.guild, "Lock", interaction.user, interaction.user, f"Locked #{interaction.channel.name}: {reason}")

    # ── /unlock ───────────────────────────────────────────────────────────────

    @app_commands.command(name="unlock", description="Unlock this channel")
    @require_mod()
    @app_commands.describe(reason="Reason for unlocking")
    async def unlock(self, interaction: discord.Interaction, reason: str = "No reason provided"):
        await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True)
        embed = E.success("Channel Unlocked", f"This channel has been unlocked.\n**Reason:** {reason}\n**By:** {interaction.user.mention}")
        await interaction.response.send_message(embed=embed)
        await self._log_mod_action(interaction.guild, "Unlock", interaction.user, interaction.user, f"Unlocked #{interaction.channel.name}: {reason}")

    # ── /slowmode ─────────────────────────────────────────────────────────────

    @app_commands.command(name="slowmode", description="Set slowmode for this channel")
    @require_mod()
    @app_commands.describe(seconds="Slowmode delay in seconds (0 to disable)")
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
        await interaction.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            embed = E.success("Slowmode Disabled", f"Slowmode has been disabled in {interaction.channel.mention}.")
        else:
            embed = E.info("Slowmode Set", f"Slowmode set to **{seconds}s** in {interaction.channel.mention}.")
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
