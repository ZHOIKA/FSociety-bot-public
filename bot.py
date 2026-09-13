import os, re, time, sqlite3, asyncio
from contextlib import closing
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
import discord
from discord import app_commands
from discord.ext import commands
from control_panel import Center, CommandTree, Panel, migrate, reply, handle_error
from tickets import install as install_tickets
from cve_alerts import install as install_cves
from darkweb_watch import install as install_darkweb
import logging

LOG = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, 'fsociety.db')
INVITE_RE = re.compile(r'(?:discord(?:\.gg|(?:app)?\.com/invite)/[\w-]+)', re.I)
URL_RE = re.compile(r'https?://\S+', re.I)


def env_load():
    p=os.path.join(ROOT,'.env')
    if os.path.exists(p):
        for line in open(p,encoding='utf-8'):
            line=line.strip()
            if line and not line.startswith('#') and '=' in line:
                k,v=line.split('=',1); os.environ.setdefault(k.strip(),v.strip().strip('"\''))
env_load()
TOKEN=os.getenv('FSOCIETY_TOKEN','').strip()
PREFIX=os.getenv('FSOCIETY_PREFIX','!').strip() or '!'


def db_init():
    con=sqlite3.connect(DB)
    con.executescript('''
    CREATE TABLE IF NOT EXISTS guilds(guild_id INTEGER PRIMARY KEY, prefix TEXT DEFAULT '!', welcome_channel INTEGER, welcome_message TEXT DEFAULT 'Bem-vindo(a), {user}, ao **{server}**!', welcome_gif TEXT, log_channel INTEGER, panel_gif TEXT, panel_emoji TEXT, anti_spam INTEGER DEFAULT 1, anti_invite INTEGER DEFAULT 1, anti_raid INTEGER DEFAULT 1, raid_limit INTEGER DEFAULT 8, raid_window INTEGER DEFAULT 10, xp_enabled INTEGER DEFAULT 1, level_channel INTEGER, economy_enabled INTEGER DEFAULT 1, auto_role INTEGER);
    CREATE TABLE IF NOT EXISTS users(guild_id INTEGER, user_id INTEGER, xp INTEGER DEFAULT 0, level INTEGER DEFAULT 0, coins INTEGER DEFAULT 0, messages INTEGER DEFAULT 0, warnings INTEGER DEFAULT 0, daily_at INTEGER DEFAULT 0, PRIMARY KEY(guild_id,user_id));
    CREATE TABLE IF NOT EXISTS warns(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,user_id INTEGER,moderator_id INTEGER,reason TEXT,created_at INTEGER);
    CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,user_id INTEGER,channel_id INTEGER,text TEXT,fire_at INTEGER);
    CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER,user_id INTEGER,channel_id INTEGER,created_at INTEGER);
    ''')
    columns = {row[1] for row in con.execute('PRAGMA table_info(users)')}
    if 'work_at' not in columns:
        con.execute('ALTER TABLE users ADD COLUMN work_at INTEGER DEFAULT 0')
    if 'xp_at' not in columns:
        con.execute('ALTER TABLE users ADD COLUMN xp_at INTEGER DEFAULT 0')
    migrate(con)
    con.commit(); con.close()

def q(sql,args=(),one=False):
    with closing(sqlite3.connect(DB)) as con, con:
        con.row_factory=sqlite3.Row
        cur=con.execute(sql,args)
        return cur.fetchone() if one else cur.fetchall()

def transfer_coins(gid, sender, recipient, amount):
    if amount <= 0 or sender == recipient:
        raise ValueError('Transferência inválida.')
    with closing(sqlite3.connect(DB)) as con, con:
        for uid in (sender, recipient):
            con.execute('INSERT OR IGNORE INTO users(guild_id,user_id) VALUES(?,?)', (gid, uid))
        changed = con.execute(
            'UPDATE users SET coins=coins-? WHERE guild_id=? AND user_id=? AND coins>=?',
            (amount, gid, sender, amount)).rowcount
        if not changed:
            return False
        con.execute('UPDATE users SET coins=coins+? WHERE guild_id=? AND user_id=?',
                    (amount, gid, recipient))
        return True

def guild(gid):
    r=q('SELECT * FROM guilds WHERE guild_id=?',(gid,),True)
    if not r:
        q('INSERT OR IGNORE INTO guilds(guild_id,prefix) VALUES(?,?)',(gid,PREFIX)); r=q('SELECT * FROM guilds WHERE guild_id=?',(gid,),True)
    return r

def user(gid,uid):
    q('INSERT OR IGNORE INTO users(guild_id,user_id) VALUES(?,?)',(gid,uid)); return q('SELECT * FROM users WHERE guild_id=? AND user_id=?',(gid,uid),True)

def clean_text(s): return discord.utils.escape_mentions(s or '')

def fmt(text, member, g):
    return (text.replace('{user}',member.mention).replace('{username}',clean_text(member.display_name)).replace('{server}',clean_text(g.name)).replace('{id}',str(member.id)).replace('{member_count}',str(g.member_count)))

# ============================================================
import asyncio
import datetime as _dt
import json as _json
import sqlite3 as _sqlite3

