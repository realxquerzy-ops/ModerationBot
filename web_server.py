import asyncio
import json
import logging
import os
import re
import secrets
import threading
import time
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import discord

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

INVITE_PERMISSIONS = (
    (1 << 6)   |  # Add Reactions
    (1 << 10)  |  # View Channels
    (1 << 11)  |  # Send Messages
    (1 << 13)  |  # Manage Messages
    (1 << 14)  |  # Embed Links
    (1 << 17)  |  # Read Message History
    (1 << 18)  |  # Use External Emojis
    (1 << 28)     # Manage Roles
)

log = logging.getLogger("web")

_REACTION_RE = re.compile(r"<((?P<anim>a)?:(?P<name>[^:>]+):(?P<id>\d+))>")


def _emoji_token(raw: str) -> str:
    m = _REACTION_RE.match(raw.strip())
    if m:
        anim = "a" if m.group("anim") else ""
        return f"custom:{m.group('id')}:{m.group('name')}:{anim}"
    return raw.strip()


def _partial_emoji(token: str):
    if token.startswith("custom:"):
        _, eid, name, anim = token.split(":", 3)
        return discord.PartialEmoji(name=name, id=int(eid), animated=(anim == "a"))
    return token


def _emoji_display(token: str) -> str:
    if token.startswith("custom:"):
        _, eid, name, anim = token.split(":", 3)
        anim_pre = "a" if anim == "a" else ""
        return f"<{anim_pre}:{name}:{eid}>"
    return token


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
    session = {
        "user": user,
        "manageable": manageable,
        "exp": time.time() + SESSION_TTL,
    }
    SESSIONS[token] = session
    db = getattr(BOT, "db", None)
    if db is not None:
        try:
            db.save_web_session(
                token,
                user.get("id"),
                user.get("username"),
                manageable,
                session["exp"],
            )
        except Exception:
            log.warning("web session persist failed", exc_info=True)
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

    reaction_panels = []
    by_msg = {}
    for b in db.get_reaction_roles(gid):
        by_msg.setdefault(b["message_id"], []).append(b)
    for mid, bindings in by_msg.items():
        chan_id = bindings[0]["channel_id"]
        channel = guild.get_channel(chan_id)
        role_bindings = []
        for b in bindings:
            role = guild.get_role(b["role_id"])
            role_bindings.append({
                "emoji": _emoji_display(b["emoji"]),
                "raw_emoji": b["emoji"],
                "role_id": str(b["role_id"]),
                "role_name": role.name if role else "Deleted role",
            })
        reaction_panels.append({
            "channel_id": str(chan_id),
            "channel_name": channel.name if channel else "Deleted channel",
            "message_id": str(mid),
            "url": f"https://discord.com/channels/{gid}/{chan_id}/{mid}",
            "bindings": role_bindings,
        })

    invite = None
    if _client_id():
        invite = (
            "https://discord.com/api/oauth2/authorize?"
            + urllib.parse.urlencode({
                "client_id": _client_id(),
                "permissions": INVITE_PERMISSIONS,
                "scope": "bot applications.commands",
                "guild_id": str(gid),
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
        "reaction_panels": reaction_panels,
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
            if channel:
                target = guild.get_channel(channel)
                if target is not None:
                    boost_cog = bot.get_cog("BoostCog")
                    if boost_cog is not None:
                        try:
                            await boost_cog._render_panel(guild, target)
                        except Exception as e:
                            log.warning("boost panel post via web failed: %s", e)
    elif section == "reactionrole":
        action = data.get("action")
        if action == "create":
            return await _apply_reaction_create(bot, guild, db, data)
        if action == "remove":
            return await _apply_reaction_remove(guild, db, data)
        if action == "delete":
            return await _apply_reaction_delete(guild, db, data)
        return {"ok": False, "error": "Unknown reaction role action."}
    return {"ok": True}


async def _apply_reaction_create(bot, guild, db, data):
    channel_id = data.get("channel_id")
    if not channel_id:
        return {"ok": False, "error": "Pick a channel."}
    channel = guild.get_channel(int(channel_id))
    if channel is None or not isinstance(channel, discord.TextChannel):
        return {"ok": False, "error": "Channel not found."}
    title = (data.get("title") or "").strip() or "Roles"
    description = (data.get("description") or "").strip()
    rows = []
    for r in data.get("rows") or []:
        emo = (r.get("emoji") or "").strip()
        rid = r.get("role_id")
        if not emo or not rid:
            continue
        rows.append((_emoji_token(emo), int(rid)))
    if not rows:
        return {"ok": False, "error": "Add at least one emoji + role row."}
    token_seen = set()
    deduped = []
    for token, rid in rows:
        if token in token_seen:
            continue
        token_seen.add(token)
        deduped.append((token, rid))
    lines = []
    for token, rid in deduped:
        role = guild.get_role(rid)
        lines.append(f"{_emoji_display(token)} → {role.mention if role else f'<@&{rid}>'}")
    embed_desc = description or "\n".join(lines) or None
    embed_color = discord.Color.blue()
    try:
        embed = discord.Embed(title=title, description=embed_desc, color=embed_color)
        msg = await channel.send(embed=embed)
    except Exception as e:
        return {"ok": False, "error": f"Could not post the message: {e}"}
    warnings = 0
    for token, rid in deduped:
        try:
            await msg.add_reaction(_partial_emoji(token))
        except Exception as e:
            warnings += 1
            log.warning("panel reaction add failed for %s: %s", token, e)
        try:
            db.set_reaction_role(guild.id, channel.id, msg.id, token, rid)
        except Exception as e:
            log.warning("panel reactionrole persist failed: %s", e)
    return {
        "ok": True,
        "url": f"https://discord.com/channels/{guild.id}/{channel.id}/{msg.id}",
        "warnings": warnings,
    }


async def _apply_reaction_remove(guild, db, data):
    channel_id = data.get("channel_id")
    message_id = data.get("message_id")
    emoji_raw = data.get("emoji")
    if not channel_id or not message_id or not emoji_raw:
        return {"ok": False, "error": "Missing fields."}
    token = _emoji_token(emoji_raw.strip())
    mid = int(message_id)
    channel = guild.get_channel(int(channel_id))
    if channel is not None:
        try:
            msg = await channel.fetch_message(mid)
            await msg.clear_reaction(_partial_emoji(token))
        except Exception:
            pass
    db.delete_reaction_role(guild.id, mid, token)
    return {"ok": True}


async def _apply_reaction_delete(guild, db, data):
    channel_id = data.get("channel_id")
    message_id = data.get("message_id")
    if not channel_id or not message_id:
        return {"ok": False, "error": "Missing fields."}
    mid = int(message_id)
    channel = guild.get_channel(int(channel_id))
    if channel is not None:
        try:
            msg = await channel.fetch_message(mid)
            await msg.delete()
        except Exception:
            pass
    db.delete_message_reaction_roles(guild.id, mid)
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

    def _doc_page(self, title, sections, updated):
        body = "".join(
            f"<h2>{heading}</h2><p>{text}</p>" for heading, text in sections
        )
        self._send(200, f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - Setup Panel</title><style>
body{{font-family:system-ui,sans-serif;background:#1e1f29;color:#e8e8f0;margin:0;line-height:1.6}}
.wrap{{max-width:760px;margin:0 auto;padding:32px 20px 60px}}
h1{{font-size:22px}} h2{{font-size:16px;margin-top:24px}}
a{{color:#8ab4ff;text-decoration:none}}
.top{{margin-bottom:8px;font-size:14px}}
</style></head><body><div class="wrap">
<p class="top"><a href="/">&larr; Home</a></p>
<h1>{title}</h1>
<p class="top">Last updated: {updated}</p>
{body}
</div></body></html>""")

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
            if key != "mb_session":
                continue
            session = SESSIONS.get(value)
            if session and session["exp"] > time.time():
                return session
            if session:
                SESSIONS.pop(value, None)
            db = getattr(BOT, "db", None)
            if db is not None:
                try:
                    saved = db.get_web_session(value)
                    if saved and saved["exp"] > time.time():
                        SESSIONS[value] = saved
                        return saved
                    if saved:
                        db.delete_web_session(value)
                except Exception:
                    pass
            return None
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
        elif path == "/privacy":
            self._doc_page(
                "Privacy Policy",
                [
                    (
                        "What this service is",
                        "ModBirdBot is a Discord moderation, leveling and reaction-role bot with a "
                        "web setup panel. The panel is used only to configure settings for servers "
                        "you manage.",
                    ),
                    (
                        "What we store",
                        "Per server: the server ID, the configured logging/announce channel IDs, "
                        "role IDs, emoji-to-role reaction bindings, leveling settings and boost "
                        "panel settings. Per user on servers that use it: your Discord user ID plus "
                        "leveling stats (XP, level, message/voice minutes) and boost start/end "
                        "events. We do not store chat message content or your Discord password.",
                    ),
                    (
                        "Login",
                        "Logging in uses Discord's OAuth and is owned by Discord. We issue a "
                        "session cookie (kept up to 7 days) to recognize you. Analytics/tracking "
                        "cookies are not used.",
                    ),
                    (
                        "Why we store it",
                        "Server settings must persist so the bot keeps working after restarts, and "
                        "leveling/boost history need user IDs to function. This data is used only "
                        "to run the bot and is never sold or shared.",
                    ),
                    (
                        "Deleting your data",
                        "Removing the bot from a server deletes that server's settings and its "
                        "members' leveling data. Boost and leveling data for a user is deleted "
                        "when the bot leaves that server or on request; contact the bot owner to "
                        "request removal.",
                    ),
                ],
                "2026-09-22",
            )
        elif path == "/terms":
            self._doc_page(
                "Terms of Service",
                [
                    (
                        "Acceptance",
                        "By adding ModBirdBot to your server or using the setup panel you agree "
                        "to these terms.",
                    ),
                    (
                        "Permission to configure",
                        "You may only change settings for servers where you have the Manage Server "
                        "permission in Discord. The bot enforces this for every change.",
                    ),
                    (
                        "Acceptable use",
                        "The bot is provided as-is, free of charge, for legitimate community setup "
                        "and moderation. Abuse (spam, deliberate role/level manipulation, automated "
                        "attacks) may result in the bot being removed from your server or access "
                        "being revoked.",
                    ),
                    (
                        "No warranty",
                        "The bot and panel are provided without warranty, express or implied. "
                        "Features may change or be discontinued at any time without notice. We are "
                        "not liable for any loss resulting from use of the bot.",
                    ),
                    (
                        "Changes",
                        "We may update these terms. Continued use after changes means you accept "
                        "the new terms.",
                    ),
                ],
                "2026-09-22",
            )
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
        if section not in ("mod_log", "leveling", "rewards", "boost", "reactionrole"):
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
            if key != "mb_session":
                continue
            SESSIONS.pop(value, None)
            db = getattr(BOT, "db", None)
            if db is not None:
                try:
                    db.delete_web_session(value)
                except Exception:
                    pass
        self._redirect("/", {"Set-Cookie": "mb_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"})

    def _handle_bootstrap(self, query):
        session = self._session()
        if not session:
            self._json(401, {"ok": False, "error": "not_authed"})
            return
        db = getattr(BOT, "db", None)
        if db is not None:
            try:
                db.cleanup_web_sessions()
            except Exception:
                pass
        if BOT is None or db is None:
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