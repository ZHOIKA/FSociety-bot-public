"""Painel de administração do F SOCIETY (Python 3.13+)."""
import logging
import re
import sqlite3
from contextlib import closing
from urllib.parse import urlsplit

import discord
from discord import app_commands

LOG = logging.getLogger(__name__)
DEFAULT_GIF = ('https://upload.wikimedia.org/wikipedia/commons/c/cc/'
               'Digital_rain_animation_medium_letters_shine.gif')
DEFAULTS = {
    'accent': '22C55E', 'panel_title': 'F SOCIETY • Central de controle',
    'panel_description': 'Seu servidor, do seu jeito.\nEscolha uma área abaixo para começar.',
    'panel_footer': 'F SOCIETY • Configure. Personalize. Conecte.',
    'banner_enabled': True,
    'ticket_enabled': False, 'ticket_panel_channel': None, 'ticket_category': None,
    'ticket_log_channel': None, 'ticket_message_id': None, 'ticket_published_channel': None,
    'ticket_roles': [], 'ticket_ping_roles': [], 'ticket_ping_author': True,
    'ticket_title': 'Central de atendimento',
    'ticket_description': 'Precisa de ajuda? Abra um ticket para conversar com nossa equipe em um canal privado.',
    'ticket_button': 'Abrir atendimento', 'ticket_emoji': '🎫', 'ticket_style': 'verde',
    'ticket_welcome': 'Olá, {user}!\nDescreva sua solicitação e aguarde nossa equipe.\n\n**Assunto:** {reason}',
    'ticket_question': 'Como podemos ajudar?', 'ticket_name': 'ticket-{username}',
    'ticket_gif': '', 'ticket_limit': 1,
    'xp_gain': 5, 'xp_cooldown': 60, 'daily_reward': 250, 'work_reward': 100,
    'spam_limit': 6, 'spam_window': 8, 'spam_timeout': 30,
    'cve_enabled': False, 'cve_channel': None, 'cve_min_score': 0,
    'cve_unscored': True, 'cve_since': 0, 'cve_error': '',
}
LEGACY = {'panel_gif', 'panel_emoji', 'welcome_channel', 'welcome_message', 'welcome_gif',
          'log_channel', 'anti_spam', 'anti_invite', 'anti_raid', 'raid_limit', 'raid_window',
          'xp_enabled', 'level_channel', 'economy_enabled', 'auto_role', 'prefix'}
SECTIONS = {
    'home': ('Visão geral', '🏠', 'Status e atalhos do servidor'),
    'tickets': ('Tickets', '🎫', 'Atendimento privado, painel e equipe'),
    'welcome': ('Boas-vindas', '👋', 'Mensagem, GIF, canal e cargo automático'),
    'security': ('Segurança', '🛡️', 'Anti-spam, convites e alertas de raid'),
    'cves': ('Alertas de CVE', '📡', 'Vulnerabilidades novas, canal e gravidade'),
    'economy': ('XP e economia', '💎', 'Progressão, recompensas e limites'),
    'appearance': ('Aparência', '🎨', 'Cor, título, descrição e GIF animado'),
    'logs': ('Logs e diagnóstico', '📋', 'Canais e permissões necessárias'),
    'help': ('Comandos', '📚', 'Catálogo organizado por categoria'),
}


def migrate(con):
    from cve_alerts import migrate as migrate_cves
    migrate_cves(con)
    con.execute('CREATE TABLE IF NOT EXISTS settings (guild_id INTEGER, key TEXT, value TEXT, PRIMARY KEY(guild_id,key))')
    con.execute('CREATE TABLE IF NOT EXISTS channel_locks (guild_id INTEGER, channel_id INTEGER, send_messages INTEGER, PRIMARY KEY(guild_id,channel_id))')
    columns = {r[1] for r in con.execute('PRAGMA table_info(tickets)')}
    for name, definition in {
        'status': "TEXT DEFAULT 'open'", 'claimed_by': 'INTEGER',
        'closed_at': 'INTEGER', 'support_roles': "TEXT DEFAULT '[]'",
    }.items():
        if name not in columns:
            con.execute(f'ALTER TABLE tickets ADD COLUMN {name} {definition}')
    con.execute('CREATE INDEX IF NOT EXISTS ticket_lookup ON tickets(guild_id,user_id,status)')
    con.execute('CREATE INDEX IF NOT EXISTS ticket_channel ON tickets(guild_id,channel_id)')


