from typing import Callable, Awaitable, Optional
import discord
from loguru import logger
from ..player.manager import PlayerManager
from ..player.queue import Track
from ..player.ytdl import get_video_info, best_stream_url
from .embeds import fmt_dur, track_embed, loading_embed


class SearchView(discord.ui.View):
    def __init__(
        self,
        results: list,
        voice_channel: discord.VoiceChannel,
        manager: PlayerManager,
        panel_cb: Optional[Callable] = None,
        setup_cb: Optional[Callable] = None,
        cancel_idle_cb: Optional[Callable] = None,
    ) -> None:
        super().__init__(timeout=60)
        self.message: discord.Message | None = None
        self.add_item(SearchSelect(results, voice_channel, manager, panel_cb, setup_cb, cancel_idle_cb))

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class SearchSelect(discord.ui.Select):
    def __init__(
        self,
        results: list,
        voice_channel: discord.VoiceChannel,
        manager: PlayerManager,
        panel_cb: Optional[Callable] = None,
        setup_cb: Optional[Callable] = None,
        cancel_idle_cb: Optional[Callable] = None,
    ) -> None:
        self._vc = voice_channel
        self._manager = manager
        self._panel_cb = panel_cb
        self._setup_cb = setup_cb
        self._cancel_idle_cb = cancel_idle_cb
        self._url_to_result = {
            r.get('url') or f"https://www.youtube.com/watch?v={r['id']}": r
            for r in results[:10]
        }
        options = [
            discord.SelectOption(
                label=f"{i + 1}. {(r.get('title') or 'Unknown')[:90]}",
                description=fmt_dur(r.get('duration')),
                value=r.get('url') or f"https://www.youtube.com/watch?v={r['id']}",
            )
            for i, r in enumerate(results[:10])
        ]
        super().__init__(placeholder='Choose a track...', min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        url = self.values[0]
        result = self._url_to_result.get(url, {})
        known_title = (result.get('title') or 'Unknown')[:100]

        await interaction.response.defer(thinking=True)

        loading = await interaction.followup.send(
            embed=loading_embed(known_title)
        )

        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await loading.edit(content='Join a voice channel first.', embed=None)
            return
        vc = member.voice.channel

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
            player = self._manager.get_or_create(interaction.guild)  # type: ignore[arg-type]
            if not player.is_connected:
                await player.join(vc)
                await player.load_settings()
                if self._setup_cb:
                    self._setup_cb(player, interaction.guild_id)

            if self._cancel_idle_cb:
                self._cancel_idle_cb()
            was_idle = not player.is_playing
            await player.enqueue(track)
            heading = 'Now playing' if was_idle else 'Added to queue'
            embed = track_embed(track, heading)

            if self._panel_cb:
                await self._panel_cb(embed, loading)
            else:
                await loading.edit(embed=embed)

            self.view.stop()  # type: ignore[union-attr]
        except Exception as e:
            logger.error(f'SearchSelect error: {e}')
            await loading.edit(content=f'Failed: {e}', embed=None)
