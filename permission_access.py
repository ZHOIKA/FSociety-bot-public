"""Permissões por cargos com interface simples e aplicação real nos comandos."""
import discord

import control_panel as cp
import advanced_config as adv

_INSTALLED = False

CATEGORIES = {
    'economy': {'label':'Economia','key':'permission_economy_roles','description':'Saldo, daily, pagamentos e trabalho.','examples':'/saldo  /daily  /pagar  /trabalhar','roots':{'saldo','daily','pagar','trabalhar','topmoedas'}},
    'books': {'label':'Livros','key':'permission_books_roles','description':'Pesquisa e recursos do sistema de livros.','examples':'/livro','roots':{'livro','livros'}},
    'tools': {'label':'Ferramentas','key':'permission_tools_roles','description':'Pesquisa e recursos de ferramentas.','examples':'/ferramentas','roots':{'ferramentas'}},
    'tickets': {'label':'Tickets','key':'permission_tickets_roles','description':'Ações do sistema de atendimento por tickets.','examples':'/ticket','roots':{'ticket'}},
    'utilities': {'label':'Utilidades','key':'permission_utilities_roles','description':'Música, perfil, informações e utilidades públicas.','examples':'/play  /perfil  /ranking  /serverstats','roots':{'play','pause','resume','skip','stop','queue','nowplaying','volume','loop','shuffle','disconnect','afk','afk_status','avatar','botinfo','canalinfo','cronometro','enquete','lembrete','painel','perfil','ping','ranking','roleinfo','servericon','serverstats','servidor','sugerir','userinfo','userinfo_cargos'}},
}


def _category_for_command(command):
    if command is None:return None
    while getattr(command,'parent',None) is not None:command=command.parent
    name=getattr(command,'name',None)
    for category,data in CATEGORIES.items():
        if name in data['roots']:return category
    return None


def _roles_text(guild,role_ids):
    valid=[]
    for role_id in role_ids:
        role=guild.get_role(int(role_id))
        if role is not None:valid.append(role.mention)
    return ', '.join(valid) if valid else 'Todos os membros'


def permissions_page(center,guild):
    e=center.embed(guild,'Permissões por cargos','Escolha uma categoria e defina quem pode usar seus comandos.\n**Sem cargos selecionados = acesso livre**, respeitando as permissões normais do Discord.',banner=False)
    for data in CATEGORIES.values():
        roles=adv.get(center,guild.id,data['key']); state=_roles_text(guild,roles); e.add_field(name=data['label'],value=f'**Acesso:** {state}\n`{data["examples"]}`',inline=False)
    e.set_footer(text='Administradores sempre mantêm acesso. Selecione uma categoria abaixo para alterar.'); return e


def category_page(center,guild,category):
    data=CATEGORIES[category]; roles=adv.get(center,guild.id,data['key']); e=center.embed(guild,f'Permissões • {data["label"]}',data['description'],banner=False)
    e.add_field(name='Acesso atual',value=_roles_text(guild,roles),inline=False); e.add_field(name='Comandos deste grupo',value=f'`{data["examples"]}`',inline=False)
    e.add_field(name='Como funciona',value='Somente membros com **pelo menos um** dos cargos selecionados podem usar este grupo. Administradores continuam liberados.' if roles else 'Este grupo está **liberado para todos** que já podem usar os comandos normalmente.',inline=False); return e


class CategorySelect(discord.ui.Select):
    def __init__(self,parent):
        self.parent_view=parent; super().__init__(placeholder='Escolha o grupo de comandos…',options=[discord.SelectOption(label=data['label'],value=key,description=data['description'][:100]) for key,data in CATEGORIES.items()],row=0)
    async def callback(self,interaction):
        category=self.values[0]; await interaction.response.edit_message(embed=category_page(self.parent_view.center,interaction.guild,category),view=PermissionCategoryView(self.parent_view.center,self.parent_view.owner,category))


class CategoryRoles(discord.ui.RoleSelect):
    def __init__(self,parent,category):
        self.parent_view=parent; self.category=category; super().__init__(placeholder='Selecionar cargos autorizados…',min_values=1,max_values=10,row=0)
    async def callback(self,interaction):
        data=CATEGORIES[self.category]; role_ids=[role.id for role in self.values if role.id!=interaction.guild.default_role.id]; adv.setv(self.parent_view.center,interaction.guild.id,data['key'],role_ids); await interaction.response.edit_message(embed=category_page(self.parent_view.center,interaction.guild,self.category),view=PermissionCategoryView(self.parent_view.center,self.parent_view.owner,self.category))


class PermissionHomeView(cp.OwnedView):
    def __init__(self,center,owner):
        super().__init__(center,owner,True); self.add_item(CategorySelect(self))
        async def back(interaction):await interaction.response.edit_message(embed=center.page(interaction.guild,section='home',admin=True),view=cp.Panel(center,owner,'home',True))
        self.button('Voltar',back,row=4)


class PermissionCategoryView(cp.OwnedView):
    def __init__(self,center,owner,category):
        super().__init__(center,owner,True); self.category=category; self.add_item(CategoryRoles(self,category))
        async def allow_all(interaction):
            data=CATEGORIES[category]; adv.setv(center,interaction.guild.id,data['key'],[]); await interaction.response.edit_message(embed=category_page(center,interaction.guild,category),view=PermissionCategoryView(center,owner,category))
        async def back(interaction):await interaction.response.edit_message(embed=permissions_page(center,interaction.guild),view=PermissionHomeView(center,owner))
        self.button('Liberar para todos',allow_all,row=1); self.button('Voltar',back,row=4)


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; original_page=adv.page; original_view=adv.AdvancedView; original_check=cp.CommandTree.interaction_check
    def routed_page(center,guild,section):return permissions_page(center,guild) if section=='permissions' else original_page(center,guild,section)
    def routed_view(center,owner,section):return PermissionHomeView(center,owner) if section=='permissions' else original_view(center,owner,section)
    async def interaction_check(self,interaction):
        if not await original_check(self,interaction):return False
        if interaction.guild is None or interaction.user.guild_permissions.administrator:return True
        category=_category_for_command(interaction.command)
        if category is None:return True
        center=getattr(interaction.client,'center',None)
        if center is None:return True
        data=CATEGORIES[category]; allowed_roles=adv.get(center,interaction.guild.id,data['key'])
        if not allowed_roles:return True
        member_roles={role.id for role in getattr(interaction.user,'roles',[])}
        if member_roles.intersection(int(role_id) for role_id in allowed_roles):return True
        await cp.reply(interaction,f'Você não tem um cargo autorizado para usar **{data["label"]}** neste servidor.'); return False
    adv.page=routed_page; adv.AdvancedView=routed_view; cp.CommandTree.interaction_check=interaction_check; print('Permissões: menu simplificado e controle real por cargos carregados')
    try:
        from staff_activity import install as install_staff_activity
        install_staff_activity()
    except Exception as exc:print(f'[STAFF] Falha ao carregar monitor de atividade: {type(exc).__name__}: {exc}')