def valid_url(value):
    if not value:
        return value
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or any(c.isspace() for c in value):
        raise ValueError('Use um link direto de imagem/GIF começando com https://, ou deixe vazio.')
    return value


def validate(key, value):
    value = value.strip()
    if not value and not (key.endswith('_gif') or key in ('panel_emoji', 'ticket_emoji')):
        raise ValueError('Este campo não pode ficar vazio.')
    ranges = {'ticket_limit': (1, 5), 'xp_gain': (1, 100), 'xp_cooldown': (5, 3600),
              'cve_min_score': (0, 10),
              'daily_reward': (1, 100000), 'work_reward': (1, 100000),
              'spam_limit': (3, 30), 'spam_window': (3, 120), 'spam_timeout': (10, 3600),
              'raid_limit': (3, 100), 'raid_window': (3, 300)}
    if key in ranges:
        low, high = ranges[key]
        if not value.isdecimal() or not low <= int(value) <= high:
            raise ValueError(f'{key}: informe um número entre {low} e {high}.')
        return int(value)
    if key.endswith('_gif'):
        return valid_url(value)
    if key == 'accent':
        value = value.lstrip('#')
        if not re.fullmatch('[0-9a-fA-F]{6}', value):
            raise ValueError('A cor deve ter seis dígitos hexadecimais. Exemplo: #22C55E.')
    if key == 'ticket_style' and value not in ('verde', 'azul', 'cinza', 'vermelho'):
        raise ValueError('Cor do botão: verde, azul, cinza ou vermelho.')
    if key in ('ticket_emoji', 'panel_emoji') and value:
        if value.isdecimal():
            value = f'<a:fsociety:{value}>'
        if not re.fullmatch(r'<a?:\w+:\d{15,22}>', value) and (len(value) > 16 or value.isascii()):
            raise ValueError('Use um emoji Unicode, um ID de emoji ou <:nome:id> / <a:nome:id>.')
    if key == 'prefix' and (not value or len(value) > 5 or any(c.isspace() for c in value)):
        raise ValueError('O prefixo deve ter de 1 a 5 caracteres, sem espaços.')
    if key == 'ticket_name' and not re.fullmatch(r'[a-z0-9{}_-]{1,70}', value):
        raise ValueError('Use letras minúsculas, números, hífen e {username} ou {id} no nome do canal.')
    if key == 'ticket_name' and ('{' in value.replace('{username}', '').replace('{id}', '') or '}' in value.replace('{username}', '').replace('{id}', '')):
        raise ValueError('No nome do canal, use apenas as variáveis {username} e {id}.')
    return value


async def reply(i, text, **kwargs):
    sender = i.followup.send if i.response.is_done() else i.response.send_message
    await sender(text, ephemeral=True, **kwargs)


async def handle_error(i, error):
    original = getattr(error, 'original', error)
    if isinstance(original, discord.Forbidden):
        message = 'Não tenho permissão para concluir. Confira meus cargos e as permissões do canal em /diagnostico.'
    elif isinstance(original, discord.NotFound):
        message = 'Esse canal, membro ou mensagem não existe mais. Atualize a configuração e tente novamente.'
    elif isinstance(original, (app_commands.CheckFailure, ValueError)):
        message = str(original)
    elif isinstance(original, discord.HTTPException):
        message = 'O Discord não concluiu a operação. Confira os dados e tente novamente em instantes.'
    else:
        LOG.error('Falha em interação', exc_info=(type(original), original, original.__traceback__))
        message = 'Não foi possível concluir esta ação. O erro foi registrado para investigação.'
    await reply(i, message[:1900])


class CommandTree(app_commands.CommandTree):
    async def interaction_check(self, i):
        if i.guild is None:
            await reply(i, 'Use os comandos do F SOCIETY dentro de um servidor.')
            return False
        command = i.command
        while command is not None:
            permissions = command.default_permissions
            if permissions and not i.user.guild_permissions.administrator:
                missing = [p for p, needed in permissions if needed and not getattr(i.user.guild_permissions, p)]
                if missing:
                    await reply(i, 'Você não tem permissão para usar este comando neste servidor.')
                    return False
            command = command.parent
        return True

    async def on_error(self, i, error):
        await handle_error(i, error)


