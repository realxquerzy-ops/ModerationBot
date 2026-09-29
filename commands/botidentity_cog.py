import asyncio
import base64
import logging

import discord
from discord import app_commands
from discord.ext import commands

MAX_NICK_LEN = 32
MAX_AVATAR_BYTES = 1 << 20
INJECTION_MARKERS = ("@everyone", "@here", "<@")


def _decode_avatar(avatar):
    if not avatar:
        return None
    try:
        return base64.b64decode(avatar)
    except Exception:
        return None


class BotIdentityCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger("modbot.identity")
        self.identities = {}

    def _require_manage(self, interaction) -> bool:
        return bool(interaction.user.guild_permissions.manage_guild)

    def _validate_nick(self, guild, nick):
        nick = nick.strip() if nick else ""
        if not nick:
            return "Nickname can't be empty."
        if len(nick) > MAX_NICK_LEN:
            return f"Nicknames must be **{MAX_NICK_LEN}** characters or fewer (you used {len(nick)})."
        if any(ord(c) < 32 for c in nick):
            return "That nickname contains invalid control characters."
        low = nick.lower()
        for marker in INJECTION_MARKERS:
            if marker in low:
                return "That nickname contains **mention syntax** (`@everyone`, `@here`, or a user ID), which is not allowed."
        for m in guild.members:
            if m.id == self.bot.user.id:
                continue
            display = (m.display_name or "").strip().lower()
            if display and display == low:
                return (
                    "That name matches a real member of this server, which could get "
                    "the bot reported for impersonation. Choose something else."
                )
        return None

    async def _apply_to_guild(self, guild, identity):
        me = guild.me
        if me is None:
            return
        kwargs = {}
        if identity.get("nick") is not None:
            kwargs["nick"] = identity["nick"]
        avatar = _decode_avatar(identity.get("avatar"))
        if avatar:
            kwargs["avatar"] = avatar
        if not kwargs:
            return
        try:
            await me.edit(**kwargs)
        except discord.Forbidden:
            self.log.warning("cannot apply identity in %s (missing permission)", guild.id)
        except Exception as e:
            self.log.warning("identity apply failed in %s: %s", guild.id, e)

    async def _persist(self, guild_id, nick, avatar, set_by):
        if self.bot.db is None:
            return
        try:
            await asyncio.to_thread(self.bot.db.set_bot_identity, guild_id, nick, avatar, set_by)
        except Exception as e:
            self.log.warning("bot identity persist failed: %s", e)
        self.identities[guild_id] = {"nick": nick, "avatar": avatar, "set_by": set_by}

    async def _clear_identity(self, guild_id):
        if self.bot.db is not None:
            try:
                await asyncio.to_thread(self.bot.db.delete_bot_identity, guild_id)
            except Exception as e:
                self.log.warning("bot identity delete failed: %s", e)
        self.identities.pop(guild_id, None)

    async def _global_avatar_bytes(self):
        user = self.bot.user
        asset = user.avatar or user.default_avatar
        try:
            return await asset.read()
        except Exception:
            return None

    def _identity_embed(self, guild):
        me = guild.me
        identity = self.identities.get(guild.id) or {}
        custom_nick = identity.get("nick")
        custom_avatar = identity.get("avatar")
        set_by = identity.get("set_by")

        embed = discord.Embed(
            title=f"🤖 Identity — {guild.name}",
            color=discord.Color.blurple(),
            description=(
                "Discord always shows the built-in **BOT** tag next to this bot's name, "
                "so it stays clearly identifiable as an automated bot in every server."
            ),
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        if custom_nick and me.nick == custom_nick:
            embed.add_field(name="Server name", value=f"`{custom_nick}`", inline=True)
        else:
            embed.add_field(name="Server name", value="(server default)", inline=True)
        embed.add_field(name="Global name", value=f"`{self.bot.user.name}`", inline=True)

        if custom_avatar:
            embed.add_field(name="Server picture", value="custom", inline=True)
        else:
            embed.add_field(name="Server picture", value="(default)", inline=True)

        if set_by:
            setter = guild.get_member(int(set_by))
            who = setter.mention if setter else f"<@{set_by}>"
            embed.add_field(name="Changed by", value=who, inline=True)
        return embed

    botidentity = app_commands.Group(
        name="botidentity",
        description="Customize this server's bot name and profile picture.",
    )

    @botidentity.command(name="show", description="Show the bot's global and per-server identity.")
    @discord.app_commands.guild_only()
    async def show(self, interaction: discord.Interaction):
        if not self._require_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=self._identity_embed(interaction.guild), ephemeral=True
        )

    @botidentity.command(name="set", description="Set the bot's nickname in this server.")
    @discord.app_commands.describe(name="The nickname to show for the bot in this server")
    @discord.app_commands.guild_only()
    async def set_name(self, interaction: discord.Interaction, name: str):
        if not self._require_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission.", ephemeral=True
            )
            return
        me = interaction.guild.me
        if not me.guild_permissions.manage_nicknames:
            await interaction.response.send_message(
                "❌ I need the **Manage Nicknames** permission to change my own nickname here.",
                ephemeral=True,
            )
            return
        err = self._validate_nick(interaction.guild, name)
        if err:
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return
        nick = name.strip()
        await interaction.response.defer(ephemeral=True)
        try:
            await me.edit(nick=nick)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I couldn't change my nickname — missing **Manage Nicknames** permission.",
                ephemeral=True,
            )
            return
        except Exception as e:
            await interaction.followup.send(
                f"❌ Could not change the nickname: {e}", ephemeral=True
            )
            return
        previous = self.identities.get(interaction.guild.id) or {}
        await self._persist(
            interaction.guild.id,
            nick,
            previous.get("avatar"),
            interaction.user.id,
        )
        embed = discord.Embed(
            title="✅ Bot name updated",
            description=(
                f"This bot now appears as **`{nick}`** in this server.\n"
                "Discord still shows the **BOT** tag next to it, so it stays clearly a bot."
            ),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @botidentity.command(name="picture", description="Set the bot's profile picture in this server.")
    @discord.app_commands.describe(picture="Image to use as this server's bot profile picture")
    @discord.app_commands.guild_only()
    async def set_picture(self, interaction: discord.Interaction, picture: discord.Attachment):
        if not self._require_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission.", ephemeral=True
            )
            return
        if picture.size > MAX_AVATAR_BYTES:
            await interaction.response.send_message(
                f"❌ That image is too large (`{picture.size:,}` bytes). Keep it under **1 MB**.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            data = await picture.read()
            await interaction.guild.me.edit(avatar=data)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I couldn't change my server profile picture — missing permission.",
                ephemeral=True,
            )
            return
        except Exception as e:
            await interaction.followup.send(
                f"❌ Could not set the profile picture: {e}", ephemeral=True
            )
            return
        previous = self.identities.get(interaction.guild.id) or {}
        avatar_b64 = base64.b64encode(data).decode("ascii")
        await self._persist(
            interaction.guild.id,
            previous.get("nick"),
            avatar_b64,
            interaction.user.id,
        )
        embed = discord.Embed(
            title="✅ Bot profile picture updated",
            description=(
                "This server's profile picture for the bot has been changed.\n"
                "Discord still shows the **BOT** tag next to it, so it stays clearly a bot."
            ),
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=picture.url)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @botidentity.command(name="reset", description="Revert the bot's server name/picture to the default.")
    @discord.app_commands.describe(what="What to reset")
    @discord.app_commands.choices(
        what=[
            app_commands.Choice(name="Nickname only", value="name"),
            app_commands.Choice(name="Profile picture only", value="picture"),
            app_commands.Choice(name="Both", value="all"),
        ]
    )
    @discord.app_commands.guild_only()
    async def reset(self, interaction: discord.Interaction, what: str):
        if not self._require_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission.", ephemeral=True
            )
            return
        current = self.identities.get(interaction.guild.id) or {}
        has_nick = current.get("nick") is not None
        has_avatar = bool(current.get("avatar"))
        if what == "all":
            target_has = has_nick or has_avatar
        elif what == "name":
            target_has = has_nick
        else:
            target_has = has_avatar
        if not target_has:
            await interaction.response.send_message(
                "❌ There is nothing customized to reset.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        me = interaction.guild.me
        kwargs = {}
        if what in ("name", "all") and has_nick:
            kwargs["nick"] = None
        if what in ("picture", "all") and has_avatar:
            global_avatar = await self._global_avatar_bytes()
            if global_avatar:
                kwargs["avatar"] = global_avatar
        try:
            await me.edit(**kwargs)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I couldn't reset my identity — missing **Manage Nicknames** permission.",
                ephemeral=True,
            )
            return
        except Exception as e:
            await interaction.followup.send(
                f"❌ Could not reset the identity: {e}", ephemeral=True
            )
            return
        new_nick = None if what in ("name", "all") else current.get("nick")
        new_avatar = None if what in ("picture", "all") else current.get("avatar")
        if new_nick is None and new_avatar is None:
            await self._clear_identity(interaction.guild.id)
        else:
            await self._persist(interaction.guild.id, new_nick, new_avatar, interaction.user.id)
        await interaction.followup.send(
            f"✅ Reset **{what}** — the bot is back to its default identity here.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_ready(self):
        if self.bot.db is None:
            return
        try:
            self.identities = await asyncio.to_thread(self.bot.db.get_all_bot_identities)
        except Exception as e:
            self.log.warning("failed to load bot identities: %s", e)
            self.identities = {}
        await asyncio.sleep(5)
        for guild in self.bot.guilds:
            identity = self.identities.get(guild.id)
            if identity:
                await self._apply_to_guild(guild, identity)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        identity = self.identities.get(guild.id)
        if identity:
            await self._apply_to_guild(guild, identity)


async def setup(bot):
    await bot.add_cog(BotIdentityCog(bot))