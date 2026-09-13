"""Sistema de música do F SOCIETY.

O bot apenas transmite áudio para o canal de voz e entra auto-ensurdecido.
"""
import asyncio
import random
from collections import deque
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
import imageio_ffmpeg
import yt_dlp

import control_panel as cp

_INSTALLED = False
_PLAYERS = {}

YTDL_SEARCH = {
    'quiet': True, 'no_warnings': True, 'extract_flat': 'in_playlist',
    'skip_download': True, 'noplaylist': True, 'default_search': 'ytsearch10',
}
YTDL_STREAM = {
    'quiet': True, 'no_warnings': True, 'format': 'bestaudio/best',
    'skip_download': True, 'noplaylist': True, 'default_search': 'ytsearch1',
}
FFMPEG_BEFORE = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
FFMPEG_OPTIONS = '-vn -loglevel warning'


def _fmt(seconds):
    try: seconds=max(0,int(seconds or 0))
    except (TypeError,ValueError): return '--:--'
    h,rem=divmod(seconds,3600); m,s=divmod(rem,60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


@dataclass
class Track:
    title:str
    webpage_url:str
    stream_url:str
    duration:int=0
    uploader:str='Desconhecido'
    thumbnail:str=''
    requester_id:int=0


async def _run_extract(query, *, search=False):
    loop=asyncio.get_running_loop(); opts=YTDL_SEARCH if search else YTDL_STREAM
    def work():
        with yt_dlp.YoutubeDL(opts) as ydl:return ydl.extract_info(query,download=False)
    return await loop.run_in_executor(None,work)


async def search_tracks(query:str, limit=10):
    data=await _run_extract(f'ytsearch{max(5,min(int(limit),25))}:{query}',search=True)
    rows=(data or {}).get('entries') or []; result=[]
    for row in rows[:max(1,min(int(limit),25))]:
        if not row:continue
        video_id=row.get('id'); url=row.get('webpage_url') or row.get('url')
        if video_id and (not url or not str(url).startswith('http')):url=f'https://www.youtube.com/watch?v={video_id}'
        if not url:continue
        result.append({'id':str(video_id or ''),'title':str(row.get('title') or 'Sem título'),'url':str(url),'duration':int(row.get('duration') or 0),'uploader':str(row.get('uploader') or row.get('channel') or 'Desconhecido'),'thumbnail':str(row.get('thumbnail') or ''),'view_count':int(row.get('view_count') or 0),'source':'youtube'})
    return result


async def resolve_track(query:str, requester_id:int):
    data=await _run_extract(query,search=False)
    if data and data.get('entries'):data=next((x for x in data['entries'] if x),None)
    if not data:raise ValueError('Não encontrei uma faixa reproduzível.')
    stream=data.get('url')
    if not stream:raise ValueError('A fonte não disponibilizou áudio reproduzível para essa faixa.')
    return Track(title=str(data.get('title') or 'Sem título'),webpage_url=str(data.get('webpage_url') or data.get('original_url') or query),stream_url=str(stream),duration=int(data.get('duration') or 0),uploader=str(data.get('uploader') or data.get('channel') or 'Desconhecido'),thumbnail=str(data.get('thumbnail') or ''),requester_id=requester_id)


def _member_voice(member):return getattr(getattr(member,'voice',None),'channel',None)


async def _ensure_same_voice(i, *, connect=False):
    channel=_member_voice(i.user)
    if channel is None:raise ValueError('Entre em um canal de voz primeiro.')
    vc=i.guild.voice_client
    if vc and vc.channel!=channel:raise ValueError(f'Eu já estou em {vc.channel.mention}. Entre nesse canal para controlar a música.')
    if connect and vc is None:
        perms=channel.permissions_for(i.guild.me)
        if not perms.connect or not perms.speak:raise ValueError('Preciso das permissões **Conectar** e **Falar** nesse canal de voz.')
        vc=await channel.connect(self_deaf=True,reconnect=True)
    return vc


class GuildPlayer:
    def __init__(self,bot,guild_id):
        self.bot=bot; self.guild_id=guild_id; self.queue=deque(); self.current:Optional[Track]=None; self.volume=.5; self.loop_mode='off'; self.text_channel_id=None; self.panel_message_id=None; self._manual_stop=False; self._starting=asyncio.Lock()
    @property
    def guild(self):return self.bot.get_guild(self.guild_id)
    @property
    def voice(self):return self.guild.voice_client if self.guild else None
    async def enqueue(self,track,text_channel_id=None):
        if text_channel_id:self.text_channel_id=text_channel_id
        self.queue.append(track)
        if not self.current and self.voice and not (self.voice.is_playing() or self.voice.is_paused()):await self.play_next()
    async def play_next(self):
        async with self._starting:
            vc=self.voice
            if not vc or not vc.is_connected() or vc.is_playing() or vc.is_paused():return
            if not self.queue:self.current=None; await self.refresh_panel(); return
            self.current=self.queue.popleft(); self._manual_stop=False
            try:
                source=discord.FFmpegPCMAudio(self.current.stream_url,executable=imageio_ffmpeg.get_ffmpeg_exe(),before_options=FFMPEG_BEFORE,options=FFMPEG_OPTIONS); source=discord.PCMVolumeTransformer(source,volume=self.volume); loop=asyncio.get_running_loop()
                def after(error):asyncio.run_coroutine_threadsafe(self._after_track(error),loop)
                vc.play(source,after=after); await self.refresh_panel()
            except Exception:
                failed=self.current; self.current=None; await self._send_notice(f'Não consegui iniciar **{failed.title}**. Pulando para a próxima.'); await self.play_next()
    async def _after_track(self,error):
        finished=self.current; self.current=None
        if error:await self._send_notice(f'Erro durante a reprodução: `{str(error)[:250]}`')
        if finished and not self._manual_stop:
            if self.loop_mode=='track':
                try:self.queue.appendleft(await resolve_track(finished.webpage_url,finished.requester_id))
                except Exception:pass
            elif self.loop_mode=='queue':
                try:self.queue.append(await resolve_track(finished.webpage_url,finished.requester_id))
                except Exception:pass
        self._manual_stop=False; await self.play_next()
    async def skip(self):
        vc=self.voice
        if not vc or not (vc.is_playing() or vc.is_paused()):raise ValueError('Não há música tocando.')
        vc.stop()
    async def stop(self):
        self.queue.clear(); self.loop_mode='off'; self._manual_stop=True; vc=self.voice
        if vc and (vc.is_playing() or vc.is_paused()):vc.stop()
        self.current=None; await self.refresh_panel()
    async def disconnect(self):
        self.queue.clear(); self.loop_mode='off'; self._manual_stop=True; vc=self.voice
        if vc:await vc.disconnect(force=True)
        self.current=None; self.panel_message_id=None
    def shuffle(self):items=list(self.queue); random.shuffle(items); self.queue=deque(items)
    def embed(self):
        if self.current:
            e=discord.Embed(title='F SOCIETY // MUSIC',description=f'**[{discord.utils.escape_markdown(self.current.title)}]({self.current.webpage_url})**\n{discord.utils.escape_markdown(self.current.uploader)}\n\n**Duração:** `{_fmt(self.current.duration)}`\n**Volume:** `{round(self.volume*100)}%`\n**Loop:** `{self.loop_mode}`\n**Na fila:** `{len(self.queue)}`',color=0xB91C1C)
            if self.current.thumbnail:e.set_thumbnail(url=self.current.thumbnail)
            return e
        return discord.Embed(title='F SOCIETY // MUSIC',description='Nenhuma música tocando agora.\nUse `/play` para pesquisar uma faixa.',color=0xB91C1C)
    def queue_embed(self):
        lines=[]
        if self.current:lines.append(f'**Tocando:** {self.current.title[:80]}')
        for idx,track in enumerate(list(self.queue)[:20],1):lines.append(f'`{idx:02d}` {track.title[:80]} • `{_fmt(track.duration)}`')
        return discord.Embed(title='F SOCIETY // FILA',description='\n'.join(lines) if lines else 'A fila está vazia.',color=0xB91C1C)
    async def refresh_panel(self):
        if not self.text_channel_id or not self.panel_message_id:return
        channel=self.bot.get_channel(self.text_channel_id)
        if not channel:return
        try:
            msg=await channel.fetch_message(self.panel_message_id); await msg.edit(embed=self.embed(),view=MusicControls(self))
        except discord.HTTPException:self.panel_message_id=None
    async def _send_notice(self,text):
        channel=self.bot.get_channel(self.text_channel_id) if self.text_channel_id else None
        if channel:
            try:await channel.send(text[:1900],delete_after=12)
            except discord.HTTPException:pass


def player_for(bot,guild_id):
    if guild_id not in _PLAYERS:_PLAYERS[guild_id]=GuildPlayer(bot,guild_id)
    return _PLAYERS[guild_id]


async def _reply_error(i,exc):
    text=str(exc)[:1800]
    if i.response.is_done():await i.followup.send(text,ephemeral=True)
    else:await i.response.send_message(text,ephemeral=True)


class VolumeModal(discord.ui.Modal,title='Volume da música'):
    volume=discord.ui.TextInput(label='Volume de 0 a 200',placeholder='50',max_length=3)
    def __init__(self,player):super().__init__(); self.player=player; self.volume.default=str(round(player.volume*100))
    async def on_submit(self,i):
        try:
            await _ensure_same_voice(i); value=int(self.volume.value)
            if value<0 or value>200:raise ValueError('O volume deve ficar entre 0 e 200.')
            self.player.volume=value/100; vc=self.player.voice
            if vc and isinstance(vc.source,discord.PCMVolumeTransformer):vc.source.volume=self.player.volume
            await i.response.edit_message(embed=self.player.embed(),view=MusicControls(self.player))
        except Exception as exc:await _reply_error(i,exc)


class MusicControls(discord.ui.View):
    def __init__(self,player):super().__init__(timeout=900); self.player=player
    async def interaction_check(self,i):
        try:await _ensure_same_voice(i); return True
        except Exception as exc:await _reply_error(i,exc); return False
    @discord.ui.button(label='Pause',style=discord.ButtonStyle.secondary,row=0)
    async def pause(self,i,button):
        vc=self.player.voice
        if not vc or not vc.is_playing():return await i.response.send_message('Não há música tocando para pausar.',ephemeral=True)
        vc.pause(); await i.response.edit_message(embed=self.player.embed(),view=MusicControls(self.player))
    @discord.ui.button(label='Resume',style=discord.ButtonStyle.success,row=0)
    async def resume(self,i,button):
        vc=self.player.voice
        if not vc or not vc.is_paused():return await i.response.send_message('A música não está pausada.',ephemeral=True)
        vc.resume(); await i.response.edit_message(embed=self.player.embed(),view=MusicControls(self.player))
    @discord.ui.button(label='Skip',style=discord.ButtonStyle.primary,row=0)
    async def skip(self,i,button):
        try:await self.player.skip(); await i.response.defer()
        except Exception as exc:await _reply_error(i,exc)
    @discord.ui.button(label='Stop',style=discord.ButtonStyle.danger,row=0)
    async def stop(self,i,button):await self.player.stop(); await i.response.edit_message(embed=self.player.embed(),view=MusicControls(self.player))
    @discord.ui.button(label='Fila',style=discord.ButtonStyle.secondary,row=0)
    async def queue(self,i,button):await i.response.send_message(embed=self.player.queue_embed(),ephemeral=True)
    @discord.ui.button(label='Loop',style=discord.ButtonStyle.secondary,row=1)
    async def loop(self,i,button):self.player.loop_mode={'off':'track','track':'queue','queue':'off'}[self.player.loop_mode]; await i.response.edit_message(embed=self.player.embed(),view=MusicControls(self.player))
    @discord.ui.button(label='Shuffle',style=discord.ButtonStyle.secondary,row=1)
    async def shuffle(self,i,button):self.player.shuffle(); await i.response.edit_message(embed=self.player.embed(),view=MusicControls(self.player))
    @discord.ui.button(label='Volume',style=discord.ButtonStyle.secondary,row=1)
    async def volume(self,i,button):await i.response.send_modal(VolumeModal(self.player))
    @discord.ui.button(label='Disconnect',style=discord.ButtonStyle.danger,row=1)
    async def disconnect(self,i,button):await self.player.disconnect(); await i.response.edit_message(embed=self.player.embed(),view=None)


class SearchSelect(discord.ui.Select):
    def __init__(self,results):
        self.results=results; options=[discord.SelectOption(label=row['title'][:100],value=str(idx),description=f"{row['uploader'][:70]} • {_fmt(row['duration'])}"[:100]) for idx,row in enumerate(results[:10])]; super().__init__(placeholder='Escolha a música',options=options,min_values=1,max_values=1)
    async def callback(self,i):
        try:
            await _ensure_same_voice(i,connect=True); await i.response.defer(); chosen=self.results[int(self.values[0])]; track=await resolve_track(chosen['url'],i.user.id); player=player_for(i.client,i.guild.id); await player.enqueue(track,i.channel_id); msg=await i.edit_original_response(content=None,embed=player.embed(),view=MusicControls(player)); player.text_channel_id=i.channel_id; player.panel_message_id=msg.id
        except Exception as exc:await _reply_error(i,exc)


class SearchView(discord.ui.View):
    def __init__(self,owner_id,results):super().__init__(timeout=180); self.owner_id=owner_id; self.add_item(SearchSelect(results))
    async def interaction_check(self,i):
        if i.user.id!=self.owner_id:await i.response.send_message('Essa pesquisa pertence a outro membro.',ephemeral=True); return False
        return True


async def _play(i,musica:str):
    await _ensure_same_voice(i,connect=True); await i.response.defer(); results=await search_tracks(musica,10)
    if not results:return await i.followup.send('Não encontrei resultados.',ephemeral=True)
    e=discord.Embed(title='F SOCIETY // MUSIC SEARCH',description='\n'.join(f'`{n:02d}` **{r["title"][:75]}** • `{_fmt(r["duration"])}`' for n,r in enumerate(results[:10],1)),color=0xB91C1C); await i.followup.send(embed=e,view=SearchView(i.user.id,results))


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; original=cp.CommandTree.__init__
    def tree_init(self,*args,**kwargs):
        original(self,*args,**kwargs)
        async def play(i:discord.Interaction,musica:str):
            try:await _play(i,musica)
            except Exception as exc:await _reply_error(i,exc)
        async def pause(i:discord.Interaction):
            try:
                vc=await _ensure_same_voice(i); vc.pause(); await i.response.send_message('Música pausada.',ephemeral=True)
            except Exception as exc:await _reply_error(i,exc)
        async def resume(i:discord.Interaction):
            try:
                vc=await _ensure_same_voice(i); vc.resume(); await i.response.send_message('Música retomada.',ephemeral=True)
            except Exception as exc:await _reply_error(i,exc)
        async def skip(i:discord.Interaction):
            try:await _ensure_same_voice(i); await player_for(i.client,i.guild.id).skip(); await i.response.send_message('Faixa pulada.',ephemeral=True)
            except Exception as exc:await _reply_error(i,exc)
        async def stop(i:discord.Interaction):
            try:await _ensure_same_voice(i); await player_for(i.client,i.guild.id).stop(); await i.response.send_message('Player parado.',ephemeral=True)
            except Exception as exc:await _reply_error(i,exc)
        async def queue(i:discord.Interaction):await i.response.send_message(embed=player_for(i.client,i.guild.id).queue_embed(),ephemeral=True)
        async def nowplaying(i:discord.Interaction):await i.response.send_message(embed=player_for(i.client,i.guild.id).embed(),ephemeral=True)
        async def volume(i:discord.Interaction,valor:app_commands.Range[int,0,200]):
            try:
                await _ensure_same_voice(i); p=player_for(i.client,i.guild.id); p.volume=int(valor)/100; vc=p.voice
                if vc and isinstance(vc.source,discord.PCMVolumeTransformer):vc.source.volume=p.volume
                await i.response.send_message(f'Volume: **{valor}%**.',ephemeral=True)
            except Exception as exc:await _reply_error(i,exc)
        async def loop(i:discord.Interaction):
            p=player_for(i.client,i.guild.id); p.loop_mode={'off':'track','track':'queue','queue':'off'}[p.loop_mode]; await i.response.send_message(f'Loop: **{p.loop_mode}**.',ephemeral=True)
        async def shuffle(i:discord.Interaction):p=player_for(i.client,i.guild.id); p.shuffle(); await i.response.send_message('Fila embaralhada.',ephemeral=True)
        async def disconnect(i:discord.Interaction):
            try:await _ensure_same_voice(i); await player_for(i.client,i.guild.id).disconnect(); await i.response.send_message('Desconectado.',ephemeral=True)
            except Exception as exc:await _reply_error(i,exc)
        specs=[('play','Pesquisa e toca uma música',play),('pause','Pausa a música atual',pause),('resume','Continua a música pausada',resume),('skip','Pula a faixa atual',skip),('stop','Para a música e limpa a fila',stop),('queue','Mostra a fila de músicas',queue),('nowplaying','Mostra a faixa atual',nowplaying),('volume','Altera o volume do player',volume),('loop','Alterna o modo de loop',loop),('shuffle','Embaralha a fila',shuffle),('disconnect','Desconecta o bot do canal de voz',disconnect)]
        for name,desc,callback in specs:
            if self.get_command(name) is None:self.add_command(app_commands.Command(name=name,description=desc,callback=callback))
    cp.CommandTree.__init__=tree_init
    print('[OK] Música • player, busca e controles carregados')
