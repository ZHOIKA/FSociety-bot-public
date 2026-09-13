"""Reprodução e diagnóstico de áudio do player de música.

Renova a URL de áudio imediatamente antes de tocar, encaminha os headers
fornecidos pelo yt-dlp ao FFmpeg e registra erros reais de reprodução.
"""
import asyncio
import shlex
import shutil
import subprocess

import discord
import imageio_ffmpeg

import music_system as music

_INSTALLED = False
_FFMPEG_CACHE = None


def _header_options(headers):
    if not headers:
        return ''
    allowed = ('User-Agent', 'Referer', 'Origin', 'Accept', 'Accept-Language')
    lines = []
    for key in allowed:
        value = headers.get(key) or headers.get(key.lower())
        if value:
            value = str(value).replace('\r', '').replace('\n', '')
            lines.append(f'{key}: {value}')
    if not lines:
        return ''
    blob = '\r\n'.join(lines) + '\r\n'
    return f' -headers {shlex.quote(blob)}'


def _probe_ffmpeg(path):
    try:
        result = subprocess.run([path,'-hide_banner','-version'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=8,check=False)
        first = (result.stdout or '').splitlines()[0] if result.stdout else ''
        return result.returncode == 0, first[:220]
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'


def _ffmpeg_executable():
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE:return _FFMPEG_CACHE
    candidates=[]; system_ffmpeg=shutil.which('ffmpeg')
    if system_ffmpeg:candidates.append(('system',system_ffmpeg))
    try:
        bundled=imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and bundled not in [p for _,p in candidates]:candidates.append(('imageio',bundled))
    except Exception as exc:print(f'Música: imageio_ffmpeg indisponível | {type(exc).__name__}: {exc}')
    for source,path in candidates:
        ok,info=_probe_ffmpeg(path); print(f'Música: teste FFmpeg | origem={source} | path={path} | ok={ok} | {info}')
        if ok:_FFMPEG_CACHE=path; return path
    raise RuntimeError('Nenhum FFmpeg funcional foi encontrado no ambiente.')


async def _fresh_stream(track):
    data=await music._run_extract(track.webpage_url,search=False)
    if data and data.get('entries'):data=next((entry for entry in data['entries'] if entry),None)
    if not data or not data.get('url'):raise RuntimeError('yt-dlp não retornou uma URL de áudio válida.')
    track.stream_url=str(data['url']); track.duration=int(data.get('duration') or track.duration or 0); track.thumbnail=str(data.get('thumbnail') or track.thumbnail or ''); track.uploader=str(data.get('uploader') or data.get('channel') or track.uploader)
    return track.stream_url,(data.get('http_headers') or {})


async def _play_next(self):
    async with self._starting:
        vc=self.voice
        if not vc or not vc.is_connected() or vc.is_playing() or vc.is_paused():return
        while self.queue:
            track=self.queue.popleft(); self.current=track; self._manual_stop=False
            try:
                stream_url,headers=await _fresh_stream(track); ffmpeg=_ffmpeg_executable(); before=music.FFMPEG_BEFORE+_header_options(headers); print(f'Música: iniciando | guild={self.guild_id} | ffmpeg={ffmpeg} | faixa={track.title[:100]}')
                source=discord.FFmpegPCMAudio(stream_url,executable=ffmpeg,before_options=before,options='-vn -loglevel error -f s16le -ar 48000 -ac 2'); source=discord.PCMVolumeTransformer(source,volume=self.volume); loop=asyncio.get_running_loop()
                def after(error):
                    if error:print(f'Música: erro no áudio | guild={self.guild_id} | {error!r}')
                    asyncio.run_coroutine_threadsafe(self._after_track(error),loop)
                vc.play(source,after=after); print(f'Música: reprodução enviada ao Discord | guild={self.guild_id} | playing={vc.is_playing()} | channel={getattr(vc.channel, "id", None)}'); await self.refresh_panel(); return
            except Exception as exc:
                failed=self.current; self.current=None; print(f'Música: falha ao iniciar | guild={self.guild_id} | faixa={getattr(failed, "title", "?")[:100]} | {type(exc).__name__}: {exc}'); await self._send_notice(f'Não consegui reproduzir **{failed.title if failed else "essa faixa"}**. Erro: `{type(exc).__name__}: {str(exc)[:300]}`')
        self.current=None; await self.refresh_panel()


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; music.GuildPlayer.play_next=_play_next
    try:_ffmpeg_executable()
    except Exception as exc:print(f'Música: diagnóstico FFmpeg na inicialização falhou | {type(exc).__name__}: {exc}')
    print('Música: runtime de áudio/FFmpeg carregado')
