import asyncio
import sys
from loguru import logger
import discord
from discord.ext import commands
import config
from db.migrations import run_migrations

logger.remove()
logger.add(sys.stdout, format='<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | {message}', level=config.LOG_LEVEL)

intents = discord.Intents.default()
intents.voice_states = True


class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=intents)

    async def setup_hook(self) -> None:
        await self.load_extension('modules.music.cog')
        try:
            await run_migrations()
        except Exception as e:
            logger.warning(f'DB migrations failed, running without persistence: {e}')
        if config.SYNC_COMMANDS:
            synced = await self.tree.sync()
            logger.info(f'Synced {len(synced)} slash commands globally')
        else:
            logger.info('Command sync skipped (SYNC_COMMANDS=false)')

    async def on_ready(self) -> None:
        logger.info(f'Logged in as {self.user} | Guilds: {len(self.guilds)}')


async def main() -> None:
    bot = Bot()
    async with bot:
        await bot.start(config.DISCORD_TOKEN)


if __name__ == '__main__':
    asyncio.run(main())
