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
    welcomer_group = discord.app_commands.Group(
        name="welcomer", description="Welcome/goodbye messages on join/leave"
    )

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
                try:
                    can_send = channel.permissions_for(guild.me).send_messages
                except Exception:
                    can_send = False
                if not can_send:
                    self.log.warning(
                        "configured welcome channel #%s (%s) denied Send Messages; falling back",
                        channel.name, channel.id,
                    )
                else:
                    return channel
        if getattr(guild, "system_channel", None) is not None:
            try:
                if guild.system_channel.permissions_for(guild.me).send_messages:
                    return guild.system_channel
            except Exception:
                pass
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
            return "disabled"
        explicit = settings.get(f"{kind}_channel") or ""
        if kind == "goodbye" and not explicit:
            explicit = settings.get("welcome_channel") or ""
        channel = self._resolve_channel(guild, explicit)
        if channel is None:
            self.log.warning("[%s] no channel found (explicit=%r text_channels=%d system=%r)",
                             kind, explicit, len(guild.text_channels),
                             getattr(guild, "system_channel", None) is not None)
            return "no_channel"
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
        except discord.Forbidden:
            self.log.warning("welcomer send failed (%s) in %s: missing Send Messages permission", kind, channel.id)
            return f"forbidden:{channel.id}"
        except Exception as e:
            self.log.warning("welcomer send failed (%s): %s", kind, e)
            return f"error:{e}"
        self.log.info("[%s] posted to #%s (%s)", kind, channel.name, channel.id)

        emoji = settings.get(f"{kind}_emoji") or ""
        if emoji:
            try:
                await msg.add_reaction(emoji)
            except Exception as e:
                self.log.warning("welcomer reaction failed (%s): %s", kind, e)
        return "ok"

    @welcomer_group.command(
        name="test",
        description="Test the welcome/goodbye message setup (Manage Server)",
    )
    @discord.app_commands.choices(
        kind=[
            discord.app_commands.Choice(name="Welcome", value="welcome"),
            discord.app_commands.Choice(name="Goodbye", value="goodbye"),
        ]
    )
    @discord.app_commands.guild_only()
    async def welcomer_test(self, interaction: discord.Interaction, kind: str):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "❌ You need the **Manage Server** permission to use this.", ephemeral=True
            )
            return
        kind_val = kind.value if isinstance(kind, discord.app_commands.Choice) else kind
        label = kind.name if isinstance(kind, discord.app_commands.Choice) else kind
        guild = interaction.guild
        settings = wc.get_welcomer(self.bot, guild.id)
        enabled = settings.get(f"{kind_val}_enabled")
        explicit = settings.get(f"{kind_val}_channel") or ""
        channel = self._resolve_channel(guild, explicit)
        await interaction.response.defer(ephemeral=True)
        result = await self._deliver(kind_val, interaction.user, WELCOME_ACCENT if kind_val == "welcome" else GOODBYE_ACCENT)
        status = {
            "ok": "✅ message posted",
            "disabled": "❌ this welcome/goodbye is disabled in settings",
            "no_channel": "⚠️ no channel could be resolved",
        }.get(result, f"❌ failed to send: {result}")
        desc = (
            f"Enabled: {enabled} · Channel: {channel.mention if channel else 'none'}"
            f" · Cache ids: {list(getattr(self.bot, 'welcomer_cache', {}).keys())}"
        )
        await interaction.followup.send(f"✅ Test fired for **{label}**.\n{desc}\n{status}", ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if getattr(member, "bot", False) or member.guild is None:
            return
        settings = wc.get_welcomer(self.bot, member.guild.id)
        role_id = settings.get("welcome_role") or ""
        if role_id:
            try:
                role = member.guild.get_role(int(role_id))
                if role is not None and role not in member.roles:
                    await member.add_roles(role, reason="Welcome: auto role on join")
                    self.log.info("[welcome-role] assigned %s to %s", role.name, member.id)
            except Exception as e:
                self.log.warning("[welcome-role] assign failed for %s: %s", member.id, e)
        await self._deliver("welcome", member, WELCOME_ACCENT)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if getattr(member, "bot", False) or member.guild is None:
            return
        await self._deliver("goodbye", member, GOODBYE_ACCENT)


async def setup(bot):
    await bot.add_cog(WelcomerCog(bot))