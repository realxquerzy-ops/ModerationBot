import asyncio
import logging
import os
import sys

import aiohttp
import discord
from discord.ext import commands

from db import Database
import web_server

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    force=True,
    format="[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s",
)

ALLOWED_GUILD_IDS = []
if os.getenv("ALLOWED_GUILD_IDS"):
    ALLOWED_GUILD_IDS = [int(x.strip()) for x in os.getenv("ALLOWED_GUILD_IDS").split(",") if x.strip()]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.moderation = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
bot.allowed_guild_ids = ALLOWED_GUILD_IDS
bot._keepalive_started = False
bot.db = None
bot.log_channels = {}
bot.boost_settings = {}
bot.welcomer_cache = {}
bot.xp_boosts = {}
bot._api_loop = None
web_server.BOT = bot


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: Exception):
    import traceback
    traceback.print_exc()
    try:
        if interaction.response.is_done():
            await interaction.followup.send(f"⚠️ Error: `{error}`", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ Error: `{error}`", ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_ready():
    logging.info("Logged in as %s. Guilds: %s", bot.user, [g.name for g in bot.guilds])
    bot._api_loop = asyncio.get_running_loop()
    try:
        await bot.tree.sync()
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        logging.info("Synced commands to %s guild(s) (+ global).", len(bot.guilds))
    except Exception as e:
        logging.error("Sync error: %s", e)
    if not bot._keepalive_started:
        bot._keepalive_started = True
        bot.loop.create_task(_self_ping_loop())


@bot.event
async def on_guild_join(guild):
    if bot.allowed_guild_ids and guild.id not in bot.allowed_guild_ids:
        try:
            await guild.leave()
            logging.info("Left unauthorized guild: %s (%s)", guild.name, guild.id)
        except Exception as e:
            logging.error("Guild leave error: %s", e)
        return
    try:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        logging.info("Synced commands to new guild: %s (%s)", guild.name, guild.id)
    except Exception as e:
        logging.error("Guild join sync error: %s", e)


def _start_web_server():
    web_server.start_server()


def _get_public_url():
    return os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_URL")


async def _self_ping_loop():
    url = _get_public_url()
    if not url:
        logging.warning("[keepalive] PUBLIC_URL not set, self-ping disabled")
        return
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{url.rstrip('/')}/health", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    logging.info("[keepalive] ping %s -> %s", url, resp.status)
        except Exception as e:
            logging.warning("[keepalive] ping failed: %s", e)
        await asyncio.sleep(300)


def _has_setup(path):
    try:
        with open(path, encoding="utf-8") as f:
            return "def setup(" in f.read()
    except OSError:
        return False


async def _load_extensions():
    for filename in sorted(os.listdir("./commands")):
        full = os.path.join("./commands", filename)
        if (
            filename.endswith(".py")
            and filename != "__init__.py"
            and _has_setup(full)
        ):
            name = f"commands.{filename[:-3]}"
            try:
                await bot.load_extension(name)
                logging.info("Loaded extension: %s", name)
            except Exception as e:
                logging.error("Failed to load extension %s: %s", name, e)


_start_web_server();

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("ERROR: DISCORD_TOKEN not found!")
    exit(1)


def _init_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        logging.warning("[db] DATABASE_URL not set; log settings will not persist")
        bot.db = None
        bot.log_channels = {}
        return
    try:
        database = Database(url)
        database.init_schema()
        bot.db = database
        bot.log_channels = database.get_all_log_channels()
        try:
            bot.boost_settings = database.get_all_boost_settings()
        except psycopg2.errors.UndefinedTable:
            logging.warning("[db] boost_settings table missing; creating it")
            database.init_schema()
            bot.boost_settings = database.get_all_boost_settings()
        try:
            bot.welcomer_cache = database.get_all_welcomer()
        except Exception as e:
            logging.warning("[db] failed to load welcomer settings: %s", e)
            bot.welcomer_cache = {}
        try:
            bot.xp_boosts = database.get_all_xp_boosts()
        except Exception as e:
            logging.warning("[db] failed to load xp boosts: %s", e)
            bot.xp_boosts = {}
        logging.info("[db] loaded %s log channel setting(s)", len(bot.log_channels))
        logging.info("[db] loaded %s boost setting(s)", len(bot.boost_settings))
        logging.info("[db] loaded %s welcomer setting(s)", len(bot.welcomer_cache))
        logging.info("[db] loaded %s xp boost guild(s)", len(bot.xp_boosts))
    except Exception as e:
        logging.error("[db] init failed: %s", e)
        bot.db = None
        bot.log_channels = {}
        bot.boost_settings = {}


async def main():
    _init_db()
    await _load_extensions()
    await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())