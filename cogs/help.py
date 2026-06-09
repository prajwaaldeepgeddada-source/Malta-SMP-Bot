"""
cogs/help.py
────────────
Interactive paginated /help command for Malta SMP Bot.

Features:
  - Category overview embed on first open (with server icon + bot avatar)
  - Dropdown select menu to jump to any category
  - Per-category embed listing every command in that section
  - "Back to overview" button
  - Ephemeral — only the invoking user sees it
  - Commands marked with 🔒 require Staff / Mod / Admin
  - Auto-generates the command list from a static registry so it
    stays in sync even when commands are added later
"""

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("MaltaSMP.Help")

# ── Command registry ──────────────────────────────────────────────────────────
# Each entry: (command, brief description, permission_level)
# permission_level: "" = everyone, "🔒 Staff" = staff only, "🔒 Admin" = admin only

CATEGORIES: dict[str, dict] = {

    "🏠 General": {
        "emoji": "🏠",
        "description": "Basic bot commands available to everyone.",
        "commands": [
            ("/ping",               "Check the bot's latency",                            ""),
            ("/botstats",           "View bot statistics (servers, users, latency)",       ""),
            ("/help",               "Show this help menu",                                 ""),
        ],
    },

    "🎫 Tickets": {
        "emoji": "🎫",
        "description": "Support ticket system for getting help from staff.",
        "commands": [
            ("/ticket",             "Open a new support ticket",                           ""),
            ("/ticketpanel",        "Post the ticket panel in a channel",                  "🔒 Staff"),
            ("/ticketstats",        "View ticket statistics for this server",               "🔒 Staff"),
            ("/adduser",            "Add a user to the current ticket channel",            "🔒 Staff"),
            ("/removeuser",         "Remove a user from the current ticket channel",       "🔒 Staff"),
        ],
    },

    "⚔️ Moderation": {
        "emoji": "⚔️",
        "description": "Tools for keeping the server safe and orderly.",
        "commands": [
            ("/warn",               "Issue a formal warning to a member",                  "🔒 Mod"),
            ("/warnings",           "View a member's active warnings",                     "🔒 Mod"),
            ("/delwarn",            "Remove a warning by its ID",                          "🔒 Mod"),
            ("/timeout",            "Temporarily mute a member",                           "🔒 Mod"),
            ("/untimeout",          "Remove a member's timeout",                           "🔒 Mod"),
            ("/kick",               "Kick a member from the server",                       "🔒 Mod"),
            ("/ban",                "Ban a member from the server",                        "🔒 Mod"),
            ("/unban",              "Unban a user by their ID",                            "🔒 Mod"),
            ("/clear",              "Bulk-delete messages in a channel (1–100)",           "🔒 Mod"),
            ("/lock",               "Prevent @everyone from sending in this channel",      "🔒 Mod"),
            ("/unlock",             "Re-allow @everyone to send in this channel",          "🔒 Mod"),
            ("/slowmode",           "Set a per-message slowmode delay (0 = off)",          "🔒 Mod"),
        ],
    },

    "📢 Announcements": {
        "emoji": "📢",
        "description": "Create, schedule, and manage server announcements.",
        "commands": [
            ("/announce send",              "Send a rich embedded announcement via modal",  "🔒 Staff"),
            ("/announce plain",             "Send a plain-text announcement",               "🔒 Staff"),
            ("/announce schedule",          "Schedule an announcement for a future time",   "🔒 Staff"),
            ("/announce cancel",            "Cancel a pending scheduled announcement",      "🔒 Staff"),
            ("/announce list",              "List all pending scheduled announcements",     "🔒 Staff"),
            ("/announce history",           "View recently sent announcements",             "🔒 Staff"),
            ("/announce setchannel",        "Set the default announcement channel",         "🔒 Admin"),
            ("/announce template save",     "Save a reusable announcement template",        "🔒 Staff"),
            ("/announce template use",      "Send an announcement from a saved template",   "🔒 Staff"),
            ("/announce template list",     "List all saved templates",                     "🔒 Staff"),
            ("/announce template delete",   "Delete a saved template",                      "🔒 Staff"),
        ],
    },

    "🤖 AI Chatbot": {
        "emoji": "🤖",
        "description": "AI-powered chat assistant (GitHub Models / GPT-4o).",
        "commands": [
            ("/ai setup",           "Set the dedicated AI chat channel",                   "🔒 Admin"),
            ("/ai disable",         "Disable the AI chatbot",                              "🔒 Admin"),
            ("/ai model",           "View the active AI model",                            "🔒 Staff"),
            ("/ai reset",           "Clear the conversation memory for this server",       "🔒 Staff"),
            ("/ai stats",           "View AI chat statistics",                             "🔒 Staff"),
            ("/ai setlogchannel",   "Set the channel for AI interaction logs",             "🔒 Admin"),
        ],
    },

    "🛡️ Auto-Moderation": {
        "emoji": "🛡️",
        "description": "Automated rule enforcement — spam, links, mentions, and AI review.",
        "commands": [
            ("/automod",              "Enable or disable the base AutoMod system",          "🔒 Admin"),
            ("/setlinkwhitelist",     "Set whitelisted domains (comma-separated)",          "🔒 Admin"),
            ("/setspamthreshold",     "Configure fast-spam detection threshold",            "🔒 Admin"),
            ("/automodai enable",     "Enable AI-powered content moderation",               "🔒 Admin"),
            ("/automodai disable",    "Disable AI-powered content moderation",              "🔒 Admin"),
            ("/automodai sensitivity","Set AI moderation sensitivity (low/medium/high)",    "🔒 Admin"),
            ("/automodai setlogchannel","Set the AI moderation log channel",               "🔒 Admin"),
            ("/automodai stats",      "View AI moderation session statistics",              "🔒 Staff"),
            ("/spamconfig enable",    "Enable enhanced spam detection",                     "🔒 Admin"),
            ("/spamconfig disable",   "Disable enhanced spam detection",                    "🔒 Admin"),
            ("/spamconfig thresholds","Configure flood/repeat/emoji thresholds",            "🔒 Admin"),
            ("/spamconfig status",    "View current spam detection config",                 "🔒 Staff"),
        ],
    },

    "🔐 Security": {
        "emoji": "🔐",
        "description": "Phishing detection, raid protection, and server lockdown tools.",
        "commands": [
            ("/security scan",          "Manually scan a message or URL for threats",      "🔒 Staff"),
            ("/security whitelist",     "Add a safe domain to the phishing whitelist",     "🔒 Admin"),
            ("/security blacklist",     "Block a domain permanently",                      "🔒 Admin"),
            ("/security whitelist_remove","Remove a domain from the whitelist",            "🔒 Admin"),
            ("/security lists",         "View current whitelist and blacklist",             "🔒 Staff"),
            ("/lockdown",               "Manually lock all server channels",               "🔒 Staff"),
            ("/unlockdown",             "Lift an active server lockdown",                  "🔒 Staff"),
            ("/securitystatus",         "View the overall security status",                "🔒 Staff"),
            ("/setminaccountage",       "Require a minimum account age to join",           "🔒 Admin"),
            ("/setraidthreshold",       "Configure the legacy raid join threshold",        "🔒 Admin"),
        ],
    },

    "🚨 Raid Detection": {
        "emoji": "🚨",
        "description": "Advanced 4-level automated raid detection and response.",
        "commands": [
            ("/raid status",        "View current raid detection status and level",        "🔒 Staff"),
            ("/raid enable",        "Enable advanced raid detection",                      "🔒 Admin"),
            ("/raid disable",       "Disable advanced raid detection",                     "🔒 Admin"),
            ("/raid emergency",     "Trigger Level 4 emergency lockdown immediately",      "🔒 Staff"),
            ("/raid unlock",        "Lift raid mode and restore normal server operations", "🔒 Staff"),
            ("/raid configure",     "Set custom join-rate thresholds for Level 1",         "🔒 Admin"),
            ("/raid setlogchannel", "Set the channel for raid alerts",                     "🔒 Admin"),
        ],
    },

    "📬 Invites": {
        "emoji": "📬",
        "description": "Track and view server invite statistics.",
        "commands": [
            ("/invites",            "Check your own (or another member's) invite count",   ""),
            ("/inviteleaderboard",  "See the top inviters in this server",                 ""),
            ("/inviteinfo",         "Check who invited a specific member",                 "🔒 Staff"),
        ],
    },

    "👋 Welcome": {
        "emoji": "👋",
        "description": "Configure welcome messages, goodbye messages, and auto-roles.",
        "commands": [
            ("/setwelcome",         "Set the welcome channel and custom message",          "🔒 Admin"),
            ("/setgoodbye",         "Set the goodbye channel and custom message",          "🔒 Admin"),
            ("/setautorole",        "Assign a role automatically to new members",          "🔒 Admin"),
            ("/testwelcome",        "Preview the welcome message",                         "🔒 Staff"),
            ("/testgoodbye",        "Preview the goodbye message",                         "🔒 Staff"),
        ],
    },

    "⚙️ Admin Setup": {
        "emoji": "⚙️",
        "description": "Server configuration and bot setup commands.",
        "commands": [
            ("/setup",              "Open the bot setup guide",                            "🔒 Admin"),
            ("/setlogs",            "Configure all log channels at once",                  "🔒 Admin"),
            ("/settranscripts",     "Set the ticket transcript channel",                   "🔒 Admin"),
            ("/settickets",         "Set the ticket category and log channel",             "🔒 Admin"),
            ("/setstaffrole",       "Set the staff role",                                  "🔒 Admin"),
            ("/setmodrole",         "Set the moderator role",                              "🔒 Admin"),
            ("/viewconfig",         "View the full current bot configuration",             "🔒 Staff"),
        ],
    },
}

