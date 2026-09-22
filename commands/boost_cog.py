import datetime as _dt

import discord
from discord import app_commands
from discord.ext import commands

import db
from .components.boost_panel import BOOST_START_DEFAULT, BOOST_END_DEFAULT, ROLE_UNSET

import logging

logger = logging.getLogger("modbot.boost")


class BoostCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger("modbot.boost")
        self._panel_message_cache: dict[int, int] = {}

    def _require_manage(self, interaction) -> bool:
        return bool(interaction.user.guild_permissions.manage_guild)

    def _serialize_history(self, rows):
        out = []
        for row in rows:
            user_id, event_type, occurred = int(row[0]), row[1], row[2]
            icon = "🟢" if event_type == "start" else "🔴"
            out.append(f"{icon} <@{user_id}> `{event_type}` · <t:{int(occurred.timestamp())}:R>")
        return "\n".join(out) if out else "None"

    async def _render_panel(self, guild: discord.Guild, channel: discord.TextChannel):
        settings = self.bot.boost_settings.get(guild.id) or {}
        embed = discord.Embed(
            title="Server Boosts",
description=(
            "This server is boosted! Thanks to everyone who keeps the perks alive."
            if guild.premium_subscription_count > 0
            else "This server has no boosts yet. Boost to unlock perks!"
        ),
            color=discord.Color.from_str("#f47fff"),
        )
        embed.add_field(
            name="Server Boosts",
            value=str(guild.premium_subscription_count),
            inline=True,
        )
        boosters = sorted(
            (m for m in guild.members if m.premium_since is not None),
            key=lambda m: (m.premium_since or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)),
            reverse=True,
        )
        embed.add_field(
            name="Boosters",
            value="\n".join(m.mention for m in boosters[:20]) or "No boosters",
            inline=True,
        )
        role_id = settings.get("boost_role_id")
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            role = guild.premium_subscriber_role
        embed.add_field(
            name="Boost Role",
            value=role.mention if role is not None else ROLE_UNSET,
            inline=True,
        )
        history = []
        if self.bot.db is not None:
            history = self.bot.db.get_recent_boost_history(guild.id, 8)
        embed.add_field(
            name="Recent Boost History",
            value=self._serialize_history(history),
            inline=False,
        )
        embed.set_footer(text=f"Unique boosters: {len(set(m.id for m in boosters))}")
        cached = self._panel_message_cache.get(guild.id)
        if cached:
            try:
                msg = await channel.fetch_message(cached)
                await msg.edit(embed=embed)
                return msg
            except Exception:
                pass
        try:
            msgs = [m async for m in channel.history(limit=30) if m.author.id == self.bot.user.id]
        except Exception:
            msgs = []
        for m in msgs:
            if m.embeds and m.embeds[0].title == "Server Boosts":
                await m.edit(embed=embed)
                self._panel_message_cache[guild.id] = m.id
                return m
        msg = await channel.send(embed=embed)
        self._panel_message_cache[guild.id] = msg.id
        return msg

    @app_commands.command(
        name="boostchannel",
        description="Set the channel where the Server Boosts panel + boost messages are posted.",
    )
    @app_commands.describe(channel="Channel to publish the boost panel / messages in")
    @app_commands.guild_only()
    async def boostchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not self._require_manage(interaction):
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
            self.bot.db.set_boost_settings(
                guild_id, channel_id=channel.id
            )
        await self._render_panel(interaction.guild, channel)
        await interaction.followup.send(
            f"✅ Boost panel posted in {channel.mention}. It stays in sync with boosts automatically.",
            ephemeral=True,
        )

    @app_commands.command(
        name="boostrole",
        description="Set the boost role shown on the Server Boosts panel.",
    )
    @app_commands.describe(role="Role displayed as the server's booster role")
    @app_commands.guild_only()
    async def boostrole(self, interaction: discord.Interaction, role: discord.Role):
        if not self._require_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        settings = self.bot.boost_settings.get(guild_id) or {}
        settings["boost_role_id"] = role.id
        self.bot.boost_settings[guild_id] = settings
        if self.bot.db is not None:
            try:
                self.bot.db.set_boost_role(guild_id, role.id)
            except Exception as e:
                self.log.warning("boost role set failed: %s", e)
        channel = interaction.guild.get_channel(settings.get("channel_id")) if settings.get("channel_id") else None
        if channel is not None:
            try:
                await self._render_panel(interaction.guild, channel)
            except Exception as e:
                self.log.warning("boost panel refresh after role set failed: %s", e)
        await interaction.followup.send(
            f"✅ Boost role set to {role.mention}.", ephemeral=True
        )

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.premium_since == after.premium_since:
            return
        guild = after.guild
        settings = self.bot.boost_settings.get(guild.id) or {}
        channel = guild.get_channel(settings.get("channel_id"))
        if channel is None:
            return
        boost_started = before.premium_since is None and after.premium_since is not None
        event = "start" if boost_started else "end"
        if self.bot.db is not None:
            try:
                self.bot.db.add_boost_history(guild.id, after.id, event)
            except Exception as e:
                self.log.warning("boost history insert failed: %s", e)
        try:
            await self._render_panel(guild, channel)
        except Exception as e:
            self.log.warning("boost panel refresh failed: %s", e)
        template = settings.get(
            "boost_start_message" if boost_started else "boost_end_message"
        ) or (BOOST_START_DEFAULT if boost_started else BOOST_END_DEFAULT)
        text = template.replace("{user}", after.mention)
        try:
            await channel.send(text)
        except Exception as e:
            self.log.warning("boost message send failed in %s: %s", guild.id, e)


async def setup(bot):
    await bot.add_cog(BoostCog(bot))