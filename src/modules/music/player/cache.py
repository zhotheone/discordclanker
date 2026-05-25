import asyncio
import re
import threading
from functools import partial
from pathlib import Path

import yt_dlp
from loguru import logger

import config

_VID_RE = re.compile(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})')


def _video_id(url: str) -> str | None:
    m = _VID_RE.search(url)
    return m.group(1) if m else None


class AudioCache:
    def __init__(self) -> None:
        self._dir = Path(config.CACHE_DIR)
        self._max_bytes = config.CACHE_MAX_BYTES
        self._dir.mkdir(parents=True, exist_ok=True)
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    def _find(self, video_id: str) -> Path | None:
        for f in self._dir.glob(f'{video_id}.*'):
            if f.is_file():
                f.touch()
                return f
        return None

    def get(self, url: str) -> str | None:
        vid = _video_id(url)
        if not vid:
            return None
        p = self._find(vid)
        return str(p) if p else None

    def _total_bytes(self) -> int:
        try:
            return sum(f.stat().st_size for f in self._dir.iterdir() if f.is_file())
        except OSError:
            return 0

    def _evict(self) -> None:
        while self._total_bytes() > self._max_bytes:
            try:
                candidates = sorted(
                    [f for f in self._dir.iterdir() if f.is_file()],
                    key=lambda f: f.stat().st_mtime,
                )
            except OSError:
                break
            if not candidates:
                break
            candidates[0].unlink(missing_ok=True)
            logger.info(f'Cache evict: {candidates[0].name}')

    def _build_opts(self) -> dict:
        import os as _os
        opts: dict = {
            'quiet': True,
            'no_warnings': True,
            'verbose': False,
            'noplaylist': True,
        }
        if _os.path.exists(config.COOKIES_PATH):
            opts['cookiefile'] = config.COOKIES_PATH
        if config.YTDLP_PROXY:
            opts['proxy'] = config.YTDLP_PROXY
        return opts

    def _sync_download(self, url: str, video_id: str) -> None:
        outtmpl = str(self._dir / f'{video_id}.%(ext)s')
        opts = {**self._build_opts(), 'format': 'bestaudio/best', 'outtmpl': outtmpl}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            with self._lock:
                self._evict()
            logger.debug(f'Cached: {video_id}')
        except Exception as e:
            logger.warning(f'Cache download failed ({video_id}): {e}')
        finally:
            with self._lock:
                self._pending.discard(video_id)

    async def schedule_download(self, url: str) -> None:
        """Start a background download if the track is not already cached or downloading."""
        vid = _video_id(url)
        if not vid:
            return
        with self._lock:
            if vid in self._pending or self._find(vid):
                return
            self._pending.add(vid)
        asyncio.get_running_loop().run_in_executor(
            None, partial(self._sync_download, url, vid)
        )


audio_cache = AudioCache()
