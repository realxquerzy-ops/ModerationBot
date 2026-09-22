import asyncio
import json
import logging
import os
import secrets
import threading
import time
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BOT = None

SESSIONS = {}
SESSION_TTL = 7 * 24 * 3600
STATES = {}
STATE_TTL = 600

INDEX_FILE = Path(__file__).resolve().parent / "web" / "index.html"

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API = "https://discord.com/api"

ADMINISTRATOR = 1 << 3
MANAGE_GUILD = 1 << 5

log = logging.getLogger("web")


def _env(key, default=""):
    return (os.getenv(key) or default).strip()


def _client_id():
    return _env("DISCORD_OAUTH_CLIENT_ID")


def _client_secret():
    return _env("DISCORD_OAUTH_CLIENT_SECRET")


def _base_url():
    return _env("RENDER_EXTERNAL_URL") or _env("PUBLIC_URL")


def _redirect_uri():
    return _env("OAUTH_REDIRECT_URI") or (_base_url() + "/auth/callback")


def _run_on_loop(coro):
    loop = getattr(BOT, "_api_loop", None)
    if loop is None:
        return {"ok": False, "error": "Bot is still starting up. Try again in a few seconds."}
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=30)


def _discord_get(endpoint, token):
    req = urllib.request.Request(
        DISCORD_API + endpoint,
        headers={"Authorization": "Bearer " + token, "User-Agent": "birdbot-web/1.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.load(resp)


def _oauth_exchange(code):
    body = urllib.parse.urlencode({
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(),
    }).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "birdbot-web/1.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def _extract_manageable(guilds):
    result = {}
    for g in guilds:
        perms = int(g.get("permissions") or 0)
        if perms & ADMINISTRATOR or perms & MANAGE_GUILD:
            result[str(g.get("id"))] = g.get("name") or "Server"
    return result


def _new_session(user, manageable):
    token = secrets.token_urlsafe(24)
    SESSIONS[token] = {
        "user": user,
        "manageable": manageable,
        "exp": time.time() + SESSION_TTL,
    }
    return token


def _cookie(token):
    return f"mb_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}"


def _available_guilds(bot, session):
    allowed = session.get("manageable") or {}
    guilds = [g for g in bot.guilds if str(g.id) in allowed]
    guilds.sort(key=lambda g: g.name.lower())
    return guilds


def _pick_guild(bot, session, guild_id):
    for g in _available_guilds(bot, session):
        if guild_id is None or str(g.id) == str(guild_id):
            return g
    return None


async def _gather(bot, user, session, guild_id):
    guild = _pick_guild(bot, session, guild_id)
    if guild is None:
        return {"ok": False, "error": "No server is available. Make sure the bot is in the server and you have Manage Server."}
    db = bot.db
    gid = guild.id
    channels = [
        {"id": str(c.id), "name": c.name, "type": str(c.type).split(".")[-1]}
        for c in guild.channels
    ]
    roles = [
        {"id": str(r.id), "name": r.name}
        for r in sorted(guild.roles, key=lambda r: (r.position, r.id), reverse=True)
    ]
    lvl = db.get_level_settings(gid)
    rewards = db.get_level_rewards(gid)
    boost = db.get_boost_settings(gid) or {}
    row = db.fetchone("SELECT channel_id FROM mod_log_settings WHERE guild_id = %s", (gid,))
    invite = None
    if _client_id():
        invite = (
            "https://discord.com/api/oauth2/authorize?"
            + urllib.parse.urlencode({
                "client_id": _client_id(),
                "permissions": 8,
                "scope": "bot applications.commands",
            })
        )
    return {
        "ok": True,
        "guilds": [{"id": str(g.id), "name": g.name} for g in _available_guilds(bot, session)],
        "guild": {"id": str(gid), "name": guild.name},
        "user": {"id": user.get("id"), "username": user.get("username")},
        "invite_url": invite,
        "channels": channels,
        "roles": roles,
        "settings": {
            "mod_log": {"channel_id": str(row[0]) if row and row[0] else None},
            "leveling": {
                "enabled": lvl["enabled"],
                "announce_channel": str(lvl["announce_channel"]) if lvl["announce_channel"] else None,
                "xp_per_message": lvl["xp_per_message"],
                "cooldown_seconds": lvl["cooldown_seconds"],
                "xp_per_voice": lvl["xp_per_voice"],
                "levelup_text": lvl["levelup_text"],
                "first_place_role": str(lvl["first_place_role"]) if lvl["first_place_role"] else None,
            },
            "rewards": {str(k): str(v) for k, v in rewards.items()},
            "boost": {
                "channel_id": str(boost.get("channel_id")) if boost.get("channel_id") else None,
                "boost_role_id": str(boost.get("boost_role_id")) if boost.get("boost_role_id") else None,
            },
        },
    }


async def _apply(bot, session, guild_id, section, data):
    guild = _pick_guild(bot, session, guild_id)
    if guild is None:
        return {"ok": False, "error": "No server is available. Make sure the bot is in the server and you have Manage Server."}
    db = bot.db
    gid = guild.id

    if section == "mod_log":
        cid = data.get("channel_id")
        if cid:
            db.set_log_channel(gid, int(cid))
            bot.log_channels[gid] = int(cid)
        else:
            db.clear_log_channel(gid)
            bot.log_channels.pop(gid, None)
    elif section == "leveling":
        kwargs = {}
        if "enabled" in data:
            kwargs["enabled"] = bool(data["enabled"])
        if "announce_channel" in data:
            v = data["announce_channel"]
            kwargs["announce_channel"] = int(v) if v else None
        if "xp_per_message" in data:
            kwargs["xp_per_message"] = int(data["xp_per_message"])
        if "cooldown_seconds" in data:
            kwargs["cooldown_seconds"] = int(data["cooldown_seconds"])
        if "xp_per_voice" in data:
            kwargs["xp_per_voice"] = int(data["xp_per_voice"])
        if "levelup_text" in data:
            kwargs["levelup_text"] = data.get("levelup_text") or None
        if "first_place_role" in data:
            v = data["first_place_role"]
            kwargs["first_place_role"] = int(v) if v else None
        if kwargs:
            db.save_level_settings(gid, **kwargs)
    elif section == "rewards":
        roles = {}
        for level, rid in (data.get("roles") or {}).items():
            if rid:
                roles[int(level)] = int(rid)
        db.save_level_rewards(gid, roles)
    elif section == "boost":
        if data.get("clear"):
            db.clear_boost_settings(gid)
            bot.boost_settings.pop(gid, None)
        else:
            cid = data.get("channel_id")
            rid = data.get("boost_role_id")
            if cid is None and rid is None:
                return {"ok": False, "error": "Nothing to save."}
            channel = int(cid) if cid else None
            role = int(rid) if rid else None
            if db.get_boost_settings(gid) is None:
                db.execute(
                    "INSERT INTO boost_settings (guild_id, channel_id, boost_role_id) VALUES (%s, %s, %s)",
                    (gid, channel, role),
                )
            else:
                db.execute(
                    "UPDATE boost_settings SET channel_id = %s, boost_role_id = %s WHERE guild_id = %s",
                    (channel, role, gid),
                )
            settings = db.get_boost_settings(gid) or {}
            bot.boost_settings[gid] = settings
    return {"ok": True}


class WebHandler(BaseHTTPRequestHandler):
    server_version = "ModBirdWeb/1.0"

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, code, payload, headers=None):
        self._send(code, json.dumps(payload, default=str), "application/json; charset=utf-8", headers)

    def _redirect(self, location, extra_headers=None):
        headers = {"Location": location}
        if extra_headers:
            headers.update(extra_headers)
        self._send(302, b"", headers=headers)

    def _error_page(self, message):
        self._send(200, f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Setup Panel</title><style>
body{{font-family:system-ui,sans-serif;background:#1e1f29;color:#e8e8f0;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#2a2c3a;border:1px solid #3a3d52;border-radius:12px;
padding:28px;max-width:420px;text-align:center}}
a{{color:#8ab4ff;text-decoration:none}}
</style></head><body><div class="card">
<h2>Setup Panel</h2><p>{message}</p><p><a href="/">← Back home</a></p>
</div></body></html>""")

    def _session(self):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            key, _, value = part.strip().partition("=")
            if key == "mb_session":
                session = SESSIONS.get(value)
                if session and session["exp"] > time.time():
                    return session
                SESSIONS.pop(value, None)
        return None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            try:
                data = INDEX_FILE.read_bytes()
            except OSError:
                self._json(500, {"ok": False, "error": "index.html not found"})
                return
            self._send(200, data)
        elif path == "/health":
            self._send(200, "OK", "text/plain")
        elif path == "/auth/login":
            self._handle_login()
        elif path == "/auth/callback":
            self._handle_callback(parsed.query)
        elif path == "/auth/logout":
            self._handle_logout()
        elif path == "/api/bootstrap":
            self._handle_bootstrap(parsed.query)
        else:
            self._json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/apply":
            self._json(404, {"ok": False, "error": "Not found"})
            return
        session = self._session()
        if not session:
            self._json(401, {"ok": False, "error": "not_authed"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                self._json(400, {"ok": False, "error": "Empty body"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            self._json(400, {"ok": False, "error": f"Bad request: {e}"})
            return
        guild_id = payload.get("guild") or None
        section = payload.get("section")
        data = payload.get("data") or {}
        if section not in ("mod_log", "leveling", "rewards", "boost"):
            self._json(400, {"ok": False, "error": "Unknown section"})
            return
        try:
            result = _run_on_loop(_apply(BOT, session, guild_id, section, data))
            if result.get("ok") is False:
                self._json(400, result)
                return
            self._json(200, result)
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"ok": False, "error": str(e)})

    def _handle_login(self):
        cid = _client_id()
        if not cid or not _client_secret():
            self._error_page("Discord login isn't configured yet. Add <code>DISCORD_OAUTH_CLIENT_ID</code> "
                             "and <code>DISCORD_OAUTH_CLIENT_SECRET</code> to this service's environment.")
            return
        state = secrets.token_urlsafe(16)
        STATES[state] = {"exp": time.time() + STATE_TTL}
        url = DISCORD_AUTHORIZE_URL + "?" + urllib.parse.urlencode({
            "client_id": cid,
            "redirect_uri": _redirect_uri(),
            "response_type": "code",
            "scope": "identify guilds",
            "state": state,
        })
        self._redirect(url)

    def _handle_callback(self, query):
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        code = params.get("code", "")
        state = params.get("state", "")
        if not code:
            self._error_page("Missing authorization code.")
            return
        expected = STATES.pop(state, None)
        if not expected or expected["exp"] < time.time():
            self._error_page("This login link has expired. <a href=\"/auth/login\">Try again</a>.")
            return
        try:
            token = _oauth_exchange(code)
        except Exception as e:
            traceback.print_exc()
            self._error_page(f"Could not complete login: {e}")
            return
        if token.get("error"):
            self._error_page(f"Discord rejected the login: {token['error']}")
            return
        access = token.get("access_token")
        try:
            status, me = _discord_get("/users/@me", access)
            if status != 200:
                self._error_page("Could not identify you with Discord.")
                return
            status, guilds = _discord_get("/users/@me/guilds", access)
            if status != 200:
                self._error_page("Could not load your servers from Discord.")
                return
        except Exception as e:
            traceback.print_exc()
            self._error_page(f"Could not reach Discord: {e}")
            return
        manageable = _extract_manageable(guilds)
        if not manageable:
            self._error_page("You don't have <strong>Manage Server</strong> permission in any server the bot is in.")
            return
        session = _new_session(
            {"id": me.get("id"), "username": me.get("username")},
            manageable,
        )
        self._redirect("/", {"Set-Cookie": _cookie(session)})

    def _handle_logout(self):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            key, _, value = part.strip().partition("=")
            if key == "mb_session":
                SESSIONS.pop(value, None)
        self._redirect("/", {"Set-Cookie": "mb_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"})

    def _handle_bootstrap(self, query):
        session = self._session()
        if not session:
            self._json(401, {"ok": False, "error": "not_authed"})
            return
        if BOT is None or BOT.db is None:
            self._json(503, {"ok": False, "error": "Bot is still starting up. Try again in a few seconds."})
            return
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        guild_id = params.get("guild") or None
        try:
            payload = _run_on_loop(_gather(BOT, session["user"], session, guild_id))
            if payload.get("ok") is False:
                self._json(409, payload)
                return
            self._json(200, payload)
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"ok": False, "error": str(e)})


def start_server():
    port = int(_env("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), WebHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("web server listening on :%s", port)