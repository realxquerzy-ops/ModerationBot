import io
import logging

import aiohttp
import discord
from discord.ext import commands

import welcomer as wc
from .welcomer_image import render_welcomer_card

WELCOME_ACCENT = (88, 101, 242)
GOODBYE_ACCENT = (240, 90, 90)


class WelcomerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger("modbot.welcomer")
        self._session = None
        self._avatar_cache = {}

    async def _avatar_pil(self, member):
        if member is None:
            return None
        if member.id in self._avatar_cache:
            return self._avatar_cache[member.id]
        try:
            if self._session is None:
                self._session = aiohttp.ClientSession()
            asset = member.display_avatar.with_format("png").with_size(128)
            async with self._session.get(asset.url) as resp:
                if resp.status != 200:
                    self._avatar_cache[member.id] = None
                    return None
                data = await resp.read()
            from PIL import Image

            img = Image.open(io.BytesIO(data)).convert("RGBA")
            self._avatar_cache[member.id] = img
            return img
        except Exception:
            self._avatar_cache[member.id] = None
            return None

    def _resolve_channel(self, guild, explicit):
        if explicit:
            try:
                channel = guild.get_channel(int(explicit))
            except (TypeError, ValueError):
                channel = None
            if isinstance(channel, discord.TextChannel):
                return channel
        if getattr(guild, "system_channel", None) is not None:
            return guild.system_channel
        for channel in guild.text_channels:
            try:
                if channel.permissions_for(guild.me).send_messages:
                    return channel
            except Exception:
                continue
        return None

    def _format(self, text, member, count):
        return (
            text.replace("{user}", member.mention)
            .replace("{name}", member.display_name)
            .replace("{server}", member.guild.name)
            .replace("{count}", f"{count:,}")
        )

    async def _deliver(self, kind, member, accent):
        guild = member.guild
        settings = wc.get_welcomer(self.bot, guild.id)
        self.log.info("[%s] guild=%s (%s) member=%s enabled=%s cache_ok=%s",
                      kind, guild.name, guild.id, member.id,
                      settings.get(f"{kind}_enabled"),
                      guild.id in getattr(self.bot, "welcomer_cache", {}))
        if not settings.get(f"{kind}_enabled"):
            return
        explicit = settings.get(f"{kind}_channel") or ""
        if kind == "goodbye" and not explicit:
            explicit = settings.get("welcome_channel") or ""
        channel = self._resolve_channel(guild, explicit)
        if channel is None:
            self.log.warning("[%s] no channel found (explicit=%r text_channels=%d system=%r)",
                             kind, explicit, len(guild.text_channels),
                             getattr(guild, "system_channel", None) is not None)
            return
        count = guild.member_count or 0
        text = settings.get(f"{kind}_text") or wc.DEFAULT_WELCOMER[f"{kind}_text"]
        content = self._format(text, member, count)

        embed = None
        file = None
        if settings.get(f"{kind}_image"):
            av_img = await self._avatar_pil(member)
            if av_img is not None:
                try:
                    buf = render_welcomer_card(
                        member.display_name,
                        guild.name,
                        av_img,
                        count,
                        kind.upper(),
                        accent,
                        bool(settings.get(f"{kind}_member_count")),
                    )
                    file = discord.File(buf, filename=f"{kind}.png")
                    embed = discord.Embed(color=discord.Color.from_rgb(*accent))
                    embed.set_image(url=f"attachment://{kind}.png")
                except Exception as e:
                    self.log.warning("welcomer image render failed: %s", e)
                    file = None
                    embed = None

        try:
            if file is not None:
                msg = await channel.send(content=content, file=file, embed=embed)
            else:
                msg = await channel.send(content=content)
        except Exception as e:
            self.log.warning("welcomer send failed (%s): %s", kind, e)
            return
        self.log.info("[%s] posted to #%s (%s)", kind, channel.name, channel.id)

        emoji = settings.get(f"{kind}_emoji") or ""
        if emoji:
            try:
                await msg.add_reaction(emoji)
            except Exception as e:
                self.log.warning("welcomer reaction failed (%s): %s", kind, e)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if getattr(member, "bot", False) or member.guild is None:
            return
        await self._deliver("welcome", member, WELCOME_ACCENT)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if getattr(member, "bot", False) or member.guild is None:
            return
        await self._deliver("goodbye", member, GOODBYE_ACCENT)


async def setup(bot):
    await bot.add_cog(WelcomerCog(bot))