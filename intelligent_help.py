"""Ajuda inteligente e contextual do F SOCIETY.

Exibe apenas slash commands que o membro pode executar e organiza o catálogo
por Música, Comunidade, Economia, Utilidades e Staff.
"""
from collections import OrderedDict

import discord
from discord import app_commands

import control_panel as cp

_INSTALLED = False
PAGE_SIZE = 12

CATEGORY_META = OrderedDict([
    ('music', ('Música', 'Player, fila e controles de voz')),
    ('community', ('Comunidade', 'Perfil, ranking, tickets e recursos sociais')),
    ('economy', ('Economia', 'Saldo, recompensas e transferências')),
    ('utilities', ('Utilidades', 'Ferramentas, consultas e recursos gerais')),
    ('staff', ('Staff', 'Moderação e administração do servidor')),
])
MUSIC_ROOTS={'play','pause','resume','skip','stop','queue','nowplaying','volume','loop','shuffle','disconnect'}
ECONOMY_ROOTS={'saldo','daily','pagar','trabalhar','topmoedas'}
COMMUNITY_ROOTS={'perfil','ranking','painel','afk','afk_status','sugerir','serverstats','roleinfo','canalinfo','ticket'}
STAFF_ROOTS={'configurar','config','diagnostico','ban','kick','timeout','untimeout','avisar','limpar','webhook','agenda'}
STAFF_ACTIONS={'configurar','publicar','ativar','pausar','excluir','criar','setup','settings','config'}


def _walk(command,prefix=''):
    full=f'{prefix} {command.name}'.strip()
    if isinstance(command,app_commands.Group):
        for child in command.commands:yield from _walk(child,full)
    else:yield command,full


def _permission_names(command):
    names=set(); current=command
    while current is not None:
        permissions=getattr(current,'default_permissions',None)
        if permissions:
            for name,enabled in permissions:
                if enabled and name!='use_application_commands':names.add(name)
        current=getattr(current,'parent',None)
    return names


def _member_has_defaults(member,command):return all(bool(getattr(member.guild_permissions,name,False)) for name in _permission_names(command))


async def _can_run(interaction,command):
    if not _member_has_defaults(interaction.user,command):return False
    checker=getattr(command,'_check_can_run',None)
    if checker is not None:
        try:
            if not await checker(interaction):return False
        except app_commands.CheckFailure:return False
        except (discord.Forbidden,discord.NotFound):return False
        except Exception:pass
    return True


def _category(command,full_name):
    root=full_name.split()[0]; parts=full_name.split(); action=parts[-1] if len(parts)>1 else root; privileged=bool(_permission_names(command))
    if root in STAFF_ROOTS or privileged:return 'staff'
    if len(parts)>1 and action in STAFF_ACTIONS and root in {'ticket','cve','ferramentas','livro','livros'}:return 'staff'
    if root in MUSIC_ROOTS:return 'music'
    if root in ECONOMY_ROOTS:return 'economy'
    if root in COMMUNITY_ROOTS:return 'community'
    return 'utilities'


def _short(text,limit):
    value=' '.join(str(text or '').split()); return value if len(value)<=limit else value[:limit-1].rstrip()+'…'


async def _catalog(interaction):
    groups={key:[] for key in CATEGORY_META}; roots=sorted(interaction.client.tree.get_commands(),key=lambda c:c.name)
    for root in roots:
        if root.name=='ajuda':continue
        for command,full_name in _walk(root):
            if not await _can_run(interaction,command):continue
            groups[_category(command,full_name)].append({'name':full_name,'description':_short(getattr(command,'description','') or 'Sem descrição.',120)})
    for items in groups.values():items.sort(key=lambda item:item['name'])
    return {key:value for key,value in groups.items() if value}


