import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands


class ReactionRoleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger("modbot.reactionrole")

    _CUSTOM_RE = re.compile(r"<((?P<anim>a)?:(?P<name>[^:>]+):(?P<id>\d+))>")

    def _require_manage(self, interaction) -> bool:
        return bool(interaction.user.guild_permissions.manage_guild)

    def _emoji_token(self, raw: str) -> str:
        s = raw.strip()
        m = self._CUSTOM_RE.match(s)
        if m:
            anim = "a" if m.group("anim") else ""
            return f"custom:{m.group('id')}:{m.group('name')}:{anim}"
        if re.fullmatch(r":[^:\s>]+:", s):
            try:
                import emoji

                resolved = emoji.emojize(s)
                if resolved != s:
                    return resolved
            except Exception:
                pass
        return s

    def _partial_emoji(self, token: str):
        if token.startswith("custom:"):
            _, eid, name, anim = token.split(":", 3)
            return discord.PartialEmoji(name=name, id=int(eid), animated=(anim == "a"))
        if re.fullmatch(r":[^:\s>]+:", token):
            try:
                import emoji

                resolved = emoji.emojize(token)
                if resolved != token:
                    return resolved
            except Exception:
                pass
        return token

    @staticmethod
    def _matches(token: str, emoji) -> bool:
        did = getattr(emoji, "id", None)
        if did is not None:
            if not token.startswith("custom:"):
                return False
            return int(token.split(":", 3)[1]) == did
        if token.startswith("custom:"):
            return False
        if re.fullmatch(r":[^:\s>]+:", token):
            try:
                import emoji as _emoji

                token = _emoji.emojize(token)
            except Exception:
                pass
        return str(emoji) == token

    async def _sync_reactions(self, channel, msg, bindings):
        for b in bindings:
            if any(self._matches(b["emoji"], r.emoji) for r in msg.reactions):
                continue
            try:
                await msg.add_reaction(self._partial_emoji(b["emoji"]))
            except Exception as e:
                self.log.warning("self-heal reaction %s on %s failed: %s", b["emoji"], msg.id, e)

    def _binding_warning(self, interaction, role, channel) -> str:
        me = interaction.guild.me
        warnings = []
        if not me.guild_permissions.manage_roles:
            warnings.append("I do not have **Manage Roles** permission, so I cannot assign roles.")
        if me.guild_permissions.manage_roles and role >= me.top_role:
            warnings.append("The role is higher than my highest role, so I cannot assign it.")
        return " ".join(warnings)

    rr = app_commands.Group(
        name="reactionrole", description="Set up reaction role messages."
    )

    @rr.command(name="setup", description="Post a message and bind a reaction to a role.")
    @app_commands.describe(
        channel="Channel to post the message in",
        message="Text of the message",
        reaction="Emoji users react with",
        role="Role granted on reaction",
    )
    @app_commands.guild_only()
    async def rr_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str,
        reaction: str,
        role: discord.Role,
    ):
        if not self._require_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        token = self._emoji_token(reaction)
        try:
            msg = await channel.send(message)
        except Exception as e:
            await interaction.followup.send(
                f"❌ Could not post the message: {e}", ephemeral=True
            )
            return
        if self.bot.db is not None:
            try:
                self.bot.db.set_reaction_role(
                    interaction.guild_id, channel.id, msg.id, token, role.id
                )
            except Exception as e:
                self.log.warning("reactionrole persist failed: %s", e)
        try:
            await msg.add_reaction(self._partial_emoji(token))
        except Exception as e:
            await interaction.followup.send(
                f"⚠️ Message posted, but the reaction could not be added "
                f"(is the emoji accessible to me?): {e}",
                ephemeral=True,
            )
            return
        warn = self._binding_warning(interaction, role, channel)
        if warn:
            await interaction.followup.send(
                f"⚠️ {warn} The binding is saved, but role assignment may not work until this is fixed.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ Reaction role set up on [this message]({msg.jump_url}). "
            f"React **{reaction}** for {role.mention}.",
            ephemeral=True,
        )

    @rr.command(name="add", description="Add another reaction/role to an existing reaction-role message.")
    @app_commands.describe(
        reaction="Emoji users react with",
        role="Role granted on reaction",
    )
    @app_commands.guild_only()
    async def rr_add(
        self,
        interaction: discord.Interaction,
        reaction: str,
        role: discord.Role,
    ):
        if not self._require_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        target = interaction.message
        if target is None:
            await interaction.followup.send(
                "❌ Reply to the target message while running this command so I know where to add it.",
                ephemeral=True,
            )
            return
        if self.bot.db is None:
            await interaction.followup.send(
                "❌ Database is not configured; reaction roles can't persist.",
                ephemeral=True,
            )
            return
        try:
            bindings = self.bot.db.get_message_reaction_roles(
                interaction.guild_id, target.channel.id, target.id
            )
        except Exception as e:
            self.log.warning("reactionrole lookup failed: %s", e)
            bindings = []
        if not bindings:
            await interaction.followup.send(
                "❌ That message has no reaction-role bindings yet. "
                "Set it up first with `/reactionrole setup`.",
                ephemeral=True,
            )
            return
        token = self._emoji_token(reaction)
        try:
            self.bot.db.set_reaction_role(
                interaction.guild_id, target.channel.id, target.id, token, role.id
            )
        except Exception as e:
            self.log.warning("reactionrole add persist failed: %s", e)
        try:
            await target.add_reaction(self._partial_emoji(token))
        except Exception as e:
            await interaction.followup.send(
                f"⚠️ Binding saved, but the reaction could not be added "
                f"(is the emoji accessible to me?): {e}",
                ephemeral=True,
            )
            return
        warn = self._binding_warning(interaction, role, target.channel)
        await interaction.followup.send(
            f"✅ Added **{reaction}** → {role.mention} on [this message]({target.jump_url})."
            + (f" ⚠️ {warn}" if warn else ""),
            ephemeral=True,
        )

    @rr.command(name="repair", description="Delete reaction messages in panel channels that have no saved bindings (orphaned).")
    @app_commands.guild_only()
    async def rr_repair(self, interaction: discord.Interaction):
        if not self._require_manage(interaction):
            await interaction.response.send_message(
                "❌ You need **Manage Server** permission.", ephemeral=True
            )
            return
        if self.bot.db is None:
            await interaction.response.send_message(
                "❌ Database is not configured.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        bindings = self.bot.db.get_reaction_roles(guild.id)
        known = {(b["channel_id"], b["message_id"]) for b in bindings}
        channels = sorted({b["channel_id"] for b in bindings})

        scanned = 0
        kept = 0
        orphans = []
        for cid in channels:
            channel = guild.get_channel(cid)
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                async for msg in channel.history(limit=200):
                    if not msg.reactions:
                        continue
                    scanned += 1
                    if (cid, msg.id) in known:
                        kept += 1
                        continue
                    orphans.append(msg)
            except Exception as e:
                self.log.warning("repair scan failed in %s: %s", cid, e)

        deleted = []
        failed = []
        for msg in orphans:
            try:
                await msg.delete()
                deleted.append(msg.id)
            except Exception as e:
                failed.append((msg.id, str(e)))

        lines = [f"Scanned {scanned} reaction message(s) in {len(channels)} panel channel(s)."]
        if kept:
            lines.append(f"Kept {kept} message(s) with saved bindings.")
        if deleted:
            lines.append(f"Deleted {len(deleted)} orphan(s): {', '.join(str(mid) for mid in deleted)}.")
        if failed:
            lines.append("Failed to delete: " + "; ".join(f"{mid} ({err})" for mid, err in failed))
        if not deleted and not failed:
            lines.append("Nothing to clean up.")
        await interaction.followup.send(
            "🧹 **Reaction role repair**\n" + "\n".join(lines), ephemeral=True
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.user_id == self.bot.user.id:
            return
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or self.bot.db is None:
            return
        try:
            bindings = self.bot.db.get_message_reaction_roles(
                guild.id, payload.channel_id, payload.message_id
            )
        except Exception:
            return
        for b in bindings:
            if not self._matches(b["emoji"], payload.emoji):
                continue
            role = guild.get_role(b["role_id"])
            member = guild.get_member(payload.user_id)
            if role is not None and member is not None and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Reaction role")
                except Exception as e:
                    self.log.warning("reaction role add failed for %s: %s", payload.user_id, e)
            break

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        if payload.user_id == self.bot.user.id:
            return
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None or self.bot.db is None:
            return
        try:
            bindings = self.bot.db.get_message_reaction_roles(
                guild.id, payload.channel_id, payload.message_id
            )
        except Exception:
            return
        for b in bindings:
            if not self._matches(b["emoji"], payload.emoji):
                continue
            role = guild.get_role(b["role_id"])
            member = guild.get_member(payload.user_id)
            if role is not None and member is not None and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Reaction role removed")
                except Exception as e:
                    self.log.warning("reaction role remove failed for %s: %s", payload.user_id, e)
            break

    @commands.Cog.listener()
    async def on_ready(self):
        if self.bot.db is None:
            return
        await asyncio.sleep(5)
        for guild in self.bot.guilds:
            try:
                bindings = self.bot.db.get_reaction_roles(guild.id)
            except Exception:
                continue
            by_msg = {}
            for b in bindings:
                by_msg.setdefault((b["channel_id"], b["message_id"]), []).append(b)
            for (cid, mid), msg_bindings in by_msg.items():
                channel = guild.get_channel(cid)
                if channel is None:
                    continue
                try:
                    msg = await channel.fetch_message(mid)
                except Exception:
                    continue
                try:
                    await self._sync_reactions(channel, msg, msg_bindings)
                except Exception as e:
                    self.log.warning("reactionrole self-heal failed on %s: %s", mid, e)


async def setup(bot):
    await bot.add_cog(ReactionRoleCog(bot))