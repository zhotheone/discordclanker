from dotenv import load_dotenv
import os

load_dotenv()

DISCORD_TOKEN: str = os.environ['DISCORD_TOKEN']
DISCORD_CLIENT_ID: str = os.environ['DISCORD_CLIENT_ID']

DB_PATH: str = os.getenv('DB_PATH', './data/bot.db')

COOKIES_PATH: str = os.getenv('COOKIES_PATH', 'www.youtube.com_cookies.txt')
YTDLP_PROXY: str | None = os.getenv('YTDLP_PROXY') or None
FFMPEG_PATH: str = os.getenv('FFMPEG_PATH', 'ffmpeg')
LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'DEBUG')
SYNC_COMMANDS: bool = os.getenv('SYNC_COMMANDS', 'true').lower() == 'true'

CACHE_DIR: str = os.getenv('CACHE_DIR', './cache')
CACHE_MAX_BYTES: int = int(os.getenv('CACHE_MAX_GB', '5')) * 1024 ** 3