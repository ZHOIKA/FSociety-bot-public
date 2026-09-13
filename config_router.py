"""Roteador oficial e estável do /configurar.

Centraliza navegação da central administrativa e normalização dos componentes
Discord usados pelas seções do painel.
"""
import logging
import re

import discord
import control_panel as cp

LOG = logging.getLogger(__name__)
_INSTALLED = False
ARROW = '<a:gif_seta:1546253994161340486>'
BITCOIN = '<:bitcoin:1536197990362746961>'
_CUSTOM_RE = re.compile(r'^<(?P<a>a?):(?P<name>[^:]+):(?P<id>\d+)>$')


def _arrow(guild):
    if not guild:
        return None
    m = _CUSTOM_RE.match(ARROW)
    if not m:
        return None
    wanted_id = int(m.group('id'))
    found = guild.get_emoji(wanted_id)
    if found:
        return found
    wanted_name = m.group('name').casefold()
    return next((e for e in guild.emojis if e.name.casefold() == wanted_name), None)


def _is_nav(item):
    if not isinstance(item, discord.ui.Select):
        return False
    values = {str(getattr(o, 'value', '')) for o in getattr(item, 'options', [])}
    return len(values & set(map(str, cp.SECTIONS))) >= 2


def _normalize(view, guild, owner_id):
    arrow = _arrow(guild)
    for item in list(getattr(view, 'children', [])):
        if isinstance(item, discord.ui.Button):
            item.emoji = None
            label = (item.label or '').strip().casefold()
            cid = (getattr(item, 'custom_id', None) or '').casefold()
            if label.startswith('voltar') or cid == 'fsociety:config:back':
                async def back(i, _owner=owner_id):
                    await Router.show(i, 'home', _owner)
                item.callback = back
        elif isinstance(item, discord.ui.Select):
            if _is_nav(item):
                for option in item.options:
                    option.emoji = arrow
                async def navigate(i, _item=item, _owner=owner_id):
                    await Router.show(i, _item.values[0], _owner)
                item.callback = navigate
            else:
                for option in getattr(item, 'options', []):
                    if getattr(getattr(option, 'emoji', None), 'id', None):
                        option.emoji = None
    return view


def _normalize_section_emojis():
    """Evita opções inválidas no Discord e incorpora a antiga correção avançada."""
    if 'advanced_economy' in cp.SECTIONS:
        label, _emoji, description = cp.SECTIONS['advanced_economy']
        cp.SECTIONS['advanced_economy'] = (label, BITCOIN, description)
    for key, (label, emoji, description) in list(cp.SECTIONS.items()):
        if emoji is None:
            continue
        try:
            discord.PartialEmoji.from_str(str(emoji))
        except Exception:
            cp.SECTIONS[key] = (label, None, description)


class HomeView(cp.OwnedView):
    def __init__(self, center, owner, guild, admin=True):
        super().__init__(center, owner, admin)
        options = []
        arrow = _arrow(guild)
        for key, (label, _emoji, desc) in list(cp.SECTIONS.items())[:25]:
            options.append(discord.SelectOption(
                label=str(label)[:100], value=str(key)[:100],
                description=str(desc)[:100] if desc else None, emoji=arrow,
                default=key == 'home'))
        nav = discord.ui.Select(
            placeholder='Escolha uma área para configurar', options=options,
            min_values=1, max_values=1, row=0)
        async def navigate(i):
            await Router.show(i, nav.values[0], owner)
        nav.callback = navigate
        self.add_item(nav)


