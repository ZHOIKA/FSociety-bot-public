"""Centraliza configurações administrativas no /configurar."""
import discord
from discord import app_commands

import control_panel as cp
import tool_config_panel

_INSTALLED = False


class UnifiedToolConfigView(tool_config_panel.ToolConfigView):
    def __init__(self, client, guild_id, owner_id):
        self.owner_id = owner_id
        super().__init__(client, guild_id)
        self._add_back_button()

    def rebuild(self, client, guild_id):
        super().rebuild(client, guild_id)
        self._add_back_button()

    def _add_back_button(self):
        if any(getattr(item, 'custom_id', None) == 'fsociety:config:back' for item in self.children):
            return
        button = discord.ui.Button(
            label='Voltar para /configurar',
            style=discord.ButtonStyle.secondary,
            custom_id='fsociety:config:back',
            row=2,
        )
        button.callback = self._back
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                'Este painel pertence a quem abriu o /configurar.',
                ephemeral=True,
            )
            return False
        return await super().interaction_check(interaction)

    async def _back(self, interaction: discord.Interaction):
        try:
            import config_router
            return await config_router._back_home(interaction, self.owner_id)
        except Exception:
            embed = interaction.client.center.page(interaction.guild, 'home', True)
            view = discord.ui.View(timeout=600)
            select = discord.ui.Select(
                placeholder='Abra /configurar novamente para continuar',
                options=[discord.SelectOption(label='Visão geral', value='home')],
                disabled=True,
            )
            view.add_item(select)
            return await interaction.response.edit_message(embed=embed, view=view)


def _tools_embed(client, guild):
    embed = tool_config_panel._config_embed(client, guild)
    embed.title = 'F SOCIETY // FERRAMENTAS E LIVROS'
    embed.description = (
        'Configurações administrativas reunidas dentro do `/configurar`.\n\n'
        + (embed.description or '')
    )
    return embed


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    cp.SECTIONS['tools'] = (
        'Ferramentas e livros',
        None,
        'Alertas de ferramentas, busca de livros, canais e filtros padrão',
    )

    original_panel_init = cp.Panel.__init__

    def panel_init(self, center, owner, section='home', admin=True):
        original_panel_init(self, center, owner, section, admin)
        nav = next((item for item in self.children if isinstance(item, discord.ui.Select)), None)
        if nav is None:
            return
        original_nav_callback = nav.callback

        async def navigate(interaction: discord.Interaction):
            selected = nav.values[0]
            if selected == 'tools' and admin:
                view = UnifiedToolConfigView(
                    interaction.client,
                    interaction.guild.id,
                    owner,
                )
                return await interaction.response.edit_message(
                    embed=_tools_embed(interaction.client, interaction.guild),
                    view=view,
                )
            await original_nav_callback(interaction)

        nav.callback = navigate

    cp.Panel.__init__ = panel_init

    original_tree_init = cp.CommandTree.__init__

    def tree_init(self, *args, **kwargs):
        original_tree_init(self, *args, **kwargs)
        group = self.get_command('ferramentas')
        if group is None:
            return
        try:
            group.remove_command('configurar')
        except Exception:
            pass

        @app_commands.command(
            name='configurar',
            description='Abre as configurações de ferramentas e livros do /configurar',
        )
        @app_commands.default_permissions(manage_guild=True)
        async def configurar(interaction: discord.Interaction):
            view = UnifiedToolConfigView(
                interaction.client,
                interaction.guild.id,
                interaction.user.id,
            )
            await interaction.response.send_message(
                embed=_tools_embed(interaction.client, interaction.guild),
                view=view,
                ephemeral=False,
            )

        group.add_command(configurar)

    cp.CommandTree.__init__ = tree_init
