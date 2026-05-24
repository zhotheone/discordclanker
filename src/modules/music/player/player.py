import asyncio
import time
import discord
from loguru import logger
from .queue import Queue, Track
from .filters import build_filter_chain
from .ytdl import get_video_info, best_stream_url, list_formats
from db.pool import execute
import config


class MusicPlayer:
    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        self.queue = Queue()
        self.filter = 'none'
        self.repeat = 'off'   # 'off' | 'one' | 'queue'
        self.volume = 0.8
        self._vc: discord.VoiceClient | None = None
        self._source: discord.PCMVolumeTransformer | None = None
        self._loop = asyncio.get_running_loop()
        self._restarting = False
        self._paused_at: float | None = None
        self.on_track_start = None
        self.on_idle = None

    # ── Connection ────────────────────────────────────────────────────────

    async def join(self, channel: discord.VoiceChannel) -> None:
        if self._vc and self._vc.is_connected():
            await self._vc.move_to(channel)
        else:
            self._vc = await channel.connect()

    # ── Persistent settings ───────────────────────────────────────────────

    async def load_settings(self) -> None:
        try:
            rows = await execute(
                'SELECT volume, filter FROM guild_settings WHERE guild_id = ?',
                (str(self.guild.id),),
            )
            if rows:
                self.volume = rows[0]['volume'] / 100
                self.filter = rows[0]['filter']
        except Exception as e:
            logger.warning(f'load_settings guild={self.guild.id}: {e}')

    async def save_settings(self) -> None:
        try:
            await execute(
                """INSERT INTO guild_settings (guild_id, volume, filter) VALUES (?, ?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET
                       volume = excluded.volume,
                       filter = excluded.filter,
                       updated_at = CURRENT_TIMESTAMP""",
                (str(self.guild.id), round(self.volume * 100), self.filter),
            )
        except Exception as e:
            logger.warning(f'save_settings guild={self.guild.id}: {e}')

    # ── Playback controls ─────────────────────────────────────────────────

    async def enqueue(self, track: Track) -> Track:
        idle = not self._vc or not (self._vc.is_playing() or self._vc.is_paused())
        self.queue.add(track)
        if idle:
            await self._play_next()
        return track

    def pause(self) -> bool | None:
        if not self._vc:
            return None
        if self._vc.is_playing():
            self._vc.pause()
            self._paused_at = time.time()
            return True
        if self._vc.is_paused():
            self._vc.resume()
            if self._paused_at and self.queue.current and self.queue.current.started_at:
                self.queue.current.started_at += time.time() - self._paused_at
            self._paused_at = None
            return False
        return None

    def skip(self) -> None:
        if self._vc and (self._vc.is_playing() or self._vc.is_paused()):
            self._vc.stop()

    def stop(self) -> None:
        self.queue.clear()
        if self._vc:
            self._vc.stop()
            asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)

    def set_volume(self, pct: int) -> None:
        self.volume = max(0.0, min(1.0, pct / 100))
        if self._source:
            self._source.volume = self.volume

    def apply_filter(self, name: str) -> None:
        self.filter = name
        if self._vc and (self._vc.is_playing() or self._vc.is_paused()):
            self._restarting = True
            self._vc.stop()

    # ── Internal ──────────────────────────────────────────────────────────

    async def _start_track(self, track: Track, log_history: bool = True) -> None:
        logger.info(f'Playing {track.title!r} | guild={self.guild.id}')
        try:
            stream_url = track.stream_url or best_stream_url(await get_video_info(track.url))
        except Exception as e:
            fmts = await list_formats(track.url)
            logger.error(f'Stream URL error: {e}\nAvailable formats:\n{fmts}')
            await self._on_finished()
            return

        if not self._vc:
            return

        raw = discord.FFmpegPCMAudio(
            stream_url,
            executable=config.FFMPEG_PATH,
            before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            options=f'-af "{build_filter_chain(self.filter)}" -vn',
        )
        self._source = discord.PCMVolumeTransformer(raw, volume=self.volume)
        self._vc.play(self._source, after=self._after_cb)
        track.started_at = time.time()
        self._paused_at = None
        if log_history:
            asyncio.create_task(self._log_history(track))
        if self.on_track_start:
            asyncio.create_task(self.on_track_start())

    async def _play_next(self) -> None:
        track = self.queue.shift()
        if not track or not self._vc:
            return
        await self._start_track(track)

    async def _replay_current(self) -> None:
        track = self.queue.current
        if not track or not self._vc:
            return
        await self._start_track(track, log_history=False)

    def _after_cb(self, error: Exception | None) -> None:
        self._source = None
        if error:
            logger.error(f'Playback error guild={self.guild.id}: {error}')
        asyncio.run_coroutine_threadsafe(self._on_finished(), self._loop)

    async def _on_finished(self) -> None:
        if self._vc and self._vc.is_playing():
            return

        if self._restarting:
            self._restarting = False
            await self._replay_current()
            return

        if self.repeat == 'one':
            await self._replay_current()
            return

        if self.repeat == 'queue' and self.queue.current:
            self.queue.add(self.queue.current)

        if not self.queue.is_empty:
            await self._play_next()
        elif self.on_idle:
            asyncio.create_task(self.on_idle())

    async def _disconnect(self) -> None:
        if self._vc:
            await self._vc.disconnect()
            self._vc = None

    async def _log_history(self, track: Track) -> None:
        try:
            await execute(
                'INSERT INTO play_history (guild_id, user_id, title, url, duration) VALUES (?, ?, ?, ?, ?)',
                (str(self.guild.id), str(track.requested_by.id) if track.requested_by else '0',
                 track.title, track.url, track.duration),
            )
        except Exception:
            pass

    # ── State ─────────────────────────────────────────────────────────────

    @property
    def current(self) -> Track | None:
        return self.queue.current

    @property
    def is_connected(self) -> bool:
        return bool(self._vc and self._vc.is_connected())

    @property
    def is_playing(self) -> bool:
        return bool(self._vc and self._vc.is_playing())

    @property
    def is_paused(self) -> bool:
        return bool(self._vc and self._vc.is_paused())

    @property
    def elapsed_time(self) -> float | None:
        track = self.queue.current
        if not track or not track.started_at:
            return None
        if self._vc and self._vc.is_paused() and self._paused_at:
            return self._paused_at - track.started_at
        if self._vc and self._vc.is_playing():
            return time.time() - track.started_at
        return None
