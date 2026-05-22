import os
import aiosqlite
from loguru import logger
import config

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
        _db = await aiosqlite.connect(config.DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute('PRAGMA journal_mode=WAL')
        logger.info(f'SQLite connected: {config.DB_PATH}')
    return _db


async def execute(sql: str, args=None):
    db = await get_db()
    async with db.execute(sql, args or ()) as cur:
        await db.commit()
        if sql.strip().upper().startswith('SELECT'):
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
