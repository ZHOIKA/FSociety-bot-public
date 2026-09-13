"""F SOCIETY: missões, conquistas, drops, mercado e analytics."""
import asyncio, random, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands
import control_panel as cp

_INSTALLED=False; DB=str(Path(__file__).resolve().parent/'fsociety.db')
DAILY=(('msg','Enviar 20 mensagens',20,150,25),('voice','Ficar 30 min em call',1800,200,35),('xp','Ganhar 100 XP',100,180,30))
WEEKLY=(('msg','Enviar 150 mensagens',150,700,100),('voice','Ficar 5h em call',18000,900,140),('xp','Ganhar 750 XP',750,800,120))
BADGES=(('milionario','Milionário','coins',1000000),('voz100','Voz de Ferro','voice',360000),('mensageiro','Mensageiro','msg',10000),('nivel25','Ascendente','level',25))
def db():c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
def schema():
    with db() as c:c.executescript('''CREATE TABLE IF NOT EXISTS activity_daily(guild_id INTEGER,user_id INTEGER,day TEXT,messages INTEGER DEFAULT 0,xp INTEGER DEFAULT 0,voice_seconds INTEGER DEFAULT 0,PRIMARY KEY(guild_id,user_id,day));CREATE TABLE IF NOT EXISTS channel_activity(guild_id INTEGER,channel_id INTEGER,day TEXT,messages INTEGER DEFAULT 0,PRIMARY KEY(guild_id,channel_id,day));CREATE TABLE IF NOT EXISTS member_seen(guild_id INTEGER,user_id INTEGER,first_seen INTEGER,last_seen INTEGER,PRIMARY KEY(guild_id,user_id));CREATE TABLE IF NOT EXISTS mission_claims(guild_id INTEGER,user_id INTEGER,period TEXT,mission TEXT,PRIMARY KEY(guild_id,user_id,period,mission));CREATE TABLE IF NOT EXISTS achievements(guild_id INTEGER,user_id INTEGER,key TEXT,unlocked_at INTEGER,PRIMARY KEY(guild_id,user_id,key));CREATE TABLE IF NOT EXISTS market_listings(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,seller_id INTEGER,inventory_id INTEGER,item_name TEXT,price INTEGER,status TEXT DEFAULT 'open',buyer_id INTEGER,created_at INTEGER,sold_at INTEGER DEFAULT 0);CREATE TABLE IF NOT EXISTS community_config(guild_id INTEGER PRIMARY KEY,drops_enabled INTEGER DEFAULT 0,drop_channel INTEGER,drop_min INTEGER DEFAULT 50,drop_max INTEGER DEFAULT 250);''')
def day():return datetime.now(timezone.utc).date().isoformat()
def week():y,w,_=datetime.now(timezone.utc).date().isocalendar();return f'{y}-W{w:02d}'
def activity(gid,uid,kind,n=1,channel=None):
    col={'msg':'messages','xp':'xp','voice':'voice_seconds'}[kind];now=int(time.time())
    with db() as c:
        c.execute('INSERT OR IGNORE INTO activity_daily(guild_id,user_id,day) VALUES(?,?,?)',(gid,uid,day()));c.execute(f'UPDATE activity_daily SET {col}={col}+? WHERE guild_id=? AND user_id=? AND day=?',(n,gid,uid,day()));c.execute('INSERT INTO member_seen VALUES(?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET last_seen=excluded.last_seen',(gid,uid,now,now))
        if channel:c.execute('INSERT OR IGNORE INTO channel_activity VALUES(?,?,?,0)',(gid,channel,day()));c.execute('UPDATE channel_activity SET messages=messages+1 WHERE guild_id=? AND channel_id=? AND day=?',(gid,channel,day()))
def stats(gid,uid,p):
    with db() as c:
        if p.startswith('D'):r=c.execute('SELECT messages m,xp x,voice_seconds v FROM activity_daily WHERE guild_id=? AND user_id=? AND day=?',(gid,uid,p[1:])).fetchone()
        else:
            y,w=p.split('-W');a=datetime.fromisocalendar(int(y),int(w),1).date().isoformat();b=datetime.fromisocalendar(int(y),int(w),7).date().isoformat();r=c.execute('SELECT COALESCE(SUM(messages),0)m,COALESCE(SUM(xp),0)x,COALESCE(SUM(voice_seconds),0)v FROM activity_daily WHERE guild_id=? AND user_id=? AND day BETWEEN ? AND ?',(gid,uid,a,b)).fetchone()
    return {'msg':int(r['m'] or 0) if r else 0,'xp':int(r['x'] or 0) if r else 0,'voice':int(r['v'] or 0) if r else 0}
