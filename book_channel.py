"""Integração do seletor de canal da busca de livros."""
import discord
import book_system

_INSTALLED = False


async def _book_channel_callback(self, interaction: discord.Interaction):
    selected = self.values[0]
    channel_id = int(selected.id)
    channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
    if channel is None and interaction.guild:
        try:
            channel = await interaction.guild.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None
    if channel is None:
        return await interaction.response.send_message(
            'Não consegui acessar esse canal. Verifique se ele ainda existe e se o bot consegue vê-lo.',
            ephemeral=True,
        )
    me = interaction.guild.me
    if me is None and interaction.client.user:
        me = interaction.guild.get_member(interaction.client.user.id)
    if me is None:
        return await interaction.response.send_message(
            'Não consegui verificar as permissões do bot nesse canal.',
            ephemeral=True,
        )
    perms = channel.permissions_for(me)
    if not perms.view_channel or not perms.send_messages or not perms.embed_links:
        return await interaction.response.send_message(
            'O bot precisa de Ver canal, Enviar mensagens e Inserir links nesse canal.',
            ephemeral=True,
        )
    book_system.set_book_channel(interaction.client, interaction.guild.id, channel_id)
    self.owner.rebuild(interaction.client, interaction.guild.id)
    await interaction.response.edit_message(
        embed=book_system.tool_config_panel._config_embed(interaction.client, interaction.guild),
        view=self.owner,
    )


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    book_system.BookChannelSelect.callback = _book_channel_callback
