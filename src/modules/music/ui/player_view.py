import asyncio
from typing import Callable, Awaitable, Optional
import discord
from ..player.manager import PlayerManager
from ..player.filters import FILTER_NAMES
from .search_select import _track_embed


class VolumeModal(discord.ui.Modal, title='Set Volume'):
    level = discord.ui.TextInput(
        label='Volume (0–100)',
        placeholder='80',
        min_length=1,
        max_length=3,
    )

    def __init__(self, manager: PlayerManager, guild_id: int) -> None:
        super().__init__()
        self._manager = manager
        self._guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            val = max(0, min(100, int(self.level.value)))
        except ValueError:
            return await interaction.response.send_message('Enter a number 0–100.', ephemeral=True)
        player = self._manager.get(self._guild_id)
        if player:
            player.set_volume(val)
            await player.save_settings()
        await interaction.response.send_message(f'Volume set to **{val}%**.', ephemeral=True)


class FilterSelect(discord.ui.Select):
    def __init__(self, manager: PlayerManager, guild_id: int, refresh_cb=None) -> None:
        self._manager = manager
        self._guild_id = guild_id
        self._refresh_cb = refresh_cb
        player = manager.get(guild_id)
        current = player.filter if player else 'none'
        options = [
            discord.SelectOption(label=f, value=f, default=(f == current))
            for f in FILTER_NAMES
        ]
        super().__init__(placeholder='Audio filter...', options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        player = self._manager.get(self._guild_id)
        if player:
            player.apply_filter(self.values[0])
            await player.save_settings()
        await interaction.response.send_message(
            f'Filter set to **{self.values[0]}**.',
            ephemeral=True,
        )
        if self._refresh_cb:
            asyncio.create_task(self._refresh_cb())


class PlayerView(discord.ui.View):
    def __init__(
        self,
        manager: PlayerManager,
        guild_id: int,
        refresh_cb: Optional[Callable[[], Awaitable[None]]] = None,
        presence_cb: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        super().__init__(timeout=3600)
        self._manager = manager
        self._guild_id = guild_id
        self._refresh_cb = refresh_cb
        self._presence_cb = presence_cb
        self.add_item(FilterSelect(manager, guild_id, refresh_cb))
        player = manager.get(guild_id)
        if player:
            _repeat_labels = {
                'off': ('Repeat: Off', '🔁', discord.ButtonStyle.secondary),
                'one': ('Repeat: One', '🔂', discord.ButtonStyle.success),
                'queue': ('Repeat: Queue', '🔁', discord.ButtonStyle.primary),
            }
            label, emoji, style = _repeat_labels[player.repeat]
            self.repeat.label = label
            self.repeat.emoji = emoji
            self.repeat.style = style

    def _player(self):
        return self._manager.get(self._guild_id)

    def build_embed(self) -> discord.Embed:
        player = self._player()
        if player and player.current:
            title = '▶ Now Playing' if player.is_playing else '⏸ Paused'
            return _track_embed(player.current, title)
        return discord.Embed(title='Music Controls', color=0xFF0000, description='Nothing playing.')

    async def _refresh(self) -> None:
        if self._refresh_cb:
            await self._refresh_cb()

    @discord.ui.button(label='Pause', emoji='⏸', style=discord.ButtonStyle.secondary, row=0)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = self._player()
        if not player:
            return await interaction.response.send_message('Nothing is playing.', ephemeral=True)
        result = player.pause()
        if result is None:
            return await interaction.response.send_message('Nothing to pause or resume.', ephemeral=True)
        button.label = 'Resume' if result else 'Pause'
        button.emoji = '▶' if result else '⏸'
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        if self._presence_cb:
            asyncio.create_task(self._presence_cb())

    @discord.ui.button(label='Skip', emoji='⏭', style=discord.ButtonStyle.secondary, row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = self._player()
        if not player or not player.current:
            return await interaction.response.send_message('Nothing is playing.', ephemeral=True)
        title = player.current.title
        player.skip()
        await interaction.response.send_message(f'Skipped **{title}**.', ephemeral=True)
        asyncio.create_task(self._refresh())

    @discord.ui.button(label='Stop', emoji='⏹', style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._player():
            return await interaction.response.send_message('Nothing is playing.', ephemeral=True)
        self._manager.destroy(self._guild_id)
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label='Queue', emoji='📋', style=discord.ButtonStyle.primary, row=0)
    async def queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = self._player()
        if not player or (not player.current and player.queue.is_empty):
            return await interaction.response.send_message('The queue is empty.', ephemeral=True)
        embed = discord.Embed(title='Queue', color=0xFF0000)
        cur = player.current
        if cur:
            icon = '▶' if player.is_playing else '⏸'
            embed.add_field(
                name=f'{icon} Now Playing',
                value=f'[{cur.title}]({cur.url}) `{cur.fmt_duration}`',
                inline=False,
            )
        upcoming = player.queue.peek(10)
        if upcoming:
            count = player.queue.size
            embed.add_field(
                name=f'Up Next ({count} track{"s" if count != 1 else ""})',
                value='\n'.join(
                    f'**{i+1}.** [{t.title[:55]}]({t.url}) `{t.fmt_duration}`'
                    for i, t in enumerate(upcoming)
                ),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label='Volume', emoji='🔊', style=discord.ButtonStyle.secondary, row=0)
    async def volume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(VolumeModal(self._manager, self._guild_id))

    @discord.ui.button(label='Shuffle', emoji='🔀', style=discord.ButtonStyle.secondary, row=2)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = self._player()
        if not player or player.queue.is_empty:
            return await interaction.response.send_message('Nothing in queue to shuffle.', ephemeral=True)
        player.queue.shuffle()
        await interaction.response.send_message('Queue shuffled.', ephemeral=True)
        asyncio.create_task(self._refresh())

    @discord.ui.button(label='Repeat: Off', emoji='🔁', style=discord.ButtonStyle.secondary, row=2)
    async def repeat(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        player = self._player()
        if not player:
            return await interaction.response.send_message('Nothing is playing.', ephemeral=True)
        cycle = {'off': 'one', 'one': 'queue', 'queue': 'off'}
        player.repeat = cycle[player.repeat]
        labels = {'off': ('Repeat: Off', '🔁', discord.ButtonStyle.secondary),
                  'one': ('Repeat: One', '🔂', discord.ButtonStyle.success),
                  'queue': ('Repeat: Queue', '🔁', discord.ButtonStyle.primary)}
        button.label, button.emoji, button.style = labels[player.repeat]
        await interaction.response.edit_message(view=self)
