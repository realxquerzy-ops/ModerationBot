import logging

import discord
from discord.ext import commands

RESOURCE_GUILD_ID = 1511406716749611171
ANNOUNCE_CHANNEL_ID = 1550550681767645244
BOOST_CHANNEL_ID = 1550211298447466606
ANNOUNCE_ROLE_ID = 1550550942724915272

BIRD = "\U0001f426"
BLACK_BIRD = "\U0001f426\u200d\u2b1b"


class AnnounceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger("modbot.announce")

    @commands.Cog.listener()
    async def on_ready(self):
        guild = self.bot.get_guild(RESOURCE_GUILD_ID)
        if guild is None:
            self.log.warning("resource guild %s not in guilds", RESOURCE_GUILD_ID)
            return

        settings = self.bot.boost_settings.get(RESOURCE_GUILD_ID) or {}
        settings["channel_id"] = BOOST_CHANNEL_ID
        self.bot.boost_settings[RESOURCE_GUILD_ID] = settings
        if self.bot.db is not None:
            try:
                self.bot.db.set_boost_settings(RESOURCE_GUILD_ID, channel_id=BOOST_CHANNEL_ID)
            except Exception as e:
                self.log.warning("boost settings update failed: %s", e)

        boost_cog = self.bot.get_cog("BoostCog")
        if boost_cog is not None:
            channel = guild.get_channel(BOOST_CHANNEL_ID)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(BOOST_CHANNEL_ID)
                except Exception:
                    channel = None
            if channel is not None:
                try:
                    await boost_cog._render_panel(guild, channel)
                except Exception as e:
                    self.log.warning("boost panel render failed: %s", e)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        try:
            if reaction.message.guild.id != RESOURCE_GUILD_ID:
                return
        except Exception:
            return
        if reaction.message.channel.id != ANNOUNCE_CHANNEL_ID:
            return
        if reaction.message.author.id != self.bot.user.id:
            return
        if str(reaction.emoji) not in (BIRD, BLACK_BIRD):
            return

        guild = reaction.message.guild
        role = guild.get_role(ANNOUNCE_ROLE_ID)
        if role is None:
            return
        try:
            await user.add_roles(role)
        except Exception as e:
            self.log.warning("role add failed for %s: %s", user.id, e)


async def setup(bot):
    await bot.add_cog(AnnounceCog(bot))