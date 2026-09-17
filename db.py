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
            "ALTER TABLE mod_log_settings ADD COLUMN IF NOT EXISTS check_message TEXT"
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
            "SELECT guild_id, channel_id, boost_start_message, boost_end_message FROM boost_settings"
        )
        return {
            int(g): {
                "channel_id": int(c) if c is not None else None,
                "boost_start_message": s,
                "boost_end_message": e,
            }
            for g, c, s, e in rows
        }

    def get_boost_settings(self, guild_id):
        row = self.fetchone(
            "SELECT channel_id, boost_start_message, boost_end_message FROM boost_settings WHERE guild_id = %s",
            (guild_id,),
        )
        if row is None:
            return None
        return {
            "channel_id": int(row[0]) if row[0] is not None else None,
            "boost_start_message": row[1],
            "boost_end_message": row[2],
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

    def set_boost_settings(self, guild_id, channel_id=None, start_message=None, end_message=None):
        existing = self.get_boost_settings(guild_id)
        if existing is None:
            self.execute(
                "INSERT INTO boost_settings (guild_id, channel_id, boost_start_message, boost_end_message) "
                "VALUES (%s, %s, %s, %s)",
                (
                    guild_id,
                    channel_id,
                    start_message,
                    end_message,
                ),
            )
        else:
            self.execute(
                "UPDATE boost_settings SET channel_id = COALESCE(%s, channel_id), "
                "boost_start_message = COALESCE(%s, boost_start_message), "
                "boost_end_message = COALESCE(%s, boost_end_message) WHERE guild_id = %s",
                (channel_id, start_message, end_message, guild_id),
            )

    def clear_boost_settings(self, guild_id):
        self.execute("DELETE FROM boost_settings WHERE guild_id = %s", (guild_id,))
