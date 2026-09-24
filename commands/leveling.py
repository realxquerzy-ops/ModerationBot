import asyncio
import io
import time
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands

from .leveling_image import render_level_card, render_leaderboard


class LevelingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_message_xp = {}
        self.voice_sessions = {}
        self._avatar_cache = {}
        self._session = None

    def xp_for_level(self, level):
        return 200 * level

    def compute_level(self, total_xp):
        level = 1
        remaining = int(total_xp)
        while remaining >= self.xp_for_level(level):
            remaining -= self.xp_for_level(level)
            level += 1
        return level

    def level_progress(self, total_xp):
        level = 1
        remaining = int(total_xp)
        need = self.xp_for_level(level)
        while remaining >= need:
            remaining -= need
            level += 1
            need = self.xp_for_level(level)
        return level, remaining, need

    async def add_xp(self, guild_id, user_id, amount, messages=0, voice_minutes=0, channel=None):
        rec = self.bot.db.get_leveling(guild_id, user_id)
        if rec is None:
            xp, old_level = 0, 1
            total_messages, voice_m, commands = 0, 0, 0
        else:
            xp, old_level = rec["xp"], rec["level"]
            total_messages, voice_m, commands = rec["total_messages"], rec["voice_minutes"], rec["commands_used"]

        new_xp = xp + amount
        new_level = self.compute_level(new_xp)
        now = datetime.now(timezone.utc)
        self.bot.db.save_leveling(
            guild_id, user_id, new_xp, new_level,
            total_messages + messages, voice_m + voice_minutes, commands, now,
        )

        if new_level <= old_level:
            return
        await self._handle_level_ups(guild_id, user_id, old_level, new_level, channel)

    async def _handle_level_ups(self, guild_id, user_id, old_level, new_level, channel):
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None

        rewards = self.bot.db.get_level_rewards(guild_id)
        for lvl in range(old_level + 1, new_level + 1):
            role_id = rewards.get(lvl)
            if role_id and member:
                role = guild.get_role(role_id)
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role, reason=f"Level up: reached level {lvl}")
                    except Exception:
                        pass

        settings = self.bot.db.get_level_settings(guild_id)
        text = settings["levelup_text"] or "🎉 {user} leveled up to **Level {level}** in {server}!"
        mention = member.mention if member else f"<@{user_id}>"
        formatted = (
            text.replace("{user}", mention)
            .replace("{level}", str(new_level))
            .replace("{server}", guild.name if guild else "this server")
        )

        target = guild.get_channel(settings["announce_channel"]) if guild and settings["announce_channel"] else None
        if target is None and channel is not None:
            target = channel
        if target is not None:
            try:
                await target.send(formatted)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        if not message.content and not message.attachments:
            return

        try:
            settings = self.bot.db.get_level_settings(message.guild.id)
        except Exception:
            return
        if not settings["enabled"]:
            return

        key = (message.guild.id, message.author.id)
        now = time.time()
        last = self.last_message_xp.get(key, 0)
        if now - last < settings["cooldown_seconds"]:
            return
        self.last_message_xp[key] = now

        try:
            await self.add_xp(
                message.guild.id, message.author.id,
                settings["xp_per_message"], messages=1, channel=message.channel,
            )
        except Exception as e:
            print(f"[leveling] on_message error: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot or not member.guild:
            return

        try:
            settings = self.bot.db.get_level_settings(member.guild.id)
        except Exception:
            return
        if not settings["enabled"]:
            return

        key = (member.guild.id, member.id)
        if before.channel == after.channel:
            return

        start = self.voice_sessions.pop(key, None)
        if start is not None:
            minutes = int((time.time() - start) // 60)
            if minutes > 0:
                try:
                    await self.add_xp(
                        member.guild.id, member.id,
                        minutes * settings["xp_per_voice"], voice_minutes=minutes, channel=None,
                    )
                except Exception as e:
                    print(f"[leveling] voice xp error: {e}")

        if after.channel is not None:
            self.voice_sessions[key] = time.time()

    async def cog_load(self):
        self._loop_started = False

    async def cog_unload(self):
        if self._session is not None:
            await self._session.close()
            self._session = None

    @commands.Cog.listener()
    async def on_ready(self):
        if self._loop_started:
            return
        self._loop_started = True
        asyncio.create_task(self._first_place_loop())

    async def _first_place_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._refresh_first_place()
            except Exception as e:
                print(f"[leveling] first place refresh error: {e}")
            await asyncio.sleep(3600)

    async def _refresh_first_place(self):
        for guild in self.bot.guilds:
            try:
                settings = self.bot.db.get_level_settings(guild.id)
            except Exception:
                continue
            role_id = settings["first_place_role"]
            if not role_id:
                continue
            role = guild.get_role(role_id)
            if not role:
                continue

            top = self.bot.db.get_leveling_top_user(guild.id)
            for m in list(role.members):
                if m.id != top:
                    try:
                        await m.remove_roles(role, reason="First place on leaderboard changed")
                    except Exception:
                        pass
            if top is not None:
                member = guild.get_member(top)
                if member and role not in member.roles:
                    try:
                        await member.add_roles(role, reason="Top XP in this server")
                    except Exception:
                        pass

    lvl_group = discord.app_commands.Group(name="leveling", description="Server leveling: XP, ranks, rewards and settings")

    def _is_admin(self, interaction):
        return interaction.user.guild_permissions.manage_guild or interaction.user.id in getattr(self.bot, "whitelisted_users", ())

    async def _settings_embed(self, guild):
        s = self.bot.db.get_level_settings(guild.id)
        rewards = self.bot.db.get_level_rewards(guild.id)
        channel_name = guild.get_channel(s["announce_channel"]) if s["announce_channel"] else None
        role_name = guild.get_role(s["first_place_role"]) if s["first_place_role"] else None

        reward_lines = "\n".join(
            f"• **Level {lvl}:** <@&{role_id}>" for lvl, role_id in sorted(rewards.items())
        ) or "None set"

        embed = discord.Embed(
            title=f"⚙️ Leveling Settings — {guild.name}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="✅ Enabled", value="Yes" if s["enabled"] else "No", inline=True)
        embed.add_field(name="📣 Announce Channel", value=channel_name.mention if channel_name else "`Auto (message channel)`", inline=True)
        embed.add_field(name="🥇 First Place Role", value=role_name.mention if role_name else "`Not set`", inline=True)
        embed.add_field(name="🕐 XP / message", value=f"`{s['xp_per_message']}`", inline=True)
        embed.add_field(name="⏱️ Cooldown", value=f"`{s['cooldown_seconds']}s`", inline=True)
        embed.add_field(name="🔊 XP / voice min", value=f"`{s['xp_per_voice']}`", inline=True)
        embed.add_field(name="🎖️ Level Rewards", value=reward_lines, inline=False)
        embed.add_field(
            name="💬 Level Up Text",
            value=f"`{s['levelup_text']}`" if s["levelup_text"] else "`🎉 {user} leveled up to **Level {level}** in {server}!`",
            inline=False,
        )
        return embed

    @discord.app_commands.command(name="level", description="View your level, XP and rank in this server as an image")
    @discord.app_commands.describe(member="The member to view (defaults to you)")
    @discord.app_commands.allowed_installs(guilds=True, users=False)
    @discord.app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def level(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send("This command can only be used in a server!", ephemeral=True)
            return
        target = member or interaction.user
        rec = self.bot.db.get_leveling(interaction.guild.id, target.id)
        if rec is None:
            level, into, need = 1, 0, self.xp_for_level(1)
            xp, messages = 0, 0
        else:
            xp = rec["xp"]
            messages = rec["total_messages"]
            level, into, need = self.level_progress(xp)

        rank, total = self.bot.db.get_leveling_rank(interaction.guild.id, target.id)
        avatar = await self._avatar_pil(target)
        buf = render_level_card(
            target.display_name, interaction.guild.name, avatar,
            level, rank, total, into, need, xp, messages,
        )
        file = discord.File(buf, filename="level.png")
        await interaction.followup.send(file=file)

    @lvl_group.command(name="test", description="Test the level up message and level card (Manage Server)")
    async def leveling_test(self, interaction: discord.Interaction):
        if not self._is_admin(interaction):
            await interaction.response.send_message(
                "❌ You need the **Manage Server** permission to use this.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        settings = self.bot.db.get_level_settings(guild.id)
        member = interaction.user

        rec = self.bot.db.get_leveling(guild.id, member.id)
        if rec is None:
            level, into, need = 1, 0, self.xp_for_level(1)
            xp, messages = 0, 0
        else:
            xp, messages = rec["xp"], rec["total_messages"]
            level, into, need = self.level_progress(xp)

        text = settings["levelup_text"] or "🎉 {user} leveled up to **Level {level}** in {server}!"
        formatted = (
            text.replace("{user}", member.mention)
            .replace("{level}", str(level))
            .replace("{server}", guild.name)
        )

        target = guild.get_channel(settings["announce_channel"]) if settings["announce_channel"] else interaction.channel
        where = f"<#{target.id}>" if target else "`no channel`"
        posted = False
        if target is not None:
            try:
                await target.send(formatted)
                posted = True
            except Exception as e:
                where = f"send failed: {e}"

        rank, total = self.bot.db.get_leveling_rank(guild.id, member.id)
        avatar = await self._avatar_pil(member)
        buf = render_level_card(
            member.display_name, guild.name, avatar,
            level, rank, total, into, need, int(xp), messages,
        )
        file = discord.File(buf, filename="level.png")

        embed = discord.Embed(title="🧪 Leveling test", color=discord.Color.blurple())
        embed.add_field(name="✅ Enabled", value="Yes" if settings["enabled"] else "No", inline=True)
        embed.add_field(name="📣 Announce channel", value=where, inline=True)
        embed.add_field(name="💬 Message sent", value="Yes" if posted else "No", inline=True)
        embed.add_field(name="🧑 Level", value=f"Level {level} (`{int(xp)} XP`)", inline=True)
        embed.add_field(name="💬 Level up text", value=f"`{text}`", inline=False)
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    @lvl_group.command(name="top", description="View the server level leaderboard")
    async def leveling_top(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server!", ephemeral=True)
            return
        guild = interaction.guild
        rows = self.bot.db.get_leveling_leaderboard(guild.id, limit=10)
        if not rows:
            await interaction.followup.send("📭 No leveling data yet. Start chatting to earn XP!")
            return
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, (user_id, xp, level) in enumerate(rows, start=1):
            member = guild.get_member(user_id)
            name = member.display_name if member else f"<@{user_id}>"
            badge = medals[i - 1] if i <= 3 else f"**{i}.**"
            lines.append(f"{badge} {name} — **Level {level}** (`{int(xp)} XP`)")
        embed = discord.Embed(
            title=f"🏆 Level Leaderboard — {guild.name}",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Top XP earns the first place role (if configured)!")
        await interaction.followup.send(embed=embed)

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

    async def _lb_entries(self, guild, user_ids):
        entries = []
        for user_id in user_ids:
            member = guild.get_member(user_id)
            if member is not None:
                rec = self.bot.db.get_leveling(guild.id, user_id)
                if rec is None:
                    continue
                level, into, need = self.level_progress(rec["xp"])
                entries.append((member.display_name, await self._avatar_pil(member), level, into, need, int(rec["xp"])))
        return entries

    @discord.app_commands.command(name="lb", description="View the server leveling leaderboard as an image")
    @discord.app_commands.allowed_installs(guilds=True, users=False)
    @discord.app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def lb(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send("This command can only be used in a server!", ephemeral=True)
            return
        guild = interaction.guild
        rows = self.bot.db.get_leveling_leaderboard(guild.id, limit=10)
        if not rows:
            await interaction.followup.send("No leveling data yet. Start chatting to earn XP!", ephemeral=True)
            return

        user_ids = [r[0] for r in rows]
        entries = await self._lb_entries(guild, user_ids)
        if not entries:
            await interaction.followup.send("No leveling data yet. Start chatting to earn XP!", ephemeral=True)
            return

        buf = render_leaderboard(entries)
        file = discord.File(buf, filename="leaderboard.png")
        await interaction.followup.send(content=f"# **{guild.name}**", file=file)

    @lvl_group.command(name="settings", description="View this server's leveling settings (Manage Server)")
    async def leveling_settings(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server!", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.followup.send("❌ You need **Manage Server** permission for this.", ephemeral=True)
            return
        await interaction.followup.send(embed=await self._settings_embed(interaction.guild))

    @lvl_group.command(name="channel", description="Set the channel where level up messages are sent (Manage Server)")
    @discord.app_commands.describe(channel="The channel to send level up messages in")
    async def leveling_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server!", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.followup.send("❌ You need **Manage Server** permission for this.", ephemeral=True)
            return
        self.bot.db.save_level_settings(interaction.guild.id, announce_channel=channel.id)
        await interaction.followup.send(f"✅ Level up messages will be sent to {channel.mention}.")

    @lvl_group.command(name="text", description="Set custom level up message text (Manage Server)")
    @discord.app_commands.describe(text='Level up message. Use {user}, {level}, {server}. Pass "default" to reset')
    async def leveling_text(self, interaction: discord.Interaction, text: str):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server!", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.followup.send("❌ You need **Manage Server** permission for this.", ephemeral=True)
            return
        if text.strip().lower() == "default":
            self.bot.db.save_level_settings(interaction.guild.id, levelup_text=None)
            await interaction.followup.send("✅ Level up message reset to the default.")
        else:
            self.bot.db.save_level_settings(interaction.guild.id, levelup_text=text)
            await interaction.followup.send(f"✅ Level up message set to: `{text}`")

    @lvl_group.command(name="xp", description="Set XP earned per message (Manage Server)")
    @discord.app_commands.describe(amount="XP per message")
    async def leveling_xp(self, interaction: discord.Interaction, amount: int):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server!", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.followup.send("❌ You need **Manage Server** permission for this.", ephemeral=True)
            return
        if amount < 1 or amount > 10000:
            await interaction.followup.send("❌ XP per message must be between **1** and **10000**.", ephemeral=True)
            return
        self.bot.db.save_level_settings(interaction.guild.id, xp_per_message=amount)
        await interaction.followup.send(f"✅ XP per message set to `{amount}`.")

    @lvl_group.command(name="cooldown", description="Set the XP cooldown between messages (Manage Server)")
    @discord.app_commands.describe(seconds="Seconds between XP gains")
    async def leveling_cooldown(self, interaction: discord.Interaction, seconds: int):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server!", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.followup.send("❌ You need **Manage Server** permission for this.", ephemeral=True)
            return
        if seconds < 0 or seconds > 86400:
            await interaction.followup.send("❌ Cooldown must be between **0** and **86400** seconds.", ephemeral=True)
            return
        self.bot.db.save_level_settings(interaction.guild.id, cooldown_seconds=seconds)
        await interaction.followup.send(f"✅ XP cooldown set to `{seconds}` seconds.")

    @lvl_group.command(name="toggle", description="Enable or disable leveling in this server (Manage Server)")
    @discord.app_commands.describe(enabled="True = enabled, False = disabled")
    async def leveling_toggle(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server!", ephemeral=True)
            return
        if not self._is_admin(interaction):
            await interaction.followup.send("❌ You need **Manage Server** permission for this.", ephemeral=True)
            return
        self.bot.db.save_level_settings(interaction.guild.id, enabled=enabled)
        await interaction.followup.send(f"✅ Leveling is now **{'enabled' if enabled else 'disabled'}** in this server.")

    @lvl_group.command(name="roles", description="View the configured level reward roles")
    async def leveling_roles(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server!", ephemeral=True)
            return
        embed = await self._settings_embed(interaction.guild)
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(LevelingCog(bot))