import discord
from loguru import logger
from .player import MusicPlayer


class PlayerManager:
    def __init__(self) -> None:
        self._players: dict[int, MusicPlayer] = {}

    def get(self, guild_id: int) -> MusicPlayer | None:
        return self._players.get(guild_id)

    def get_or_create(self, guild: discord.Guild) -> MusicPlayer:
        if guild.id not in self._players:
            self._players[guild.id] = MusicPlayer(guild)
            logger.debug(f'Created MusicPlayer guild={guild.id}')
        return self._players[guild.id]

    def all_players(self) -> list[MusicPlayer]:
        return list(self._players.values())

    def destroy(self, guild_id: int) -> None:
        player = self._players.pop(guild_id, None)
        if player:
            player.stop()
            logger.debug(f'Destroyed MusicPlayer guild={guild_id}')