class Center:
    def __init__(self, bot, db_path, query, guild_settings, formatter):
        self.bot, self.db_path, self.q, self.guild, self.fmt = bot, db_path, query, guild_settings, formatter

    def settings(self, gid):
        import json
        result = dict(DEFAULTS)
        result.update(dict(self.guild(gid)))
        for row in self.q('SELECT key,value FROM settings WHERE guild_id=?', (gid,)):
            if row['key'] in DEFAULTS:
                result[row['key']] = json.loads(row['value'])
        return result

    def save(self, gid, changes):
        import json
        unknown = changes.keys() - (DEFAULTS.keys() | LEGACY)
        if unknown:
            raise ValueError('Configuração desconhecida.')
        self.guild(gid)
        with closing(sqlite3.connect(self.db_path())) as con, con:
            for key, value in changes.items():
                if key in LEGACY:
                    con.execute(f'UPDATE guilds SET {key}=? WHERE guild_id=?', (value, gid))
                else:
                    con.execute('INSERT INTO settings(guild_id,key,value) VALUES(?,?,?) '
                                'ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value',
                                (gid, key, json.dumps(value, ensure_ascii=False)))

    def embed(self, g, title=None, description=None, banner=True):
        s = self.settings(g.id)
        e = discord.Embed(title=title or s['panel_title'], description=description or s['panel_description'],
                          color=int(s['accent'], 16))
        if s['panel_emoji']:
            e.title = f"{s['panel_emoji']} {e.title}"
        e.set_author(name=g.name, **({'icon_url': g.icon.url} if g.icon else {}))
        e.set_footer(text=s['panel_footer'])
        if banner and s['banner_enabled']:
            e.set_image(url=s['panel_gif'] or DEFAULT_GIF)
        return e

    def page(self, g, section='home', admin=False):
        s = self.settings(g.id)
        on = lambda value: '🟢 Ativo' if value else '⚫ Desativado'
        channel = lambda value: f'<#{value}>' if value else 'Não configurado'
        roles = lambda values: ', '.join(f'<@&{v}>' for v in values) or 'Nenhum'
        e = self.embed(g)
        if section != 'home':
            label, emoji, desc = SECTIONS[section]
            e.title, e.description = f'{emoji} {label}', desc
        if section == 'home':
            count = self.q("SELECT COUNT(*) AS n FROM tickets WHERE guild_id=? AND status='open'", (g.id,), True)['n']
            e.add_field(name='🎫 Atendimento', value=f"{on(s['ticket_enabled'])}\n{count} ticket(s) aberto(s)")
            e.add_field(name='🛡️ Proteção', value=f"Spam: {on(s['anti_spam'])}\nConvites: {on(s['anti_invite'])}")
            e.add_field(name='💎 Comunidade', value=f"XP: {on(s['xp_enabled'])}\nEconomia: {on(s['economy_enabled'])}")
            e.add_field(name='Comece por aqui', value='Selecione uma área no menu.\n'
                        + ('As alterações são salvas ao confirmar cada formulário.\nEm Tickets, use **Publicar / atualizar** para aplicar o visual ao painel público.' if admin
                           else 'Use **Configurar servidor** para abrir as opções de administração.'), inline=False)
        elif section == 'tickets':
            e.add_field(name='Disponibilidade', value=on(s['ticket_enabled']))
            e.add_field(name='Painel público', value=channel(s['ticket_panel_channel']))
            e.add_field(name='Categoria de atendimento', value=channel(s['ticket_category']))
            e.add_field(name='Equipe com acesso', value=roles(s['ticket_roles']), inline=False)
            e.add_field(name='Cargos mencionados ao abrir', value=roles(s['ticket_ping_roles']), inline=False)
            e.add_field(name='Experiência', value=f"Botão: {s['ticket_emoji']} {s['ticket_button']}\n"
                        f"Limite por pessoa: {s['ticket_limit']}\nMencionar autor: {'Sim' if s['ticket_ping_author'] else 'Não'}", inline=False)
            e.add_field(name='Configuração', value='**Canais e equipe** → **Textos e botão** → **Prévia** → **Publicar / atualizar**', inline=False)
        elif section == 'cves':
            checked = self.q("SELECT value FROM cve_state WHERE key='checked'", one=True)
            error = self.q("SELECT value FROM cve_state WHERE key='error'", one=True)
            e.add_field(name='Monitor', value=on(s['cve_enabled']))
            e.add_field(name='Canal de alertas', value=channel(s['cve_channel']))
            e.add_field(name='Filtros', value=f"CVSS mínimo: {s['cve_min_score']}/10\nSem nota: {'incluir' if s['cve_unscored'] else 'aguardar classificação'}")
            e.add_field(name='Última consulta à fonte', value=f"<t:{int(float(checked['value']))}:R>" if checked else 'Aguardando primeira consulta', inline=False)
            e.add_field(name='Como funciona', value='Consulta a NVD a cada 5 minutos, enquanto o bot estiver online.\n'
                        'Na primeira ativação, acompanha publicações a partir daquele momento.\n'
                        'A fonte pode atrasar a publicação e a classificação de uma CVE.', inline=False)
            if s['cve_error'] or error:
                e.add_field(name='⚠️ Atenção', value=s['cve_error'] or error['value'], inline=False)
        elif section == 'welcome':
            e.add_field(name='Canal', value=channel(s['welcome_channel']))
            e.add_field(name='Cargo automático', value=roles([s['auto_role']]) if s['auto_role'] else 'Nenhum')
            e.add_field(name='Mensagem', value=s['welcome_message'][:1000], inline=False)
            e.add_field(name='Variáveis disponíveis', value='`{user}` `{username}` `{server}` `{id}` `{member_count}`', inline=False)
        elif section == 'security':
            e.add_field(name='Anti-spam', value=f"{on(s['anti_spam'])}\n{s['spam_limit']} mensagens / {s['spam_window']}s\nTimeout: {s['spam_timeout']}s")
            e.add_field(name='Anti-convites', value=on(s['anti_invite']))
            e.add_field(name='Alerta de raid', value=f"{on(s['anti_raid'])}\n{s['raid_limit']} entradas / {s['raid_window']}s")
            e.add_field(name='Funcionamento', value='Anti-raid gera um alerta nos logs. Membros com Gerenciar Mensagens são isentos do anti-spam e anti-convites.', inline=False)
        elif section == 'economy':
            e.add_field(name='Experiência', value=f"{on(s['xp_enabled'])}\n+{s['xp_gain']} XP a cada {s['xp_cooldown']}s")
            e.add_field(name='Economia', value=f"{on(s['economy_enabled'])}\nDaily: {s['daily_reward']} moedas\nTrabalho: {s['work_reward']} + nível × 10")
            e.add_field(name='Aviso de nível', value=channel(s['level_channel']))
        elif section == 'appearance':
            e.add_field(name='Identidade', value=f"Cor: `#{s['accent']}`\nEmoji: {s['panel_emoji'] or 'Não definido'}\nPrefixo: `{s['prefix']}`")
            e.add_field(name='Banner animado', value=on(s['banner_enabled']))
            e.add_field(name='Personalização', value='Edite título, descrição, rodapé, cor e GIF. Deixe o link vazio para usar o GIF padrão.', inline=False)
        elif section == 'logs':
            e.add_field(name='Logs gerais', value=channel(s['log_channel']))
            e.add_field(name='Logs de tickets', value=channel(s['ticket_log_channel']))
            e.add_field(name='Verificação', value='Use **Diagnóstico** para conferir canais, equipe e permissões antes de publicar.', inline=False)
        elif section == 'help':
            for name, value in HELP.items():
                e.add_field(name=name, value=value, inline=False)
        return e

    async def open(self, i, section='home', admin=True):
        if not i.guild:
            return await reply(i, 'Abra o painel dentro de um servidor.')
        if admin and not i.user.guild_permissions.manage_guild:
            return await reply(i, 'Você precisa de **Gerenciar Servidor** para configurar o bot.')
        view = Panel(self, i.user.id, section, admin)
        await i.response.send_message(embed=self.page(i.guild, section, admin), view=view, ephemeral=True)

    def diagnostics(self, g):
        s = self.settings(g.id)
        lines = []
        for key, title, kind in [('ticket_panel_channel', 'Painel de tickets', discord.TextChannel),
                                 ('ticket_category', 'Categoria de tickets', discord.CategoryChannel),
                                 ('ticket_log_channel', 'Logs de tickets', discord.TextChannel),
                                 ('log_channel', 'Logs gerais', discord.TextChannel),
                                 ('welcome_channel', 'Boas-vindas', discord.TextChannel)]:
            ch = g.get_channel(s[key]) if s[key] else None
            if not isinstance(ch, kind):
                lines.append(f'⚠️ {title}: selecione um canal válido.')
                continue
            perms = ch.permissions_for(g.me)
            needed = ['view_channel', 'send_messages', 'embed_links']
            if kind is discord.CategoryChannel:
                needed += ['manage_channels', 'manage_roles', 'read_message_history', 'attach_files']
            missing = [p for p in needed if not getattr(perms, p)]
            lines.append(f"{'⚠️' if missing else '✅'} {title}: " + (', '.join(missing) if missing else 'pronto'))
        valid_roles = [g.get_role(r) for r in s['ticket_roles']]
        lines.append('✅ Equipe de tickets configurada.' if any(valid_roles) else '⚠️ Selecione ao menos um cargo da equipe.')
        role = g.get_role(s['auto_role']) if s['auto_role'] else None
        if role and (role.managed or role >= g.me.top_role or not g.me.guild_permissions.manage_roles):
            lines.append('⚠️ Não consigo atribuir o cargo automático: confira hierarquia e Gerenciar Cargos.')
        for p in ('moderate_members', 'manage_messages', 'ban_members', 'kick_members'):
            if not getattr(g.me.guild_permissions, p):
                lines.append(f'ℹ️ Moderação: falta {p}.')
        return '\n'.join(lines)


