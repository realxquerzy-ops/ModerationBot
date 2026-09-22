import json
import time

from psycopg2.pool import ThreadedConnectionPool


class Database:
    def __init__(self, database_url):
        self._pool = ThreadedConnectionPool(
            1,
            4,
            database_url,
            connect_timeout=5,
            application_name="moderationbot",
        )

    def close(self):
        self._pool.closeall()

    def _get_conn(self):
        return self._pool.getconn()

    def _put_conn(self, conn):
        self._pool.putconn(conn)

    def fetchall(self, sql, params=None):
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            conn.commit()
            return rows
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put_conn(conn)

    def fetchone(self, sql, params=None):
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
            conn.commit()
            return row
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put_conn(conn)

    def execute(self, sql, params=None):
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put_conn(conn)

    def init_schema(self):
        self.execute(
            "CREATE TABLE IF NOT EXISTS mod_log_settings ("
            "guild_id BIGINT PRIMARY KEY, channel_id BIGINT)"
        )
        self.execute(
            "CREATE TABLE IF NOT EXISTS boost_settings ("
            "guild_id BIGINT PRIMARY KEY, channel_id BIGINT, "
            "boost_start_message TEXT, boost_end_message TEXT)"
        )
        self.execute(
            "ALTER TABLE boost_settings ADD COLUMN IF NOT EXISTS boost_role_id BIGINT"
        )
        self.execute(
            "ALTER TABLE mod_log_settings ADD COLUMN IF NOT EXISTS check_message TEXT"
        )
        self.execute(
            "CREATE TABLE IF NOT EXISTS boost_history ("
            "id BIGSERIAL PRIMARY KEY, guild_id BIGINT, user_id BIGINT, "
            "event_type TEXT NOT NULL CHECK (event_type IN ('start','end')), "
            "occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ")"
        )
        self.execute(
            "CREATE TABLE IF NOT EXISTS reaction_roles ("
            "id BIGSERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, "
            "message_id BIGINT NOT NULL, emoji TEXT NOT NULL, role_id BIGINT NOT NULL, "
            "UNIQUE (message_id, emoji))"
        )
        self.execute(
            "CREATE TABLE IF NOT EXISTS leveling ("
            "guild_id BIGINT, user_id BIGINT, xp BIGINT DEFAULT 0, "
            "level INTEGER DEFAULT 0, total_messages INTEGER DEFAULT 0, "
            "voice_minutes INTEGER DEFAULT 0, commands_used INTEGER DEFAULT 0, "
            "last_gain TIMESTAMP, PRIMARY KEY (guild_id, user_id))"
        )
        self.execute(
            "CREATE TABLE IF NOT EXISTS level_rewards ("
            "guild_id BIGINT, level INTEGER, role_id BIGINT, "
            "PRIMARY KEY (guild_id, level))"
        )
        self.execute(
            "CREATE TABLE IF NOT EXISTS level_settings ("
            "guild_id BIGINT PRIMARY KEY, enabled BOOLEAN DEFAULT TRUE, "
            "announce_channel BIGINT, xp_per_message INTEGER DEFAULT 25, "
            "cooldown_seconds INTEGER DEFAULT 60, levelup_text TEXT, "
            "xp_per_voice INTEGER DEFAULT 20, first_place_role BIGINT)"
        )
        self.execute("ALTER TABLE level_settings ADD COLUMN IF NOT EXISTS levelup_text TEXT")
        self.execute("ALTER TABLE level_settings ADD COLUMN IF NOT EXISTS xp_per_voice INTEGER DEFAULT 20")
        self.execute(
            "CREATE TABLE IF NOT EXISTS web_sessions ("
            "token TEXT PRIMARY KEY, user_id TEXT, username TEXT, "
            "manageable TEXT NOT NULL DEFAULT '{}', expires BIGINT NOT NULL)"
        )

    def save_web_session(self, token, user_id, username, manageable, expires):
        self.execute(
            "INSERT INTO web_sessions (token, user_id, username, manageable, expires) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (token) DO UPDATE SET user_id = EXCLUDED.user_id, "
            "username = EXCLUDED.username, manageable = EXCLUDED.manageable, "
            "expires = EXCLUDED.expires",
            (token, user_id, username, json.dumps(manageable), int(expires)),
        )

    def get_web_session(self, token):
        row = self.fetchone(
            "SELECT user_id, username, manageable, expires FROM web_sessions WHERE token = %s",
            (token,),
        )
        if not row:
            return None
        return {
            "user": {"id": row[0], "username": row[1]},
            "manageable": json.loads(row[2] or "{}"),
            "exp": float(row[3]),
        }

    def delete_web_session(self, token):
        self.execute("DELETE FROM web_sessions WHERE token = %s", (token,))

    def cleanup_web_sessions(self):
        self.execute("DELETE FROM web_sessions WHERE expires < %s", (int(time.time()),))

    def get_all_log_channels(self):
        rows = self.fetchall("SELECT guild_id, channel_id FROM mod_log_settings")
        return {int(g): int(c) for g, c in rows if c is not None}

    def set_log_channel(self, guild_id, channel_id):
        self.execute(
            "INSERT INTO mod_log_settings (guild_id, channel_id) VALUES (%s, %s) "
            "ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id",
            (guild_id, channel_id),
        )

    def clear_log_channel(self, guild_id):
        self.execute("DELETE FROM mod_log_settings WHERE guild_id = %s", (guild_id,))

    def get_all_boost_settings(self):
        rows = self.fetchall(
            "SELECT guild_id, channel_id, boost_start_message, boost_end_message, boost_role_id FROM boost_settings"
        )
        return {
            int(g): {
                "channel_id": int(c) if c is not None else None,
                "boost_start_message": s,
                "boost_end_message": e,
                "boost_role_id": int(r) if r is not None else None,
            }
            for g, c, s, e, r in rows
        }

    def get_boost_settings(self, guild_id):
        row = self.fetchone(
            "SELECT channel_id, boost_start_message, boost_end_message, boost_role_id FROM boost_settings WHERE guild_id = %s",
            (guild_id,),
        )
        if row is None:
            return None
        return {
            "channel_id": int(row[0]) if row[0] is not None else None,
            "boost_start_message": row[1],
            "boost_end_message": row[2],
            "boost_role_id": int(row[3]) if row[3] is not None else None,
        }

    def set_boost_channel(self, guild_id, channel_id):
        self.set_boost_settings(guild_id, channel_id=channel_id)

    def set_boost_start_message(self, guild_id, message):
        self.set_boost_settings(guild_id, start_message=message)

    def set_boost_end_message(self, guild_id, message):
        self.set_boost_settings(guild_id, end_message=message)

    def set_boost_channel(self, guild_id, channel_id):
        self.set_boost_settings(guild_id, channel_id=channel_id)

    def set_boost_start_message(self, guild_id, message):
        self.set_boost_settings(guild_id, start_message=message)

    def set_boost_end_message(self, guild_id, message):
        self.set_boost_settings(guild_id, end_message=message)

    def set_boost_channel(self, guild_id, channel_id):
        self.set_boost_settings(guild_id, channel_id=channel_id)

    def set_boost_start_message(self, guild_id, message):
        self.set_boost_settings(guild_id, start_message=message)

    def set_boost_end_message(self, guild_id, message):
        self.set_boost_settings(guild_id, end_message=message)

    def set_boost_settings(self, guild_id, channel_id=None, start_message=None, end_message=None, role_id=None):
        existing = self.get_boost_settings(guild_id)
        if existing is None:
            self.execute(
                "INSERT INTO boost_settings (guild_id, channel_id, boost_start_message, boost_end_message, boost_role_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    guild_id,
                    channel_id,
                    start_message,
                    end_message,
                    role_id,
                ),
            )
        else:
            self.execute(
                "UPDATE boost_settings SET channel_id = COALESCE(%s, channel_id), "
                "boost_start_message = COALESCE(%s, boost_start_message), "
                "boost_end_message = COALESCE(%s, boost_end_message), "
                "boost_role_id = COALESCE(%s, boost_role_id) WHERE guild_id = %s",
                (channel_id, start_message, end_message, role_id, guild_id),
            )

    def set_boost_role(self, guild_id, role_id):
        self.set_boost_settings(guild_id, role_id=role_id)

    def clear_boost_settings(self, guild_id):
        self.execute("DELETE FROM boost_settings WHERE guild_id = %s", (guild_id,))

    def log_boost_event(self, guild_id, user_id, event_type):
        self.execute(
            "CREATE TABLE IF NOT EXISTS boost_history ("
            "id BIGSERIAL PRIMARY KEY, guild_id BIGINT, user_id BIGINT, "
            "event_type TEXT NOT NULL CHECK (event_type IN ('start','end')), "
            "occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            ")"
        )
        self.execute(
            "INSERT INTO boost_history (guild_id, user_id, event_type) VALUES (%s, %s, %s)",
            (guild_id, user_id, event_type),
        )

    def get_recent_boost_history(self, guild_id, limit=8):
        try:
            rows = self.fetchall(
                "SELECT user_id, event_type, occurred_at FROM boost_history "
                "WHERE guild_id = %s ORDER BY occurred_at DESC LIMIT %s",
                (guild_id, limit),
            )
        except Exception:
            return []
        return [(int(u), t, ts) for u, t, ts in rows]


    def add_boost_history(self, guild_id, user_id, event_type):
        self.execute(
            "INSERT INTO boost_history (guild_id, user_id, event_type) VALUES (%s, %s, %s)",
            (guild_id, user_id, event_type),
        )

    def set_reaction_role(self, guild_id, channel_id, message_id, emoji, role_id):
        self.execute(
            "INSERT INTO reaction_roles (guild_id, channel_id, message_id, emoji, role_id) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (message_id, emoji) DO UPDATE SET role_id = EXCLUDED.role_id",
            (guild_id, channel_id, message_id, emoji, role_id),
        )

    def get_reaction_roles(self, guild_id):
        rows = self.fetchall(
            "SELECT channel_id, message_id, emoji, role_id FROM reaction_roles "
            "WHERE guild_id = %s ORDER BY id",
            (guild_id,),
        )
        return [
            {
                "channel_id": int(c),
                "message_id": int(m),
                "emoji": e,
                "role_id": int(r),
            }
            for c, m, e, r in rows
        ]

    def get_message_reaction_roles(self, guild_id, channel_id, message_id):
        rows = self.fetchall(
            "SELECT emoji, role_id FROM reaction_roles "
            "WHERE guild_id = %s AND channel_id = %s AND message_id = %s",
            (guild_id, channel_id, message_id),
        )
        return [{"emoji": e, "role_id": int(r)} for e, r in rows]

    def delete_reaction_role(self, guild_id, message_id, emoji):
        self.execute(
            "DELETE FROM reaction_roles WHERE guild_id = %s AND message_id = %s AND emoji = %s",
            (guild_id, message_id, emoji),
        )

    def delete_message_reaction_roles(self, guild_id, message_id):
        self.execute(
            "DELETE FROM reaction_roles WHERE guild_id = %s AND message_id = %s",
            (guild_id, message_id),
        )

    def get_leveling(self, guild_id, user_id):
        row = self.fetchone(
            "SELECT xp, level, total_messages, voice_minutes, commands_used, last_gain FROM leveling WHERE guild_id = %s AND user_id = %s",
            (guild_id, user_id),
        )
        if not row:
            return None
        return {
            "xp": int(row[0]),
            "level": int(row[1]),
            "total_messages": int(row[2]) if row[2] else 0,
            "voice_minutes": int(row[3]) if row[3] else 0,
            "commands_used": int(row[4]) if row[4] else 0,
            "last_gain": row[5],
        }

    def save_leveling(self, guild_id, user_id, xp, level, total_messages, voice_minutes, commands_used, last_gain=None):
        self.execute(
            """
            INSERT INTO leveling (guild_id, user_id, xp, level, total_messages, voice_minutes, commands_used, last_gain)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                xp = EXCLUDED.xp,
                level = EXCLUDED.level,
                total_messages = EXCLUDED.total_messages,
                voice_minutes = EXCLUDED.voice_minutes,
                commands_used = EXCLUDED.commands_used,
                last_gain = EXCLUDED.last_gain
            """,
            (guild_id, user_id, xp, level, total_messages, voice_minutes, commands_used, last_gain),
        )

    def get_level_settings(self, guild_id):
        row = self.fetchone(
            "SELECT enabled, announce_channel, xp_per_message, cooldown_seconds, levelup_text, xp_per_voice, first_place_role FROM level_settings WHERE guild_id = %s",
            (guild_id,),
        )
        if not row:
            return {
                "enabled": True,
                "announce_channel": None,
                "xp_per_message": 25,
                "cooldown_seconds": 60,
                "levelup_text": None,
                "xp_per_voice": 20,
                "first_place_role": None,
            }
        return {
            "enabled": row[0] is not False,
            "announce_channel": int(row[1]) if row[1] else None,
            "xp_per_message": int(row[2]) if row[2] else 25,
            "cooldown_seconds": int(row[3]) if row[3] else 60,
            "levelup_text": row[4],
            "xp_per_voice": int(row[5]) if row[5] else 20,
            "first_place_role": int(row[6]) if row[6] else None,
        }

    _SKIP = object()

    def save_level_settings(self, guild_id, *, enabled=_SKIP, announce_channel=_SKIP, xp_per_message=_SKIP,
                            cooldown_seconds=_SKIP, levelup_text=_SKIP, xp_per_voice=_SKIP, first_place_role=_SKIP):
        cur = self.get_level_settings(guild_id)
        def pick(v, key):
            return cur[key] if v is self._SKIP else v
        self.execute(
            """
            INSERT INTO level_settings (guild_id, enabled, announce_channel, xp_per_message, cooldown_seconds, levelup_text, xp_per_voice, first_place_role)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (guild_id) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                announce_channel = EXCLUDED.announce_channel,
                xp_per_message = EXCLUDED.xp_per_message,
                cooldown_seconds = EXCLUDED.cooldown_seconds,
                levelup_text = EXCLUDED.levelup_text,
                xp_per_voice = EXCLUDED.xp_per_voice,
                first_place_role = EXCLUDED.first_place_role
            """,
            (
                guild_id,
                pick(enabled, "enabled"),
                pick(announce_channel, "announce_channel"),
                pick(xp_per_message, "xp_per_message"),
                pick(cooldown_seconds, "cooldown_seconds"),
                pick(levelup_text, "levelup_text"),
                pick(xp_per_voice, "xp_per_voice"),
                pick(first_place_role, "first_place_role"),
            ),
        )

    def get_level_rewards(self, guild_id):
        rows = self.fetchall(
            "SELECT level, role_id FROM level_rewards WHERE guild_id = %s ORDER BY level",
            (guild_id,),
        )
        return {int(level): int(role_id) for level, role_id in rows}

    def save_level_rewards(self, guild_id, roles):
        self.execute("DELETE FROM level_rewards WHERE guild_id = %s", (guild_id,))
        for level, role_id in roles.items():
            self.execute(
                "INSERT INTO level_rewards (guild_id, level, role_id) VALUES (%s, %s, %s)",
                (guild_id, level, role_id),
            )

    def get_leveling_leaderboard(self, guild_id, limit=10):
        rows = self.fetchall(
            "SELECT user_id, xp, level FROM leveling WHERE guild_id = %s ORDER BY xp DESC LIMIT %s",
            (guild_id, limit),
        )
        return [(int(user_id), int(xp), int(level)) for user_id, xp, level in rows]

    def get_leveling_top_user(self, guild_id):
        row = self.fetchone(
            "SELECT user_id FROM leveling WHERE guild_id = %s ORDER BY xp DESC LIMIT 1",
            (guild_id,),
        )
        return int(row[0]) if row else None

    def get_leveling_rank(self, guild_id, user_id):
        row = self.fetchone(
            "SELECT COUNT(*) FROM leveling WHERE guild_id = %s AND xp > COALESCE((SELECT xp FROM leveling WHERE guild_id = %s AND user_id = %s), 0)",
            (guild_id, guild_id, user_id),
        )
        total = self.fetchone(
            "SELECT COUNT(*) FROM leveling WHERE guild_id = %s",
            (guild_id,),
        )
        return int(row[0]) + 1, int(total[0]) if total and total[0] else 0