def _schedule_db():
    con = _sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            hour INTEGER NOT NULL,
            minute INTEGER NOT NULL,
            weekdays TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'recorrente',
            run_date TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            last_sent TEXT
        )
    """)
    con.commit()
    return con

def create_scheduled_message(guild_id, channel_id, author_id, content,
                             hour, minute, weekdays, mode="recorrente",
                             run_date=None):
    con = _schedule_db()
    cur = con.execute("""
        INSERT INTO scheduled_messages
        (guild_id, channel_id, author_id, content, hour, minute, weekdays, mode, run_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (guild_id, channel_id, author_id, content, hour, minute,
          _json.dumps(sorted(set(weekdays))), mode, run_date))
    con.commit()
    ident = cur.lastrowid
    con.close()
    return ident

def list_scheduled_messages(guild_id):
    con = _schedule_db()
    rows = con.execute("""
        SELECT id, channel_id, content, hour, minute, weekdays, mode, run_date, active, last_sent
        FROM scheduled_messages WHERE guild_id=? ORDER BY hour, minute, id
    """, (guild_id,)).fetchall()
    con.close()
    return rows

def set_scheduled_message_active(guild_id, ident, active):
    con = _schedule_db()
    con.execute("UPDATE scheduled_messages SET active=? WHERE guild_id=? AND id=?",
                (1 if active else 0, guild_id, ident))
    con.commit()
    con.close()

def delete_scheduled_message(guild_id, ident):
    con = _schedule_db()
    con.execute("DELETE FROM scheduled_messages WHERE guild_id=? AND id=?",
                (guild_id, ident))
    con.commit()
    con.close()

async def fsociety_scheduled_message_loop(bot):
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = _dt.datetime.now()
        weekday = now.weekday()
        today = now.date().isoformat()
        minute_key = now.strftime("%Y-%m-%d %H:%M")
        con = _schedule_db()
        rows = con.execute("""
            SELECT id, guild_id, channel_id, content, hour, minute, weekdays, mode, run_date, last_sent
            FROM scheduled_messages
            WHERE active=1 AND hour=? AND minute=?
        """, (now.hour, now.minute)).fetchall()

        for ident, guild_id, channel_id, content, hour, minute, weekdays_json, mode, run_date, last_sent in rows:
            try:
                weekdays = _json.loads(weekdays_json)
                allowed = weekday in weekdays
                if mode == "uma_vez":
                    allowed = run_date == today
                if not allowed or last_sent == minute_key:
                    continue

                channel = bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await bot.fetch_channel(channel_id)
                    except Exception:
                        channel = None
                if channel is None:
                    continue

                await channel.send(content)
                con.execute(
                    "UPDATE scheduled_messages SET last_sent=? WHERE id=?",
                    (minute_key, ident)
                )
                if mode == "uma_vez":
                    con.execute("UPDATE scheduled_messages SET active=0 WHERE id=?", (ident,))
                con.commit()
            except Exception:
                continue

        con.close()
        await asyncio.sleep(20)


async def configured_prefix(bot, message):
    return (guild(message.guild.id)['prefix'] or PREFIX) if message.guild else PREFIX

def check_target(i, member, *, timeout=False):
    if member.id in (i.user.id, i.guild.owner_id, i.guild.me.id):
        raise ValueError('Não é possível moderar você mesmo, o dono do servidor ou o bot.')
    if i.user.id != i.guild.owner_id and member.top_role >= i.user.top_role:
        raise ValueError('Esse membro tem um cargo igual ou superior ao seu.')
    if member.top_role >= i.guild.me.top_role:
        raise ValueError('Meu cargo precisa estar acima do cargo desse membro.')
    if timeout and member.guild_permissions.administrator:
        raise ValueError('Administradores não podem receber timeout.')

def check_role(i, role):
    if role.is_default() or role.managed:
        raise ValueError('Não posso alterar cargos de integração nem @everyone.')
    if role >= i.guild.me.top_role:
        raise ValueError('O cargo precisa ficar abaixo do meu maior cargo.')
    if i.user.id != i.guild.owner_id and role >= i.user.top_role:
        raise ValueError('Você só pode gerenciar cargos abaixo do seu maior cargo.')

