"""Painel central de configuração do sistema de ferramentas.

Reúne canal de alertas, ativação do monitor e filtro padrão de estrelas em
/ferramentas configurar. As preferências ficam persistidas no SQLite já usado
pelo bot.
"""
import sqlite3
from contextlib import closing

import discord
from discord import app_commands

import control_panel as cp
import tool_alerts
import tool_search_dynamic

_INSTALLED = False

STAR_OPTIONS = (
    (0, 'Todas as estrelas', 'Sem mínimo de estrelas'),
    (10, '10+ estrelas', 'Pelo menos 10 estrelas'),
    (100, '100+ estrelas', 'Pelo menos 100 estrelas'),
    (1000, '1.000+ estrelas', 'Pelo menos 1 mil estrelas'),
    (10000, '10.000+ estrelas', 'Pelo menos 10 mil estrelas'),
    (50000, '50.000+ estrelas', 'Pelo menos 50 mil estrelas'),
)


def _db(client):
    return tool_alerts._db(client)


def _migrate(client):
    with closing(sqlite3.connect(_db(client))) as con, con:
        con.execute('''
            CREATE TABLE IF NOT EXISTS tool_search_preferences(
                guild_id INTEGER PRIMARY KEY,
                min_stars INTEGER NOT NULL DEFAULT 0
            )
        ''')


def get_min_stars(client, guild_id):
    _migrate(client)
    with closing(sqlite3.connect(_db(client))) as con:
        row = con.execute('SELECT min_stars FROM tool_search_preferences WHERE guild_id=?',(guild_id,)).fetchone()
    return int(row[0]) if row else 0


def set_min_stars(client, guild_id, value):
    value = max(0, int(value or 0)); _migrate(client)
    with closing(sqlite3.connect(_db(client))) as con, con:
        con.execute('INSERT INTO tool_search_preferences(guild_id,min_stars) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET min_stars=excluded.min_stars',(guild_id,value))


def _star_label(value):
    value=int(value or 0); return 'Todas' if value <= 0 else f'{value:,}+'.replace(',', '.')


def _config_embed(client, guild):
    settings=tool_alerts._settings(client,guild.id); min_stars=get_min_stars(client,guild.id)
    channel=f'<#{settings["channel_id"]}>' if settings.get('channel_id') else '`Não configurado`'; monitor='`ATIVO`' if settings.get('enabled') else '`DESATIVADO`'; base='`PRONTA`' if settings.get('initialized_at') else '`PENDENTE`'
    embed=discord.Embed(title='F SOCIETY // CONFIGURAÇÃO DE FERRAMENTAS',description=('Central de configuração da busca e dos alertas de ferramentas.\n\n'+f'**Canal de alertas:** {channel}\n'+f'**Monitor automático:** {monitor}\n'+f'**Filtro padrão de estrelas:** `{_star_label(min_stars)}`\n'+f'**Base de releases:** {base}\n'+f'**Intervalo do monitor:** `{tool_alerts._POLL_SECONDS // 60} min`\n\n'+'Use os controles abaixo para alterar tudo sem sair desta tela.'),color=0x991B1B)
    embed.set_footer(text='F SOCIETY • Configurações administrativas'); return embed


async def _resolve_text_channel(interaction, selected):
    channel_id=int(getattr(selected,'id',0) or 0)
    if not channel_id:return None
    channel=interaction.guild.get_channel(channel_id)
    if channel is None:
        try:channel=await interaction.guild.fetch_channel(channel_id)
        except (discord.NotFound,discord.Forbidden,discord.HTTPException):return None
    return channel if isinstance(channel,discord.TextChannel) else None


class AlertChannelSelect(discord.ui.ChannelSelect):
    def __init__(self,owner):
        self.owner=owner; super().__init__(placeholder='Selecionar canal de alertas',channel_types=[discord.ChannelType.text],min_values=1,max_values=1,custom_id='fsociety:tools:config:channel',row=0)
    async def callback(self,interaction):
        channel=await _resolve_text_channel(interaction,self.values[0])
        if channel is None:return await interaction.response.send_message('Não consegui acessar esse canal. Escolha um canal de texto válido.',ephemeral=True)
        perms=channel.permissions_for(interaction.guild.me); required=('view_channel','send_messages','embed_links','create_public_threads','send_messages_in_threads'); missing=[name for name in required if not getattr(perms,name,False)]
        if missing:return await interaction.response.send_message('Faltam permissões no canal: '+', '.join(missing),ephemeral=True)
        tool_alerts._save_settings(interaction.client,interaction.guild.id,channel_id=channel.id)
        await interaction.response.edit_message(embed=_config_embed(interaction.client,interaction.guild),view=self.owner)


