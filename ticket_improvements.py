"""Melhorias de fechamento e controles de tickets do F SOCIETY."""
import asyncio
import io
import time

import discord

import tickets
from control_panel import reply

_INSTALLED = False


def _authorize_close(service, interaction, row):
    """Antes de assumir: autor ou equipe. Depois de assumir: atendente responsável ou admin."""
    claimed_by = row['claimed_by']
    if claimed_by:
        if interaction.user.id == claimed_by or interaction.user.guild_permissions.manage_guild:
            return
        if interaction.user.id == row['user_id']:
            raise ValueError(
                'Este atendimento já foi assumido. A partir de agora, somente o atendente responsável pode fechar o ticket.'
            )
        raise ValueError('Somente o atendente responsável por este ticket pode fechá-lo.')
    service.authorize(interaction, row)


async def _send_transcript_to_log(service, interaction, row):
    """Salva uma cópia do histórico no canal de logs antes de excluir o ticket."""
    settings = service.center.settings(interaction.guild.id)
    log_channel = interaction.guild.get_channel(settings.get('ticket_log_channel')) if settings.get('ticket_log_channel') else None
    if not isinstance(log_channel, discord.TextChannel):
        return False

    messages = [m async for m in interaction.channel.history(limit=1001)]
    truncated = len(messages) > 1000
    lines = [
        f'F SOCIETY | Ticket #{row["id"]}',
        f'Canal excluído: #{interaction.channel.name} ({interaction.channel.id})',
        f'Autor: {row["user_id"]}',
        f'Atendente: {row["claimed_by"] or "não assumido"}',
        f'Fechado por: {interaction.user} ({interaction.user.id})',
        'Últimas 1000 mensagens.' if truncated else 'Histórico completo disponível no momento do fechamento.',
        '',
    ]
    for message in reversed(messages[:1000]):
        text = message.content or '[mensagem sem texto]'
        lines.append(f'[{message.created_at.isoformat()}] {message.author} ({message.author.id}): {text}')
        for embed in message.embeds:
            lines.append(f'[embed] {embed.title or ""} | {embed.description or ""}')
            lines.extend(f'{field.name}: {field.value}' for field in embed.fields)
        lines.extend(f'[anexo] {attachment.url}' for attachment in message.attachments)

    payload = '\n'.join(lines).encode('utf-8')
    limit = min(interaction.guild.filesize_limit, 7_500_000)
    if len(payload) > limit:
        return False

    embed = service.center.embed(
        interaction.guild,
        'Ticket encerrado',
        f'Ticket **#{row["id"]}** de <@{row["user_id"]}> foi fechado por {interaction.user.mention}.\n'
        f'O canal `#{interaction.channel.name}` será excluído.',
        banner=False,
    )
    await log_channel.send(
        embed=embed,
        file=discord.File(io.BytesIO(payload), filename=f'ticket-{row["id"]}-historico.txt'),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return True


async def _request_close(self, interaction):
    row = self.lookup(interaction)
    _authorize_close(self, interaction, row)
    if row['status'] != 'open':
        raise ValueError('Este ticket já está fechado.')
    view = tickets.CloseConfirmation(self, interaction.user.id, interaction.channel.id)
    if row['claimed_by']:
        text = 'Encerrar este atendimento? Após confirmar, o histórico será salvo nos logs quando configurado e o canal será excluído.'
    else:
        text = 'Fechar este ticket? Como ele ainda não foi assumido, o autor ou a equipe podem encerrá-lo. O canal será excluído após a confirmação.'
    await reply(interaction, text, view=view)


async def _close_and_delete(self, interaction):
    await interaction.response.defer(ephemeral=True)
    async with self.lock(interaction.guild.id):
        row = self.lookup(interaction)
        _authorize_close(self, interaction, row)
        if row['status'] != 'open':
            raise ValueError('Este ticket já está fechado.')

        transcript_saved = False
        try:
            transcript_saved = await _send_transcript_to_log(self, interaction, row)
        except discord.HTTPException:
            transcript_saved = False

        channel = interaction.channel
        channel_name = channel.name
        channel_id = channel.id

        await reply(
            interaction,
            'Ticket encerrado. O canal será excluído em alguns segundos.'
            + (' O histórico foi enviado ao canal de logs.' if transcript_saved else ''),
        )

        self.q("UPDATE tickets SET status='deleted',closed_at=? WHERE id=?", (int(time.time()), row['id']))
        await asyncio.sleep(2)
        try:
            await channel.delete(reason=f'Ticket fechado por {interaction.user.id}')
        except discord.HTTPException:
            self.q("UPDATE tickets SET status='closed',closed_at=? WHERE id=?", (int(time.time()), row['id']))
            raise ValueError(
                'O ticket foi fechado, mas não consegui excluir o canal. '
                'Confira se o bot possui a permissão **Gerenciar Canais**.'
            )

        if not transcript_saved:
            settings = self.center.settings(interaction.guild.id)
            log_channel = interaction.guild.get_channel(settings.get('ticket_log_channel')) if settings.get('ticket_log_channel') else None
            if isinstance(log_channel, discord.TextChannel):
                try:
                    await log_channel.send(
                        f'Ticket **#{row["id"]}** encerrado e canal `#{channel_name}` (`{channel_id}`) excluído por {interaction.user.mention}.',
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    pass


async def _claim(self, interaction):
    row = self.lookup(interaction)
    self.authorize(interaction, row, staff=True)
    if row['status'] != 'open':
        raise ValueError('Reabra o ticket antes de assumir o atendimento.')
    if row['claimed_by']:
        if row['claimed_by'] == interaction.user.id:
            raise ValueError('Você já é o atendente responsável por este ticket.')
        raise ValueError(f'Este atendimento já foi assumido por <@{row["claimed_by"]}>.')

    changed = self.q(
        'UPDATE tickets SET claimed_by=? WHERE id=? AND claimed_by IS NULL RETURNING id',
        (interaction.user.id, row['id']),
    )
    if not changed:
        latest = self.lookup(interaction)
        raise ValueError(f'Este atendimento já foi assumido por <@{latest["claimed_by"]}>.')

    await reply(interaction, 'Você assumiu este atendimento. A partir de agora, somente você (ou um administrador) pode fechar o ticket.')
    await interaction.channel.send(
        f'Atendimento assumido por {interaction.user.mention}.\n'
        f'<@{row["user_id"]}>, o fechamento agora fica sob responsabilidade do atendente.',
        view=tickets.TicketControls(self),
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    await self.log(interaction.guild, 'Atendimento assumido', f'{interaction.user.mention} assumiu {interaction.channel.mention}.')


async def _confirm_delete(self, interaction, button):
    if interaction.channel.id != self.channel_id:
        raise ValueError('Confirmação inválida para este canal.')
    self.stop()
    await self.service.close(interaction)


class TicketActionSelect(discord.ui.Select):
    def __init__(self, service):
        self.service = service
        super().__init__(
            placeholder='Menu do atendimento',
            min_values=1,
            max_values=1,
            custom_id='fsociety:ticket:actions:v1',
            options=[
                discord.SelectOption(label='Assumir atendimento', value='claim', description='Equipe assume a responsabilidade pelo ticket'),
                discord.SelectOption(label='Fechar ticket', value='close', description='Encerra e exclui o canal após confirmação'),
                discord.SelectOption(label='Exportar histórico', value='transcript', description='Baixa o histórico do atendimento em TXT'),
                discord.SelectOption(label='Reabrir ticket', value='reopen', description='Disponível para a equipe quando aplicável'),
            ],
        )

    async def callback(self, interaction):
        action = self.values[0]
        if action == 'claim':
            await self.service.claim(interaction)
        elif action == 'close':
            await self.service.request_close(interaction)
        elif action == 'transcript':
            await self.service.transcript(interaction)
        elif action == 'reopen':
            await self.service.reopen(interaction)


def _controls_init(self, service):
    discord.ui.View.__init__(self, timeout=None)
    self.clear_items()
    self.service = service
    self.add_item(TicketActionSelect(service))


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    tickets.TicketService.request_close = _request_close
    tickets.TicketService.close = _close_and_delete
    tickets.TicketService.claim = _claim
    tickets.CloseConfirmation.confirm = _confirm_delete
    tickets.TicketControls.__init__ = _controls_init