class FSociety(commands.Bot):
    def __init__(self):
        intents=discord.Intents.all(); super().__init__(command_prefix=configured_prefix,intents=intents,help_command=None,tree_cls=CommandTree,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False))
        self.spam=defaultdict(lambda:defaultdict(deque)); self.joins=defaultdict(deque); self.synced=False; self.started_at=time.time()
    async def setup_hook(self):
        db_init()
        self.center.tickets.register_views()
        await self.tree.sync()
        self.synced = True
        self.schedule_task = asyncio.create_task(fsociety_scheduled_message_loop(self))
        self.center.cves.start()
    async def close(self):
        if hasattr(self, 'center'):
            await self.center.cves.close()
        task = getattr(self, 'schedule_task', None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await super().close()
    async def on_ready(self):
        if not self.synced:
            try: await self.tree.sync(); self.synced=True
            except Exception as e: print('Falha ao sincronizar comandos:',e)
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching,name='F SOCIETY | /painel'))
        print(f'F SOCIETY online como {self.user} | {len(self.guilds)} servidores')
    async def log(self,gid,title,desc):
        r=guild(gid); ch=self.get_channel(r['log_channel']) if r['log_channel'] else None
        if ch:
            try: await ch.send(embed=discord.Embed(title=title,description=desc,timestamp=datetime.now(timezone.utc)))
            except: pass
    async def show_profile(self,i,member):
        u=user(i.guild.id,member.id); e=discord.Embed(title=f'Perfil de {member.display_name}',description=f'{member.mention}\n\n**Nível:** {u["level"]}\n**XP:** {u["xp"]}\n**Moedas:** {u["coins"]}\n**Mensagens:** {u["messages"]}\n**Avisos:** {u["warnings"]}')
        e.set_thumbnail(url=member.display_avatar.url); await i.response.edit_message(embed=e,view=Panel(self.center,i.user.id,admin=False))
    async def on_member_join(self,m):
        r=self.center.settings(m.guild.id); now=time.time(); dq=self.joins[m.guild.id]; dq.append(now)
        while dq and now-dq[0]>r['raid_window']: dq.popleft()
        if r['anti_raid'] and len(dq)==r['raid_limit']:
            await self.log(m.guild.id,'ANTI-RAID',f'Entrada em massa detectada: **{len(dq)}** membros em {r["raid_window"]} segundos.')
        if r['welcome_channel']:
            ch=m.guild.get_channel(r['welcome_channel'])
            if ch:
                e=discord.Embed(title='NOVO MEMBRO',description=fmt(r['welcome_message'],m,m.guild)[:4000])
                e.set_thumbnail(url=m.display_avatar.replace(size=512).url)
                if r['welcome_gif']:
                    e.set_image(url=r['welcome_gif'])
                try:
                    await ch.send(embed=e)
                except discord.HTTPException:
                    LOG.exception('Falha nas boas-vindas do servidor %s', m.guild.id)
        role = m.guild.get_role(r['auto_role']) if r['auto_role'] else None
        if role and not role.managed and role < m.guild.me.top_role and not role.permissions.administrator and not role.permissions.manage_guild and not role.permissions.manage_roles:
            try:
                await m.add_roles(role, reason='Cargo automático de boas-vindas')
            except discord.HTTPException:
                LOG.exception('Falha no cargo automático do servidor %s', m.guild.id)
        await self.log(m.guild.id,'ENTRADA',f'{m.mention} entrou no servidor.')
    async def on_member_remove(self,m): await self.log(m.guild.id,'SAÍDA',f'{m} saiu do servidor.')
    async def on_message(self,m):
        if m.author.bot or not m.guild: return
        r=self.center.settings(m.guild.id); u=user(m.guild.id,m.author.id); now=time.time()
        q('UPDATE users SET messages=messages+1 WHERE guild_id=? AND user_id=?', (m.guild.id,m.author.id))
        if r['anti_invite'] and INVITE_RE.search(m.content) and not m.author.guild_permissions.manage_messages:
            try:
                await m.delete()
            except discord.HTTPException:
                LOG.exception('Não foi possível remover convite no servidor %s', m.guild.id)
                return
            await self.log(m.guild.id,'ANTI-DIVULGAÇÃO',f'{m.author.mention} teve uma mensagem removida.')
            try:
                await m.channel.send(f'{m.author.mention}, divulgação de servidores não é permitida aqui.',delete_after=5)
            except discord.HTTPException:
                LOG.exception('Falha ao enviar aviso de convite no servidor %s', m.guild.id)
            return
        if r['anti_spam'] and not m.author.guild_permissions.manage_messages:
            dq=self.spam[m.guild.id][m.author.id]; dq.append(now)
            while dq and now-dq[0]>r['spam_window']: dq.popleft()
            if len(dq)>=r['spam_limit']:
                dq.clear()
                try:
                    await m.author.timeout(timedelta(seconds=r['spam_timeout']),reason='Anti-spam automático')
                except discord.HTTPException:
                    LOG.exception('Falha ao aplicar anti-spam no servidor %s', m.guild.id)
                    return
                await self.log(m.guild.id,'ANTI-SPAM',f'{m.author.mention} recebeu timeout automático por spam.')
                return
        if r['xp_enabled'] and now-u['xp_at'] >= r['xp_cooldown']:
            gained=r['xp_gain']
            newxp=u['xp']+gained; oldlevel=u['level']; level=int((newxp//100)**0.5)
            q('UPDATE users SET xp=?,level=?,xp_at=? WHERE guild_id=? AND user_id=?',(newxp,level,int(now),m.guild.id,m.author.id))
            if level>oldlevel:
                ch=m.guild.get_channel(r['level_channel']) if r['level_channel'] else m.channel
                ch = ch or m.channel
                try:
                    await ch.send(f'{m.author.mention} alcançou o **nível {level}**!')
                except discord.HTTPException:
                    LOG.exception('Falha no aviso de nível do servidor %s', m.guild.id)
        await self.process_commands(m)

bot=FSociety()
bot.center = Center(bot, lambda: DB, q, guild, fmt)
install_tickets(bot.center)
install_cves(bot.center)
install_darkweb(bot.center)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, (commands.CheckFailure, commands.UserInputError)):
        await ctx.send('Confira as permissões e os argumentos. Use /ajuda para consultar os comandos.')
    else:
        LOG.error('Falha em comando de prefixo', exc_info=(type(error), error, error.__traceback__))
        await ctx.send('Não foi possível concluir o comando. Confira as permissões do bot.')

@bot.tree.command(name='configurar', description='Abre a central completa de configuração do servidor')
@app_commands.default_permissions(manage_guild=True)
async def configurar(i: discord.Interaction):
    await bot.center.open(i)

@bot.tree.command(name='diagnostico', description='Verifica canais e permissões dos recursos configurados')
@app_commands.default_permissions(manage_guild=True)
async def diagnostico(i: discord.Interaction):
    await reply(i, bot.center.diagnostics(i.guild))

@bot.tree.command(name='untimeout', description='Remove o timeout de um membro')
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.bot_has_permissions(moderate_members=True)
async def untimeout(i: discord.Interaction, usuario: discord.Member):
    check_target(i, usuario)
    await i.response.defer(ephemeral=True)
    await usuario.timeout(None, reason=f'Timeout removido por {i.user.id}')
    await reply(i, f'Timeout de {usuario.mention} removido.')
    await bot.log(i.guild.id, 'TIMEOUT REMOVIDO', f'{i.user.mention} removeu o timeout de {usuario.mention}.')


def perm(): return app_commands.default_permissions(manage_guild=True)

@bot.tree.command(name='painel',description='Abre o painel premium do F SOCIETY')
async def painel(i:discord.Interaction):
    await bot.center.open(i, admin=False)

@bot.tree.command(name='perfil',description='Mostra o perfil, XP e moedas')
async def perfil(i,usuario:discord.Member=None):
    usuario=usuario or i.user; u=user(i.guild.id,usuario.id); e=discord.Embed(title=f'Perfil de {usuario.display_name}',description=f'{usuario.mention}\n\n**Nível:** {u["level"]}\n**XP:** {u["xp"]}\n**Moedas:** {u["coins"]}\n**Mensagens:** {u["messages"]}\n**Avisos:** {u["warnings"]}'); e.set_thumbnail(url=usuario.display_avatar.url); await i.response.send_message(embed=e)

@bot.tree.command(name='ranking',description='Ranking de XP do servidor')
async def ranking(i):
    rows=q('SELECT user_id,level,xp FROM users WHERE guild_id=? ORDER BY xp DESC LIMIT 10',(i.guild.id,)); lines=[]
    for n,r in enumerate(rows,1): lines.append(f'**{n}.** <@{r["user_id"]}> — nível **{r["level"]}** · {r["xp"]} XP')
    await i.response.send_message(embed=discord.Embed(title='RANKING DE XP',description='\n'.join(lines) or 'Ainda não há dados.'))

@bot.tree.command(name='saldo',description='Mostra seu saldo')
async def saldo(i,usuario:discord.Member=None):
    usuario=usuario or i.user; await i.response.send_message(f'{usuario.mention} possui **{user(i.guild.id,usuario.id)["coins"]} moedas**.')

@bot.tree.command(name='daily',description='Resgata sua recompensa diária')
async def daily(i):
    if not guild(i.guild.id)['economy_enabled']:
        return await i.response.send_message('A economia está desativada neste servidor.',ephemeral=True)
    u=user(i.guild.id,i.user.id); now=int(time.time());
    if now-u['daily_at']<86400: return await i.response.send_message(f'Você já resgatou. Tente novamente em <t:{u["daily_at"]+86400}:R>.',ephemeral=True)
    reward=bot.center.settings(i.guild.id)['daily_reward']
    q('UPDATE users SET coins=coins+?,daily_at=? WHERE guild_id=? AND user_id=?',(reward,now,i.guild.id,i.user.id))
    await i.response.send_message(f'{i.user.mention}, você recebeu **{reward} moedas**!')

@bot.tree.command(name='pagar',description='Transfere moedas para outro membro')
async def pagar(i,usuario:discord.Member,quantia:app_commands.Range[int,1,1000000]):
    if not guild(i.guild.id)['economy_enabled']:
        return await i.response.send_message('A economia está desativada neste servidor.',ephemeral=True)
    if usuario.id == i.user.id or usuario.bot:
        return await i.response.send_message('Escolha outro membro que não seja um bot.',ephemeral=True)
    if not transfer_coins(i.guild.id, i.user.id, usuario.id, quantia):
        return await i.response.send_message('Saldo insuficiente.',ephemeral=True)
    await i.response.send_message(f'{i.user.mention} enviou **{quantia} moedas** para {usuario.mention}.')

@bot.tree.command(name='ban',description='Bane um membro')
@app_commands.default_permissions(ban_members=True)
async def ban(i,usuario:discord.Member,razao:app_commands.Range[str,1,400]='Sem motivo informado'):
    check_target(i, usuario)
    await i.response.defer()
    await usuario.ban(reason=razao); await i.followup.send(f'{usuario} foi banido. Motivo: **{razao}**'); await bot.log(i.guild.id,'BAN',f'{i.user.mention} baniu **{usuario}**.\nMotivo: {razao}')

@bot.tree.command(name='kick',description='Expulsa um membro')
@app_commands.default_permissions(kick_members=True)
async def kick(i,usuario:discord.Member,razao:app_commands.Range[str,1,400]='Sem motivo informado'):
    check_target(i, usuario)
    await i.response.defer()
    await usuario.kick(reason=razao); await i.followup.send(f'{usuario} foi expulso.'); await bot.log(i.guild.id,'KICK',f'{i.user.mention} expulsou **{usuario}**.\nMotivo: {razao}')

@bot.tree.command(name='timeout',description='Coloca um membro em timeout')
@app_commands.default_permissions(moderate_members=True)
async def timeout(i,usuario:discord.Member,minutos:app_commands.Range[int,1,10080],razao:app_commands.Range[str,1,400]='Sem motivo informado'):
    check_target(i, usuario, timeout=True)
    await i.response.defer()
    await usuario.timeout(timedelta(minutes=minutos),reason=razao); await i.followup.send(f'{usuario.mention} recebeu timeout de **{minutos} minutos**.'); await bot.log(i.guild.id,'TIMEOUT',f'{i.user.mention} aplicou timeout em {usuario.mention} por {minutos} minutos.')

@bot.tree.command(name='avisar',description='Registra um aviso')
@app_commands.default_permissions(moderate_members=True)
async def avisar(i,usuario:discord.Member,razao:app_commands.Range[str,1,400]='Sem motivo informado'):
    check_target(i, usuario)
    user(i.guild.id,usuario.id); q('UPDATE users SET warnings=warnings+1 WHERE guild_id=? AND user_id=?',(i.guild.id,usuario.id)); q('INSERT INTO warns(guild_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)',(i.guild.id,usuario.id,i.user.id,razao,int(time.time()))); await i.response.send_message(f'{usuario.mention} recebeu um aviso. **{razao}**'); await bot.log(i.guild.id,'AVISO',f'{usuario.mention} recebeu aviso de {i.user.mention}.\nMotivo: {razao}')

@bot.tree.command(name='limpar',description='Apaga mensagens do canal')
@app_commands.default_permissions(manage_messages=True)
async def limpar(i,quantidade:app_commands.Range[int,1,100]):
    await i.response.defer(ephemeral=True); deleted=await i.channel.purge(limit=quantidade); await i.followup.send(f'Foram apagadas **{len(deleted)} mensagens**.',ephemeral=True)

@bot.tree.command(name='cronometro',description='Envia uma contagem regressiva')
async def cronometro(i,segundos:app_commands.Range[int,3,300],mensagem:app_commands.Range[str,1,1800]='Tempo encerrado!'):
    await i.response.send_message(f'**{segundos}s** restantes — {mensagem}'); msg=await i.original_response()
    for n in range(segundos-1,0,-1):
        await asyncio.sleep(1)
        if n % 5 == 0 or n <= 3:
            try: await msg.edit(content=f'**{n}s** restantes — {mensagem}')
            except: pass
    await asyncio.sleep(1)
    try: await msg.edit(content=f'**0s** — {mensagem}')
    except: pass

@bot.tree.command(name='config',description='Configura recursos do servidor')
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(recurso='Recurso',canal='Canal relacionado',ativo='Ativar/desativar')
async def config(i,recurso:str=None,canal:discord.TextChannel=None,ativo:bool=None):
    if recurso is None:
        return await bot.center.open(i)
    r=guild(i.guild.id); recurso=recurso.lower()
    cols={'spam':'anti_spam','antispam':'anti_spam','divulgacao':'anti_invite','convites':'anti_invite','raid':'anti_raid','xp':'xp_enabled','economia':'economy_enabled'}
    if recurso=='logs': col='log_channel'
    elif recurso=='boasvindas': col='welcome_channel'
    elif recurso=='level': col='level_channel'
    elif recurso in cols: col=cols[recurso]
    else: return await i.response.send_message('Recursos: `logs`, `boasvindas`, `level`, `spam`, `divulgacao`, `raid`, `xp`, `economia`.',ephemeral=True)
    if col.endswith('_channel'):
        if not canal: return await i.response.send_message('Informe um canal.',ephemeral=True)
        q(f'UPDATE guilds SET {col}=? WHERE guild_id=?',(canal.id,i.guild.id)); msg=f'{recurso} configurado em {canal.mention}.'
    else:
        val=1 if ativo is None or ativo else 0; q(f'UPDATE guilds SET {col}=? WHERE guild_id=?',(val,i.guild.id)); msg=f'{recurso}: **{"ATIVO" if val else "INATIVO"}**.'
    await i.response.send_message(msg)

@bot.tree.command(name='webhook',description='Cria um webhook no canal atual')
@app_commands.default_permissions(manage_webhooks=True)
async def webhook(i,nome:app_commands.Range[str,1,80]='F SOCIETY Webhook'):
    if not isinstance(i.channel,discord.TextChannel): return await i.response.send_message('Use este comando em um canal de texto.',ephemeral=True)
    await i.response.defer(ephemeral=True)
    w=await i.channel.create_webhook(name=nome,reason=f'Criado por {i.user}')
    await i.followup.send(f'Webhook criado com sucesso.\nURL: {w.url}',ephemeral=True)

@bot.tree.command(name='ajuda',description='Mostra os principais comandos')
async def ajuda(i):
    await bot.center.open(i, 'help', admin=False)

@bot.command(name='painel')
async def painel_prefix(ctx):
    await ctx.send(embed=bot.center.page(ctx.guild),view=Panel(bot.center,ctx.author.id,admin=False))


def human_uptime(seconds):
    d, seconds = divmod(int(seconds), 86400); h, seconds = divmod(seconds, 3600); m, s = divmod(seconds, 60)
    return ' '.join(x for x in (f'{d}d' if d else '', f'{h}h' if h else '', f'{m}min' if m else '', f'{s}s') if x) or '0s'

def is_mod(member):
    return member.guild_permissions.manage_messages or member.guild_permissions.moderate_members or member.guild_permissions.manage_guild

@bot.tree.command(name='ping',description='Mostra a latência do F SOCIETY')
async def ping(i): await i.response.send_message(f'**Pong!** `{round(bot.latency*1000)} ms`')

@bot.tree.command(name='botinfo',description='Mostra informações do F SOCIETY')
async def botinfo(i):
    e=discord.Embed(title='F SOCIETY — SISTEMA',description=f'**Servidores:** {len(bot.guilds)}\n**Latência:** {round(bot.latency*1000)} ms\n**Uptime:** {human_uptime(time.time()-bot.started_at)}\n**Python:** {__import__("platform").python_version()}\n**Discord.py:** {discord.__version__}')
    e.set_thumbnail(url=bot.user.display_avatar.url); await i.response.send_message(embed=e)

@bot.tree.command(name='servidor',description='Mostra informações do servidor')
async def servidor(i):
    g=i.guild; owner=g.owner.mention if g.owner else 'Indisponível'
    e=discord.Embed(title=g.name,description=f'**Dono:** {owner}\n**Membros:** {g.member_count}\n**Canais:** {len(g.channels)}\n**Cargos:** {len(g.roles)}\n**ID:** `{g.id}`\n**Criado:** <t:{int(g.created_at.timestamp())}:F>')
    if g.icon: e.set_thumbnail(url=g.icon.url)
    await i.response.send_message(embed=e)

@bot.tree.command(name='avatar',description='Mostra o avatar de um membro')
async def avatar(i,usuario:discord.Member=None):
    usuario=usuario or i.user; e=discord.Embed(title=f'Avatar de {usuario.display_name}'); e.set_image(url=usuario.display_avatar.replace(size=1024).url); await i.response.send_message(embed=e)

@bot.tree.command(name='userinfo',description='Mostra informações de um membro')
async def userinfo(i,usuario:discord.Member=None):
    usuario=usuario or i.user; u=user(i.guild.id,usuario.id)
    e=discord.Embed(title=f'Informações — {usuario.display_name}',description=f'{usuario.mention}\n**ID:** `{usuario.id}`\n**Conta criada:** <t:{int(usuario.created_at.timestamp())}:D>\n**Entrou:** <t:{int(usuario.joined_at.timestamp())}:D>\n**Cargo principal:** {usuario.top_role.mention}\n**XP:** {u["xp"]} · **Nível:** {u["level"]}\n**Avisos:** {u["warnings"]}')
    e.set_thumbnail(url=usuario.display_avatar.url); await i.response.send_message(embed=e)

@bot.tree.command(name='avisos',description='Consulta os avisos de um membro')
@app_commands.default_permissions(moderate_members=True)
async def avisos(i,usuario:discord.Member):
    rows=q('SELECT reason,moderator_id,created_at FROM warns WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 15',(i.guild.id,usuario.id))
    desc='\n'.join(f'• <t:{r["created_at"]}:d> — {r["reason"]} — por <@{r["moderator_id"]}>' for r in rows) or 'Nenhum aviso registrado.'
    await i.response.send_message(embed=discord.Embed(title=f'AVISOS — {usuario.display_name}',description=desc[:4000]),ephemeral=True)

@bot.tree.command(name='limparavisos',description='Remove todos os avisos de um membro')
@app_commands.default_permissions(moderate_members=True)
async def limparavisos(i,usuario:discord.Member):
    q('DELETE FROM warns WHERE guild_id=? AND user_id=?',(i.guild.id,usuario.id)); q('UPDATE users SET warnings=0 WHERE guild_id=? AND user_id=?',(i.guild.id,usuario.id)); await i.response.send_message(f'Histórico de avisos de {usuario.mention} limpo.'); await bot.log(i.guild.id,'AVISOS LIMPOS',f'{i.user.mention} limpou os avisos de {usuario.mention}.')

@bot.tree.command(name='slowmode',description='Define o modo lento de um canal')
@app_commands.default_permissions(manage_channels=True)
async def slowmode(i,segundos:app_commands.Range[int,0,21600]):
    if not isinstance(i.channel, (discord.TextChannel, discord.Thread)):
        raise ValueError('Use este comando em um canal de texto ou tópico.')
    await i.response.defer(ephemeral=True)
    await i.channel.edit(slowmode_delay=segundos); await i.followup.send(f'Modo lento definido para **{segundos}s**.')

@bot.tree.command(name='travar',description='Trava o canal atual para membros')
@app_commands.default_permissions(manage_channels=True)
async def travar(i):
    if not isinstance(i.channel, discord.TextChannel):
        raise ValueError('Use este comando em um canal de texto.')
    await i.response.defer(ephemeral=True)
    async with bot.center.tickets.lock(i.guild.id):
        role = i.guild.default_role
        overwrite = i.channel.overwrites_for(role)
        q('INSERT OR IGNORE INTO channel_locks(guild_id,channel_id,send_messages) VALUES(?,?,?)',
          (i.guild.id, i.channel.id, overwrite.send_messages))
        overwrite.send_messages = False
        await i.channel.set_permissions(role, overwrite=overwrite, reason=f'Bloqueado por {i.user.id}')
    await reply(i, 'Canal travado. As outras permissões foram preservadas.')

@bot.tree.command(name='destravar',description='Destrava o canal atual')
@app_commands.default_permissions(manage_channels=True)
async def destravar(i):
    if not isinstance(i.channel, discord.TextChannel):
        raise ValueError('Use este comando em um canal de texto.')
    await i.response.defer(ephemeral=True)
    async with bot.center.tickets.lock(i.guild.id):
        saved = q('SELECT send_messages FROM channel_locks WHERE guild_id=? AND channel_id=?', (i.guild.id, i.channel.id), True)
        if saved is None:
            raise ValueError('Não há bloqueio registrado pelo bot neste canal. Confira as permissões do canal no Discord.')
        overwrite = i.channel.overwrites_for(i.guild.default_role)
        overwrite.send_messages = None if saved['send_messages'] is None else bool(saved['send_messages'])
        await i.channel.set_permissions(i.guild.default_role, overwrite=overwrite, reason=f'Desbloqueado por {i.user.id}')
        q('DELETE FROM channel_locks WHERE guild_id=? AND channel_id=?', (i.guild.id, i.channel.id))
    await reply(i, 'Permissão de envio restaurada ao valor anterior ao bloqueio.')

@bot.tree.command(name='anuncio',description='Envia um anúncio formatado')
@app_commands.default_permissions(manage_messages=True)
async def anuncio(i,titulo:app_commands.Range[str,1,256],mensagem:app_commands.Range[str,1,4000]):
    e=discord.Embed(title=titulo,description=mensagem); e.set_footer(text=f'Publicado por {i.user.display_name}')
    await i.response.send_message(embed=e)

@bot.tree.command(name='enquete',description='Cria uma enquete simples')
async def enquete(i,pergunta:app_commands.Range[str,1,2000]):
    await i.response.send_message(embed=discord.Embed(title='ENQUETE',description=pergunta)); m=await i.original_response(); await m.add_reaction('👍'); await m.add_reaction('👎')

@bot.tree.command(name='roleadd',description='Adiciona um cargo a um membro')
@app_commands.default_permissions(manage_roles=True)
async def roleadd(i,usuario:discord.Member,cargo:discord.Role):
    check_role(i, cargo)
    await i.response.defer()
    await usuario.add_roles(cargo,reason=f'{i.user} via F SOCIETY'); await i.followup.send(f'{cargo.mention} adicionado a {usuario.mention}.')

@bot.tree.command(name='roleremove',description='Remove um cargo de um membro')
@app_commands.default_permissions(manage_roles=True)
async def roleremove(i,usuario:discord.Member,cargo:discord.Role):
    check_role(i, cargo)
    await i.response.defer()
    await usuario.remove_roles(cargo,reason=f'{i.user} via F SOCIETY'); await i.followup.send(f'{cargo.mention} removido de {usuario.mention}.')

@bot.tree.command(name='userinfo_cargos',description='Lista os cargos de um membro')
async def userinfo_cargos(i,usuario:discord.Member=None):
    usuario=usuario or i.user; roles=[r.mention for r in reversed(usuario.roles) if r != i.guild.default_role]; await i.response.send_message(f'**Cargos de {usuario.mention}:**\n' + (', '.join(roles) or 'Nenhum cargo personalizado.'))

@bot.tree.command(name='topmoedas',description='Ranking de moedas')
async def topmoedas(i):
    rows=q('SELECT user_id,coins FROM users WHERE guild_id=? ORDER BY coins DESC LIMIT 10',(i.guild.id,)); desc='\n'.join(f'**{n}.** <@{r["user_id"]}> — **{r["coins"]}** moedas' for n,r in enumerate(rows,1)) or 'Sem dados.'; await i.response.send_message(embed=discord.Embed(title='RANKING DE MOEDAS',description=desc))

@bot.tree.command(name='trabalhar',description='Trabalha e recebe moedas')
async def trabalhar(i):
    if not guild(i.guild.id)['economy_enabled']:
        return await i.response.send_message('A economia está desativada neste servidor.',ephemeral=True)
    u=user(i.guild.id,i.user.id); now=int(time.time())
    if now-u['work_at']<3600: return await i.response.send_message(f'Você está cansado. Tente novamente <t:{u["work_at"]+3600}:R>.',ephemeral=True)
    reward=bot.center.settings(i.guild.id)['work_reward'] + (u['level']*10); q('UPDATE users SET coins=coins+?,work_at=? WHERE guild_id=? AND user_id=?',(reward,now,i.guild.id,i.user.id)); await i.response.send_message(f'Você trabalhou e recebeu **{reward} moedas**.')

@bot.tree.command(name='servericon',description='Mostra o ícone do servidor')
async def servericon(i):
    if not i.guild.icon: return await i.response.send_message('Este servidor não possui ícone.')
    await i.response.send_message(i.guild.icon.url)

@bot.tree.command(name='say',description='Faz o bot enviar uma mensagem')
@app_commands.default_permissions(manage_messages=True)
async def say(i,mensagem:app_commands.Range[str,1,2000]):
    await i.response.defer(ephemeral=True); await i.channel.send(mensagem); await i.followup.send('Mensagem enviada.',ephemeral=True)

@bot.command(name='ping')
async def ping_prefix(ctx): await ctx.send(f'**Pong!** `{round(bot.latency*1000)} ms`')

@bot.command(name='perfil')
async def perfil_prefix(ctx, membro:discord.Member=None):
    membro=membro or ctx.author; u=user(ctx.guild.id,membro.id); e=discord.Embed(title=f'Perfil de {membro.display_name}',description=f'{membro.mention}\n**Nível:** {u["level"]}\n**XP:** {u["xp"]}\n**Moedas:** {u["coins"]}\n**Mensagens:** {u["messages"]}'); e.set_thumbnail(url=membro.display_avatar.url); await ctx.send(embed=e)

@bot.command(name='ranking')
async def ranking_prefix(ctx):
    rows=q('SELECT user_id,level,xp FROM users WHERE guild_id=? ORDER BY xp DESC LIMIT 10',(ctx.guild.id,)); await ctx.send(embed=discord.Embed(title='RANKING DE XP',description='\n'.join(f'**{n}.** <@{r["user_id"]}> — nível **{r["level"]}** · {r["xp"]} XP' for n,r in enumerate(rows,1)) or 'Ainda não há dados.'))

# ============================================================
from discord.ext import commands as _commands
import discord as _discord

@_commands.hybrid_group(name="agenda", description="Gerencia mensagens automáticas.")
@ _commands.has_guild_permissions(manage_guild=True)
async def agenda(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send("Use `/agenda criar`, `/agenda lista`, `/agenda pausar`, `/agenda ativar` ou `/agenda excluir`.")

@agenda.command(name="criar", description="Cria uma mensagem automática.")
@_commands.has_guild_permissions(manage_guild=True)
async def agenda_criar(ctx, canal: _discord.TextChannel, horario: str,
                       dias: str, mensagem: str, tipo: str = "recorrente",
                       data: str = None):
    try:
        hour, minute = map(int, horario.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await ctx.send("Horário inválido. Use `HH:MM`, por exemplo `08:30`.", ephemeral=True)
        return

    mapa = {"seg":0,"segunda":0,"ter":1,"terça":1,"terca":1,
            "qua":2,"quarta":2,"qui":3,"quinta":3,
            "sex":4,"sexta":4,"sab":5,"sábado":5,"sabado":5,
            "dom":6,"domingo":6}
    try:
        weekdays = [mapa[x.strip().lower()] for x in dias.split(",")]
    except KeyError:
        await ctx.send("Dias inválidos. Use: `seg,ter,qua,qui,sex,sab,dom`.", ephemeral=True)
        return

    tipo = tipo.lower()
    if tipo not in ("recorrente", "uma_vez"):
        await ctx.send("O tipo deve ser `recorrente` ou `uma_vez`.", ephemeral=True)
        return
    if tipo == "uma_vez" and not data:
        await ctx.send("Para `uma_vez`, informe a data no formato `AAAA-MM-DD`.", ephemeral=True)
        return

    if not mensagem.strip() or len(mensagem) > 2000:
        await ctx.send('A mensagem deve ter entre 1 e 2000 caracteres.', ephemeral=True)
        return
    if tipo == 'uma_vez':
        try:
            scheduled = datetime.combine(datetime.strptime(data, '%Y-%m-%d').date(), _dt.time(hour, minute))
            if scheduled <= datetime.now():
                raise ValueError
        except ValueError:
            await ctx.send('Informe uma data válida em AAAA-MM-DD e um horário futuro.', ephemeral=True)
            return

    ident = create_scheduled_message(ctx.guild.id, canal.id, ctx.author.id, mensagem, hour, minute, weekdays, tipo, data)
    await ctx.send(f"Programação **#{ident}** criada em {canal.mention} para **{horario}**. Tipo: **{tipo}**.")

@agenda.command(name="lista", description="Lista as mensagens programadas.")
@_commands.has_guild_permissions(manage_guild=True)
async def agenda_lista(ctx):
    rows = list_scheduled_messages(ctx.guild.id)
    if not rows:
        await ctx.send("Nenhuma mensagem programada neste servidor.")
        return
    linhas = []
    nomes = ["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"]
    for r in rows:
        ident, channel_id, content, hour, minute, wd, mode, run_date, active, last_sent = r
        dias_txt = ", ".join(nomes[i] for i in _json.loads(wd))
        status = "ATIVA" if active else "PAUSADA"
        extra = f" em {run_date}" if mode == "uma_vez" else ""
        linhas.append(f"`#{ident}` • <#{channel_id}> • `{hour:02d}:{minute:02d}` • {dias_txt} • {mode}{extra} • **{status}**\n{content[:120]}")
    page = ''
    for line in linhas:
        if len(page) + len(line) + 2 > 1900:
            await ctx.send(page)
            page = ''
        page += ('\n\n' if page else '') + line
    if page:
        await ctx.send(page)

@agenda.command(name="pausar", description="Pausa uma programação.")
@_commands.has_guild_permissions(manage_guild=True)
async def agenda_pausar(ctx, id: int):
    set_scheduled_message_active(ctx.guild.id, id, False)
    await ctx.send(f"Programação **#{id}** pausada.")

@agenda.command(name="ativar", description="Ativa uma programação.")
@_commands.has_guild_permissions(manage_guild=True)
async def agenda_ativar(ctx, id: int):
    set_scheduled_message_active(ctx.guild.id, id, True)
    await ctx.send(f"Programação **#{id}** ativada.")

@agenda.command(name="excluir", description="Exclui uma programação.")
@_commands.has_guild_permissions(manage_guild=True)
async def agenda_excluir(ctx, id: int):
    delete_scheduled_message(ctx.guild.id, id)
    await ctx.send(f"Programação **#{id}** excluída.")

bot.add_command(agenda)

for name, required in {
    'ban': 'ban_members', 'kick': 'kick_members', 'timeout': 'moderate_members',
    'limpar': 'manage_messages', 'roleadd': 'manage_roles', 'roleremove': 'manage_roles',
    'slowmode': 'manage_channels', 'travar': 'manage_roles', 'destravar': 'manage_roles',
    'webhook': 'manage_webhooks',
}.items():
    app_commands.checks.bot_has_permissions(**{required: True})(bot.tree.get_command(name))

for command in bot.tree.get_commands():
    command.guild_only = True

@bot.check
async def server_only(ctx):
    return ctx.guild is not None

def main():
    if not TOKEN:
        raise SystemExit('FSOCIETY_TOKEN não configurado no .env')
    bot.run(TOKEN)

if __name__ == '__main__':
    main()

