import logging

import discord
from discord import app_commands
from discord.ext import commands


BOOST_START_DEFAULT = "Tysm for boosting the server! {user}"
BOOST_END_DEFAULT = "A boost just ended. Thanks for boosting the server earlier, {user}!"


class BoostCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger("modbot.boost")

    def _has_manage(self, interaction):
        return interaction.user.guild_permissions.manage_guild

    @app_commands.command(
        name="boostchannel",
        description="Set the channel where boost start/end messages are sent.",
    )
    @app_commands.describe(channel="Channel to post boost messages in")
    @app_commands.guild_only()
    async def boostchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not self._has_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        settings = self.bot.boost_settings.get(guild_id) or {}
        settings["channel_id"] = channel.id
        self.bot.boost_settings[guild_id] = settings
        if self.bot.db is not None:
            self.bot.db.set_boost_channel(guild_id, channel.id)
        await interaction.followup.send(f"✅ Boost messages will now be posted in {channel.mention}.", ephemeral=True)

    @app_commands.command(
        name="booststartmessage",
        description="Set the message shown when someone boosts the server. Use {user} for the booster.",
    )
    @app_commands.describe(message="Message text; {user} becomes the booster's mention")
    @app_commands.guild_only()
    async def booststartmessage(self, interaction: discord.Interaction, message: str):
        if not self._has_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        settings = self.bot.boost_settings.get(guild_id) or {}
        settings["boost_start_message"] = message
        self.bot.boost_settings[guild_id] = settings
        if self.bot.db is not None:
            self.bot.db.set_boost_start_message(guild_id, message)
        await interaction.followup.send("✅ Boost start message set.", ephemeral=True)

    @app_commands.command(
        name="boostendmessage",
        description="Set the message shown when a boost ends. Use {user} for the ex-booster.",
    )
    @app_commands.describe(message="Message text; {user} becomes the ex-booster's mention")
    @app_commands.guild_only()
    async def boostendmessage(self, interaction: discord.Interaction, message: str):
        if not self._has_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        settings = self.bot.boost_settings.get(guild_id) or {}
        settings["boost_end_message"] = message
        self.bot.boost_settings[guild_id] = settings
        if self.bot.db is not None:
            self.bot.db.set_boost_end_message(guild_id, message)
        await interaction.followup.send("✅ Boost end message set.", ephemeral=True)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.premium_since == after.premium_since:
            return
        guild = after.guild
        settings = self.bot.boost_settings.get(guild.id)
        if not settings or not settings.get("channel_id"):
            return
        channel = guild.get_channel(settings["channel_id"])
        if channel is None:
            return
        boost_started = before.premium_since is None and after.premium_since is not None
        template = settings.get(
            "boost_start_message" if boost_started else "boost_end_message"
        )
        if not template:
            template = BOOST_START_DEFAULT if boost_started else BOOST_END_DEFAULT
        text = template.replace("{user}", after.mention)
        try:
            await channel.send(text)
        except Exception as e:
            self.log.warning("boost message send failed in %s: %s", guild.id, e)


async def setup(bot):
    await bot.add_cog(BoostCog(bot))
