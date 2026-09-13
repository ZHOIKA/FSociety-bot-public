"""Configurações avançadas integradas ao /configurar do F SOCIETY."""
import json, sqlite3
from contextlib import closing
import discord
import control_panel as cp

_INSTALLED=False
SECTIONS={
 'automation':('Automação','⏱️','Mensagens programadas e rotinas do servidor'),
 'suggestions':('Sugestões','💡','Canal, votação e destino das sugestões'),
 'advanced_logs':('Logs avançados','📑','Escolha exatamente quais eventos registrar'),
 'system_channels':('Canais do sistema','🗂️','Centralize os canais usados pelos recursos'),
 'permissions':('Permissões','🔐','Cargos autorizados por grupo de comandos'),
 'levels':('Níveis avançados','📈','Multiplicadores, exclusões e recompensas de nível'),
 'advanced_economy':('Economia avançada','₿','Cooldowns, streaks, transferências e canais'),
 'advanced_tickets':('Tickets avançados','🎟️','Departamentos, prioridade, SLA e avaliação'),
}
DEFAULTS={
 'suggestion_channel':None,'suggestion_approved_channel':None,'suggestion_rejected_channel':None,'suggestion_votes':True,'suggestion_threads':True,
 'log_join':True,'log_leave':True,'log_delete':True,'log_edit':True,'log_voice':True,'log_moderation':True,'log_tickets':True,'log_roles':True,'log_channels':True,
 'announcement_channel':None,'permission_economy_roles':[],'permission_books_roles':[],'permission_tools_roles':[],'permission_tickets_roles':[],'permission_utilities_roles':[],
 'xp_multiplier':1.0,'xp_ignored_channels':[],'level_roles':{},'level_message':'{user} alcançou o nível **{level}**!',
 'daily_cooldown':86400,'work_cooldown':3600,'work_reward_min':50,'work_reward_max':150,'daily_streak_enabled':True,'transfer_limit':100000,'economy_channels':[],
 'ticket_departments':['Suporte'],'ticket_priority_enabled':True,'ticket_sla_minutes':60,'ticket_rating_enabled':True,'ticket_rating_message':'Como você avalia este atendimento?'
}

def _db(center):return center.db_path()
def _migrate(center):
    with closing(sqlite3.connect(_db(center))) as con,con:con.execute('CREATE TABLE IF NOT EXISTS advanced_settings(guild_id INTEGER,key TEXT,value TEXT,PRIMARY KEY(guild_id,key))')
def get(center,gid,key):
    _migrate(center)
    with closing(sqlite3.connect(_db(center))) as con:row=con.execute('SELECT value FROM advanced_settings WHERE guild_id=? AND key=?',(gid,key)).fetchone()
    return json.loads(row[0]) if row else DEFAULTS[key]
def setv(center,gid,key,value):
    _migrate(center)
    with closing(sqlite3.connect(_db(center))) as con,con:con.execute('INSERT INTO advanced_settings(guild_id,key,value) VALUES(?,?,?) ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value',(gid,key,json.dumps(value,ensure_ascii=False)))
def ch(value):return f'<#{value}>' if value else 'Não configurado'
def yes(v):return 'Ativado' if v else 'Desativado'

