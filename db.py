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
            """
            CREATE TABLE IF NOT EXISTS mod_log_settings (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT
            )
            """
        )

    def get_all_log_channels(self):
        rows = self.fetchall("SELECT guild_id, channel_id FROM mod_log_settings")
        return {int(g): int(c) for g, c in rows if c is not None}

    def set_log_channel(self, guild_id, channel_id):
        self.execute(
            """
            INSERT INTO mod_log_settings (guild_id, channel_id) VALUES (%s, %s)
            ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id
            """,
            (guild_id, channel_id),
        )

    def clear_log_channel(self, guild_id):
        self.execute("DELETE FROM mod_log_settings WHERE guild_id = %s", (guild_id,))