def _overview_embed(interaction,catalog):
    total=sum(len(items) for items in catalog.values()); embed=discord.Embed(title='F SOCIETY // AJUDA',description=f'{interaction.user.mention}, este menu mostra **somente os comandos disponíveis para você** neste servidor.\n\nEscolha uma categoria abaixo para ver os comandos e suas funções.',color=0xB91C1C)
    for key,(label,description) in CATEGORY_META.items():
        items=catalog.get(key)
        if items:embed.add_field(name=f'{label} — {len(items)}',value=description,inline=False)
    embed.set_footer(text=f'{total} comando(s) disponível(is) para sua conta • catálogo dinâmico'); return embed


def _category_embed(interaction,catalog,category,page):
    label,description=CATEGORY_META[category]; items=catalog[category]; pages=max(1,(len(items)+PAGE_SIZE-1)//PAGE_SIZE); page=max(0,min(page,pages-1)); start=page*PAGE_SIZE; chunk=items[start:start+PAGE_SIZE]; lines=[f'**`/{item["name"]}`**\n└ {_short(item["description"],115)}' for item in chunk]; embed=discord.Embed(title=f'F SOCIETY // {label.upper()}',description=f'{description}\n\n'+'\n\n'.join(lines),color=0xB91C1C); embed.set_footer(text=f'Página {page+1}/{pages} • {len(items)} comando(s) nesta categoria • filtrado para {interaction.user.display_name}'); return embed


class HelpCategorySelect(discord.ui.Select):
    def __init__(self,catalog,selected=None):
        options=[discord.SelectOption(label='Visão geral',value='overview',description='Voltar para todas as categorias',default=selected is None)]
        for key,(label,description) in CATEGORY_META.items():
            items=catalog.get(key)
            if items:options.append(discord.SelectOption(label=label,value=key,description=_short(f'{len(items)} comandos • {description}',100),default=selected==key))
        super().__init__(placeholder='Escolha uma categoria',min_values=1,max_values=1,options=options,row=0)
    async def callback(self,interaction):
        view=self.view
        if not isinstance(view,SmartHelpView):return
        selected=self.values[0]; view.category=None if selected=='overview' else selected; view.page=0; view.rebuild(); await interaction.response.edit_message(embed=view.embed(interaction),view=view)


class SmartHelpView(discord.ui.View):
    def __init__(self,owner_id,catalog):super().__init__(timeout=300); self.owner_id=owner_id; self.catalog=catalog; self.category=None; self.page=0; self.rebuild()
    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id:await interaction.response.send_message('Este menu de ajuda foi gerado para outro membro.',ephemeral=True); return False
        return True
    def rebuild(self):
        self.clear_items(); self.add_item(HelpCategorySelect(self.catalog,self.category))
        if self.category:
            items=self.catalog[self.category]; pages=max(1,(len(items)+PAGE_SIZE-1)//PAGE_SIZE); previous=discord.ui.Button(label='Anterior',style=discord.ButtonStyle.secondary,row=1,disabled=self.page<=0); next_button=discord.ui.Button(label='Próxima',style=discord.ButtonStyle.secondary,row=1,disabled=self.page>=pages-1)
            async def go_previous(interaction):self.page=max(0,self.page-1); self.rebuild(); await interaction.response.edit_message(embed=self.embed(interaction),view=self)
            async def go_next(interaction):self.page=min(pages-1,self.page+1); self.rebuild(); await interaction.response.edit_message(embed=self.embed(interaction),view=self)
            previous.callback=go_previous; next_button.callback=go_next; self.add_item(previous); self.add_item(next_button)
    def embed(self,interaction):return _overview_embed(interaction,self.catalog) if self.category is None else _category_embed(interaction,self.catalog,self.category,self.page)


async def open_help(interaction):
    await interaction.response.defer(ephemeral=True); catalog=await _catalog(interaction); view=SmartHelpView(interaction.user.id,catalog); await interaction.edit_original_response(embed=view.embed(interaction),view=view)


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; original_open=cp.Center.open
    async def open_router(self,interaction,section='home',admin=True,*args,**kwargs):
        if section=='help' and admin is False:return await open_help(interaction)
        return await original_open(self,interaction,section,admin,*args,**kwargs)
    cp.Center.open=open_router; print('Ajuda: catálogo inteligente por permissões e categorias carregado')