def page(center,g,section):
    e=center.embed(g,SECTIONS[section][0],SECTIONS[section][2],banner=False); v=lambda k:get(center,g.id,k)
    if section=='automation':
        try:rows=center.q('SELECT id,channel_id,hour,minute,active FROM scheduled_messages WHERE guild_id=? ORDER BY id DESC LIMIT 8',(g.id,))
        except Exception:rows=[]
        e.add_field(name='Mensagens programadas',value='\n'.join(f"#{r['id']} • <#{r['channel_id']}> • {r['hour']:02d}:{r['minute']:02d} • {'ativa' if r['active'] else 'pausada'}" for r in rows) or 'Nenhuma automação criada.',inline=False)
    elif section=='suggestions':
        e.add_field(name='Canal de sugestões',value=ch(v('suggestion_channel'))); e.add_field(name='Votação',value=yes(v('suggestion_votes'))); e.add_field(name='Tópico automático',value=yes(v('suggestion_threads'))); e.add_field(name='Aprovadas',value=ch(v('suggestion_approved_channel'))); e.add_field(name='Rejeitadas',value=ch(v('suggestion_rejected_channel')))
    elif section=='advanced_logs':
        labels={'log_join':'Entrada','log_leave':'Saída','log_delete':'Mensagem apagada','log_edit':'Mensagem editada','log_voice':'Voz','log_moderation':'Punições','log_tickets':'Tickets','log_roles':'Cargos','log_channels':'Canais'}; e.description='\n'.join(f'**{label}:** {yes(v(key))}' for key,label in labels.items())
    elif section=='system_channels':
        base=center.settings(g.id); items=[('Anúncios',v('announcement_channel')),('Sugestões',v('suggestion_channel')),('Logs',base.get('log_channel')),('Níveis',base.get('level_channel')),('CVEs',base.get('cve_channel')),('Boas-vindas',base.get('welcome_channel'))]; e.description='\n'.join(f'**{name}:** {ch(value)}' for name,value in items)
    elif section=='permissions':
        for key,label in [('permission_economy_roles','Economia'),('permission_books_roles','Livros'),('permission_tools_roles','Ferramentas'),('permission_tickets_roles','Tickets'),('permission_utilities_roles','Utilidades')]:e.add_field(name=label,value=', '.join(f'<@&{x}>' for x in v(key)) or 'Permissões padrão do Discord')
    elif section=='levels':
        e.add_field(name='Multiplicador global',value=f"{v('xp_multiplier')}x"); e.add_field(name='Canais sem XP',value=', '.join(f'<#{x}>' for x in v('xp_ignored_channels')) or 'Nenhum'); e.add_field(name='Cargos por nível',value=str(len(v('level_roles')))); e.add_field(name='Mensagem de level up',value=v('level_message')[:1000],inline=False)
    elif section=='advanced_economy':
        e.add_field(name='Daily',value=f"Cooldown: {v('daily_cooldown')}s\nStreak: {yes(v('daily_streak_enabled'))}"); e.add_field(name='Trabalho',value=f"{v('work_reward_min')}–{v('work_reward_max')} moedas\nCooldown: {v('work_cooldown')}s"); e.add_field(name='Transferências',value=f"Limite: {v('transfer_limit')} moedas"); e.add_field(name='Canais permitidos',value=', '.join(f'<#{x}>' for x in v('economy_channels')) or 'Todos',inline=False)
    elif section=='advanced_tickets':
        e.add_field(name='Departamentos',value=', '.join(v('ticket_departments')) or 'Suporte'); e.add_field(name='Prioridade',value=yes(v('ticket_priority_enabled'))); e.add_field(name='SLA',value=f"{v('ticket_sla_minutes')} minutos"); e.add_field(name='Avaliação ao fechar',value=yes(v('ticket_rating_enabled'))); e.add_field(name='Mensagem da avaliação',value=v('ticket_rating_message')[:1000],inline=False)
    return e

class AdvancedView(cp.OwnedView):
    def __init__(self,center,owner,section):
        super().__init__(center,owner,True); self.section=section
        async def back(i):
            if hasattr(cp,'ConfigRouter'):return await cp.ConfigRouter.show(i,'home',owner)
            await i.response.edit_message(embed=center.page(i.guild,'home',True),view=cp.Panel(center,owner,'home',True))
        self.button('Voltar',back,row=4)
        if section=='suggestions':
            for row,(key,label) in enumerate((('suggestion_channel','Canal de sugestões'),('suggestion_approved_channel','Canal de aprovadas'),('suggestion_rejected_channel','Canal de rejeitadas'))):
                item=discord.ui.ChannelSelect(placeholder=label,channel_types=[discord.ChannelType.text],min_values=0,max_values=1,row=row)
                async def save(i,item=item,key=key):setv(center,i.guild.id,key,item.values[0].id if item.values else None); await i.response.edit_message(embed=page(center,i.guild,section),view=AdvancedView(center,owner,section))
                item.callback=save; self.add_item(item)
        elif section=='permissions':
            for row,(key,label) in enumerate((('permission_economy_roles','Cargos: economia'),('permission_books_roles','Cargos: livros'),('permission_tools_roles','Cargos: ferramentas'),('permission_tickets_roles','Cargos: tickets'))):
                item=discord.ui.RoleSelect(placeholder=label,min_values=0,max_values=10,row=row)
                async def save(i,item=item,key=key):setv(center,i.guild.id,key,[r.id for r in item.values]); await i.response.edit_message(embed=page(center,i.guild,section),view=AdvancedView(center,owner,section))
                item.callback=save; self.add_item(item)
        elif section=='advanced_logs':
            keys=['log_join','log_leave','log_delete','log_edit','log_voice','log_moderation','log_tickets','log_roles','log_channels']; opts=discord.ui.Select(placeholder='Eventos registrados',min_values=0,max_values=len(keys),options=[discord.SelectOption(label=k.replace('log_','').replace('_',' ').title(),value=k,default=bool(get(center,0,k)) if False else False) for k in keys],row=0)
            async def save_logs(i):
                chosen=set(opts.values)
                for k in keys:setv(center,i.guild.id,k,k in chosen)
                await i.response.edit_message(embed=page(center,i.guild,section),view=AdvancedView(center,owner,section))
            opts.callback=save_logs; self.add_item(opts)

def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; cp.SECTIONS.update(SECTIONS); print('[OK] Configuração avançada • seções carregadas')