class DefaultStarsSelect(discord.ui.Select):
    def __init__(self,owner,current):
        self.owner=owner; super().__init__(placeholder='Filtro padrão de estrelas da pesquisa',min_values=1,max_values=1,options=[discord.SelectOption(label=label,description=description,value=str(value),default=value==current) for value,label,description in STAR_OPTIONS],custom_id='fsociety:tools:config:stars',row=1)
    async def callback(self,interaction):
        value=int(self.values[0]); set_min_stars(interaction.client,interaction.guild.id,value); self.owner.rebuild(interaction.client,interaction.guild.id); await interaction.response.edit_message(embed=_config_embed(interaction.client,interaction.guild),view=self.owner)


class ToolConfigView(discord.ui.View):
    def __init__(self,client,guild_id):
        super().__init__(timeout=600); self.rebuild(client,guild_id)
    def rebuild(self,client,guild_id):
        self.clear_items(); self.add_item(AlertChannelSelect(self)); self.add_item(DefaultStarsSelect(self,get_min_stars(client,guild_id))); settings=tool_alerts._settings(client,guild_id)
        toggle=discord.ui.Button(label='Desativar monitor' if settings.get('enabled') else 'Ativar monitor',style=discord.ButtonStyle.danger if settings.get('enabled') else discord.ButtonStyle.success,custom_id='fsociety:tools:config:toggle',row=2)
        reset=discord.ui.Button(label='Refazer base de releases',style=discord.ButtonStyle.secondary,custom_id='fsociety:tools:config:baseline',row=2); toggle.callback=self._toggle; reset.callback=self._reset; self.add_item(toggle); self.add_item(reset)
    async def interaction_check(self,interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message('Você precisa da permissão Gerenciar Servidor.',ephemeral=True); return False
        return True
    async def _toggle(self,interaction):
        settings=tool_alerts._settings(interaction.client,interaction.guild.id)
        if not settings.get('enabled') and not settings.get('channel_id'):return await interaction.response.send_message('Escolha primeiro o canal de alertas.',ephemeral=True)
        new_state=not bool(settings.get('enabled')); tool_alerts._save_settings(interaction.client,interaction.guild.id,enabled=new_state,initialized_at=None if new_state else 'keep'); self.rebuild(interaction.client,interaction.guild.id); await interaction.response.edit_message(embed=_config_embed(interaction.client,interaction.guild),view=self)
    async def _reset(self,interaction):
        settings=tool_alerts._settings(interaction.client,interaction.guild.id)
        if not settings.get('channel_id'):return await interaction.response.send_message('Configure primeiro o canal de alertas.',ephemeral=True)
        tool_alerts._save_settings(interaction.client,interaction.guild.id,initialized_at=None); self.rebuild(interaction.client,interaction.guild.id); await interaction.response.edit_message(embed=_config_embed(interaction.client,interaction.guild),view=self)


async def _search_with_default(interaction,query):
    min_stars=get_min_stars(interaction.client,interaction.guild.id) if interaction.guild else 0; await interaction.response.defer(thinking=True); data=await tool_search_dynamic._fetch_page(query,1,min_stars); items=(data or {}).get('items') or []
    if not items:return await interaction.followup.send('Nenhuma ferramenta encontrada para essa pesquisa.')
    total_count=int((data or {}).get('total_count') or len(items)); view=tool_search_dynamic.DynamicToolSearchView(query=query,items=items,total_count=total_count,min_stars=min_stars); message=await interaction.followup.send(embed=view.embed(),view=view,allowed_mentions=discord.AllowedMentions.none(),wait=True); view.message=message


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; tool_alerts._search_tools=_search_with_default; original_tree_init=cp.CommandTree.__init__
    def tree_init(self,*args,**kwargs):
        original_tree_init(self,*args,**kwargs); group=self.get_command('ferramentas')
        if group is None:return
        for name in ('configurar','ativar','desativar'):
            try:group.remove_command(name)
            except Exception:pass
        @app_commands.command(name='configurar',description='Abre todas as configurações do sistema de ferramentas')
        @app_commands.default_permissions(manage_guild=True)
        async def configurar(interaction:discord.Interaction):
            view=ToolConfigView(interaction.client,interaction.guild.id); await interaction.response.send_message(embed=_config_embed(interaction.client,interaction.guild),view=view,ephemeral=False)
        group.add_command(configurar)
    cp.CommandTree.__init__=tree_init