def reward(gid,uid,coins,xp):
    with db() as c:c.execute('INSERT OR IGNORE INTO users(guild_id,user_id) VALUES(?,?)',(gid,uid));c.execute('UPDATE users SET coins=coins+?,xp=xp+? WHERE guild_id=? AND user_id=?',(coins,xp,gid,uid))
def claim(gid,uid):
    tc=tx=count=0
    for p,missions in [('D'+day(),DAILY),(week(),WEEKLY)]:
        s=stats(gid,uid,p)
        with db() as c:
            for key,name,target,coins,xp in missions:
                if s[key]>=target and c.execute('INSERT OR IGNORE INTO mission_claims VALUES(?,?,?,?)',(gid,uid,p,key)).rowcount:tc+=coins;tx+=xp;count+=1
    if count:reward(gid,uid,tc,tx)
    return count,tc,tx
def mission_embed(g,u):
    e=discord.Embed(title='🎯 F SOCIETY • MISSÕES',description='Objetivos diários e semanais com F-Coins + XP.',color=0x7C3AED)
    for title,p,missions in [('DIÁRIAS','D'+day(),DAILY),('SEMANAIS',week(),WEEKLY)]:
        s=stats(g.id,u.id,p)
        with db() as c:done={r['mission'] for r in c.execute('SELECT mission FROM mission_claims WHERE guild_id=? AND user_id=? AND period=?',(g.id,u.id,p))}
        lines=[]
        for key,name,target,coins,xp in missions:
            v=min(s[key],target);prog=f'{v//60}/{target//60} min' if key=='voice' else f'{v}/{target}';lines.append(f"{'✅' if key in done else '🟣' if v>=target else '▫️'} **{name}** — {prog}\n↳ {coins} F-Coins + {xp} XP")
        e.add_field(name=title,value='\n'.join(lines),inline=False)
    return e
def badge_embed(g,u):
    with db() as c:
        r=c.execute('SELECT coins,level,messages FROM users WHERE guild_id=? AND user_id=?',(g.id,u.id)).fetchone();v=c.execute('SELECT COALESCE(SUM(voice_seconds),0)n FROM activity_daily WHERE guild_id=? AND user_id=?',(g.id,u.id)).fetchone();owned={x['key'] for x in c.execute('SELECT key FROM achievements WHERE guild_id=? AND user_id=?',(g.id,u.id))};vals={'coins':int(r['coins'] or 0) if r else 0,'level':int(r['level'] or 0) if r else 0,'msg':int(r['messages'] or 0) if r else 0,'voice':int(v['n'] or 0)}
        for k,n,m,t in BADGES:
            if vals[m]>=t:c.execute('INSERT OR IGNORE INTO achievements VALUES(?,?,?,?)',(g.id,u.id,k,int(time.time())));owned.add(k)
    return discord.Embed(title=f'🏆 Conquistas • {u.display_name}',description='\n'.join(f"{'🏆' if k in owned else '🔒'} **{n}**" for k,n,m,t in BADGES),color=0xF59E0B)
def analytics(g):
    with db() as c:a=c.execute('SELECT COUNT(*)n FROM member_seen WHERE guild_id=? AND last_seen>=?',(g.id,int(time.time())-604800)).fetchone()['n'];s=c.execute("SELECT COALESCE(SUM(messages),0)m,COALESCE(SUM(voice_seconds),0)v FROM activity_daily WHERE guild_id=? AND day>=date('now','-6 day')",(g.id,)).fetchone();top=c.execute("SELECT channel_id,SUM(messages)n FROM channel_activity WHERE guild_id=? AND day>=date('now','-6 day') GROUP BY channel_id ORDER BY n DESC LIMIT 5",(g.id,)).fetchall()
    e=discord.Embed(title='📊 F SOCIETY • ANALYTICS',description='Atividade dos últimos 7 dias.',color=0x22C55E);e.add_field(name='Membros ativos',value=str(a));e.add_field(name='Mensagens',value=str(s['m']));e.add_field(name='Tempo em call',value=f"{int(s['v'])//3600}h {(int(s['v'])%3600)//60}min");e.add_field(name='Canais mais ativos',value='\n'.join(f'<#{r["channel_id"]}> — {r["n"]}' for r in top) or 'Sem dados',inline=False);return e
