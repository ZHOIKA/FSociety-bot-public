"""Descoberta e alertas de ferramentas de segurança/pentest usando metadados públicos do GitHub."""
import asyncio, math, os, sqlite3, time
from contextlib import closing
from datetime import datetime, timezone
import aiohttp, discord
from discord import app_commands
import control_panel as cp

_INSTALLED=False; _POLL_SECONDS=15*60; _API='https://api.github.com'; _SEARCH_LIMIT=50; _SEARCH_PAGE_SIZE=5
CATALOG={'Nmap':'nmap/nmap','Nuclei':'projectdiscovery/nuclei','FFUF':'ffuf/ffuf','Feroxbuster':'epi052/feroxbuster','OWASP Amass':'owasp-amass/amass','OWASP ZAP':'zaproxy/zaproxy','Metasploit Framework':'rapid7/metasploit-framework','Mitmproxy':'mitmproxy/mitmproxy','RustScan':'RustScan/RustScan','Nikto':'sullo/nikto'}

def _db(client):
    center=getattr(client,'center',None)
    if center is None:raise RuntimeError('Central do bot ainda não inicializada.')
    return center.db_path()
def _migrate(client):
    with closing(sqlite3.connect(_db(client))) as con,con:con.executescript('''CREATE TABLE IF NOT EXISTS tool_watch_settings(guild_id INTEGER PRIMARY KEY,channel_id INTEGER,enabled INTEGER NOT NULL DEFAULT 0,initialized_at REAL);CREATE TABLE IF NOT EXISTS tool_seen(guild_id INTEGER NOT NULL,repo TEXT NOT NULL,release_id INTEGER NOT NULL,sent_at REAL NOT NULL,PRIMARY KEY(guild_id,repo,release_id));''')
def _settings(client,guild_id):
    _migrate(client)
    with closing(sqlite3.connect(_db(client))) as con:
        con.row_factory=sqlite3.Row; row=con.execute('SELECT * FROM tool_watch_settings WHERE guild_id=?',(guild_id,)).fetchone(); return dict(row) if row else {'guild_id':guild_id,'channel_id':None,'enabled':0,'initialized_at':None}
def _save_settings(client,guild_id,*,channel_id=None,enabled=None,initialized_at='keep'):
    cur=_settings(client,guild_id); channel=cur['channel_id'] if channel_id is None else channel_id; active=cur['enabled'] if enabled is None else int(bool(enabled)); init=cur['initialized_at'] if initialized_at=='keep' else initialized_at
    with closing(sqlite3.connect(_db(client))) as con,con:con.execute('INSERT INTO tool_watch_settings(guild_id,channel_id,enabled,initialized_at) VALUES(?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,enabled=excluded.enabled,initialized_at=excluded.initialized_at',(guild_id,channel,active,init))
def _is_seen(client,gid,repo,rid):
    with closing(sqlite3.connect(_db(client))) as con:return con.execute('SELECT 1 FROM tool_seen WHERE guild_id=? AND repo=? AND release_id=?',(gid,repo,rid)).fetchone() is not None
def _mark_seen(client,gid,repo,rid):
    with closing(sqlite3.connect(_db(client))) as con,con:
        con.execute('INSERT OR IGNORE INTO tool_seen(guild_id,repo,release_id,sent_at) VALUES(?,?,?,?)',(gid,repo,rid,time.time())); con.execute('DELETE FROM tool_seen WHERE sent_at<?',(time.time()-180*86400,))
def _headers():
    h={'Accept':'application/vnd.github+json','User-Agent':'FSociety-Tool-Monitor/1.0','X-GitHub-Api-Version':'2022-11-28'}; token=os.getenv('GITHUB_TOKEN','').strip()
    if token:h['Authorization']=f'Bearer {token}'
    return h
async def _github_json(session,path,*,params=None):
    async with session.get(f'{_API}{path}',params=params,headers=_headers()) as response:
        if response.status==404:return None
        if response.status==403:raise RuntimeError(f'GitHub API limitou as consultas (remaining={response.headers.get("X-RateLimit-Remaining","?")}).')
        response.raise_for_status(); return await response.json()
def _release_embed(name,repo,release):
    tag=release.get('tag_name') or release.get('name') or 'nova versão'; body=(release.get('body') or 'Sem notas de versão publicadas.').strip(); published=release.get('published_at') or release.get('created_at'); ts=None
    if published:
        try:ts=datetime.fromisoformat(published.replace('Z','+00:00'))
        except ValueError:pass
    e=discord.Embed(title=f'{name} • {tag}',url=release.get('html_url'),description=discord.utils.escape_mentions(body)[:3000],color=0xB91C1C,timestamp=ts or datetime.now(timezone.utc)); e.add_field(name='Projeto',value=f'`{repo}`',inline=False); e.add_field(name='Versão',value=f'`{tag}`'); e.add_field(name='Pré-release',value='Sim' if release.get('prerelease') else 'Não'); e.add_field(name='Fonte',value='Release oficial no GitHub',inline=False); e.set_footer(text='F SOCIETY • Monitor de ferramentas'); return e
