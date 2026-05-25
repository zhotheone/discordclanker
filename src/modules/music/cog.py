import asyncio
import re
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
from loguru import logger

from .player.manager import PlayerManager
from .player.queue import Track
from .player.ytdl import get_video_info, search_youtube, best_stream_url, list_formats
from .player.filters import FILTER_NAMES
from .ui.search_select import SearchView
from .ui.embeds import fmt_dur, track_embed, loading_embed, queue_embed
from .ui.player_view import PlayerView

_YT_RE = re.compile(r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/')

_ALONE_TIMEOUT  = 60
_IDLE_TIMEOUT   = 60
_PAUSED_TIMEOUT = 300
_PROGRESS_INTERVAL = 15


class GuildSession:
    """Per-guild state: the panel message and all named background tasks."""

    __slots__ = ('panel', '_tasks')

    def __init__(self) -> None:
        self.panel: discord.Message | None = None
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self, name: str, coro) -> None:
        """(Re)start a named task, cancelling any prior instance."""
        old = self._tasks.pop(name, None)
        if old and not old.done():
            old.cancel()
        self._tasks[name] = asyncio.create_task(coro)

    def cancel(self, *names: str) -> None:
        for name in names:
            task = self._tasks.pop(name, None)
            if task and not task.done():
                task.cancel()

    def teardown(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self.panel = None


class MusicCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.manager = PlayerManager()
        self._sessions: dict[int, GuildSession] = {}

    def _session(self, guild_id: int) -> GuildSession:
        if guild_id not in self._sessions:
            self._sessions[guild_id] = GuildSession()
        return self._sessions[guild_id]

    def _teardown(self, guild_id: int) -> None:
        session = self._sessions.pop(guild_id, None)
        if session:
            session.teardown()
        self.manager.destroy(guild_id)

    # ── Presence ──────────────────────────────────────────────────────────────

    async def _update_presence(self) -> None:
        playing = [
            p for p in self.manager.all_players()
            if p.current and (p.is_playing or p.is_paused)
        ]

        if not playing:
            await self.bot.change_presence(activity=None)
            return

        if len(playing) > 1:
            await self.bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.listening,
                    name=f'music in {len(playing)} servers',
                )
            )
            return

        player = playing[0]
        track = player.current

        if player.is_paused:
            await self.bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.listening,
                    name=f'{track.title} (Paused)',  # type: ignore[union-attr]
                )
            )
        else:
            kwargs: dict = dict(
                type=discord.ActivityType.listening,
                name=track.title,  # type: ignore[union-attr]
            )
            if track.started_at:  # type: ignore[union-attr]
                kwargs['start'] = datetime.fromtimestamp(
                    track.started_at, tz=timezone.utc  # type: ignore[union-attr]
                )
                if track.duration:  # type: ignore[union-attr]
                    kwargs['end'] = datetime.fromtimestamp(
                        track.started_at + track.duration, tz=timezone.utc  # type: ignore[union-attr]
                    )
            await self.bot.change_presence(activity=discord.Activity(**kwargs))

    # ── Panel helpers ──────────────────────────────────────────────────────────

    def _make_view(self, guild_id: int) -> PlayerView:
        async def refresh() -> None:
            await self._refresh_panel(guild_id)

        async def on_pause(paused: bool) -> None:
            await self._on_pause_change(guild_id, paused)

        return PlayerView(self.manager, guild_id, refresh_cb=refresh, pause_cb=on_pause)

    async def _refresh_panel(self, guild_id: int) -> None:
        session = self._sessions.get(guild_id)
        if not session or not session.panel:
            return
        view = self._make_view(guild_id)
        try:
            await session.panel.edit(embed=view.build_embed(), view=view)
        except discord.NotFound:
            session.panel = None
        except discord.HTTPException:
            pass

    async def _set_panel(self, guild_id: int, msg: discord.Message) -> None:
        """Register msg as the Now Playing panel and immediately render it with the progress bar."""
        session = self._session(guild_id)
        session.panel = msg
        view = self._make_view(guild_id)
        try:
            await msg.edit(embed=view.build_embed(), view=view)
        except discord.HTTPException:
            pass

    def _setup_player(self, player, guild_id: int) -> None:
        async def on_track_start() -> None:
            session = self._session(guild_id)
            session.cancel('paused')
            session.start('progress', self._progress_loop(guild_id))
            await self._refresh_panel(guild_id)
            await self._update_presence()

        async def on_idle() -> None:
            session = self._session(guild_id)
            session.cancel('progress')
            session.start('idle', self._idle_timeout(guild_id))
            await self._update_presence()

        player.on_track_start = on_track_start
        player.on_idle = on_idle

    # ── /play ──────────────────────────────────────────────────────────────────

    @app_commands.command(name='play', description='Play a YouTube URL or search for a track')
    @app_commands.describe(query='YouTube URL or search query')
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            return await interaction.response.send_message('You must be in a voice channel.', ephemeral=True)
        vc = member.voice.channel
        await interaction.response.defer(thinking=True)

        if _YT_RE.match(query):
            await self._play_url(interaction, vc, query)
        else:
            await self._show_search(interaction, vc, query)

    async def _play_url(
        self,
        interaction: discord.Interaction,
        vc: discord.VoiceChannel,
        url: str,
    ) -> None:
        gid: int = interaction.guild_id  # type: ignore[assignment]
        loading = await interaction.followup.send(embed=loading_embed('Fetching track info...'))
        try:
            info = await get_video_info(url)
            track = Track(
                title=info.get('title', 'Unknown'),
                url=info.get('webpage_url') or url,
                duration=info.get('duration'),
                thumbnail=info.get('thumbnail'),
                requested_by=interaction.user,
                stream_url=best_stream_url(info),
            )

            player = self.manager.get_or_create(interaction.guild)  # type: ignore[arg-type]
            if not player.is_connected:
                await player.join(vc)
                await player.load_settings()
                self._setup_player(player, gid)

            session = self._session(gid)
            session.cancel('idle')
            await player.enqueue(track)

            if session.panel:
                await self._refresh_panel(gid)
                try:
                    await loading.edit(content=f'Added to queue: **{track.title}**', embed=None)
                except discord.HTTPException:
                    pass
            else:
                await self._set_panel(gid, loading)

        except Exception as e:
            fmts = await list_formats(url)
            logger.error(f'/play url error: {e}\nAvailable formats:\n{fmts}')
            try:
                await loading.edit(content=f'Error: {e}', embed=None)
            except discord.HTTPException:
                pass

    async def _show_search(
        self,
        interaction: discord.Interaction,
        vc: discord.VoiceChannel,
        query: str,
    ) -> None:
        gid: int = interaction.guild_id  # type: ignore[assignment]
        try:
            results = await search_youtube(query, 10)
            if not results:
                return await interaction.followup.send('No results found.')

            entries = results[:10]
            desc = '\n'.join(
                f"**{i + 1}.** [{(r.get('title') or 'Unknown')[:80]}]"
                f"({r.get('url') or ''}) `{fmt_dur(r.get('duration'))}`"
                for i, r in enumerate(entries)
            )
            embed = discord.Embed(title=f'Search: {query}', description=desc, color=0xFF0000)

            async def panel_cb(_: discord.Embed, loading_msg: discord.Message | None = None) -> None:
                session = self._session(gid)
                if session.panel:
                    await self._refresh_panel(gid)
                    if loading_msg:
                        try:
                            await loading_msg.edit(content='Added to queue.', embed=None)
                        except discord.HTTPException:
                            pass
                elif loading_msg:
                    await self._set_panel(gid, loading_msg)
                else:
                    try:
                        view = self._make_view(gid)
                        msg = await interaction.channel.send(  # type: ignore[union-attr]
                            embed=view.build_embed(), view=view,
                        )
                        session.panel = msg
                    except discord.HTTPException:
                        pass

            view = SearchView(
                entries, vc, self.manager,
                panel_cb=panel_cb,
                setup_cb=self._setup_player,
                cancel_idle_cb=lambda: self._session(gid).cancel('idle'),
            )
            view.message = await interaction.followup.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f'/play search error: {e}')
            await interaction.followup.send(f'Search failed: {e}')

    # ── /pause ─────────────────────────────────────────────────────────────────

    @app_commands.command(name='pause', description='Toggle pause / resume')
    async def pause(self, interaction: discord.Interaction) -> None:
        gid: int = interaction.guild_id  # type: ignore[assignment]
        player = self.manager.get(gid)
        if not player:
            return await interaction.response.send_message('Nothing is playing.', ephemeral=True)
        result = player.pause()
        if result is None:
            return await interaction.response.send_message('Nothing to pause or resume.', ephemeral=True)
        await self._on_pause_change(gid, result)
        await interaction.response.send_message('Paused.' if result else 'Resumed.', ephemeral=True)
        asyncio.create_task(self._refresh_panel(gid))

    # ── /skip ──────────────────────────────────────────────────────────────────

    @app_commands.command(name='skip', description='Skip the current track')
    async def skip(self, interaction: discord.Interaction) -> None:
        gid: int = interaction.guild_id  # type: ignore[assignment]
        player = self.manager.get(gid)
        if not player or not player.current:
            return await interaction.response.send_message('Nothing is playing.', ephemeral=True)
        title = player.current.title
        player.skip()
        await interaction.response.send_message(f'Skipped **{title}**.', ephemeral=True)

    # ── /stop ──────────────────────────────────────────────────────────────────

    @app_commands.command(name='stop', description='Stop playback, clear queue and disconnect')
    async def stop(self, interaction: discord.Interaction) -> None:
        gid: int = interaction.guild_id  # type: ignore[assignment]
        if not self.manager.get(gid):
            return await interaction.response.send_message('Nothing is playing.', ephemeral=True)
        self._teardown(gid)
        await interaction.response.send_message('Stopped and disconnected.')
        await self._update_presence()

    # ── /queue ─────────────────────────────────────────────────────────────────

    @app_commands.command(name='queue', description='Show the current queue')
    async def queue(self, interaction: discord.Interaction) -> None:
        gid: int = interaction.guild_id  # type: ignore[assignment]
        player = self.manager.get(gid)
        if not player or (not player.current and player.queue.is_empty):
            return await interaction.response.send_message('The queue is empty.', ephemeral=True)
        await interaction.response.send_message(embed=queue_embed(player), ephemeral=True)

    # ── /filter ────────────────────────────────────────────────────────────────

    @app_commands.command(name='filter', description='Set audio filter (takes effect on next track)')
    @app_commands.describe(name='Filter to apply')
    @app_commands.choices(name=[app_commands.Choice(name=f, value=f) for f in FILTER_NAMES])
    async def filter(self, interaction: discord.Interaction, name: str) -> None:
        gid: int = interaction.guild_id  # type: ignore[assignment]
        player = self.manager.get(gid)
        if player:
            player.apply_filter(name)
            await player.save_settings()
        await interaction.response.send_message(f'Filter set to **{name}**.', ephemeral=True)
        asyncio.create_task(self._refresh_panel(gid))

    # ── /volume ────────────────────────────────────────────────────────────────

    @app_commands.command(name='volume', description='Set playback volume (0–100)')
    @app_commands.describe(level='Volume level (0–100)')
    async def volume(self, interaction: discord.Interaction, level: app_commands.Range[int, 0, 100]) -> None:
        gid: int = interaction.guild_id  # type: ignore[assignment]
        player = self.manager.get(gid)
        if player:
            player.set_volume(level)
            await player.save_settings()
        await interaction.response.send_message(f'Volume set to **{level}%**.', ephemeral=True)

    # ── Pause/resume ───────────────────────────────────────────────────────────

    async def _on_pause_change(self, guild_id: int, paused: bool) -> None:
        session = self._session(guild_id)
        if paused:
            session.cancel('progress')
            session.start('paused', self._paused_timeout(guild_id))
        else:
            session.cancel('paused')
            session.start('progress', self._progress_loop(guild_id))
        await self._update_presence()

    # ── Background tasks ───────────────────────────────────────────────────────

    async def _progress_loop(self, guild_id: int) -> None:
        while True:
            await asyncio.sleep(_PROGRESS_INTERVAL)
            player = self.manager.get(guild_id)
            if not player or not player.is_playing:
                break
            await self._refresh_panel(guild_id)

    async def _paused_timeout(self, guild_id: int) -> None:
        await asyncio.sleep(_PAUSED_TIMEOUT)
        player = self.manager.get(guild_id)
        if player and player.is_paused:
            logger.info(f'Auto-leaving guild={guild_id}: paused for {_PAUSED_TIMEOUT}s')
            self._teardown(guild_id)
            await self._update_presence()

    async def _idle_timeout(self, guild_id: int) -> None:
        await asyncio.sleep(_IDLE_TIMEOUT)
        player = self.manager.get(guild_id)
        if player and not player.is_playing and not player.is_paused:
            logger.info(f'Auto-leaving guild={guild_id}: idle for {_IDLE_TIMEOUT}s')
            self._teardown(guild_id)
            await self._update_presence()

    async def _alone_timeout(self, guild_id: int) -> None:
        await asyncio.sleep(_ALONE_TIMEOUT)
        player = self.manager.get(guild_id)
        if player and player._vc and not any(not m.bot for m in player._vc.channel.members):
            logger.info(f'Auto-leaving guild={guild_id}: alone for {_ALONE_TIMEOUT}s')
            self._teardown(guild_id)
            await self._update_presence()

    # ── Voice events ───────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        guild_id = member.guild.id
        player = self.manager.get(guild_id)
        if not player or not player._vc:
            return

        humans = [m for m in player._vc.channel.members if not m.bot]
        if not humans:
            self._session(guild_id).start('alone', self._alone_timeout(guild_id))
        else:
            self._session(guild_id).cancel('alone')


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MusicCog(bot))
