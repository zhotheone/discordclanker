import asyncio
import os
from functools import partial
from typing import Any
import yt_dlp
from loguru import logger
import config

_verbose = config.LOG_LEVEL.upper() == 'DEBUG'
_cookies = config.COOKIES_PATH if os.path.exists(config.COOKIES_PATH) else None
_proxy = config.YTDLP_PROXY


class _YtdlLogger:
    def debug(self, msg: str) -> None:
        if msg.startswith('[debug] '):
            logger.debug(f'yt-dlp: {msg[8:]}')
        else:
            self.info(msg)

    def info(self, msg: str) -> None:
        logger.debug(f'yt-dlp: {msg}')

    def warning(self, msg: str) -> None:
        logger.warning(f'yt-dlp: {msg}')

    def error(self, msg: str) -> None:
        logger.error(f'yt-dlp: {msg}')


_BASE_OPTS: dict[str, Any] = {
    **({'cookiefile': _cookies} if _cookies else {}),
    **({'proxy': _proxy} if _proxy else {}),
    'quiet': not _verbose,
    'no_warnings': not _verbose,
    'verbose': _verbose,
    'logger': _YtdlLogger(),
    'noplaylist': True,
    'extractor_args': {
        'youtube': {
            'player_client': ['web', 'android'],
        },
    },
}


def _sync_extract(url: str, extra: dict) -> dict:
    opts = {**_BASE_OPTS, **extra}
    logger.debug(f'yt-dlp extract: url={url!r}')
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise ValueError(f'yt-dlp returned no info for {url!r}')
    logger.debug(f'yt-dlp result: title={info.get("title")!r}')
    return info


async def _extract(url: str, extra: dict | None = None) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_sync_extract, url, extra or {}))


def _fmt_formats(info: dict) -> str:
    formats = info.get('formats', [])
    if not formats:
        return '(no formats)'
    header = f"{'ID':<12} {'EXT':<6} {'NOTE':<20} {'VCODEC':<16} {'ACODEC':<12} TBR"
    rows = []
    for f in formats:
        tbr = f.get('tbr')
        rows.append(
            f"{f.get('format_id', '?'):<12} "
            f"{f.get('ext', '?'):<6} "
            f"{(f.get('format_note') or ''):<20} "
            f"{(f.get('vcodec') or 'none'):<16} "
            f"{(f.get('acodec') or 'none'):<12} "
            f"{f'{tbr:.0f}k' if tbr else ''}"
        )
    return header + '\n' + '\n'.join(rows)


def _sync_list_formats(url: str) -> str:
    opts = {**_BASE_OPTS, 'quiet': True, 'no_warnings': True, 'verbose': False}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
        return _fmt_formats(info or {})
    except Exception as e:
        return f'(list_formats failed: {e})'


async def list_formats(url: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_sync_list_formats, url))


async def get_video_info(url: str) -> dict:
    return await _extract(url, {'format': 'bestaudio/best'})


async def search_youtube(query: str, limit: int = 10) -> list[dict]:
    data = await _extract(
        f'ytsearch{limit}:{query}',
        {'extract_flat': True, 'noplaylist': False},
    )
    return data.get('entries', [])


def best_stream_url(info: dict) -> str:
    if 'url' in info:
        return info['url']
    formats = [f for f in info.get('formats', []) if f.get('url')]
    audio_only = [f for f in formats if f.get('vcodec') == 'none']
    pool = audio_only or formats
    if not pool:
        raise ValueError(f"No stream URL for: {info.get('webpage_url', '?')}")
    return pool[-1]['url']
