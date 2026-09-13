"""Perfil avançado e interativo do F SOCIETY."""
import math

import discord
from discord import app_commands

_INSTALLED = False


def _fmt(n):
    try:
        return f'{int(n):,}'.replace(',', '.')
    except Exception:
        return str(n)


def _ensure_user(center, guild_id, user_id):
    center.q('INSERT OR IGNORE INTO users(guild_id,user_id) VALUES(?,?)',(guild_id,user_id))
    return center.q('SELECT level,xp,coins,messages,warnings FROM users WHERE guild_id=? AND user_id=?',(guild_id,user_id),True)


def _position(center, guild_id, user_id, column):
    row = center.q(
        f'SELECT 1 + COUNT(*) AS pos FROM users AS u '
        f'WHERE u.guild_id=? AND ('
        f'u.{column} > COALESCE((SELECT t.{column} FROM users AS t WHERE t.guild_id=? AND t.user_id=?),0) '
        f'OR (u.{column} = COALESCE((SELECT t.{column} FROM users AS t WHERE t.guild_id=? AND t.user_id=?),0) '
        f'AND u.user_id < ?))',
        (guild_id,guild_id,user_id,guild_id,user_id,user_id),True)
    return int(row['pos']) if row else 1


def _xp_progress(level,xp):
    level=max(0,int(level or 0)); xp=max(0,int(xp or 0))
    current=100*(level**2); target=100*((level+1)**2); span=max(1,target-current)
    gained=max(0,min(xp-current,span)); pct=max(0.0,min(1.0,gained/span)); filled=max(0,min(12,round(pct*12)))
    return current,target,int(pct*100),'━'*filled+'─'*(12-filled)


def _member_roles(member,limit=6):
    roles=[r for r in reversed(member.roles) if not r.is_default()]
    if not roles:return 'Nenhum cargo adicional'
    shown=roles[:limit]; text=' '.join(r.mention for r in shown)
    if len(roles)>limit:text+=f' +{len(roles)-limit}'
    return text


def _color(member):
    if getattr(member,'color',None) and member.color.value:return member.color
    return discord.Color.from_rgb(22,22,27)


def _base_embed(member,title='F SOCIETY • PERFIL'):
    e=discord.Embed(title=title,color=_color(member)); e.set_thumbnail(url=member.display_avatar.replace(size=512).url)
    e.set_author(name=str(member),icon_url=member.display_avatar.replace(size=128).url); e.set_footer(text=f'F SOCIETY • ID {member.id}')
    return e


def profile_embed(center,guild,member):
    u=_ensure_user(center,guild.id,member.id); level=int(u['level'] or 0); xp=int(u['xp'] or 0); coins=int(u['coins'] or 0); messages=int(u['messages'] or 0); warnings=int(u['warnings'] or 0)
    _,target,pct,bar=_xp_progress(level,xp); xp_pos=_position(center,guild.id,member.id,'xp'); coin_pos=_position(center,guild.id,member.id,'coins'); msg_pos=_position(center,guild.id,member.id,'messages')
    e=_base_embed(member); e.description=f'{member.mention}\n**{discord.utils.escape_markdown(member.display_name)}**'
    e.add_field(name='PROGRESSO',value=f'**Nível {level}**\n`{bar}` **{pct}%**\n`{_fmt(xp)} / {_fmt(target)} XP`',inline=False)
    e.add_field(name='ECONOMIA',value=f'**{_fmt(coins)}** moedas\nPosição: **#{coin_pos}**',inline=True)
    e.add_field(name='ATIVIDADE',value=f'**{_fmt(messages)}** mensagens\nPosição: **#{msg_pos}**',inline=True)
    e.add_field(name='REPUTAÇÃO',value=f'**{warnings}** aviso(s)\nXP: **#{xp_pos}**',inline=True)
    e.add_field(name='CARGOS',value=_member_roles(member),inline=False)
    if member.joined_at:e.add_field(name='NO SERVIDOR DESDE',value=f'<t:{int(member.joined_at.timestamp())}:D>\n<t:{int(member.joined_at.timestamp())}:R>',inline=True)
    e.add_field(name='CONTA CRIADA',value=f'<t:{int(member.created_at.timestamp())}:D>\n<t:{int(member.created_at.timestamp())}:R>',inline=True)
    return e