async def _latest_release(session,repo):return await _github_json(session,f'/repos/{repo}/releases/latest')
async def _baseline(client,session,gid):
    for repo in CATALOG.values():
        rel=await _latest_release(session,repo)
        if rel and rel.get('id'):_mark_seen(client,gid,repo,int(rel['id']))
    _save_settings(client,gid,initialized_at=time.time())
async def _poll_guild(client,session,guild):
    s=_settings(client,guild.id)
    if not s['enabled'] or not s['channel_id']:return
    ch=guild.get_channel(s['channel_id'])
    if not isinstance(ch,discord.TextChannel):return
    p=ch.permissions_for(guild.me)
    if not all(getattr(p,x,False) for x in ('view_channel','send_messages','embed_links')):return
    if not s['initialized_at']:return await _baseline(client,session,guild.id)
    for name,repo in CATALOG.items():
        try:rel=await _latest_release(session,repo)
        except Exception:continue
        if not rel or not rel.get('id') or rel.get('draft'):continue
        rid=int(rel['id'])
        if _is_seen(client,guild.id,repo,rid):continue
        msg=await ch.send(embed=_release_embed(name,repo,rel),allowed_mentions=discord.AllowedMentions.none())
        try:
            if p.create_public_threads:
                th=await msg.create_thread(name=f'{name} • {rel.get("tag_name") or "release"}'[:100],auto_archive_duration=1440); await th.send(f'**Projeto:** `{repo}`\n**Link oficial:** {rel.get("html_url")}',allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:pass
        _mark_seen(client,guild.id,repo,rid); await asyncio.sleep(1)
async def _monitor_loop(client):
    await client.wait_until_ready(); timeout=aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while not client.is_closed():
            for guild in list(client.guilds):
                try:await _poll_guild(client,session,guild)
                except Exception:pass
            await asyncio.sleep(_POLL_SECONDS)
async def _search_tools(i,query):
    await i.response.defer(thinking=True); timeout=aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:data=await _github_json(session,'/search/repositories',params={'q':f'{query} security in:name,description,topics','sort':'stars','order':'desc','per_page':10})
    items=(data or {}).get('items') or []
    if not items:return await i.followup.send('Nenhuma ferramenta encontrada para essa pesquisa.')
    e=discord.Embed(title='F SOCIETY // TOOL SEARCH',description=f'**Consulta:** `{discord.utils.escape_markdown(query)}`',color=0xB91C1C)
    for n,item in enumerate(items[:10],1):e.add_field(name=f'{n:02d} // {item.get("full_name")}',value=f'{discord.utils.escape_mentions(item.get("description") or "Sem descrição")[:300]}\n`Stars {item.get("stargazers_count",0)}` • [Abrir]({item.get("html_url")})',inline=False)
    await i.followup.send(embed=e)
def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; old=cp.CommandTree.__init__
    def tree_init(self,*a,**kw):
        old(self,*a,**kw)
        if self.get_command('ferramentas') is not None:return
        group=app_commands.Group(name='ferramentas',description='Pesquisa e alertas de ferramentas de segurança',guild_only=True)
        @group.command(name='pesquisar',description='Pesquisa ferramentas de segurança no GitHub')
        async def pesquisar(i:discord.Interaction,termo:app_commands.Range[str,2,100]):await _search_tools(i,termo)
        @group.command(name='configurar',description='Define o canal dos alertas')
        @app_commands.default_permissions(manage_guild=True)
        async def configurar(i:discord.Interaction,canal:discord.TextChannel):_save_settings(i.client,i.guild.id,channel_id=canal.id); await i.response.send_message(f'Canal configurado: {canal.mention}',ephemeral=True)
        @group.command(name='ativar',description='Ativa os alertas de novas releases')
        @app_commands.default_permissions(manage_guild=True)
        async def ativar(i:discord.Interaction):
            s=_settings(i.client,i.guild.id)
            if not s['channel_id']:return await i.response.send_message('Configure primeiro o canal.',ephemeral=True)
            _save_settings(i.client,i.guild.id,enabled=True,initialized_at=None); await i.response.send_message('Monitor de ferramentas ativado.',ephemeral=True)
        @group.command(name='desativar',description='Desativa os alertas')
        @app_commands.default_permissions(manage_guild=True)
        async def desativar(i:discord.Interaction):_save_settings(i.client,i.guild.id,enabled=False); await i.response.send_message('Monitor desativado.',ephemeral=True)
        self.add_command(group)
    cp.CommandTree.__init__=tree_init
    original_bot_init=discord.Client.__init__
    def client_init(self,*a,**kw):original_bot_init(self,*a,**kw)
    # monitor is started by setup hook wrapper below
    cp._tool_monitor_loop=_monitor_loop
