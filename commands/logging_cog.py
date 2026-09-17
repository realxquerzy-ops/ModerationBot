import asyncio
import datetime
import logging

import discord
from discord.ext import commands


PERMISSIONS = [
    "view_channel",
    "manage_channels",
    "manage_roles",
    "manage_webhooks",
    "send_messages",
    "send_messages_in_threads",
    "create_public_threads",
    "create_private_threads",
    "embed_links",
    "attach_files",
    "add_reactions",
    "external_emojis",
    "mention_everyone",
    "manage_messages",
    "read_message_history",
    "connect",
    "speak",
    "stream",
    "mute_members",
    "deafen_members",
    "move_members",
    "use_voice_activation",
    "priority_speaker",
]


def humanize_delta(delta):
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs and not days:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    if not parts:
        return "0 seconds"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def channel_kind(channel):
    if isinstance(channel, discord.TextChannel):
        return "Text"
    if isinstance(channel, discord.VoiceChannel):
        return "Voice"
    if isinstance(channel, discord.CategoryChannel):
        return "Category"
    if isinstance(channel, discord.ForumChannel):
        return "Forum"
    if isinstance(channel, discord.StageChannel):
        return "Stage"
    return "Channel"


def perm_symbol(value):
    if value is True:
        return "✅"
    if value is False:
        return "❌"
    return "⬜"


def target_label(target):
    if isinstance(target, (discord.Role, discord.Member, discord.User)):
        return target.mention
    return str(target)


class LoggingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger("modbot.logging")

    def _get_log_channel(self, guild):
        if guild is None:
            return None
        channel_id = self.bot.log_channels.get(guild.id)
        if not channel_id:
            return None
        return guild.get_channel(channel_id)

    async def _send(self, guild, embed):
        channel = self._get_log_channel(guild)
        if channel is None:
            return
        try:
            await channel.send(embed=embed)
        except Exception as e:
            self.log.warning("failed to send log: %s", e)

    def _new_embed(self, color):
        return discord.Embed(color=color, timestamp=discord.utils.utcnow())

    def _member_embed(self, member, title, color):
        embed = self._new_embed(color)
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.title = title
        return embed

    @discord.app_commands.command(
        name="logsetchannel",
        description="Set the channel where server logs are posted (admin only)",
    )
    @discord.app_commands.describe(channel="The channel to post logs in")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    @discord.app_commands.guild_only()
    async def logsetchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        self.bot.log_channels[interaction.guild_id] = channel.id
        if self.bot.db is not None:
            try:
                await asyncio.to_thread(
                    self.bot.db.set_log_channel, interaction.guild_id, channel.id
                )
            except Exception as e:
                self.log.error("failed to persist log channel: %s", e)
                await interaction.followup.send(
                    "⚠️ Set for now, but saving failed (it may reset after a restart).",
                    ephemeral=True,
                )
                return
        await interaction.followup.send(
            f"✅ Logs will now be posted in {channel.mention}.", ephemeral=True
        )

    @logsetchannel.error
    async def logsetchannel_error(self, interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ You need the **Manage Server** permission to use this.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ You need the **Manage Server** permission to use this.", ephemeral=True
                )

    @commands.Cog.listener()
    async def on_member_join(self, member):
        embed = self._member_embed(member, "Member joined", discord.Color.green())
        age = humanize_delta(discord.utils.utcnow() - member.created_at)
        count = member.guild.member_count
        joined_line = f"{member.mention} is the {ordinal(count)} to join" if count else f"{member.mention} joined"
        embed.description = f"{joined_line}\nCreated {age} ago"
        embed.set_footer(text=f"ID: {member.id}")
        await self._send(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        embed = self._member_embed(member, "Member left", discord.Color.red())
        if member.joined_at:
            joined_ago = humanize_delta(discord.utils.utcnow() - member.joined_at)
        else:
            joined_ago = "unknown"
        roles = ", ".join(r.mention for r in member.roles if not r.is_default()) or "None"
        embed.description = f"{member.mention} joined {joined_ago} ago\nRoles: {roles}"
        embed.set_footer(text=f"ID: {member.id}")
        await self._send(member.guild, embed)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        guild = after.guild

        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]

        for role in added:
            if role.is_default():
                continue
            embed = self._member_embed(after, "Role added", discord.Color.green())
            embed.description = role.mention
            embed.set_footer(text=f"ID: {after.id}")
            embed.add_field(name="Role ID", value=str(role.id), inline=True)
            await self._send(guild, embed)

        for role in removed:
            if role.is_default():
                continue
            embed = self._member_embed(after, "Role removed", discord.Color.red())
            embed.description = role.mention
            embed.set_footer(text=f"ID: {after.id}")
            embed.add_field(name="Role ID", value=str(role.id), inline=True)
            await self._send(guild, embed)

        if before.nick != after.nick:
            embed = self._member_embed(after, "Nickname changed", discord.Color.blurple())
            embed.description = (
                f"**Before:** {before.nick or before.name}\n"
                f"**After:** {after.nick or after.name}"
            )
            embed.set_footer(text=f"ID: {after.id}")
            await self._send(guild, embed)

        if before.timed_out_until != after.timed_out_until:
            embed = self._member_embed(after, "Member timeout updated", discord.Color.orange())
            if after.timed_out_until:
                delta = humanize_delta(after.timed_out_until - discord.utils.utcnow())
                embed.description = f"{after.mention} was timed out for {delta}."
            else:
                embed.description = f"{after.mention}'s timeout was removed."
            embed.set_footer(text=f"ID: {after.id}")
            await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.guild is None or message.author.bot:
            return
        if self.bot.log_channels.get(message.guild.id) == message.channel.id:
            return

        embed = self._new_embed(discord.Color.dark_red())
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.title = f"Message deleted in #{message.channel.name}"
        content = message.content.strip()
        embed.description = content[:4000] if content else "*[no cached content]*"
        if message.attachments:
            embed.add_field(
                name="Attachments",
                value="\n".join(a.filename for a in message.attachments)[:1024],
                inline=False,
            )
        embed.add_field(name="Author", value=message.author.mention, inline=True)
        embed.add_field(name="Message ID", value=str(message.id), inline=True)
        embed.set_footer(text=f"ID: {message.author.id}")
        await self._send(message.guild, embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content:
            return
        if self.bot.log_channels.get(before.guild.id) == before.channel.id:
            return

        embed = self._new_embed(discord.Color.orange())
        embed.set_author(name=str(before.author), icon_url=before.author.display_avatar.url)
        embed.title = f"Message edited in #{before.channel.name}"
        embed.description = (
            f"**Before:** {before.content[:1000] or '*[empty]*'}\n"
            f"**After:** {after.content[:1000] or '*[empty]*'}"
        )
        embed.add_field(name="Author", value=before.author.mention, inline=True)
        embed.add_field(name="Message ID", value=str(before.id), inline=True)
        embed.set_footer(text=f"ID: {before.author.id}")
        await self._send(before.guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        if isinstance(channel, discord.CategoryChannel):
            category = "None"
        else:
            category = channel.category.name if getattr(channel, "category", None) else "None"
        embed = self._new_embed(discord.Color.green())
        embed.title = f"{channel_kind(channel)} channel created"
        embed.add_field(name="Name", value=channel.name, inline=True)
        embed.add_field(name="Category", value=category, inline=True)
        embed.set_footer(text=f"Channel ID: {channel.id}")
        await self._send(channel.guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if isinstance(channel, discord.CategoryChannel):
            category = "None"
        else:
            category = channel.category.name if getattr(channel, "category", None) else "None"
        embed = self._new_embed(discord.Color.red())
        embed.title = f"{channel_kind(channel)} channel deleted"
        embed.add_field(name="Name", value=channel.name, inline=True)
        embed.add_field(name="Category", value=category, inline=True)
        embed.set_footer(text=f"Channel ID: {channel.id}")
        await self._send(channel.guild, embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        guild = after.guild
        kind = channel_kind(after)

        if before.name != after.name:
            embed = self._new_embed(discord.Color.blurple())
            embed.title = f"{kind} channel updated"
            embed.add_field(name="Name", value=f"{before.name} ➜ {after.name}", inline=False)
            embed.set_footer(text=f"Channel ID: {after.id}")
            await self._send(guild, embed)

        if before.category != after.category:
            embed = self._new_embed(discord.Color.blurple())
            embed.title = f"{kind} channel updated"
            old = before.category.name if before.category else "None"
            new = after.category.name if after.category else "None"
            embed.add_field(name="Category", value=f"{old} ➜ {new}", inline=False)
            embed.set_footer(text=f"Channel ID: {after.id}")
            await self._send(guild, embed)

        before_ow = dict(before.overwrites)
        after_ow = dict(after.overwrites)
        for target in set(before_ow) | set(after_ow):
            b = before_ow.get(target)
            a = after_ow.get(target)
            if b == a:
                continue
            if b is None:
                action = "created"
            elif a is None:
                action = "deleted"
            else:
                action = "updated"

            changes = []
            for perm in PERMISSIONS:
                bv = getattr(b, perm, None) if b is not None else None
                av = getattr(a, perm, None) if a is not None else None
                if bv != av:
                    changes.append(
                        (perm.replace("_", " ").title(), f"{perm_symbol(bv)} ➜ {perm_symbol(av)}")
                    )
            if not changes:
                continue

            embed = self._new_embed(discord.Color.blurple())
            embed.title = f"{kind} channel updated"
            embed.description = (
                f"Overwrites for {target_label(target)} in {after.mention} {action}"
            )
            for i, (name, value) in enumerate(changes[:24]):
                embed.add_field(name=name, value=value, inline=True)
            if len(changes) > 24:
                embed.add_field(name="…and more", value=f"+{len(changes) - 24} more", inline=False)
            embed.set_footer(text=f"Channel ID: {after.id}")
            await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        before_ids = {e.id for e in before}
        after_ids = {e.id for e in after}

        for emoji in after:
            if emoji.id in before_ids:
                continue
            embed = self._new_embed(discord.Color.green())
            embed.title = "Emoji created"
            embed.description = f"{emoji} `:{emoji.name}:`"
            embed.set_footer(text=f"Emoji ID: {emoji.id}")
            await self._send(guild, embed)

        for emoji in before:
            if emoji.id in after_ids:
                continue
            embed = self._new_embed(discord.Color.red())
            embed.title = "Emoji deleted"
            embed.description = f"`:{emoji.name}:`"
            embed.set_footer(text=f"Emoji ID: {emoji.id}")
            await self._send(guild, embed)

        for old in before:
            new = next((e for e in after if e.id == old.id), None)
            if new is not None and new.name != old.name:
                embed = self._new_embed(discord.Color.blurple())
                embed.title = "Emoji renamed"
                embed.description = f"`:{old.name}:` ➜ `:{new.name}:`"
                embed.set_footer(text=f"Emoji ID: {new.id}")
                await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        embed = self._new_embed(discord.Color.green())
        embed.title = "Role created"
        embed.description = role.mention
        embed.set_footer(text=f"Role ID: {role.id}")
        await self._send(role.guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        embed = self._new_embed(discord.Color.red())
        embed.title = "Role deleted"
        embed.description = f"`@{role.name}`"
        embed.set_footer(text=f"Role ID: {role.id}")
        await self._send(role.guild, embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        embed = self._new_embed(discord.Color.dark_red())
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.title = "Member banned"
        embed.description = f"{user.mention} was banned."
        embed.set_footer(text=f"ID: {user.id}")
        await self._send(guild, embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        embed = self._new_embed(discord.Color.green())
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.title = "Member unbanned"
        embed.description = f"{user.mention} was unbanned."
        embed.set_footer(text=f"ID: {user.id}")
        await self._send(guild, embed)


async def setup(bot):
    await bot.add_cog(LoggingCog(bot))
