import discord
from discord.ext import commands
from typing import Callable
import logging

log = logging.getLogger("MaltaSMP.Permissions")


async def get_staff_role(bot, guild: discord.Guild) -> discord.Role | None:
    role_id = await bot.db.get_config_int(guild.id, "staff_role_id")
    return guild.get_role(role_id) if role_id else None


async def get_mod_role(bot, guild: discord.Guild) -> discord.Role | None:
    role_id = await bot.db.get_config_int(guild.id, "mod_role_id")
    return guild.get_role(role_id) if role_id else None


async def is_staff(bot, member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    role = await get_staff_role(bot, member.guild)
    if role and role in member.roles:
        return True
    mod_role = await get_mod_role(bot, member.guild)
    if mod_role and mod_role in member.roles:
        return True
    return False


async def is_mod(bot, member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    mod_role = await get_mod_role(bot, member.guild)
    if mod_role and mod_role in member.roles:
        return True
    return False


def require_staff():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        result = await is_staff(interaction.client, interaction.user)
        if not result:
            await interaction.response.send_message(
                "❌ You need the **Staff** role to use this command.", ephemeral=True
            )
            return False
        return True
    return discord.app_commands.check(predicate)


def require_mod():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        result = await is_mod(interaction.client, interaction.user)
        if not result:
            await interaction.response.send_message(
                "❌ You need the **Moderator** role to use this command.", ephemeral=True
            )
            return False
        return True
    return discord.app_commands.check(predicate)


def require_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message(
            "❌ You need **Administrator** permission to use this command.", ephemeral=True
        )
        return False
    return discord.app_commands.check(predicate)