class EmojiConfigView(discord.ui.View):
    def __init__(self, guild, owner_id):
        super().__init__(timeout=180)
        self.guild = guild
        self.owner_id = owner_id
        import emoji_commands
        base = emoji_commands.EmojiPanelView(guild)
        for item in list(base.children):
            base.remove_item(item)
            self.add_item(item)
        back = discord.ui.Button(label='Voltar', style=discord.ButtonStyle.secondary, row=1)
        async def go_back(i):
            await Router.show(i, 'home', owner_id)
        back.callback = go_back
        self.add_item(back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('Somente quem abriu o /configurar pode usar este painel.', ephemeral=True)
            return False
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message('Você precisa de **Gerenciar Servidor** para usar este painel.', ephemeral=True)
            return False
        return True


def _agenda_dashboard(guild, owner_id, page=0):
    import agenda_ui
    embed, _, page = agenda_ui._dashboard_embed(guild, page)
    view = agenda_ui.AgendaDashboardView(guild, owner_id, page)
    back = discord.ui.Button(
        label='Menu geral', emoji='↩️', style=discord.ButtonStyle.secondary,
        row=2, custom_id='fsociety:agenda:config_back')
    async def go_back(i):
        await Router.show(i, 'home', owner_id)
    back.callback = go_back
    view.add_item(back)
    return embed, view


class Router:
    @staticmethod
    def build(center, guild, owner_id, section, admin=True):
        if section == 'home':
            return center.page(guild, 'home', admin), HomeView(center, owner_id, guild, admin)
        if section == 'tools':
            import unified_config
            view = unified_config.UnifiedToolConfigView(center.bot, guild.id, owner_id)
            return unified_config._tools_embed(center.bot, guild), _normalize(view, guild, owner_id)
        if section == 'emojis':
            embed = discord.Embed(
                title='F SOCIETY • Gerenciador de Emojis',
                description='Crie, copie ou apague emojis personalizados do servidor.',
                color=0x7C3AED)
            embed.add_field(name='Criar', value='Transforma uma imagem enviada em emoji **128×128**.', inline=False)
            embed.add_field(name='Copiar', value='Importa um emoji existente de outro servidor.', inline=False)
            embed.add_field(name='Apagar', value='Seleciona e remove um emoji deste servidor.', inline=False)
            embed.set_footer(text=f'{len(guild.emojis)} emoji(s) personalizado(s) no servidor')
            return embed, EmojiConfigView(guild, owner_id)
        if section == 'agenda':
            return _agenda_dashboard(guild, owner_id)
        import advanced_config
        if section in advanced_config.SECTIONS:
            view = advanced_config.AdvancedView(center, owner_id, section)
            return advanced_config.page(center, guild, section), _normalize(view, guild, owner_id)
        if section not in cp.SECTIONS:
            return center.page(guild, 'home', admin), HomeView(center, owner_id, guild, admin)
        view = cp.Panel(center, owner_id, section, admin)
        return center.page(guild, section, admin), _normalize(view, guild, owner_id)

    @staticmethod
    async def show(i, section='home', owner_id=None, *, new_message=False, admin=True):
        if not i.guild:
            return await cp.reply(i, 'Abra o painel dentro de um servidor.')
        owner_id = owner_id or i.user.id
        if admin and not i.user.guild_permissions.manage_guild:
            return await cp.reply(i, 'Você precisa de **Gerenciar Servidor** para configurar o bot.')
        center = i.client.center
        try:
            embed, view = Router.build(center, i.guild, owner_id, section, admin)
            if new_message:
                return await i.response.send_message(embed=embed, view=view, ephemeral=False)
            return await i.response.edit_message(embed=embed, view=view)
        except Exception as exc:
            LOG.exception('Falha no roteador do /configurar | seção=%s', section)
            if not i.response.is_done():
                return await i.response.send_message(
                    f'Não consegui abrir **{section}**. O erro foi salvo no log.', ephemeral=True)
            raise exc


async def _back_home(interaction, owner_id):
    return await Router.show(interaction, 'home', owner_id)


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    cp.SECTIONS.setdefault('emojis', ('Emojis', '😀', 'Criar, copiar e apagar emojis do servidor'))
    cp.SECTIONS.setdefault('agenda', ('Agenda', '📅', 'Criar e gerenciar mensagens automáticas'))
    _normalize_section_emojis()

    original_panel_init = cp.Panel.__init__
    def panel_init(self, center, owner, section='home', admin=True):
        original_panel_init(self, center, owner, section, admin)
        self.section = section
        self.owner = owner
        self.admin = admin
    cp.Panel.__init__ = panel_init

    async def refresh(self, i):
        await Router.show(i, self.section, self.owner, admin=self.admin)
    cp.Panel.refresh = refresh

    original_pickers_init = cp.Pickers.__init__
    def pickers_init(self, center, owner, kind, g=None):
        original_pickers_init(self, center, owner, kind, g)
        section = {'ticket_channels':'tickets','ticket_roles':'tickets','level':'economy'}.get(kind, kind)
        for item in self.children:
            if isinstance(item, discord.ui.Button) and (item.label or '').strip().casefold().startswith('voltar'):
                item.emoji = None
                async def back(i, _section=section, _owner=owner):
                    await Router.show(i, _section, _owner)
                item.callback = back
    cp.Pickers.__init__ = pickers_init

    async def open_router(self, i, section='home', admin=True):
        await Router.show(i, section, i.user.id, new_message=True, admin=admin)
    cp.Center.open = open_router
    cp.ConfigRouter = Router
