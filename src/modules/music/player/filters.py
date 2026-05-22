_LN = 'loudnorm=I=-14:TP=-2:LRA=11'

AUDIO_FILTERS: dict[str, str] = {
    'none':          _LN,
    'slowed+reverb': f'atempo=0.85,{_LN},aecho=0.8:0.88:60:0.4',
    '8d':            f'{_LN},apulsator=hz=0.125,extrastereo=m=2.5',
    'nightcore':     f'asetrate=48000*1.25,aresample=48000,{_LN}',
    'vaporwave':     f'asetrate=48000*0.8,aresample=48000,{_LN}',
    'bassboost':     f'bass=g=8,{_LN}',
}

FILTER_NAMES = list(AUDIO_FILTERS)


def build_filter_chain(name: str) -> str:
    return AUDIO_FILTERS.get(name, AUDIO_FILTERS['none'])