HELP = {
    '📡 Vulnerabilidades': '`/cve configurar` `/cve status` `/cve testar`',
    '🎛️ Configuração': '`/painel` `/configurar` `/config` `/diagnostico`',
    '🎫 Atendimento': '`/ticket configurar` `/ticket publicar` `/ticket fechar` `/ticket reabrir`\n`/ticket assumir` `/ticket historico` `/ticket lista`',
    '🛡️ Moderação': '`/ban` `/kick` `/timeout` `/untimeout` `/avisar` `/avisos` `/limparavisos` `/limpar`',
    '💎 Comunidade': '`/perfil` `/ranking` `/saldo` `/daily` `/trabalhar` `/pagar` `/topmoedas`',
    '🔧 Servidor': '`/slowmode` `/travar` `/destravar` `/roleadd` `/roleremove` `/anuncio` `/enquete` `/say` `/webhook`',
    '📚 Utilidades': '`/agenda` `/ping` `/botinfo` `/servidor` `/avatar` `/userinfo` `/userinfo_cargos` `/servericon` `/cronometro`',
}


class SafeView(discord.ui.View):
    async def on_error(self, i, error, item):
        await handle_error(i, error)


class OwnedView(SafeView):
    def __init__(self, center, owner, admin=True):
        super().__init__(timeout=600)
        self.center, self.owner, self.admin = center, owner, admin

    async def interaction_check(self, i):
        if not i.guild or i.user.id != self.owner:
            await reply(i, 'Este painel pertence a quem o abriu. Abra o seu com /painel.')
            return False
        if self.admin and not i.user.guild_permissions.manage_guild:
            await reply(i, 'Você precisa de **Gerenciar Servidor** para alterar estas opções.')
            return False
        return True

    def button(self, label, callback, style=discord.ButtonStyle.secondary, row=1, emoji=None):
        button = discord.ui.Button(label=label, style=style, row=row, emoji=emoji)
        button.callback = callback
        self.add_item(button)
        return button


