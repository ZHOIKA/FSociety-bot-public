"""Painel de atividade da staff integrado ao /configurar."""
import logging, math, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
import discord
from discord.ext import commands
import control_panel as cp

LOG=logging.getLogger(__name__); ROOT=Path(__file__).resolve().parent; DB=ROOT/'fsociety.db'; _INSTALLED=False; _ORIGINAL_BOT_INIT=None; PAGE_SIZE=8
MODERATION_ACTIONS={'kick','ban','unban','member_update','member_role_update','message_delete','message_bulk_delete'}
def _connect():return sqlite3.connect(str(DB))
def _ensure_tables():
    with _connect() as con:con.executescript('''CREATE TABLE IF NOT EXISTS staff_message_activity(guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,day TEXT NOT NULL,messages INTEGER NOT NULL DEFAULT 0,last_message INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(guild_id,user_id,day));CREATE TABLE IF NOT EXISTS staff_moderation_activity(guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,day TEXT NOT NULL,actions INTEGER NOT NULL DEFAULT 0,last_action INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(guild_id,user_id,day));''')
def _day(ts=None):return datetime.fromtimestamp(ts or time.time(),timezone.utc).strftime('%Y-%m-%d')
def _monitored_member(center,member):
    if member is None or getattr(member,'bot',False):return False
    roles={int(r) for r in center.settings(member.guild.id).get('staff_activity_roles',[])};return bool(roles and any(role.id in roles for role in member.roles))
def _record_message(gid,uid,ts):
    with _connect() as con:con.execute('INSERT INTO staff_message_activity(guild_id,user_id,day,messages,last_message) VALUES(?,?,?,?,?) ON CONFLICT(guild_id,user_id,day) DO UPDATE SET messages=messages+1,last_message=excluded.last_message',(gid,uid,_day(ts),1,ts))
def _record_action(gid,uid,ts):
    with _connect() as con:con.execute('INSERT INTO staff_moderation_activity(guild_id,user_id,day,actions,last_action) VALUES(?,?,?,?,?) ON CONFLICT(guild_id,user_id,day) DO UPDATE SET actions=actions+1,last_action=excluded.last_action',(gid,uid,_day(ts),1,ts))
def _stats(gid,uid):
    cutoff=datetime.fromtimestamp(time.time()-6*86400,timezone.utc).strftime('%Y-%m-%d')
    with _connect() as con:
        msg=con.execute('SELECT COALESCE(SUM(messages),0),COALESCE(MAX(last_message),0) FROM staff_message_activity WHERE guild_id=? AND user_id=? AND day>=?',(gid,uid,cutoff)).fetchone();mod=con.execute('SELECT COALESCE(SUM(actions),0),COALESCE(MAX(last_action),0) FROM staff_moderation_activity WHERE guild_id=? AND user_id=? AND day>=?',(gid,uid,cutoff)).fetchone()
    return int(msg[0] or 0),int(mod[0] or 0),max(int(msg[1] or 0),int(mod[1] or 0))
def _members_for_roles(guild,role_ids):
    members={}
    for rid in {int(x) for x in role_ids}:
        role=guild.get_role(rid)
        if role:
            for member in role.members:
                if not member.bot:members[member.id]=member
    return sorted(members.values(),key=lambda m:(-m.top_role.position,m.display_name.casefold()))
