import asyncio
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import discord
from discord.ext import commands

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


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: Exception):
    import traceback
    traceback.print_exc()
    try:
        if interaction.response.is_done():
            await interaction.followup.send(f"⚠️ Hata: `{error}`", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ Hata: `{error}`", ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_ready():
    logging.info("%s olarak giriş yapıldı. Guilds: %s", bot.user, [g.name for g in bot.guilds])
    try:
        synced = await bot.tree.sync()
        logging.info("%s global komut senkronize edildi.", len(synced))
    except Exception as e:
        logging.error("Senkronizasyon hatası: %s", e)
    bot.loop.create_task(_self_ping_loop())


@bot.event
async def on_guild_join(guild):
    if bot.allowed_guild_ids and guild.id not in bot.allowed_guild_ids:
        try:
            await guild.leave()
            logging.info("İzin verilmeyen sunucudan ayrılındı: %s (%s)", guild.name, guild.id)
        except Exception as e:
            logging.error("Guild leave hatası: %s", e)


def _start_health_server():
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logging.info("[health] Listening on :%s", port)


def _get_public_url():
    return os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_URL")


async def _self_ping_loop():
    url = _get_public_url()
    if not url:
        logging.warning("[keepalive] PUBLIC_URL yok, self-ping kapalı")
        return
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{url.rstrip('/')}/health", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    logging.info("[keepalive] ping %s -> %s", url, resp.status)
        except Exception as e:
            logging.warning("[keepalive] ping failed: %s", e)
        await asyncio.sleep(300)


async def _load_extensions():
    for filename in sorted(os.listdir("./commands")):
        if filename.endswith(".py") and filename != "__init__.py":
            name = f"commands.{filename[:-3]}"
            try:
                await bot.load_extension(name)
                logging.info("Modül yüklendi: %s", name)
            except Exception as e:
                logging.error("Modül yüklenemedi %s: %s", name, e)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass


_start_health_server()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("❌ HATA: DISCORD_TOKEN bulunamadı!")
    exit(1)


async def main():
    await _load_extensions()
    await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())