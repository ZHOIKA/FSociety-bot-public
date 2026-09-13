"""Tickets privados com controles persistentes e configuração por servidor."""
import asyncio
import io
import json
import logging
import re
import time
import weakref

import discord
from discord import app_commands

from control_panel import SafeView, OwnedView, handle_error, reply

LOG = logging.getLogger(__name__)
STYLES = {'verde': discord.ButtonStyle.success, 'azul': discord.ButtonStyle.primary,
          'cinza': discord.ButtonStyle.secondary, 'vermelho': discord.ButtonStyle.danger}


def channel_name(template, member):
    username = re.sub(r'[^a-z0-9_-]', '-', member.display_name.lower()).strip('-') or str(member.id)
    name = template.replace('{username}', username).replace('{id}', str(member.id))
    return re.sub(r'[^a-z0-9_-]', '-', name)[:90] or f'ticket-{member.id}'


class TicketService:
    def __init__(self, center):
        self.center = center
        self.bot, self.q = center.bot, center.q
        self.locks = weakref.WeakValueDictionary()

    def lock(self, gid):
        lock = self.locks.get(gid)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[gid] = lock
        return lock

    def register_views(self):
        self.bot.add_view(OpenTicketView(self))
        self.bot.add_view(TicketControls(self))

    def panel_embed(self, g):
        s = self.center.settings(g.id)
        e = self.center.embed(g, s['ticket_title'], s['ticket_description'])
        if s['ticket_gif']:
            e.set_image(url=s['ticket_gif'])
        e.add_field(name='🎫 Como funciona', value='Clique no botão → informe o assunto → converse com a equipe.', inline=False)
        e.add_field(name='🔒 Atendimento privado', value='O canal fica disponível para você, a equipe e administradores.', inline=False)
        return e

    def configuration(self, g):
        s = self.center.settings(g.id)
        category = g.get_channel(s['ticket_category']) if s['ticket_category'] else None
        roles = [g.get_role(r) for r in s['ticket_roles']]
        if not isinstance(category, discord.CategoryChannel):
            raise ValueError('Configure uma categoria válida em /ticket configurar → Canais e categoria.')
        if not roles or any(r is None or r.is_default() or r.managed for r in roles):
            raise ValueError('Configure cargos válidos para a equipe em /ticket configurar → Equipe e menções.')
        perms = category.permissions_for(g.me)
        required = ('view_channel', 'manage_channels', 'manage_roles', 'send_messages',
                    'embed_links', 'read_message_history', 'attach_files')
        missing = [p for p in required if not getattr(perms, p)]
        if missing:
            raise ValueError('Faltam permissões na categoria de tickets: ' + ', '.join(missing))
        if not perms.mention_everyone and any(r.id in s['ticket_ping_roles'] and not r.mentionable for r in roles):
            raise ValueError('Para mencionar a equipe, torne os cargos escolhidos mencionáveis ou permita Mencionar @everyone ao bot na categoria. Nenhuma menção a @everyone será enviada.')
        return s, category, roles

    async def publish(self, i):
        if not i.user.guild_permissions.manage_guild:
            return await reply(i, 'Você precisa de **Gerenciar Servidor** para publicar o painel.')
        await i.response.defer(ephemeral=True)
        async with self.lock(i.guild.id):
            s, _, _ = self.configuration(i.guild)
            channel = i.guild.get_channel(s['ticket_panel_channel']) if s['ticket_panel_channel'] else None
            if not isinstance(channel, discord.TextChannel):
                raise ValueError('Selecione o canal do painel em Canais e categoria.')
            p = channel.permissions_for(i.guild.me)
            if not (p.view_channel and p.send_messages and p.embed_links and p.read_message_history):
                raise ValueError('Preciso de Ver Canal, Enviar Mensagens, Inserir Links e Ler Histórico no canal do painel.')
            message = None
            if s['ticket_message_id'] and s['ticket_published_channel'] == channel.id:
                try:
                    message = await channel.fetch_message(s['ticket_message_id'])
                except discord.NotFound:
                    pass
            view = OpenTicketView(self, s)
            if message:
                await message.edit(embed=self.panel_embed(i.guild), view=view)
            else:
                message = await channel.send(embed=self.panel_embed(i.guild), view=view,
                                             allowed_mentions=discord.AllowedMentions.none())
            self.center.save(i.guild.id, {'ticket_message_id': message.id, 'ticket_published_channel': channel.id})
            suffix = '' if s['ticket_enabled'] else '\nO atendimento está pausado. Use **Ativar / pausar** para liberar novas aberturas.'
            await reply(i, f'Painel publicado/atualizado: {message.jump_url}{suffix}')

    def lookup(self, i):
        if not i.guild:
            raise ValueError('Use esta ação em um ticket do servidor.')
        row = self.q('SELECT * FROM tickets WHERE guild_id=? AND channel_id=? ORDER BY id DESC LIMIT 1',
                     (i.guild.id, i.channel.id), True)
        if not row:
            raise ValueError('Este canal não é um ticket registrado.')
        return row

    def is_staff(self, member, row):
        return member.guild_permissions.manage_guild or bool(
            {role.id for role in member.roles} & set(json.loads(row['support_roles'])))

    def authorize(self, i, row, staff=False):
        if self.is_staff(i.user, row) or (not staff and i.user.id == row['user_id']):
            return
        raise ValueError('Esta ação está disponível apenas para a equipe.' if staff else
                         'Apenas o autor do ticket ou a equipe podem usar esta ação.')

    async def active_for(self, g, uid):
        rows = self.q("SELECT * FROM tickets WHERE guild_id=? AND user_id=? AND status='open'", (g.id, uid))
        active = []
        for row in rows:
            channel = g.get_channel(row['channel_id'])
            if channel is None:
                try:
                    channel = await g.fetch_channel(row['channel_id'])
                except discord.NotFound:
                    self.q("UPDATE tickets SET status='deleted',closed_at=? WHERE id=?", (int(time.time()), row['id']))
                    continue
            active.append(channel)
        return active

    async def open_ticket(self, i, reason):
        await i.response.defer(ephemeral=True, thinking=True)
        async with self.lock(i.guild.id):
            s, category, roles = self.configuration(i.guild)
            if not s['ticket_enabled']:
                raise ValueError('Novos atendimentos estão pausados. Tente novamente mais tarde.')
            active = await self.active_for(i.guild, i.user.id)
            if len(active) >= s['ticket_limit']:
                return await reply(i, 'Você já atingiu o limite de tickets abertos: ' + ', '.join(c.mention for c in active))
            if len(category.channels) >= 50:
                raise ValueError('A categoria de atendimento está cheia. Avise a equipe para configurar outra categoria.')
            readwrite = dict(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
            overwrites = {
                i.guild.default_role: discord.PermissionOverwrite(view_channel=False),
                i.guild.me: discord.PermissionOverwrite(**readwrite, manage_channels=True, manage_roles=True),
                i.user: discord.PermissionOverwrite(**readwrite),
            }
            overwrites.update({r: discord.PermissionOverwrite(**readwrite) for r in roles})
            channel = await i.guild.create_text_channel(channel_name(s['ticket_name'], i.user), category=category,
                                                        overwrites=overwrites,
                                                        topic=f'F SOCIETY • Atendimento de {i.user.id}',
                                                        reason=f'Ticket solicitado por {i.user.id}')
            try:
                self.q('INSERT INTO tickets(guild_id,user_id,channel_id,created_at,support_roles) VALUES(?,?,?,?,?)',
                       (i.guild.id, i.user.id, channel.id, int(time.time()), json.dumps([r.id for r in roles])))
            except Exception:
                await channel.delete(reason='Falha ao registrar ticket; reversão da criação incompleta')
                raise
            welcome = self.center.fmt(s['ticket_welcome'], i.user, i.guild).replace('{reason}', discord.utils.escape_mentions(reason))
            e = self.center.embed(i.guild, '🎫 Atendimento iniciado', welcome[:3500], banner=False)
            e.add_field(name='Assunto informado', value=discord.utils.escape_mentions(reason)[:1000], inline=False)
            e.add_field(name='Próximos passos', value='Envie os detalhes da sua solicitação. A equipe pode assumir o atendimento pelo botão abaixo.', inline=False)
            if s['ticket_gif']:
                e.set_image(url=s['ticket_gif'])
            ping_roles = [r for r in roles if r.id in s['ticket_ping_roles']]
            mentions = ([i.user.mention] if s['ticket_ping_author'] else []) + [r.mention for r in ping_roles]
            try:
                await channel.send(' '.join(mentions) or None, embed=e, view=TicketControls(self),
                                   allowed_mentions=discord.AllowedMentions(everyone=False, roles=ping_roles,
                                                                          users=[i.user] if s['ticket_ping_author'] else [], replied_user=False))
            except discord.HTTPException:
                await reply(i, f'Ticket criado: {channel.mention}. Não consegui enviar os controles; use `/ticket fechar` ou peça ajuda à equipe.')
                LOG.exception('Falha ao enviar controles do ticket %s', channel.id)
                return
            await reply(i, f'Seu atendimento está pronto: {channel.mention}')
            await self.log(i.guild, '🎫 Ticket aberto', f'Autor: {i.user.mention}\nCanal: {channel.mention}')

    async def log(self, g, title, description):
        s = self.center.settings(g.id)
        channel = g.get_channel(s['ticket_log_channel']) if s['ticket_log_channel'] else None
        if channel:
            try:
                await channel.send(embed=self.center.embed(g, title, description, banner=False),
                                   allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                LOG.exception('Falha ao registrar evento de ticket no servidor %s', g.id)

    async def claim(self, i):
        row = self.lookup(i)
        self.authorize(i, row, staff=True)
        if row['status'] != 'open':
            raise ValueError('Reabra o ticket antes de assumir o atendimento.')
        changed = self.q('UPDATE tickets SET claimed_by=? WHERE id=? AND claimed_by IS NULL RETURNING id',
                         (i.user.id, row['id']))
        if not changed:
            raise ValueError(f'Este atendimento já foi assumido por <@{row["claimed_by"]}>.')
        await reply(i, 'Atendimento atribuído a você.')
        await i.channel.send(f'👤 {i.user.mention} assumiu este atendimento.', allowed_mentions=discord.AllowedMentions.none())
        await self.log(i.guild, '👤 Atendimento assumido', f'{i.user.mention} assumiu {i.channel.mention}.')

    async def request_close(self, i):
        row = self.lookup(i)
        self.authorize(i, row)
        if row['status'] != 'open':
            raise ValueError('Este ticket já está fechado.')
        view = CloseConfirmation(self, i.user.id, i.channel.id)
        await reply(i, 'Fechar este atendimento? O histórico será preservado e o canal ficará somente para leitura. A equipe poderá reabri-lo.', view=view)

    async def close(self, i):
        await i.response.defer(ephemeral=True)
        async with self.lock(i.guild.id):
            row = self.lookup(i)
            self.authorize(i, row)
            if row['status'] != 'open':
                raise ValueError('Este ticket já foi fechado.')
            overwrites = dict(i.channel.overwrites)
            for target, overwrite in overwrites.items():
                if target.id != i.guild.me.id:
                    overwrite.send_messages = False
                    overwrite.add_reactions = False
                    overwrite.create_public_threads = False
                    overwrite.create_private_threads = False
                    overwrite.send_messages_in_threads = False
            await i.channel.edit(overwrites=overwrites, reason=f'Ticket fechado por {i.user.id}')
            self.q("UPDATE tickets SET status='closed',closed_at=? WHERE id=?", (int(time.time()), row['id']))
            await reply(i, 'Ticket fechado. O canal e seu histórico foram preservados.')
            await i.channel.send('🔒 Atendimento encerrado. A equipe pode usar `/ticket reabrir`. O histórico pode ser exportado pelo botão abaixo.',
                                 view=TicketControls(self))
            await self.log(i.guild, '🔒 Ticket fechado', f'{i.user.mention} fechou {i.channel.mention}.')

    async def reopen(self, i):
        await i.response.defer(ephemeral=True)
        async with self.lock(i.guild.id):
            row = self.lookup(i)
            self.authorize(i, row, staff=True)
            if row['status'] != 'closed':
                raise ValueError('Este ticket não está fechado.')
            s = self.center.settings(i.guild.id)
            if len(await self.active_for(i.guild, row['user_id'])) >= s['ticket_limit']:
                raise ValueError('O autor já atingiu o limite de tickets abertos.')
            overwrites = dict(i.channel.overwrites)
            targets = {row['user_id'], *json.loads(row['support_roles'])}
            for target, overwrite in overwrites.items():
                if target.id in targets:
                    overwrite.send_messages = True
                    overwrite.add_reactions = None
            await i.channel.edit(overwrites=overwrites, reason=f'Ticket reaberto por {i.user.id}')
            self.q("UPDATE tickets SET status='open',closed_at=NULL,claimed_by=NULL WHERE id=?", (row['id'],))
            await reply(i, 'Ticket reaberto.')
            await i.channel.send('🔓 Atendimento reaberto.', view=TicketControls(self))
            await self.log(i.guild, '🔓 Ticket reaberto', f'{i.user.mention} reabriu {i.channel.mention}.')

    async def transcript(self, i):
        row = self.lookup(i)
        self.authorize(i, row)
        await i.response.defer(ephemeral=True)
        messages = [m async for m in i.channel.history(limit=1001)]
        truncated = len(messages) > 1000
        lines = [f'F SOCIETY | Ticket #{row["id"]} | Canal {i.channel.id}',
                 'Últimas 1000 mensagens (limite atingido).' if truncated else 'Histórico de mensagens disponíveis.',
                 'Anexos aparecem como links; arquivos não são incorporados.\n']
        for m in reversed(messages[:1000]):
            text = m.content or '[mensagem sem texto]'
            lines.append(f'[{m.created_at.isoformat()}] {m.author} ({m.author.id}): {text}')
            for e in m.embeds:
                lines.append(f'[embed] {e.title or ""}\n{e.description or ""}')
                lines.extend(f'{f.name}: {f.value}' for f in e.fields)
            lines.extend(f'[anexo] {a.url}' for a in m.attachments)
        content = '\n'.join(lines).encode('utf-8')
        if len(content) > min(i.guild.filesize_limit, 7_500_000):
            raise ValueError('O histórico ultrapassou o tamanho permitido de arquivo. Consulte o canal preservado.')
        await i.followup.send(file=discord.File(io.BytesIO(content), filename=f'ticket-{row["id"]}.txt'), ephemeral=True)


class OpenTicketView(SafeView):
    def __init__(self, service, settings=None, preview=False):
        super().__init__(timeout=None)
        self.service, self.preview = service, preview
        if settings:
            self.open.label = settings['ticket_button']
            self.open.emoji = settings['ticket_emoji'] or None
            self.open.style = STYLES[settings['ticket_style']]

    @discord.ui.button(label='Abrir atendimento', emoji='🎫', style=discord.ButtonStyle.success, custom_id='fsociety:ticket:open:v1')
    async def open(self, i, button):
        if self.preview:
            return await reply(i, 'Esta é uma prévia. Publique o painel para receber atendimentos.')
        if not i.guild:
            raise ValueError('Abra um ticket no servidor.')
        s = self.service.center.settings(i.guild.id)
        if not s['ticket_enabled']:
            raise ValueError('O atendimento está pausado no momento.')
        if i.message.id != s['ticket_message_id'] or i.channel.id != s['ticket_published_channel']:
            raise ValueError('Este painel foi substituído. Procure o painel mais recente do servidor.')
        await i.response.send_modal(TicketReason(self.service, s['ticket_question']))


class TicketReason(discord.ui.Modal):
    def __init__(self, service, question):
        super().__init__(title='Novo atendimento', timeout=300)
        self.service = service
        self.reason = discord.ui.TextInput(style=discord.TextStyle.paragraph, max_length=1000, min_length=3)
        self.add_item(discord.ui.Label(text=question, component=self.reason))

    async def on_submit(self, i):
        await self.service.open_ticket(i, self.reason.value)

    async def on_error(self, i, error):
        await handle_error(i, error)


class TicketControls(SafeView):
    def __init__(self, service):
        super().__init__(timeout=None)
        self.service = service

    @discord.ui.button(label='Assumir', emoji='👤', style=discord.ButtonStyle.primary, custom_id='fsociety:ticket:claim:v1')
    async def claim(self, i, button):
        await self.service.claim(i)

    @discord.ui.button(label='Fechar', emoji='🔒', style=discord.ButtonStyle.danger, custom_id='fsociety:ticket:close:v1')
    async def close(self, i, button):
        await self.service.request_close(i)

    @discord.ui.button(label='Histórico', emoji='📄', custom_id='fsociety:ticket:transcript:v1')
    async def transcript(self, i, button):
        await self.service.transcript(i)

    @discord.ui.button(label='Reabrir', emoji='🔓', custom_id='fsociety:ticket:reopen:v1')
    async def reopen(self, i, button):
        await self.service.reopen(i)


class CloseConfirmation(OwnedView):
    def __init__(self, service, owner, channel_id):
        super().__init__(service.center, owner, admin=False)
        self.service, self.channel_id = service, channel_id
        self.timeout = 60

    @discord.ui.button(label='Confirmar fechamento', style=discord.ButtonStyle.danger)
    async def confirm(self, i, button):
        if i.channel.id != self.channel_id:
            raise ValueError('Confirmação inválida para este canal.')
        await self.service.close(i)
        self.stop()
        await i.edit_original_response(content='Fechamento confirmado.', view=None)

    @discord.ui.button(label='Cancelar')
    async def cancel(self, i, button):
        self.stop()
        await i.response.edit_message(content='Fechamento cancelado.', view=None)


def install(center):
    service = TicketService(center)
    center.tickets = service
    group = app_commands.Group(name='ticket', description='Atendimento e configuração de tickets', guild_only=True)

    @group.command(name='configurar', description='Configura canais, textos, botão, GIF e equipe dos tickets')
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def configure(i: discord.Interaction):
        await center.open(i, 'tickets')

    @group.command(name='publicar', description='Publica ou atualiza o painel no canal configurado')
    @app_commands.checks.has_permissions(manage_guild=True)
    async def publish(i: discord.Interaction):
        await service.publish(i)

    @group.command(name='fechar', description='Fecha o ticket atual com confirmação, preservando o histórico')
    async def close(i: discord.Interaction):
        await service.request_close(i)

    @group.command(name='reabrir', description='Reabre o atendimento atual (somente equipe)')
    async def reopen(i: discord.Interaction):
        await service.reopen(i)

    @group.command(name='assumir', description='Assume o atendimento atual (somente equipe)')
    async def claim(i: discord.Interaction):
        await service.claim(i)

    @group.command(name='historico', description='Exporta até 1000 mensagens do ticket em arquivo de texto')
    async def transcript(i: discord.Interaction):
        await service.transcript(i)

    @group.command(name='lista', description='Mostra seus tickets ou todos para administradores')
    async def list_tickets(i: discord.Interaction):
        sql = 'SELECT * FROM tickets WHERE guild_id=?'
        args = [i.guild.id]
        if not i.user.guild_permissions.manage_guild:
            sql += ' AND user_id=?'
            args.append(i.user.id)
        rows = center.q(sql + ' ORDER BY id DESC LIMIT 15', args)
        text = '\n'.join(f'`#{r["id"]}` • <#{r["channel_id"]}> • {r["status"]}' for r in rows)
        await reply(i, text or 'Nenhum ticket encontrado.')

    center.bot.tree.add_command(group)
    return service
