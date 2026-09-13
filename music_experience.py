"""Experiência avançada do sistema de música."""
import asyncio
import re
import unicodedata

import discord

import control_panel as cp
import music_system as music

_INSTALLED=False


def _norm(value):
    value=unicodedata.normalize('NFKD',str(value or '')); value=''.join(ch for ch in value if not unicodedata.combining(ch)); value=value.lower().replace('&',' e '); return ' '.join(re.findall(r'[a-z0-9]+',value))


def _score(query,row):
    q=_norm(query); title=_norm(row.get('title')); artist=_norm(row.get('uploader')); hay=f'{title} {artist}'; tokens=[x for x in q.split() if len(x)>1]; score=0
    if title==q:score+=1200
    if title.startswith(q):score+=500
    if q and q in title:score+=360
    if q and q in hay:score+=220
    score+=sum(60 for token in tokens if token in title)+sum(25 for token in tokens if token in artist)
    raw=str(row.get('title') or '').lower(); uploader=str(row.get('uploader') or '').lower()
    if 'official' in raw or 'official' in uploader or 'vevo' in uploader:score+=30
    if 'audio' in raw:score+=12
    if any(word in raw for word in ('reaction','reacts','cover','karaoke','nightcore','slowed','sped up')):score-=20
    duration=int(row.get('duration') or 0)
    if duration and duration<25:score-=25
    if duration>10800:score-=35
    return score


def _row_from_entry(row):
    if not row:return None
    video_id=str(row.get('id') or ''); url=row.get('webpage_url') or row.get('url')
    if video_id and (not url or not str(url).startswith('http')):url=f'https://www.youtube.com/watch?v={video_id}'
    if not url:return None
    return {'id':video_id,'title':str(row.get('title') or 'Sem título'),'url':str(url),'duration':int(row.get('duration') or 0),'uploader':str(row.get('uploader') or row.get('channel') or 'Desconhecido'),'thumbnail':str(row.get('thumbnail') or ''),'view_count':int(row.get('view_count') or 0),'source':'youtube'}


async def _search_once(term,count=25):
    try:return (await music._run_extract(f'ytsearch{count}:{term}',search=True) or {}).get('entries') or []
    except Exception as exc:print(f'Música: busca parcial falhou | termo={term[:80]!r} | {type(exc).__name__}: {exc}'); return []


async def search_tracks(query:str,limit=10):
    clean=' '.join(str(query or '').split())[:180]
    if not clean:return []
    normalized=_norm(clean); variants=[clean]
    if not any(word in normalized.split() for word in ('official','audio','video','remix','cover','karaoke','slowed','sped')):variants += [f'{clean} official audio',f'{clean} topic']
    batches=await asyncio.gather(*(_search_once(term,25) for term in variants)); rows=[]; seen=set()
    for batch in batches:
        for raw in batch:
            item=_row_from_entry(raw)
            if not item:continue
            key=item['id'] or item['url']
            if key in seen:continue
            seen.add(key); item['_score']=_score(clean,item); rows.append(item)
    rows.sort(key=lambda x:(x['_score'],x.get('view_count',0)),reverse=True); return rows[:max(1,min(int(limit),25))]


def _short(text,limit):
    text=str(text or '').replace('\n',' ').strip(); return text if len(text)<=limit else text[:limit-1].rstrip()+'…'


def search_embed(query,results):
    lines=[f'**`{idx:02d}` {_short(row["title"],72)}**\n└ {_short(row["uploader"],45)} • `{music._fmt(row["duration"])}`' for idx,row in enumerate(results[:10],1)]; e=discord.Embed(title='F SOCIETY // MUSIC SEARCH',description=f'**Busca:** `{discord.utils.escape_markdown(_short(query,100))}`\n\n'+('\n'.join(lines) if lines else 'Nenhum resultado encontrado.'),color=0xB91C1C); e.set_footer(text='Escolha uma faixa no menu abaixo • resultados ordenados por relevância'); return e


class BetterSearchSelect(discord.ui.Select):
    def __init__(self,results):
        self.results=results; super().__init__(placeholder='Selecione uma música para tocar',min_values=1,max_values=1,options=[discord.SelectOption(label=f'{idx:02d} • {_short(row["title"],82)}',value=str(idx-1),description=_short(f'{row["uploader"]} • {music._fmt(row["duration"])}',100)) for idx,row in enumerate(results[:10],1)],row=0)
    async def callback(self,i):
        view=self.view
        if not isinstance(view,BetterSearchView):return
        try:
            await music._ensure_same_voice(i,connect=True); await i.response.defer(); chosen=self.results[int(self.values[0])]; track=await music.resolve_track(chosen['url'],i.user.id); player=music.player_for(i.client,i.guild.id); before=len(player.queue); await player.enqueue(track,i.channel_id); now=player.current is track; status='Reproduzindo agora' if now else f'Adicionada à fila • posição {before+1}'; msg=await i.edit_original_response(content=f'**{status}:** {_short(track.title,120)}',embed=player.embed(),view=music.MusicControls(player)); player.text_channel_id=i.channel_id; player.panel_message_id=msg.id
        except Exception as exc:await music._reply_error(i,exc)


class BetterSearchView(discord.ui.View):
    def __init__(self,owner_id,results,query=''):super().__init__(timeout=180); self.owner_id=owner_id; self.results=results; self.query=query; self.add_item(BetterSearchSelect(results))
    async def interaction_check(self,i):
        if i.user.id!=self.owner_id:await i.response.send_message('Essa pesquisa pertence a quem usou `/play`.',ephemeral=True); return False
        return True


class SearchAgainModal(discord.ui.Modal,title='Pesquisar outra música'):
    termo=discord.ui.TextInput(label='Nome da música, artista ou trecho do título',placeholder='Ex.: Linkin Park Numb',min_length=1,max_length=180)
    def __init__(self,player):super().__init__(); self.player=player
    async def on_submit(self,i):
        try:
            await i.response.defer(); results=await search_tracks(self.termo.value,10)
            if not results:return await i.edit_original_response(content='Não encontrei resultados. Tente incluir o artista junto do nome.',embed=None,view=None)
            await i.edit_original_response(content=None,embed=search_embed(self.termo.value,results),view=BetterSearchView(i.user.id,results,self.termo.value))
        except Exception as exc:await music._reply_error(i,exc)


async def _autocomplete(i,current:str):
    current=' '.join(current.split())[:100]
    if len(current)<2:return []
    try:raw=await asyncio.wait_for(_search_once(current,6),timeout=1.6)
    except Exception:return []
    rows=[]; seen=set()
    for entry in raw:
        row=_row_from_entry(entry)
        if not row:continue
        key=row['id'] or row['url']
        if key in seen:continue
        seen.add(key); row['_score']=_score(current,row); rows.append(row)
    rows.sort(key=lambda x:(x['_score'],x.get('view_count',0)),reverse=True)
    return [discord.app_commands.Choice(name=_short(f'{row["title"]} — {row["uploader"]}',100),value=_short(f'{row["title"]} {row["uploader"]}',100)) for row in rows[:6]]


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; music.search_tracks=search_tracks; music.SearchSelect=BetterSearchSelect; music.SearchView=BetterSearchView
    previous=cp.CommandTree.__init__
    def tree_init(self,*args,**kwargs):
        previous(self,*args,**kwargs); command=self.get_command('play')
        if command is not None:
            try:command.autocomplete('musica')(_autocomplete)
            except Exception as exc:print(f'Música: autocomplete não pôde ser ativado: {exc}')
    cp.CommandTree.__init__=tree_init; print('[OK] Música • busca avançada e autocomplete carregados')