class SettingsModal(discord.ui.Modal):
    def __init__(self, panel, title, fields):
        super().__init__(title=title, timeout=600)
        self.panel = panel
        self.inputs = {}
        settings = panel.center.settings(panel.gid)
        for key, label, limit, optional, long in fields:
            field = discord.ui.TextInput(default=str(settings.get(key) or ''),
                                         max_length=limit, required=not optional,
                                         style=discord.TextStyle.paragraph if long else discord.TextStyle.short)
            self.inputs[key] = field
            self.add_item(discord.ui.Label(text=label, component=field))

    async def on_submit(self, i):
        if not await self.panel.interaction_check(i):
            return
        changes = {key: validate(key, field.value) for key, field in self.inputs.items()}
        self.panel.center.save(i.guild.id, changes)
        await self.panel.refresh(i)

    async def on_error(self, i, error):
        await handle_error(i, error)


FORMS = {
    'cves': ('Filtro de gravidade', [('cve_min_score', 'CVSS mínimo: 0=todas, 7=altas, 9=críticas', 2, False, False)]),
    'identity': ('Identidade do painel', [('panel_title', 'Título', 100, False, False), ('panel_description', 'Descrição', 1000, False, True), ('panel_footer', 'Rodapé', 150, False, False), ('accent', 'Cor hexadecimal (#22C55E)', 7, False, False)]),
    'media': ('GIF e personalização', [('panel_gif', 'URL HTTPS do GIF (vazio = padrão)', 500, True, False), ('panel_emoji', 'Emoji ou ID animado (vazio = remover)', 80, True, False), ('prefix', 'Prefixo dos comandos de texto', 5, False, False)]),
    'ticket_text': ('Textos do atendimento', [('ticket_title', 'Título do painel público', 100, False, False), ('ticket_description', 'Texto do painel público', 2000, False, True), ('ticket_welcome', 'Mensagem inicial: {user} {reason} {server}', 1800, False, True), ('ticket_question', 'Pergunta do formulário de abertura', 45, False, False)]),
    'ticket_button': ('Botão e canal do ticket', [('ticket_button', 'Texto do botão', 80, False, False), ('ticket_emoji', 'Emoji do botão (vazio = sem emoji)', 80, True, False), ('ticket_style', 'Cor: verde, azul, cinza ou vermelho', 10, False, False), ('ticket_name', 'Nome: ticket-{username} ou ticket-{id}', 70, False, False), ('ticket_limit', 'Tickets simultâneos por pessoa (1–5)', 1, False, False)]),
    'ticket_gif': ('Imagem do atendimento', [('ticket_gif', 'URL HTTPS do GIF (vazio = banner geral)', 500, True, False)]),
    'welcome': ('Mensagem de boas-vindas', [('welcome_message', 'Texto: {user} {server} {member_count}', 1800, False, True), ('welcome_gif', 'URL HTTPS do GIF (vazio = sem imagem)', 500, True, False)]),
    'security': ('Limites de segurança', [('spam_limit', 'Mensagens para detectar spam (3–30)', 3, False, False), ('spam_window', 'Janela de spam em segundos (3–120)', 3, False, False), ('spam_timeout', 'Timeout em segundos (10–3600)', 4, False, False), ('raid_limit', 'Entradas para alertar raid (3–100)', 3, False, False), ('raid_window', 'Janela de raid em segundos (3–300)', 3, False, False)]),
    'economy': ('XP e recompensas', [('xp_gain', 'XP por mensagem (1–100)', 3, False, False), ('xp_cooldown', 'Intervalo de XP em segundos (5–3600)', 4, False, False), ('daily_reward', 'Moedas do daily (1–100000)', 6, False, False), ('work_reward', 'Moedas base do trabalho (1–100000)', 6, False, False)]),
}