def ranking_embed(center,guild,member):
    u=_ensure_user(center,guild.id,member.id); values=[('XP','xp',int(u['xp'] or 0),'XP'),('Moedas','coins',int(u['coins'] or 0),'moedas'),('Mensagens','messages',int(u['messages'] or 0),'mensagens')]
    e=_base_embed(member,'F SOCIETY • POSIÇÕES'); e.description=f'Posições atuais de {member.mention} dentro de **{discord.utils.escape_markdown(guild.name)}**.'
    for label,column,value,unit in values:e.add_field(name=label.upper(),value=f'**#{_position(center,guild.id,member.id,column)}**\n`{_fmt(value)} {unit}`',inline=True)
    rows=center.q('SELECT user_id,xp FROM users WHERE guild_id=? ORDER BY xp DESC,user_id ASC LIMIT 5',(guild.id,)); top='\n'.join(f'**{n}.** <@{r["user_id"]}> — `{_fmt(r["xp"])} XP`' for n,r in enumerate(rows,1))
    e.add_field(name='TOP 5 • XP',value=top or 'Ainda não há dados suficientes.',inline=False); return e


def server_embed(center,guild,member):
    u=_ensure_user(center,guild.id,member.id); e=_base_embed(member,'F SOCIETY • MEMBRO'); e.description=f'Informações de {member.mention} dentro do servidor.'
    e.add_field(name='CARGO PRINCIPAL',value=member.top_role.mention if not member.top_role.is_default() else 'Nenhum cargo adicional',inline=False)
    e.add_field(name='CARGOS',value=_member_roles(member,10),inline=False); e.add_field(name='AVISOS',value=f'**{int(u["warnings"] or 0)}**',inline=True); e.add_field(name='MENSAGENS',value=f'**{_fmt(u["messages"])}**',inline=True); e.add_field(name='NÍVEL',value=f'**{int(u["level"] or 0)}**',inline=True)
    if member.joined_at:e.add_field(name='ENTROU NO SERVIDOR',value=f'<t:{int(member.joined_at.timestamp())}:F>\n<t:{int(member.joined_at.timestamp())}:R>',inline=False)
    e.add_field(name='CONTA CRIADA',value=f'<t:{int(member.created_at.timestamp())}:F>\n<t:{int(member.created_at.timestamp())}:R>',inline=False); return e


class ProfileView(discord.ui.View):
    def __init__(self,center,guild,target,owner_id):
        super().__init__(timeout=300); self.center=center; self.guild=guild; self.target=target; self.owner_id=owner_id; self.page='profile'; self._sync()
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id:
            await interaction.response.send_message('Abra seu próprio perfil com `/perfil`.',ephemeral=True); return False
        return True
    def _sync(self):
        self.overview.disabled=self.page=='profile'; self.rankings.disabled=self.page=='ranking'; self.member_info.disabled=self.page=='server'
    async def _show(self,interaction,page):
        self.page=page; self._sync(); embed=ranking_embed(self.center,self.guild,self.target) if page=='ranking' else server_embed(self.center,self.guild,self.target) if page=='server' else profile_embed(self.center,self.guild,self.target)
        await interaction.response.edit_message(embed=embed,view=self)
    @discord.ui.button(label='Perfil',style=discord.ButtonStyle.secondary,row=0)
    async def overview(self,interaction,button):await self._show(interaction,'profile')
    @discord.ui.button(label='Posições',style=discord.ButtonStyle.secondary,row=0)
    async def rankings(self,interaction,button):await self._show(interaction,'ranking')
    @discord.ui.button(label='Servidor',style=discord.ButtonStyle.secondary,row=0)
    async def member_info(self,interaction,button):await self._show(interaction,'server')
    @discord.ui.button(label='Atualizar',style=discord.ButtonStyle.primary,row=0)
    async def refresh(self,interaction,button):await self._show(interaction,self.page)


async def _slash_profile(interaction:discord.Interaction,usuario:discord.Member=None):
    if not interaction.guild:return await interaction.response.send_message('Use `/perfil` dentro de um servidor.',ephemeral=True)
    member=usuario or interaction.user; center=interaction.client.center
    await interaction.response.send_message(embed=profile_embed(center,interaction.guild,member),view=ProfileView(center,interaction.guild,member,interaction.user.id))


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; original_tree_command=app_commands.CommandTree.command
    def tree_command(self,*args,**kwargs):
        decorator=original_tree_command(self,*args,**kwargs)
        def wrapped(func):
            command=decorator(func)
            if command.name=='perfil':command._callback=_slash_profile; command.description='Perfil completo com XP, economia, atividade e posições'
            return command
        return wrapped
    app_commands.CommandTree.command=tree_command
