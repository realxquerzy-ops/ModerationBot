import datetime
import logging

import discord
from discord.ext import commands


class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.log = logging.getLogger("modbot.moderation")

    def _can_moderate(self, interaction, member):
        if member == interaction.guild.owner:
            return False, "You cannot moderate the server owner."
        if member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
            return False, "You cannot moderate someone with an equal or higher role than you."
        if member.top_role >= interaction.guild.me.top_role:
            return False, "My role is too low to moderate this member."
        return True, ""

    async def _send_dm(self, member, lines):
        try:
            await member.send("\n".join(lines))
            return True
        except Exception:
            return False

    async def _reply(self, interaction, content, view=None):
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)

    @discord.app_commands.command(name="ban", description="Ban a member from the server (sends a DM notification)")
    @discord.app_commands.describe(
        member="The member to ban",
        reason="Reason for the ban (shown in the DM)",
        delete_days="Delete message history for this many days (0-7)",
    )
    @discord.app_commands.checks.has_permissions(ban_members=True)
    @discord.app_commands.guild_only()
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str, delete_days: int = 0):
        await interaction.response.defer(ephemeral=True)

        ok, err = self._can_moderate(interaction, member)
        if not ok:
            await interaction.followup.send(f"❌ {err}", ephemeral=True)
            return

        delete_days = max(0, min(7, delete_days))

        dm_sent = await self._send_dm(member, [
            f"🚫 You have been **banned** from **{interaction.guild.name}**.",
            f"👤 Moderator: {interaction.user.mention}",
            f"📝 Reason: {reason}",
        ])

        try:
            await member.ban(reason=f"Banned by {interaction.user}: {reason}", delete_message_days=delete_days)
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have enough permissions to ban this member.", ephemeral=True)
            return

        dm_note = " 📩 DM sent." if dm_sent else " ⚠️ DM could not be sent (user has DMs closed)."
        await interaction.followup.send(
            f"✅ **{member}** has been banned.{dm_note}\n📝 Reason: {reason}",
            ephemeral=True,
        )
        self.log.info("BAN %s by %s in %s (%s) DM=%s", member, interaction.user, interaction.guild, reason, dm_sent)

    @ban.error
    async def ban_error(self, interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            await self._reply(interaction, "❌ You need the **Ban Members** permission to use this.")

    @discord.app_commands.command(name="mute", description="Temporarily mute a member (timeout)")
    @discord.app_commands.describe(
        member="The member to mute",
        minutes="Duration in minutes",
        reason="Reason for the mute (shown in the DM)",
    )
    @discord.app_commands.checks.has_permissions(moderate_members=True)
    @discord.app_commands.guild_only()
    async def mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)

        ok, err = self._can_moderate(interaction, member)
        if not ok:
            await interaction.followup.send(f"❌ {err}", ephemeral=True)
            return

        minutes = max(1, min(minutes, 60 * 24 * 27))
        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)

        dm_sent = await self._send_dm(member, [
            f"🔇 You have been **muted** in **{interaction.guild.name}**.",
            f"⏱️ Duration: **{minutes} minutes**",
            f"👤 Moderator: {interaction.user.mention}",
            f"📝 Reason: {reason}",
        ])

        try:
            await member.timeout(until, reason=f"Muted by {interaction.user}: {reason}")
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have enough permissions to mute this member.", ephemeral=True)
            return

        dm_note = " 📩 DM sent." if dm_sent else " ⚠️ DM could not be sent (user has DMs closed)."
        await interaction.followup.send(
            f"✅ **{member}** has been muted ({minutes} min).{dm_note}\n📝 Reason: {reason}",
            ephemeral=True,
        )
        self.log.info("MUTE %s for %s min by %s in %s (%s) DM=%s", member, minutes, interaction.user, interaction.guild, reason, dm_sent)

    @mute.error
    async def mute_error(self, interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            await self._reply(interaction, "❌ You need the **Moderate Members** permission to use this.")

    @discord.app_commands.command(name="unban", description="Unban a user")
    @discord.app_commands.describe(
        user_id="The ID of the user to unban",
        reason="Reason",
    )
    @discord.app_commands.checks.has_permissions(ban_members=True)
    @discord.app_commands.guild_only()
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        try:
            user = await self.bot.fetch_user(int(user_id))
        except Exception:
            await interaction.followup.send("❌ Invalid user ID.", ephemeral=True)
            return
        try:
            await interaction.guild.unban(user, reason=f"Unbanned by {interaction.user}: {reason}")
        except discord.NotFound:
            await interaction.followup.send("❌ This user is not banned.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have enough permissions.", ephemeral=True)
            return
        await interaction.followup.send(f"✅ **{user}** has been unbanned.", ephemeral=True)

    @unban.error
    async def unban_error(self, interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            await self._reply(interaction, "❌ You need the **Ban Members** permission to use this.")


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))