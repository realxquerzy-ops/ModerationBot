import logging

import discord
from discord.ext import commands

RESOURCE_GUILD_ID = 1511406716749611171
ANNOUNCE_CHANNEL_ID = 1550550681767645244
BOOST_CHANNEL_ID = 1550211298447466606
ANNOUNCE_ROLE_ID = 1550550942724915272

BIRD = "\U0001f426"
BLACK_BIRD = "\U0001f426\u200d\u2b1b"
OLD_BLACK_HEART = "\U0001f5a4"

ANNOUNCE_TITLE = "BirdBot Announcements"


class AnnounceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger("modbot.announce")

    async def _ensure_embed(self, channel, embed):
        try:
            msgs = [m async for m in channel.history(limit=40) if m.author.id == self.bot.user.id]
        except Exception:
            msgs = []
        for m in msgs:
            if m.embeds and m.embeds[0].title == embed.title:
                try:
                    await m.edit(embed=embed)
                    return m
                except Exception:
                    pass
        return await channel.send(embed=embed)

    def _announce_embed(self):
        return discord.Embed(
            title=ANNOUNCE_TITLE,
            description=(
                "**React with :bird: for Announcements Ping** or "
                "**react with :black_bird: for Hall of Bird Ping**"
            ),
            color=discord.Color.blue(),
        )

    async def _ensure_announce(self, guild):
        channel = guild.get_channel(ANNOUNCE_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ANNOUNCE_CHANNEL_ID)
            except Exception:
                self.log.warning("announce channel unavailable")
                return
        msg = await self._ensure_embed(channel, self._announce_embed())
        for emoji in (BIRD, BLACK_BIRD):
            try:
                await msg.add_reaction(emoji)
            except Exception as e:
                self.log.warning("add_reaction %r failed: %s", emoji, e)
        try:
            await msg.clear_reaction(OLD_BLACK_HEART)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_ready(self):
        guild = self.bot.get_guild(RESOURCE_GUILD_ID)
        if guild is None:
            self.log.warning("resource guild %s not in guilds", RESOURCE_GUILD_ID)
            return

        await self._ensure_announce(guild)

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