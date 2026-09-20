import discord
from discord import app_commands
from discord.ext import commands


class SetupCog(commands.Cog):
    @app_commands.command(
        name="setup",
        description="How to set up reaction roles, boost panels and more.",
    )
    @app_commands.guild_only()
    async def setup(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Setup Guide",
            description=(
                "All commands below require **Manage Server** permission.\n"
                "Give the bot the **Manage Roles** permission so it can assign roles."
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="🔁 Reaction Roles",
            value=(
                "**1.** `/reactionrole setup <channel> <message> <reaction> <role>`\n"
                "Post a message and bind a reaction to a role.\n"
                "**2.** `/reactionrole add <reaction> <role>`\n"
                "Add another reaction/role to a message — **reply to the "
                "target message first**, then run the command.\n"
                "› Reacting grants the role, unreacting removes it. "
                "Your message stays untouched and bindings survive bot restarts."
            ),
            inline=False,
        )
        embed.add_field(
            name="🚀 Server Boosts",
            value=(
                "**1.** `/boostchannel <channel>`\n"
                "Set where the Server Boosts panel + boost messages go "
                "(it auto-posts and stays in sync).\n"
                "**2.** `/boostrole <role>`\n"
                "Choose which role is shown as the boost role "
                "(falls back to Discord's booster role)."
            ),
            inline=False,
        )
        embed.add_field(
            name="📢 Announcements",
            value=(
                "The announce panel with reaction roles for the resource "
                "guild is managed automatically by the bot."
            ),
            inline=False,
        )
        embed.set_footer(text="Tips: custom emojis are supported — just paste them.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(SetupCog(bot))