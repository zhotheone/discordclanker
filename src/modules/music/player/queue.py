from dataclasses import dataclass, field
from typing import Optional
import discord


@dataclass
class Track:
    title: str
    url: str
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    requested_by: Optional[discord.User] = None
    stream_url: Optional[str] = None
    started_at: Optional[float] = field(default=None, repr=False)

    @property
    def fmt_duration(self) -> str:
        if not self.duration:
            return 'Live / Unknown'
        m, s = divmod(self.duration, 60)
        return f'{m}:{s:02d}'


class Queue:
    def __init__(self) -> None:
        self._tracks: list[Track] = []
        self._current: Optional[Track] = None

    @property
    def current(self) -> Optional[Track]:
        return self._current

    @property
    def size(self) -> int:
        return len(self._tracks)

    @property
    def is_empty(self) -> bool:
        return not self._tracks

    def add(self, track: Track) -> None:
        self._tracks.append(track)

    def shift(self) -> Optional[Track]:
        self._current = self._tracks.pop(0) if self._tracks else None
        return self._current

    def clear(self) -> None:
        self._tracks.clear()
        self._current = None

    def peek(self, n: int = 10) -> list[Track]:
        return self._tracks[:n]

    def shuffle(self) -> None:
        import random
        random.shuffle(self._tracks)