class Panel(OwnedView):
    def __init__(self, center, owner, section='home', admin=True):
        super().__init__(center, owner, admin)
        self.section = section
        self.gid = None
        options = [discord.SelectOption(label=label, value=key, emoji=emoji, description=desc,
                                        default=key == section) for key, (label, emoji, desc) in SECTIONS.items()]
        nav = discord.ui.Select(placeholder='Explore a central do servidor…', options=options, row=0)

        async def navigate(i):
            view = Panel(center, owner, nav.values[0], admin)
            await i.response.edit_message(embed=center.page(i.guild, view.section, admin), view=view)
        nav.callback = navigate
        self.add_item(nav)

        async def configure(i):
            await center.open(i, section if section != 'help' else 'home')
        if not admin:
            self.button('Configurar servidor', configure, discord.ButtonStyle.primary, emoji='⚙️')
            return

        def form(name, label, row=1):
            async def callback(i):
                self.gid = i.guild.id
                title, fields = FORMS[name]
                await i.response.send_modal(SettingsModal(self, title, fields))
            self.button(label, callback, row=row)

        def toggle(key, label, row=1):
            async def callback(i):
                s = center.settings(i.guild.id)
                center.save(i.guild.id, {key: not s[key]})
                await self.refresh(i)
            self.button(label, callback, row=row)

        def picker(kind, label, row=1):
            async def callback(i):
                await i.response.edit_message(embed=center.embed(i.guild, label, 'Selecione canais e cargos abaixo. Cada seleção é salva imediatamente.\nLimpe a seleção para remover um valor.'),
                                              view=Pickers(center, owner, kind, i.guild))
            self.button(label, callback, row=row)

        if section == 'home':
            async def tickets(i):
                await i.response.edit_message(embed=center.page(i.guild, 'tickets', True), view=Panel(center, owner, 'tickets'))
            self.button('Configurar tickets', tickets, discord.ButtonStyle.success, emoji='🎫')
        elif section == 'tickets':
            picker('ticket_channels', 'Canais e categoria')
            picker('ticket_roles', 'Equipe e menções')
            form('ticket_text', 'Textos e formulário')
            form('ticket_button', 'Botão e limites')
            form('ticket_gif', 'GIF do ticket', 2)
            toggle('ticket_enabled', 'Ativar / pausar', 2)
            async def preview(i):
                from tickets import OpenTicketView
                await i.response.send_message(embed=center.tickets.panel_embed(i.guild),
                                              view=OpenTicketView(center.tickets, center.settings(i.guild.id), preview=True), ephemeral=True)
            async def publish(i):
                await center.tickets.publish(i)
            self.button('Prévia', preview, row=2, emoji='👁️')
            self.button('Publicar / atualizar', publish, discord.ButtonStyle.success, row=2)
        elif section == 'cves':
            picker('cves', 'Canal dos alertas')
            form('cves', 'Gravidade mínima')
            toggle('cve_unscored', 'Incluir / excluir sem nota')
            async def activate_cves(i):
                center.cves.set_active(i.guild.id, not center.settings(i.guild.id)['cve_enabled'])
                await self.refresh(i)
            self.button('Ativar / pausar', activate_cves, discord.ButtonStyle.success, row=2)
            self.button('Testar canal', center.cves.test_channel, row=2)
        elif section == 'welcome':
            picker('welcome', 'Canal e cargo')
            form('welcome', 'Mensagem e GIF')
            async def preview_welcome(i):
                s = center.settings(i.guild.id)
                e = center.embed(i.guild, '👋 Boas-vindas', center.fmt(s['welcome_message'], i.user, i.guild)[:4000], banner=False)
                if s['welcome_gif']:
                    e.set_image(url=s['welcome_gif'])
                await i.response.send_message(embed=e, ephemeral=True)
            self.button('Prévia', preview_welcome, row=1)
        elif section == 'security':
            toggle('anti_spam', 'Alternar anti-spam')
            toggle('anti_invite', 'Alternar anti-convites')
            toggle('anti_raid', 'Alternar alerta de raid')
            form('security', 'Limites e duração', 2)
        elif section == 'economy':
            toggle('xp_enabled', 'Ativar / pausar XP')
            toggle('economy_enabled', 'Ativar / pausar economia')
            form('economy', 'XP e recompensas')
            picker('level', 'Canal de níveis', 2)
        elif section == 'appearance':
            form('identity', 'Título, textos e cor')
            form('media', 'GIF, emoji e prefixo')
            toggle('banner_enabled', 'Mostrar / ocultar GIF')
        elif section == 'logs':
            picker('logs', 'Canais de logs')

        if section in ('home', 'logs', 'tickets'):
            async def diagnose(i):
                await reply(i, center.diagnostics(i.guild))
            self.button('Diagnóstico', diagnose, row=3, emoji='🔎')

    async def refresh(self, i):
        await i.response.edit_message(embed=self.center.page(i.guild, self.section, self.admin),
                                      view=Panel(self.center, self.owner, self.section, self.admin))


