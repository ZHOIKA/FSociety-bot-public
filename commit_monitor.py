"""F SOCIETY commit/deploy notifications."""
import asyncio, json, logging, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
import aiohttp, discord
from discord import app_commands

LOG=logging.getLogger(__name__); ROOT=Path(__file__).resolve().parent; DB=ROOT/'fsociety.db'; _INSTALLED=False
GITHUB_REPO=os.getenv('FSOCIETY_GITHUB_REPO','ZHOIKA/FSociety-bot-public').strip() or 'ZHOIKA/FSociety-bot-public'; GITHUB_BRANCH=os.getenv('FSOCIETY_GITHUB_BRANCH','main').strip() or 'main'; GITHUB_TOKEN=os.getenv('FSOCIETY_GITHUB_TOKEN','').strip() or os.getenv('GITHUB_TOKEN','').strip(); POLL_SECONDS=max(30,min(int(os.getenv('FSOCIETY_COMMIT_POLL_SECONDS','60')),900))
def _connect():return sqlite3.connect(str(DB))
def _ensure_tables():
    with _connect() as con:con.execute('CREATE TABLE IF NOT EXISTS commit_monitor(guild_id INTEGER PRIMARY KEY,channel_id INTEGER)');con.execute('CREATE TABLE IF NOT EXISTS commit_monitor_state(key TEXT PRIMARY KEY,value TEXT)')
def _get_channel(gid):
    _ensure_tables()
    with _connect() as con:r=con.execute('SELECT channel_id FROM commit_monitor WHERE guild_id=?',(gid,)).fetchone();return int(r[0]) if r and r[0] else None
def _set_channel(gid,cid):
    _ensure_tables()
    with _connect() as con:con.execute('INSERT INTO commit_monitor(guild_id,channel_id) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id',(gid,cid))
def _state_get(key):
    _ensure_tables()
    with _connect() as con:r=con.execute('SELECT value FROM commit_monitor_state WHERE key=?',(key,)).fetchone();return r[0] if r else None
