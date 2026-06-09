import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import io
import json
import asyncio
from datetime import datetime, timezone

from utils.permissions import require_staff, is_staff
from utils.transcript import generate_transcript
import utils.embeds as E

log = logging.getLogger("MaltaSMP.Tickets")

CATEGORIES = {
    "Support":           {"emoji": "🆘", "color": 0x3498DB},
    "Player Report":     {"emoji": "⚠️", "color": 0xE74C3C},
    "Bug Report":        {"emoji": "🐛", "color": 0x2ECC71},
    "Staff Application": {"emoji": "📋", "color": 0xF39C12},
}


# ── Persistent Views ──────────────────────────────────────────────────────────

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for category in CATEGORIES:
            self.add_item(TicketCreateButton(category))


class TicketCreateButton(discord.ui.Button):
    def __init__(self, category: str):
        meta = CATEGORIES[category]
        super().__init__(
            label=category,
            emoji=meta["emoji"],
            style=discord.ButtonStyle.secondary,
            custom_id=f"ticket_create_{category.lower().replace(' ', '_')}",
        )
        self.category = category

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketCreateModal(self.category))


class TicketCreateModal(discord.ui.Modal):
    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Briefly describe your issue...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
    )

    def __init__(self, category: str):
        super().__init__(title=f"Create {category} Ticket")
        self.category = category

    async def on_submit(self, interaction: discord.Interaction):
        cog: Tickets = interaction.client.cogs.get("Tickets")
        if cog:
            await cog.create_ticket(interaction, self.category, self.reason.value)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.primary, custom_id="ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: Tickets = interaction.client.cogs.get("Tickets")
        if cog:
            await cog.handle_claim(interaction)

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: Tickets = interaction.client.cogs.get("Tickets")
        if cog:
            await interaction.response.send_modal(CloseReasonModal())

    @discord.ui.button(label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="ticket_delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: Tickets = interaction.client.cogs.get("Tickets")
        if cog:
            await cog.handle_delete(interaction)


class ClosedTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Reopen", emoji="🔓", style=discord.ButtonStyle.success, custom_id="ticket_reopen")
    async def reopen(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: Tickets = interaction.client.cogs.get("Tickets")
        if cog:
            await cog.handle_reopen(interaction)

    @discord.ui.button(label="Delete", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="ticket_delete_closed")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: Tickets = interaction.client.cogs.get("Tickets")
        if cog:
            await cog.handle_delete(interaction)


class CloseReasonModal(discord.ui.Modal, title="Close Ticket"):
    reason = discord.ui.TextInput(
        label="Closure Reason",
        placeholder="Why are you closing this ticket?",
        required=False,
        max_length=300,
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog: Tickets = interaction.client.cogs.get("Tickets")
        if cog:
            await cog.handle_close(interaction, self.reason.value or "No reason provided")


# ── Cog ───────────────────────────────────────────────────────────────────────

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._ticket_counter: dict[int, int] = {}

    async def cog_load(self):
        self.bot.add_view(TicketPanelView())
        self.bot.add_view(TicketControlView())
        self.bot.add_view(ClosedTicketView())
        self.inactivity_check.start()

    async def cog_unload(self):
        self.inactivity_check.cancel()

    # ── Helper: next ticket number ────────────────────────────────────────────

    async def _next_ticket_id(self, guild_id: int) -> str:
        stats = await self.bot.db.get_ticket_stats(guild_id)
        num = stats["total"] + 1
        return f"TICKET-{num:04d}"

    # ── Helper: send log ──────────────────────────────────────────────────────

    async def _send_ticket_log(self, guild: discord.Guild, embed: discord.Embed, file: discord.File = None):
        channel_id = await self.bot.db.get_config_int(guild.id, "ticket_log_channel_id")
        if not channel_id:
            return
        ch = guild.get_channel(channel_id)
        if ch:
            try:
                await ch.send(embed=embed, file=file)
            except discord.Forbidden:
                pass

    async def _send_transcript(self, guild: discord.Guild, ticket: dict, file_bytes: bytes, ticket_id: str):
        channel_id = await self.bot.db.get_config_int(guild.id, "transcript_channel_id")
        if not channel_id:
            return
        ch = guild.get_channel(channel_id)
        if ch:
            f = discord.File(io.BytesIO(file_bytes), filename=f"{ticket_id}.html")
            embed = E.info(
                "Transcript Generated",
                f"**Ticket:** {ticket_id}\n**Category:** {ticket['category']}",
            )
            try:
                await ch.send(embed=embed, file=f)
            except discord.Forbidden:
                pass

    # ── Core: create ticket ───────────────────────────────────────────────────

    async def create_ticket(self, interaction: discord.Interaction, category: str, reason: str):
        guild = interaction.guild
        user = interaction.user

        # Check existing
        existing = await self.bot.db.get_open_ticket_by_user(guild.id, user.id)
        if existing:
            ch = guild.get_channel(existing["channel_id"])
            mention = ch.mention if ch else f"#{existing['ticket_id']}"
            await interaction.response.send_message(
                f"❌ You already have an open ticket: {mention}", ephemeral=True
            )
            return

        ticket_id = await self._next_ticket_id(guild.id)

        # Permissions overwrites
        category_channel_id = await self.bot.db.get_config_int(guild.id, "ticket_category_id")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
        }

        staff_role_id = await self.bot.db.get_config_int(guild.id, "staff_role_id")
        if staff_role_id:
            staff_role = guild.get_role(staff_role_id)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        mod_role_id = await self.bot.db.get_config_int(guild.id, "mod_role_id")
        if mod_role_id:
            mod_role = guild.get_role(mod_role_id)
            if mod_role:
                overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        cat_obj = None
        if category_channel_id:
            cat_obj = guild.get_channel(category_channel_id)

        channel_name = f"ticket-{user.name[:20].lower().replace(' ', '-')}-{ticket_id[-4:]}"

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=cat_obj,
                overwrites=overwrites,
                topic=f"{ticket_id} | {category} | {user}",
                reason=f"Ticket created by {user}",
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing permissions to create ticket channel.", ephemeral=True)
            return

        await self.bot.db.create_ticket(ticket_id, guild.id, channel.id, user.id, category, reason)

        embed = E.ticket_embed(ticket_id, category, user, reason)
        view = TicketControlView()

        await channel.send(content=user.mention, embed=embed, view=view)

        # Log
        log_embed = E.log_embed(
            "🎫 Ticket Created",
            color=0x9B59B6,
            ticket_id=ticket_id,
            category=category,
            creator=f"{user} ({user.id})",
            channel=channel.mention,
        )
        await self._send_ticket_log(guild, log_embed)
        await self.bot.db.log_staff_action(guild.id, user.id, "ticket_create", None, f"{ticket_id} | {category}")

        await interaction.response.send_message(
            f"✅ Your ticket has been created: {channel.mention}", ephemeral=True
        )
        log.info(f"Ticket {ticket_id} created by {user} in {guild.name}")

    # ── Core: claim ───────────────────────────────────────────────────────────

    async def handle_claim(self, interaction: discord.Interaction):
        if not await is_staff(self.bot, interaction.user):
            await interaction.response.send_message("❌ Only staff can claim tickets.", ephemeral=True)
            return

        ticket = await self.bot.db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
            return

        if ticket["status"] != "open":
            await interaction.response.send_message("❌ This ticket is not open.", ephemeral=True)
            return

        if ticket["claimer_id"]:
            claimer = interaction.guild.get_member(ticket["claimer_id"])
            await interaction.response.send_message(
                f"❌ This ticket is already claimed by {claimer.mention if claimer else ticket['claimer_id']}.",
                ephemeral=True,
            )
            return

        await self.bot.db.claim_ticket(ticket["ticket_id"], interaction.user.id)
        embed = E.success("Ticket Claimed", f"{interaction.user.mention} has claimed this ticket.")
        await interaction.response.send_message(embed=embed)

        log_embed = E.log_embed("🙋 Ticket Claimed", color=0x3498DB,
                                 ticket_id=ticket["ticket_id"], claimer=f"{interaction.user} ({interaction.user.id})")
        await self._send_ticket_log(interaction.guild, log_embed)
        await self.bot.db.log_staff_action(interaction.guild.id, interaction.user.id, "ticket_claim", ticket["creator_id"], ticket["ticket_id"])

    # ── Core: close ───────────────────────────────────────────────────────────

    async def handle_close(self, interaction: discord.Interaction, reason: str):
        ticket = await self.bot.db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
            return

        if ticket["status"] != "open":
            await interaction.response.send_message("❌ This ticket is already closed.", ephemeral=True)
            return

        creator = interaction.guild.get_member(ticket["creator_id"])
        closer_is_staff = await is_staff(self.bot, interaction.user)

        if not closer_is_staff and interaction.user.id != ticket["creator_id"]:
            await interaction.response.send_message("❌ Only staff or the ticket creator can close this ticket.", ephemeral=True)
            return

        await self.bot.db.update_ticket_status(ticket["ticket_id"], "closed", interaction.user.id, reason)

        # Generate transcript
        messages = await self.bot.db.get_ticket_messages(ticket["ticket_id"])
        updated_ticket = await self.bot.db.get_ticket(ticket["ticket_id"])
        closer_user = interaction.user

        transcript_bytes = generate_transcript(dict(updated_ticket), [dict(m) for m in messages], creator, closer_user)

        # Update channel permissions - remove creator write access
        try:
            if creator:
                await interaction.channel.set_permissions(creator, send_messages=False)
        except Exception:
            pass

        embed = E.warning(
            "Ticket Closed",
            f"This ticket has been closed by {interaction.user.mention}.\n**Reason:** {reason}",
        )
        view = ClosedTicketView()
        await interaction.response.send_message(embed=embed, view=view)

        # Send transcript
        await self._send_transcript(interaction.guild, dict(updated_ticket), transcript_bytes, ticket["ticket_id"])

        # Log
        log_embed = E.log_embed(
            "🔒 Ticket Closed",
            color=0xE74C3C,
            ticket_id=ticket["ticket_id"],
            closed_by=f"{interaction.user} ({interaction.user.id})",
            reason=reason,
        )
        f = discord.File(io.BytesIO(transcript_bytes), filename=f"{ticket['ticket_id']}.html")
        await self._send_ticket_log(interaction.guild, log_embed, file=f)
        await self.bot.db.log_staff_action(interaction.guild.id, interaction.user.id, "ticket_close", ticket["creator_id"],
                                            f"{ticket['ticket_id']} | {reason}")

    # ── Core: reopen ──────────────────────────────────────────────────────────

    async def handle_reopen(self, interaction: discord.Interaction):
        if not await is_staff(self.bot, interaction.user):
            await interaction.response.send_message("❌ Only staff can reopen tickets.", ephemeral=True)
            return

        ticket = await self.bot.db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
            return

        if ticket["status"] != "closed":
            await interaction.response.send_message("❌ This ticket is not closed.", ephemeral=True)
            return

        await self.bot.db.update_ticket_status(ticket["ticket_id"], "open")

        creator = interaction.guild.get_member(ticket["creator_id"])
        if creator:
            try:
                await interaction.channel.set_permissions(creator, send_messages=True, view_channel=True, read_message_history=True)
            except Exception:
                pass

        embed = E.success("Ticket Reopened", f"This ticket has been reopened by {interaction.user.mention}.")
        view = TicketControlView()
        await interaction.response.send_message(embed=embed, view=view)

        log_embed = E.log_embed("🔓 Ticket Reopened", color=0x2ECC71,
                                 ticket_id=ticket["ticket_id"], reopened_by=f"{interaction.user} ({interaction.user.id})")
        await self._send_ticket_log(interaction.guild, log_embed)
        await self.bot.db.log_staff_action(interaction.guild.id, interaction.user.id, "ticket_reopen", ticket["creator_id"], ticket["ticket_id"])

    # ── Core: delete ──────────────────────────────────────────────────────────

    async def handle_delete(self, interaction: discord.Interaction):
        if not await is_staff(self.bot, interaction.user):
            await interaction.response.send_message("❌ Only staff can delete tickets.", ephemeral=True)
            return

        ticket = await self.bot.db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
            return

        embed = E.error("Deleting Ticket", "This channel will be deleted in 5 seconds...")
        await interaction.response.send_message(embed=embed)

        await self.bot.db.update_ticket_status(ticket["ticket_id"], "deleted", interaction.user.id, "Channel deleted")

        log_embed = E.log_embed("🗑️ Ticket Deleted", color=0x992D22,
                                 ticket_id=ticket["ticket_id"], deleted_by=f"{interaction.user} ({interaction.user.id})")
        await self._send_ticket_log(interaction.guild, log_embed)
        await self.bot.db.log_staff_action(interaction.guild.id, interaction.user.id, "ticket_delete", ticket["creator_id"], ticket["ticket_id"])

        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")
        except discord.Forbidden:
            pass

    # ── Message tracking ──────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        ticket = await self.bot.db.get_ticket_by_channel(message.channel.id)
        if not ticket:
            return

        attachments = json.dumps([{"url": a.url, "filename": a.filename} for a in message.attachments])
        embeds_data = json.dumps([{"title": e.title, "description": e.description} for e in message.embeds if e.title])

        await self.bot.db.save_ticket_message(
            ticket["ticket_id"],
            message.id,
            message.author.id,
            str(message.author),
            message.content,
            attachments,
            embeds_data,
        )
        await self.bot.db.update_ticket_activity(ticket["ticket_id"])

    # ── Inactivity check ──────────────────────────────────────────────────────

    @tasks.loop(hours=12)
    async def inactivity_check(self):
        for guild in self.bot.guilds:
            days_str = await self.bot.db.get_config(guild.id, "ticket_inactivity_days")
            days = int(days_str) if days_str else 7
            inactive = await self.bot.db.get_inactive_tickets(days)
            for ticket in inactive:
                if ticket["guild_id"] != guild.id:
                    continue
                ch = guild.get_channel(ticket["channel_id"])
                if not ch:
                    continue
                embed = E.warning(
                    "Ticket Inactivity Warning",
                    f"This ticket has been inactive for **{days} days** and will be automatically closed soon.\n"
                    "Please respond or close this ticket.",
                )
                try:
                    await ch.send(embed=embed)
                except Exception:
                    pass

    @inactivity_check.before_loop
    async def before_inactivity(self):
        await self.bot.wait_until_ready()

    # ── Slash commands ────────────────────────────────────────────────────────

    @app_commands.command(name="ticket", description="Create a new support ticket")
    async def ticket_cmd(self, interaction: discord.Interaction):
        view = TicketPanelView()
        embed = discord.Embed(
            title="🎫 Create a Ticket",
            description="Select a category below to open a ticket.",
            color=0x9B59B6,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="ticketpanel", description="Send the ticket panel to this channel")
    @require_staff()
    async def ticket_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎫 Malta SMP Support",
            description=(
                "Need help? Open a ticket by clicking the button for your category below.\n\n"
                "🆘 **Support** — General help\n"
                "⚠️ **Player Report** — Report a player\n"
                "🐛 **Bug Report** — Report a bug\n"
                "📋 **Staff Application** — Apply for staff"
            ),
            color=0x9B59B6,
        )
        embed.set_footer(text="Malta SMP • One ticket at a time per user")
        view = TicketPanelView()
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ Panel sent.", ephemeral=True)

    @app_commands.command(name="ticketstats", description="View ticket statistics")
    @require_staff()
    async def ticket_stats(self, interaction: discord.Interaction):
        stats = await self.bot.db.get_ticket_stats(interaction.guild_id)
        embed = E.info(
            "Ticket Statistics",
            f"**Total:** {stats['total']}\n**Open:** {stats['open']}\n**Closed:** {stats['closed']}",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="adduser", description="Add a user to this ticket")
    @require_staff()
    @app_commands.describe(user="User to add")
    async def add_user(self, interaction: discord.Interaction, user: discord.Member):
        ticket = await self.bot.db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        await interaction.channel.set_permissions(user, view_channel=True, send_messages=True, read_message_history=True)
        await interaction.response.send_message(f"✅ Added {user.mention} to this ticket.")

    @app_commands.command(name="removeuser", description="Remove a user from this ticket")
    @require_staff()
    @app_commands.describe(user="User to remove")
    async def remove_user(self, interaction: discord.Interaction, user: discord.Member):
        ticket = await self.bot.db.get_ticket_by_channel(interaction.channel_id)
        if not ticket:
            await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
            return
        await interaction.channel.set_permissions(user, view_channel=False)
        await interaction.response.send_message(f"✅ Removed {user.mention} from this ticket.")


async def setup(bot):
    await bot.add_cog(Tickets(bot))