class Pickers(OwnedView):
    def __init__(self, center, owner, kind, g=None):
        super().__init__(center, owner)
        self.kind = kind
        entries = {
            'cves': [('cve_channel', 'Canal para receber os alertas de CVE', 'text')],
            'ticket_channels': [('ticket_panel_channel', 'Canal do painel público', 'text'), ('ticket_category', 'Categoria dos novos tickets', 'category'), ('ticket_log_channel', 'Canal dos logs de tickets', 'text')],
            'ticket_roles': [('ticket_roles', 'Cargos da equipe com acesso aos tickets', 'roles'), ('ticket_ping_roles', 'Cargos mencionados ao abrir (da equipe)', 'roles')],
            'welcome': [('welcome_channel', 'Canal de boas-vindas', 'text'), ('auto_role', 'Cargo automático dos novos membros', 'role')],
            'logs': [('log_channel', 'Canal de logs gerais', 'text'), ('ticket_log_channel', 'Canal de logs de tickets', 'text')],
            'level': [('level_channel', 'Canal de avisos de nível', 'text')],
        }[kind]
        settings = center.settings(g.id) if g else {}
        for row, (key, label, selector) in enumerate(entries):
            ids = settings.get(key) or []
            if not isinstance(ids, list):
                ids = [ids]
            defaults = [] if not g else [obj for uid in ids if (obj := (g.get_role(uid) if selector.startswith('role') else g.get_channel(uid)))]
            if selector.startswith('role'):
                item = discord.ui.RoleSelect(placeholder=label, min_values=0, max_values=5 if selector == 'roles' else 1, row=row, default_values=defaults)
            else:
                item = discord.ui.ChannelSelect(placeholder=label, min_values=0, max_values=1,
                                                channel_types=[discord.ChannelType.category if selector == 'category' else discord.ChannelType.text], row=row, default_values=defaults)
            async def save(i, item=item, key=key, selector=selector):
                values = list(item.values)
                if selector.startswith('role'):
                    if any(r.is_default() or r.managed for r in values):
                        raise ValueError('Escolha cargos personalizados. @everyone e cargos de integrações não são permitidos.')
                    if key == 'auto_role' and values:
                        role = values[0]
                        if role >= i.guild.me.top_role or (i.user.id != i.guild.owner_id and role >= i.user.top_role):
                            raise ValueError('O cargo automático deve ficar abaixo do seu cargo e do cargo do bot.')
                        if role.permissions.administrator or role.permissions.manage_guild or role.permissions.manage_roles:
                            raise ValueError('Escolha um cargo de membro, sem permissões administrativas, para atribuição automática.')
                s = center.settings(i.guild.id)
                selected = [v.id for v in values]
                changes = {key: selected if selector == 'roles' else (selected[0] if selected else None)}
                if key == 'cve_channel' and not selected:
                    changes['cve_enabled'] = False
                if key == 'ticket_ping_roles' and not set(selected) <= set(s['ticket_roles']):
                    raise ValueError('Os cargos mencionados precisam estar na equipe com acesso ao ticket.')
                if key == 'ticket_roles':
                    changes['ticket_ping_roles'] = [r for r in s['ticket_ping_roles'] if r in selected]
                center.save(i.guild.id, changes)
                summary = 'Salvo: ' + (', '.join(v.mention for v in values) if values else 'nenhum')
                if key in ('ticket_roles', 'ticket_category'):
                    summary += '\nEsta seleção vale para novos tickets. Os existentes preservam o acesso da equipe original.'
                await i.response.edit_message(embed=center.embed(i.guild, '✅ Configuração salva', summary),
                                              view=Pickers(center, owner, kind, i.guild))
            item.callback = save
            self.add_item(item)
        if kind == 'ticket_roles':
            async def ping_author(i):
                s = center.settings(i.guild.id)
                center.save(i.guild.id, {'ticket_ping_author': not s['ticket_ping_author']})
                await reply(i, 'Mencionar quem abriu: ' + ('ativado' if not s['ticket_ping_author'] else 'desativado'))
            self.button('Alternar menção ao autor', ping_author, row=3)
        section = {'ticket_channels': 'tickets', 'ticket_roles': 'tickets', 'level': 'economy'}.get(kind, kind)
        async def back(i):
            await i.response.edit_message(embed=center.page(i.guild, section, True), view=Panel(center, owner, section))
        self.button('Voltar', back, row=4, emoji='↩️')