class Drop(discord.ui.View):
    def __init__(self,gid,n):super().__init__(timeout=180);self.gid=gid;self.n=n;self.used=False
    @discord.ui.button(label='Resgatar',style=discord.ButtonStyle.success)
    async def take(self,i,b):
        if self.used:return await i.response.send_message('Esse drop já foi resgatado.',ephemeral=True)
        self.used=True;reward(self.gid,i.user.id,self.n,0);b.disabled=True;await i.response.edit_message(content=f'💸 {i.user.mention} resgatou **{self.n} F-Coins**!',view=self)
async def drops(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(random.randint(1800,3600))
        with db() as c:rows=c.execute('SELECT * FROM community_config WHERE drops_enabled=1 AND drop_channel IS NOT NULL').fetchall()
        for r in rows:
            ch=bot.get_channel(r['drop_channel'])
            if ch:
                try:await ch.send(f'🎁 **DROP F SOCIETY** • {random.randint(r["drop_min"],r["drop_max"])} F-Coins!',view=Drop(r['guild_id'],random.randint(r['drop_min'],r['drop_max'])))
                except discord.HTTPException:pass
class CommunityPanel(discord.ui.View):
    def __init__(self,g,owner):super().__init__(timeout=300);self.g=g;self.owner=owner
    async def interaction_check(self,i):return i.user.id==self.owner
    @discord.ui.button(label='Drops ON/OFF',style=discord.ButtonStyle.primary)
    async def toggle(self,i,b):
        with db() as c:c.execute('INSERT OR IGNORE INTO community_config(guild_id) VALUES(?)',(i.guild.id,));c.execute('UPDATE community_config SET drops_enabled=1-drops_enabled WHERE guild_id=?',(i.guild.id,));r=c.execute('SELECT drops_enabled FROM community_config WHERE guild_id=?',(i.guild.id,)).fetchone()
        await i.response.send_message(f"Drops **{'ativados' if r['drops_enabled'] else 'desativados'}**.",ephemeral=True)
    @discord.ui.button(label='Usar este canal para drops',style=discord.ButtonStyle.secondary)
    async def channel(self,i,b):
        with db() as c:c.execute('INSERT OR IGNORE INTO community_config(guild_id) VALUES(?)',(i.guild.id,));c.execute('UPDATE community_config SET drop_channel=? WHERE guild_id=?',(i.channel.id,i.guild.id))
        await i.response.send_message(f'Drops configurados para {i.channel.mention}.',ephemeral=True)
def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True;schema();cp.SECTIONS['community']=('Comunidade','🎯','Missões, conquistas, drops, mercado e analytics');oldtree=cp.CommandTree.__init__
    def tree(self,*a,**kw):
        oldtree(self,*a,**kw)
        @app_commands.command(name='missoes',description='Veja e resgate suas missões')
        async def missoes(i):n,c,x=claim(i.guild.id,i.user.id);e=mission_embed(i.guild,i.user);e.description+=(f'\n\n🎁 Resgatado: **{c} F-Coins + {x} XP**' if n else '');await i.response.send_message(embed=e,ephemeral=True)
        @app_commands.command(name='conquistas',description='Veja suas conquistas')
        async def conquistas(i):await i.response.send_message(embed=badge_embed(i.guild,i.user),ephemeral=True)
        @app_commands.command(name='analytics',description='Analytics do servidor')
        @app_commands.default_permissions(manage_guild=True)
        async def ana(i):await i.response.send_message(embed=analytics(i.guild),ephemeral=True)
        @app_commands.command(name='mercado',description='Veja o mercado entre membros')
        async def mercado(i):
            with db() as c:rows=c.execute("SELECT * FROM market_listings WHERE guild_id=? AND status='open' ORDER BY id DESC LIMIT 15",(i.guild.id,)).fetchall()
            await i.response.send_message(embed=discord.Embed(title='🛒 F SOCIETY • MERCADO',description='\n'.join(f'**#{r["id"]} • {r["item_name"]}** — {r["price"]} F-Coins • <@{r["seller_id"]}>' for r in rows) or 'Nenhum item anunciado.',color=0x7C3AED))
        for cmd in (missoes,conquistas,ana,mercado):
            if self.get_command(cmd.name) is None:self.add_command(cmd)
    cp.CommandTree.__init__=tree
    original_bot_init=commands.Bot.__init__
    def bot_init(self,*a,**kw):
        original_bot_init(self,*a,**kw)
        old_setup=self.setup_hook
        async def setup():await old_setup(); asyncio.create_task(drops(self),name='fsociety-community-drops')
        self.setup_hook=setup
    commands.Bot.__init__=bot_init
    print('[OK] Comunidade • missões, conquistas, drops, mercado e analytics carregados')
