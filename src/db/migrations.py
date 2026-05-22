from db.pool import execute
from loguru import logger

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id   TEXT PRIMARY KEY,
        volume     INTEGER NOT NULL DEFAULT 80,
        filter     TEXT    NOT NULL DEFAULT 'none',
        created_at TEXT    DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT    DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS play_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id   TEXT    NOT NULL,
        user_id    TEXT    NOT NULL,
        title      TEXT    NOT NULL,
        url        TEXT    NOT NULL,
        duration   INTEGER,
        played_at  TEXT    DEFAULT CURRENT_TIMESTAMP
    )""",
    'CREATE INDEX IF NOT EXISTS idx_guild ON play_history (guild_id)',
]


async def run_migrations() -> None:
    for sql in _SCHEMA:
        await execute(sql)
    logger.info('Database migrations complete')
