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
            return False, "Sunucu sahibini moderasyona alamazsın."
        if member.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
            return False, "Kendi rolüne eşit veya üstü roldeki birini moderasyona alamazsın."
        if member.top_role >= interaction.guild.me.top_role:
            return False, "Botun rolü bu üyeden düşük, işlem yapamam."
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

    @discord.app_commands.command(name="ban", description="Sunucudan bir üyeyi banla (DM bildirimi gönderir)")
    @discord.app_commands.describe(
        member="Banlanacak üye",
        reason="Ban sebebi (DM'de gösterilir)",
        delete_days="Son mesaj silme süresi (gün, 0-7)",
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
            f"🚫 **{interaction.guild.name}** sunucusundan **banlandın**.",
            f"👤 Yetkili: {interaction.user.mention}",
            f"📝 Sebep: {reason}",
        ])

        try:
            await member.ban(reason=f"{interaction.user} tarafından: {reason}", delete_message_days=delete_days)
        except discord.Forbidden:
            await interaction.followup.send("❌ Botun yetkisi yetersiz (ban izni eksik veya rol düşük).", ephemeral=True)
            return

        dm_note = " 📩 DM gönderildi." if dm_sent else " ⚠️ DM gönderilemedi (kapalı olabilir)."
        await interaction.followup.send(
            f"✅ **{member}** banlandı.{dm_note}\n📝 Sebep: {reason}",
            ephemeral=True,
        )
        self.log.info("BAN %s by %s in %s (%s) DM=%s", member, interaction.user, interaction.guild, reason, dm_sent)

    @ban.error
    async def ban_error(self, interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            await self._reply(interaction, "❌ Bunun için **Ban Members** iznine ihtiyacın var.")

    @discord.app_commands.command(name="mute", description="Bir üyeyi geçici olarak sustur (timeout)")
    @discord.app_commands.describe(
        member="Susturulacak üye",
        minutes="Süre (dakika)",
        reason="Susturma sebebi (DM'de gösterilir)",
    )
    @discord.app_commands.checks.has_permissions(moderate_members=True)
    @discord.app_commands.guild_only()
    async def mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "Sebep belirtilmedi"):
        await interaction.response.defer(ephemeral=True)

        ok, err = self._can_moderate(interaction, member)
        if not ok:
            await interaction.followup.send(f"❌ {err}", ephemeral=True)
            return

        minutes = max(1, min(minutes, 60 * 24 * 27))
        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)

        dm_sent = await self._send_dm(member, [
            f"🔇 **{interaction.guild.name}** sunucusunda **susturuldun**.",
            f"⏱️ Süre: **{minutes} dakika**",
            f"👤 Yetkili: {interaction.user.mention}",
            f"📝 Sebep: {reason}",
        ])

        try:
            await member.timeout(until, reason=f"{interaction.user} tarafından: {reason}")
        except discord.Forbidden:
            await interaction.followup.send("❌ Botun yetkisi yetersiz (Timeout izni eksik veya rol düşük).", ephemeral=True)
            return

        dm_note = " 📩 DM gönderildi." if dm_sent else " ⚠️ DM gönderilemedi (kapalı olabilir)."
        await interaction.followup.send(
            f"✅ **{member}** susturuldu ({minutes} dk).{dm_note}\n📝 Sebep: {reason}",
            ephemeral=True,
        )
        self.log.info("MUTE %s for %s min by %s in %s (%s) DM=%s", member, minutes, interaction.user, interaction.guild, reason, dm_sent)

    @mute.error
    async def mute_error(self, interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            await self._reply(interaction, "❌ Bunun için **Moderate Members** iznine ihtiyacın var.")

    @discord.app_commands.command(name="unban", description="Bir üyenin banını kaldır")
    @discord.app_commands.describe(
        user_id="Banı kaldırılacak kullanıcının ID'si",
        reason="Sebep",
    )
    @discord.app_commands.checks.has_permissions(ban_members=True)
    @discord.app_commands.guild_only()
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str = "Belirtilmedi"):
        await interaction.response.defer(ephemeral=True)
        try:
            user = await self.bot.fetch_user(int(user_id))
        except Exception:
            await interaction.followup.send("❌ Geçersiz kullanıcı ID.", ephemeral=True)
            return
        try:
            await interaction.guild.unban(user, reason=f"{interaction.user} tarafından: {reason}")
        except discord.NotFound:
            await interaction.followup.send("❌ Bu kullanıcı banlı değil.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send("❌ Botun yetkisi yetersiz.", ephemeral=True)
            return
        await interaction.followup.send(f"✅ **{user}** banı kaldırıldı.", ephemeral=True)

    @unban.error
    async def unban_error(self, interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            await self._reply(interaction, "❌ Bunun için **Ban Members** iznine ihtiyacın var.")


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))