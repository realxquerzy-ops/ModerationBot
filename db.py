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