def _state_set(key,value):
    _ensure_tables()
    with _connect() as con:con.execute('INSERT INTO commit_monitor_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))
def _pick(data,*paths,default=''):
    for path in paths:
        cur=data
        try:
            for key in path.split('.'):cur=cur[key]
            if cur not in (None,''):return cur
        except (KeyError,TypeError):continue
    return default
def _embed(payload,event='deploy'):
    head=payload.get('head_commit') or payload.get('commit') or {};sha=str(_pick(head,'id','sha',default=_pick(payload,'sha','after',default='')));message=str(_pick(head,'message',default='Evento recebido'));author=_pick(head,'author.name','committer.name',default=_pick(payload,'pusher.name','sender.login',default=''));ref=str(_pick(payload,'ref','branch',default=''));branch=ref.removeprefix('refs/heads/');color=0x22C55E
    if event=='push':
        lines=['```','╭─────────────────────────────╮','        F SOCIETY • COMMIT','╰─────────────────────────────╯','```','✦ **Nova alteração registrada**','',discord.utils.escape_mentions(message)[:3000],'','━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━']
        if branch:lines.append(f'**Branch**  •  `{branch[:100]}`')
        if author:lines.append(f'**Autor**   •  {discord.utils.escape_mentions(str(author))[:200]}')
        if sha:lines.append(f'**Commit**  •  `{sha[:7]}`')
        lines.extend(['━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━','','*F SOCIETY • Commit Monitor*']);return discord.Embed(description='\n'.join(lines),color=color,timestamp=datetime.now(timezone.utc))
    return discord.Embed(title='🚀 F SOCIETY • DEPLOY',description=discord.utils.escape_mentions(message)[:3500],color=color,timestamp=datetime.now(timezone.utc))
def _github_payload(commit):
    info=commit.get('commit') or {};author=info.get('author') or {};return {'ref':f'refs/heads/{GITHUB_BRANCH}','status':'detected','head_commit':{'id':commit.get('sha',''),'message':info.get('message','Novo commit detectado'),'author':{'name':author.get('name') or (commit.get('author') or {}).get('login') or 'GitHub'}}}
async def _configured_channel(bot,guild):
    cid=_get_channel(guild.id)
    if not cid:return None
    ch=guild.get_channel(cid) or bot.get_channel(cid)
    if ch is None:
        try:ch=await bot.fetch_channel(cid)
        except discord.HTTPException:return None
    return ch
async def _create_suggestion_thread(message,payload):
    head=payload.get('head_commit') or {};sha=str(_pick(head,'id','sha',default=''));short=sha[:7] if sha else 'commit'
    try:
        if getattr(message,'thread',None) is not None:return True
        th=await message.create_thread(name=f'💡 Sugestões • {short}',auto_archive_duration=1440);await th.send('💡 **Sugestões para esta alteração**\n\nUse este tópico para ideias, melhorias, correções ou feedback.');return True
    except (discord.Forbidden,discord.HTTPException,AttributeError):return False
async def _find_commit_message(channel,sha):
    short=sha[:7]
    try:
        async for message in channel.history(limit=30):
            if message.author.bot and any(short in (e.description or '') for e in message.embeds):return message
    except Exception:pass
    return None
async def _broadcast(bot,payload,event):
    sent=0;complete=True;configured=0
    for guild in bot.guilds:
        ch=await _configured_channel(bot,guild)
        if ch is None:continue
        configured+=1
        try:
            msg=await ch.send(embed=_embed(payload,event));sent+=1
            if event=='push' and not await _create_suggestion_thread(msg,payload):complete=False
        except discord.HTTPException:complete=False
    if configured==0:complete=False
    return sent,complete
async def _fetch_latest_commit(session):
    url=f'https://api.github.com/repos/{GITHUB_REPO}/commits/{GITHUB_BRANCH}';headers={'Accept':'application/vnd.github+json','User-Agent':'FSociety-Commit-Monitor'}
    if GITHUB_TOKEN:headers['Authorization']=f'Bearer {GITHUB_TOKEN}'
    async with session.get(url,headers=headers,timeout=aiohttp.ClientTimeout(total=20)) as response:
        if response.status==401:raise RuntimeError('GitHub recusou o token configurado (HTTP 401).')
        if response.status==403:raise RuntimeError('GitHub recusou a consulta ou aplicou limite de API (HTTP 403).')
        if response.status==404:raise RuntimeError('Repositório/branch não encontrado ou token sem acesso (HTTP 404).')
        if response.status>=400:raise RuntimeError(f'GitHub respondeu HTTP {response.status}.')
        return await response.json()
class CommitChannelSelect(discord.ui.ChannelSelect):
    def __init__(self,owner_id):super().__init__(placeholder='Escolha o canal de commits/deploys',channel_types=[discord.ChannelType.text,discord.ChannelType.news],min_values=1,max_values=1,row=0);self.owner_id=owner_id
    async def callback(self,i):
        if i.user.id!=self.owner_id:return await i.response.send_message('Este menu pertence a quem o abriu.',ephemeral=True)
        _set_channel(i.guild.id,self.values[0].id);await i.response.send_message(f'Canal configurado: {self.values[0].mention}',ephemeral=True)
class CommitConfigView(discord.ui.View):
    def __init__(self,bot,owner_id):super().__init__(timeout=600);self.bot=bot;self.owner_id=owner_id;self.add_item(CommitChannelSelect(owner_id))
def _status_text(gid):
    cid=_get_channel(gid);last=_state_get(f'last_sha:{GITHUB_REPO}:{GITHUB_BRANCH}');error=_state_get('poll_error') or '';text=f'**Canal:** {f"<#{cid}>" if cid else "não configurado"}\n**Repositório:** `{GITHUB_REPO}` (`{GITHUB_BRANCH}`)\n**Autenticação:** `{"configurada" if GITHUB_TOKEN else "AUSENTE"}`\n**Último commit:** `{last[:12] if last else "aguardando"}`'
    if error:text+=f'\n**Erro:** `{error[:500]}`'
    return text
def _register_commands(bot):
    if bot.tree.get_command('commitconfig') is None:
        @bot.tree.command(name='commitconfig',description='Abre o menu de commits e deploys')
        @app_commands.default_permissions(manage_guild=True)
        async def commitconfig(i:discord.Interaction):await i.response.send_message(embed=discord.Embed(title='🚀 Commits & Deploys',description=_status_text(i.guild.id),color=0x5865F2),view=CommitConfigView(bot,i.user.id),ephemeral=True)
def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True;_ensure_tables();print('[OK] Commits • monitor base carregado')
