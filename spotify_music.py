"""Integração opcional do Spotify ao sistema de música.

O Spotify é usado somente para pesquisa/metadados. A reprodução continua sendo
resolvida por uma fonte de áudio compatível com o player atual (YouTube/yt-dlp),
pois a Web API do Spotify não fornece o stream bruto da faixa para bots.
"""
import asyncio
import base64
import os
import re
import time

import aiohttp

import music_system as music

_INSTALLED = False
_TOKEN = None
_TOKEN_EXPIRES = 0.0
_TOKEN_LOCK = asyncio.Lock()

SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID', '').strip()
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET', '').strip()

_SPOTIFY_TRACK_RE = re.compile(r'(?:open\.spotify\.com/track/|spotify:track:)([A-Za-z0-9]+)', re.I)


def enabled():
    return bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)


async def _token():
    global _TOKEN, _TOKEN_EXPIRES
    if _TOKEN and time.monotonic() < _TOKEN_EXPIRES - 30:
        return _TOKEN
    async with _TOKEN_LOCK:
        if _TOKEN and time.monotonic() < _TOKEN_EXPIRES - 30:
            return _TOKEN
        auth = base64.b64encode(f'{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}'.encode()).decode()
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                'https://accounts.spotify.com/api/token',
                data={'grant_type': 'client_credentials'},
                headers={'Authorization': f'Basic {auth}'},
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f'Spotify OAuth respondeu HTTP {response.status}')
                data = await response.json()
        _TOKEN = data.get('access_token')
        if not _TOKEN:
            raise RuntimeError('Spotify não retornou access_token')
        _TOKEN_EXPIRES = time.monotonic() + int(data.get('expires_in') or 3600)
        return _TOKEN


async def _api_get(path, params=None):
    if not enabled():
        return None
    token = await _token()
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            f'https://api.spotify.com/v1/{path.lstrip("/")}',
            params=params,
            headers={'Authorization': f'Bearer {token}'},
        ) as response:
            if response.status == 401:
                global _TOKEN, _TOKEN_EXPIRES
                _TOKEN = None
                _TOKEN_EXPIRES = 0
                return await _api_get(path, params=params)
            if response.status != 200:
                return None
            return await response.json()


def _spotify_row(track):
    if not track:
        return None
    artists = ', '.join(a.get('name', '') for a in track.get('artists') or [] if a.get('name')) or 'Artista desconhecido'
    images = ((track.get('album') or {}).get('images') or [])
    image = images[0].get('url', '') if images else ''
    track_id = track.get('id')
    if not track_id:
        return None
    return {
        'id': f'spotify:{track_id}',
        'title': str(track.get('name') or 'Sem título'),
        'url': f'spotify:track:{track_id}',
        'duration': int((track.get('duration_ms') or 0) / 1000),
        'uploader': f'Spotify • {artists}',
        'thumbnail': str(image or ''),
        'view_count': 0,
        '_spotify_artist': artists,
        '_source': 'spotify',
    }


async def search_spotify(query, limit=8):
    if not enabled():
        return []
    try:
        data = await _api_get('search', {'q': query, 'type': 'track', 'limit': max(1, min(int(limit), 10))})
        items = (((data or {}).get('tracks') or {}).get('items') or [])
        return [row for item in items if (row := _spotify_row(item))]
    except Exception as exc:
        if os.getenv('FSOCIETY_VERBOSE_LOGS', '0').strip().lower() in {'1', 'true', 'yes', 'on'}:
            print(f'Spotify: busca indisponível temporariamente | {type(exc).__name__}: {exc}')
        return []


async def spotify_track(track_id):
    if not enabled():
        return None
    try:
        return await _api_get(f'tracks/{track_id}')
    except Exception:
        return None


def _spotify_id(value):
    match = _SPOTIFY_TRACK_RE.search(str(value or ''))
    return match.group(1) if match else None


async def install_search_wrapper():
    pass


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    previous_search = music.search_tracks
    previous_resolve = music.resolve_track

    async def combined_search(query: str, limit=10):
        yt_task = asyncio.create_task(previous_search(query, limit))
        sp_task = asyncio.create_task(search_spotify(query, min(8, limit))) if enabled() else None
        youtube = await yt_task
        spotify = await sp_task if sp_task else []

        merged = []
        seen = set()
        for row in spotify + list(youtube or []):
            title = re.sub(r'[^a-z0-9]+', ' ', str(row.get('title', '')).lower()).strip()
            artist = re.sub(r'[^a-z0-9]+', ' ', str(row.get('_spotify_artist') or row.get('uploader', '')).lower()).strip()
            key = (title, artist.replace('spotify ', '').strip())
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
            if len(merged) >= limit:
                break
        return merged

    async def resolve_with_spotify(query: str, requester_id: int):
        track_id = _spotify_id(query)
        if not track_id:
            return await previous_resolve(query, requester_id)
        if not enabled():
            raise ValueError('A pesquisa do Spotify ainda não foi configurada no servidor.')
        data = await spotify_track(track_id)
        if not data:
            raise ValueError('Não consegui consultar essa faixa no Spotify agora.')
        title = str(data.get('name') or '').strip()
        artists = ', '.join(a.get('name', '') for a in data.get('artists') or [] if a.get('name')).strip()
        if not title:
            raise ValueError('A faixa do Spotify não possui metadados suficientes.')
        search_query = f'{title} {artists}'.strip()
        track = await previous_resolve(search_query, requester_id)
        track.title = title
        if artists:
            track.uploader = artists
        images = ((data.get('album') or {}).get('images') or [])
        if images:
            track.thumbnail = images[0].get('url') or track.thumbnail
        return track

    music.search_tracks = combined_search
    music.resolve_track = resolve_with_spotify
    print('Spotify: pesquisa de faixas integrada ao sistema de música' if enabled() else 'Spotify: integração carregada; credenciais não configuradas')
