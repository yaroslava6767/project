from contextlib import contextmanager
from urllib.parse import unquote, urlparse

import pg8000.dbapi
from pg8000 import exceptions as pg_exceptions

from .config import DATABASE_URL


_db_ready = False


def quote_identifier(value):
    return '"' + value.replace('"', '""') + '"'


class DictCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, *args, **kwargs):
        return self.cursor.execute(*args, **kwargs)

    def fetchone(self):
        row = self.cursor.fetchone()
        return self._row_to_dict(row)

    def fetchall(self):
        return [self._row_to_dict(row) for row in self.cursor.fetchall()]

    def _row_to_dict(self, row):
        if row is None:
            return None
        columns = [column[0] for column in self.cursor.description]
        return dict(zip(columns, row))


def database_config():
    parsed = urlparse(DATABASE_URL)
    return {
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/"),
    }


def ensure_database_exists():
    config = database_config()
    try:
        conn = pg8000.dbapi.connect(**config)
        conn.close()
        return
    except pg_exceptions.DatabaseError as exc:
        error = getattr(exc, "args", [{}])[0]
        if not isinstance(error, dict) or error.get("C") != "3D000":
            raise

    database_name = config["database"]
    maintenance_config = {**config, "database": "postgres"}
    conn = pg8000.dbapi.connect(**maintenance_config)
    try:
        conn.autocommit = True
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (database_name,),
            )
            if cursor.fetchone() is None:
                cursor.execute(f"CREATE DATABASE {quote_identifier(database_name)}")
        finally:
            cursor.close()
    finally:
        conn.close()


@contextmanager
def db_cursor():
    conn = pg8000.dbapi.connect(**database_config())
    try:
        cursor = conn.cursor()
        try:
            yield DictCursor(cursor)
        finally:
            cursor.close()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    ensure_database_exists()
    with db_cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS card_sets (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                topic VARCHAR(120) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id SERIAL PRIMARY KEY,
                set_id INTEGER NOT NULL REFERENCES card_sets(id) ON DELETE CASCADE,
                word VARCHAR(120) NOT NULL,
                translation VARCHAR(120) NOT NULL,
                example TEXT NOT NULL,
                learned BOOLEAN NOT NULL DEFAULT FALSE
            );
            """
        )
        cursor.execute("ALTER TABLE cards DROP COLUMN IF EXISTS created_at")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                topic VARCHAR(120) NOT NULL,
                english_level VARCHAR(2) NOT NULL DEFAULT 'A2',
                cards_count INTEGER NOT NULL DEFAULT 10,
                request_prompt TEXT NOT NULL,
                response_text TEXT,
                success BOOLEAN NOT NULL DEFAULT FALSE,
                error_message TEXT
            );
            """
        )
        cursor.execute(
            "ALTER TABLE ai_log ADD COLUMN IF NOT EXISTS english_level VARCHAR(2) NOT NULL DEFAULT 'A2'"
        )
        cursor.execute(
            "ALTER TABLE ai_log ADD COLUMN IF NOT EXISTS cards_count INTEGER NOT NULL DEFAULT 10"
        )


def ensure_db_ready():
    global _db_ready
    if not _db_ready:
        init_db()
        _db_ready = True


def register_db_hooks(app):
    @app.before_request
    def prepare_database():
        ensure_db_ready()

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        print("Database tables are ready.")