def _staff_embed(center,guild,page=0):
    s=center.settings(guild.id); role_ids=[int(x) for x in s.get('staff_activity_roles',[])]; inactive_days=int(s.get('staff_inactive_days') or 7); members=_members_for_roles(guild,role_ids); pages=max(1,math.ceil(len(members)/PAGE_SIZE));page=max(0,min(int(page),pages-1));snap=[];active=inactive=unknown=0
    for m in members:
        msgs,actions,last=_stats(guild.id,m.id); status='Ativo' if last and time.time()-last<=inactive_days*86400 else 'Inativo' if last else 'Sem dados';active+=status=='Ativo';inactive+=status=='Inativo';unknown+=status=='Sem dados';snap.append((m,msgs,actions,last,status))
    e=center.embed(guild,'F SOCIETY • STAFF ACTIVITY','Atividade da equipe nos últimos 7 dias.',banner=False);e.add_field(name='Equipe',value=f'**{len(members)}** membro(s) • **{len(role_ids)}** cargo(s) • limite **{inactive_days}d**',inline=False);e.add_field(name='Ativos',value=f'🟢 {active}');e.add_field(name='Inativos',value=f'🔴 {inactive}');e.add_field(name='Sem dados',value=f'⏳ {unknown}')
    for m,msgs,actions,last,status in snap[page*PAGE_SIZE:(page+1)*PAGE_SIZE]:e.add_field(name=f'{"🟢" if status=="Ativo" else "🔴" if status=="Inativo" else "⏳"} {m.display_name}',value=f'**{status}** • mensagens: **{msgs}** • moderação: **{actions}**\nÚltima atividade: {f"<t:{last}:R>" if last else "não registrada"}',inline=False)
    e.set_footer(text=f'Página {page+1}/{pages}');return e
class StaffActivityView(cp.OwnedView):
    def __init__(self,center,owner,guild,page=0):
        super().__init__(center,owner,True);self.page=page;self.guild_id=guild.id;s=center.settings(guild.id);defaults=[guild.get_role(int(r)) for r in s.get('staff_activity_roles',[])];defaults=[r for r in defaults if r][:10];roles=discord.ui.RoleSelect(placeholder='Selecionar cargos da staff',min_values=0,max_values=10,default_values=defaults,row=0)
        async def save(i):center.save(i.guild.id,{'staff_activity_roles':[r.id for r in roles.values if not r.is_default()]});await i.response.edit_message(embed=_staff_embed(center,i.guild,0),view=StaffActivityView(center,owner,i.guild,0))
        roles.callback=save;self.add_item(roles)
        async def back(i):await cp.ConfigRouter.show(i,'home',owner)
        self.button('Voltar',back,row=4)
def install():
    global _INSTALLED,_ORIGINAL_BOT_INIT
    if _INSTALLED:return
    _INSTALLED=True;_ensure_tables();cp.DEFAULTS.setdefault('staff_activity_roles',[]);cp.DEFAULTS.setdefault('staff_inactive_days',7);cp.SECTIONS.setdefault('staff_activity',('Atividade da Staff','♱','Cargos, mensagens e moderação'))
    if hasattr(cp,'ConfigRouter'):
        router=cp.ConfigRouter;old=router.build
        @staticmethod
        def build(center,guild,owner,section,admin=True):
            if section=='staff_activity':return _staff_embed(center,guild,0),StaffActivityView(center,owner,guild,0)
            return old(center,guild,owner,section,admin)
        router.build=build
    _ORIGINAL_BOT_INIT=commands.Bot.__init__
    def bot_init(self,*a,**kw):
        _ORIGINAL_BOT_INIT(self,*a,**kw)
        async def on_msg(message):
            if message.guild and isinstance(message.author,discord.Member) and _monitored_member(getattr(self,'center',None),message.author):_record_message(message.guild.id,message.author.id,int(time.time()))
        async def on_audit(entry):
            center=getattr(self,'center',None);guild=getattr(entry,'guild',None);actor=getattr(entry,'user',None);name=getattr(getattr(entry,'action',None),'name','')
            if center and guild and actor and name in MODERATION_ACTIONS:
                member=guild.get_member(actor.id)
                if member and _monitored_member(center,member):_record_action(guild.id,actor.id,int(time.time()))
        self.add_listener(on_msg,'on_message');self.add_listener(on_audit,'on_audit_log_entry_create')
    commands.Bot.__init__=bot_init;print('[OK] Staff Activity • métricas carregadas')
