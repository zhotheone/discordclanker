import discord
from ..player.queue import Track


def fmt_dur(seconds) -> str:
    if not seconds:
        return 'Unknown'
    m, s = divmod(int(seconds), 60)
    return f'{m}:{s:02d}'


def progress_bar(elapsed: float, total: float, width: int = 20) -> str:
    filled = int(min(1.0, elapsed / total) * width)
    bar = '▓' * filled + '░' * (width - filled)
    m_e, s_e = divmod(int(elapsed), 60)
    m_t, s_t = divmod(int(total), 60)
    return f'`{bar}` {m_e}:{s_e:02d} / {m_t}:{s_t:02d}'


def track_embed(track: Track, heading: str, elapsed: float | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=heading,
        description=f'[{track.title}]({track.url})',
        color=0xFF0000,
    )
    if elapsed is not None and track.duration:
        embed.add_field(
            name='Progress',
            value=progress_bar(elapsed, track.duration),
            inline=False,
        )
    else:
        embed.add_field(name='Duration', value=track.fmt_duration, inline=True)
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    if track.requested_by:
        embed.set_footer(text=f'Requested by {track.requested_by}')
    return embed


def loading_embed(title: str, url: str | None = None) -> discord.Embed:
    desc = f'Loading [{title}]({url})...' if url else f'Loading **{title[:100]}**...'
    return discord.Embed(title='Loading...', description=desc, color=0xFF0000)


def queue_embed(player) -> discord.Embed:
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
                f'**{i + 1}.** [{t.title[:55]}]({t.url}) `{t.fmt_duration}`'
                for i, t in enumerate(upcoming)
            ),
            inline=False,
        )
    return embed