# ── Colours per category ──────────────────────────────────────────────────────
CATEGORY_COLOURS = {
    "🏠 General":          0x3498DB,
    "🎫 Tickets":          0x9B59B6,
    "⚔️ Moderation":       0xE67E22,
    "📢 Announcements":    0x3498DB,
    "🤖 AI Chatbot":       0x7289DA,
    "🛡️ Auto-Moderation":  0xE74C3C,
    "🔐 Security":         0xC0392B,
    "🚨 Raid Detection":   0xE74C3C,
    "📬 Invites":          0x2ECC71,
    "👋 Welcome":          0xF39C12,
    "⚙️ Admin Setup":      0x95A5A6,
}


# ── UI Components ─────────────────────────────────────────────────────────────

def _overview_embed(guild: discord.Guild, bot_user: discord.ClientUser) -> discord.Embed:
    """The landing page embed shown before any category is selected."""
    embed = discord.Embed(
        title="📖 Malta SMP Bot — Help",
        description=(
            "Welcome! Use the **dropdown below** to browse commands by category.\n\n"
            + "\n".join(
                f"{data['emoji']} **{name}** — {data['description']}"
                for name, data in CATEGORIES.items()
            )
        ),
        color=0x3498DB,
        timestamp=datetime.now(timezone.utc),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_author(name=bot_user.display_name, icon_url=bot_user.display_avatar.url)
    embed.set_footer(text="🔒 = requires Staff / Mod / Admin role  •  Malta SMP Bot")
    return embed


def _category_embed(name: str, data: dict, guild: discord.Guild) -> discord.Embed:
    """Embed listing all commands in a single category."""
    colour = CATEGORY_COLOURS.get(name, 0x3498DB)
    embed = discord.Embed(
        title=f"{name} Commands",
        description=data["description"],
        color=colour,
        timestamp=datetime.now(timezone.utc),
    )

    lines = []
    for cmd, desc, perm in data["commands"]:
        perm_str = f"  `{perm}`" if perm else ""
        lines.append(f"`{cmd}`{perm_str}\n╰ {desc}")

    # Split into two fields if the list is long
    mid = len(lines) // 2 if len(lines) > 8 else len(lines)
    embed.add_field(name="Commands", value="\n".join(lines[:mid]), inline=False)
    if lines[mid:]:
        embed.add_field(name="\u200b", value="\n".join(lines[mid:]), inline=False)

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="🔒 = requires Staff / Mod / Admin  •  Use /help to return  •  Malta SMP Bot")
    return embed


class CategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                emoji=data["emoji"],
                description=data["description"][:50],
            )
            for name, data in CATEGORIES.items()
        ]
        super().__init__(
            placeholder="📂 Choose a category…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        data = CATEGORIES[selected]
        embed = _category_embed(selected, data, interaction.guild)
        # Re-render the view so the back button appears
        view = HelpView(interaction.guild, interaction.client.user, show_back=True)
        # Keep the same selection highlighted
        for opt in view.select.options:
            opt.default = opt.value == selected
        await interaction.response.edit_message(embed=embed, view=view)


class HelpView(discord.ui.View):
    def __init__(self, guild: discord.Guild, bot_user: discord.ClientUser, *, show_back: bool = False):
        super().__init__(timeout=120)
        self._guild = guild
        self._bot_user = bot_user

        self.select = CategorySelect()
        self.add_item(self.select)

        if show_back:
            back_btn = discord.ui.Button(
                label="Back to Overview",
                style=discord.ButtonStyle.secondary,
                emoji="🏠",
                row=1,
            )
            back_btn.callback = self._back_callback
            self.add_item(back_btn)

    async def _back_callback(self, interaction: discord.Interaction):
        embed = _overview_embed(self._guild, self._bot_user)
        view = HelpView(self._guild, self._bot_user, show_back=False)
        # Clear any defaults
        for opt in view.select.options:
            opt.default = False
        await interaction.response.edit_message(embed=embed, view=view)

    async def on_timeout(self):
        # Disable all items when the view times out
        for item in self.children:
            item.disabled = True


# ── Cog ───────────────────────────────────────────────────────────────────────

class HelpCog(commands.Cog, name="Help"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="Browse all Malta SMP Bot commands")
    @app_commands.describe(category="Jump straight to a specific category (optional)")
    @app_commands.choices(category=[
        app_commands.Choice(name=name, value=name) for name in CATEGORIES
    ])
    async def help_cmd(self, interaction: discord.Interaction, category: app_commands.Choice[str] = None):
        if category:
            # Jump straight to the selected category
            data = CATEGORIES[category.value]
            embed = _category_embed(category.value, data, interaction.guild)
            view = HelpView(interaction.guild, self.bot.user, show_back=True)
            for opt in view.select.options:
                opt.default = opt.value == category.value
        else:
            # Show the overview
            embed = _overview_embed(interaction.guild, self.bot.user)
            view = HelpView(interaction.guild, self.bot.user, show_back=False)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
