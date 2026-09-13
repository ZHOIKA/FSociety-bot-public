"""Tema visual e balanceamento leve do F SOCIETY."""
import re

import discord

import control_panel as cp
from emojis import (
    ADMIN, ARROW, BANNED, LOADING, VERIFIED, WELCOME, YUUPIII,
    SAD, RED_ARROW, BITCOIN, BLUE_ARROW, BAN_GIRL,
)

_INSTALLED = False
_CUSTOM_EMOJI_RE = re.compile(r'<a?:\w+:\d{15,22}>|:\w+:')
_STOCK_PREFIX_RE = re.compile(r'^[\s🎫🛡️💎⚠️🏠👋📡🎨📋📚⚙️🔎✅⏳⌛🔨🚫⛔🪙💰➡️⬅️▶️◀️]+')
_MONEY_RE = re.compile(
    r'\b(?:dinheiro|moeda|moedas|saldo|economia|recompensa|recompensas|daily|'
    r'trabalho|trabalhar|pagamento|pagar|pagou|recebeu|receber|ganhou|ganhar|'
    r'valor|valores|topmoedas)\b', re.IGNORECASE,
)


def _clean_text(value):
    value = str(value or '')
    value = _CUSTOM_EMOJI_RE.sub('', value)
    return ' '.join(value.split()).strip()


def _clean_stock_prefix(value):
    return _STOCK_PREFIX_RE.sub('', _clean_text(value)).strip()


def _partial(value: str) -> discord.PartialEmoji:
    return discord.PartialEmoji.from_str(value)


def _guild_markup(g, fallback: str, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for emoji in getattr(g, 'emojis', []):
        if emoji.name.casefold() in wanted:
            return str(emoji)
    return fallback


def _line_prefix(text, g=None):
    if g:
        if _MONEY_RE.search(text):
            return _guild_markup(g, BITCOIN, 'bitcoin')
        return _guild_markup(g, RED_ARROW, 'ICON_redarrow', 'icon_redarrow')
    return BITCOIN if _MONEY_RE.search(text) else RED_ARROW


def _arrow_lines(value, g=None):
    result = []
    for raw in str(value or '').splitlines():
        text = raw.strip()
        if not text:
            result.append('')
            continue
        text = _clean_stock_prefix(text)
        result.append(f'{_line_prefix(text, g)} {text}' if text else '')
    return '\n'.join(result)


YUUPIII_EMOJI = _partial(YUUPIII)
WELCOME_EMOJI = _partial(WELCOME)
LOADING_EMOJI = _partial(LOADING)
VERIFIED_EMOJI = _partial(VERIFIED)
BANNED_EMOJI = _partial(BANNED)
ADMIN_EMOJI = _partial(ADMIN)
ARROW_EMOJI = _partial(ARROW)
SAD_EMOJI = _partial(SAD)
RED_ARROW_EMOJI = _partial(RED_ARROW)
BITCOIN_EMOJI = _partial(BITCOIN)
BLUE_ARROW_EMOJI = _partial(BLUE_ARROW)
BAN_GIRL_EMOJI = _partial(BAN_GIRL)


def _button_configured(view, label):
    """Retorna True quando o botão representa uma opção já configurada/ativa."""
    gid = getattr(view, 'gid', None)
    if not gid:
        return False
    s = view.center.settings(gid)
    label = _clean_stock_prefix(label).casefold()

    checks = {
        'canais e categoria': bool(s.get('ticket_panel_channel') or s.get('ticket_category') or s.get('ticket_log_channel')),
        'equipe e menções': bool(s.get('ticket_roles') or s.get('ticket_ping_roles')),
        'textos e formulário': any(s.get(k) != cp.DEFAULTS.get(k) for k in ('ticket_title','ticket_description','ticket_welcome','ticket_question')),
        'botão e limites': any(s.get(k) != cp.DEFAULTS.get(k) for k in ('ticket_button','ticket_emoji','ticket_style','ticket_name','ticket_limit')),
        'gif do ticket': bool(s.get('ticket_gif')),
        'ativar / pausar': bool(s.get('ticket_enabled')),
        'canal dos alertas': bool(s.get('cve_channel')),
        'gravidade mínima': bool(s.get('cve_min_score')),
        'incluir / excluir sem nota': bool(s.get('cve_unscored')),
        'canal e cargo': bool(s.get('welcome_channel') or s.get('auto_role')),
        'mensagem e gif': bool(s.get('welcome_gif')) or s.get('welcome_message') != cp.DEFAULTS.get('welcome_message'),
        'alternar anti-spam': bool(s.get('anti_spam')),
        'alternar anti-convites': bool(s.get('anti_invite')),
        'alternar alerta de raid': bool(s.get('anti_raid')),
        'limites e duração': any(s.get(k) != cp.DEFAULTS.get(k) for k in ('spam_limit','spam_window','spam_timeout','raid_limit','raid_window')),
        'ativar / pausar xp': bool(s.get('xp_enabled')),
        'ativar / pausar economia': bool(s.get('economy_enabled')),
        'xp e recompensas': any(s.get(k) != cp.DEFAULTS.get(k) for k in ('xp_gain','xp_cooldown','daily_reward','work_reward')),
        'canal de níveis': bool(s.get('level_channel')),
        'título, textos e cor': any(s.get(k) != cp.DEFAULTS.get(k) for k in ('panel_title','panel_description','panel_footer','accent')),
        'gif, emoji e prefixo': bool(s.get('panel_gif') or s.get('panel_emoji')) or s.get('prefix') not in (None, '!', cp.DEFAULTS.get('prefix')),
        'mostrar / ocultar gif': bool(s.get('banner_enabled')),
        'canais de logs': bool(s.get('log_channel') or s.get('ticket_log_channel')),
    }
    return checks.get(label, False)


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_settings = cp.Center.settings

    def themed_settings(self, gid):
        settings = original_settings(self, gid)
        settings['panel_emoji'] = ''
        settings['panel_title'] = _clean_stock_prefix(settings.get('panel_title')) or 'F SOCIETY • Central de controle'
        if settings.get('xp_gain') == 5: settings['xp_gain'] = 8
        if settings.get('xp_cooldown') == 60: settings['xp_cooldown'] = 45
        if settings.get('daily_reward') == 250: settings['daily_reward'] = 350
        if settings.get('work_reward') == 100: settings['work_reward'] = 150
        return settings

    cp.Center.settings = themed_settings

    sections = dict(cp.SECTIONS)
    mapping = {'home': ADMIN_EMOJI, 'tickets': RED_ARROW_EMOJI, 'welcome': WELCOME_EMOJI,
               'security': BAN_GIRL_EMOJI, 'cves': VERIFIED_EMOJI, 'economy': BITCOIN_EMOJI,
               'appearance': ADMIN_EMOJI, 'logs': VERIFIED_EMOJI, 'help': BLUE_ARROW_EMOJI}
    for key, emoji in mapping.items():
        label, _, description = sections[key]
        sections[key] = (_clean_stock_prefix(label), emoji, _clean_stock_prefix(description))
    cp.SECTIONS = sections

    original_button = cp.OwnedView.button

    def themed_button(self, label, callback, style=discord.ButtonStyle.secondary, row=1, emoji=None):
        text = str(emoji)
        replacements = {'⚙️': ADMIN_EMOJI, '🔎': VERIFIED_EMOJI, '👋': WELCOME_EMOJI,
            '💎': BITCOIN_EMOJI, '🪙': BITCOIN_EMOJI, '💰': BITCOIN_EMOJI,
            '✅': VERIFIED_EMOJI, '⏳': LOADING_EMOJI, '⌛': LOADING_EMOJI,
            '🔨': BAN_GIRL_EMOJI, '🔨️': BAN_GIRL_EMOJI, '🚫': BAN_GIRL_EMOJI,
            '⛔': BAN_GIRL_EMOJI, '⬅️': RED_ARROW_EMOJI, '◀️': RED_ARROW_EMOJI,
            '➡️': BLUE_ARROW_EMOJI, '▶️': BLUE_ARROW_EMOJI, '😢': SAD_EMOJI}
        custom = replacements.get(text, RED_ARROW_EMOJI)
        if style == discord.ButtonStyle.secondary and _button_configured(self, label):
            style = discord.ButtonStyle.success
        return original_button(self, _clean_stock_prefix(label), callback, style, row, custom)

    cp.OwnedView.button = themed_button

    original_embed = cp.Center.embed
    def themed_embed(self, g, title=None, description=None, banner=True):
        title = _clean_stock_prefix(title) if title else title
        description = _arrow_lines(description, g) if description else description
        return original_embed(self, g, title, description, banner)
    cp.Center.embed = themed_embed

    original_page = cp.Center.page
    def themed_page(self, g, section='home', admin=False):
        embed = original_page(self, g, section, admin)
        embed.title = _clean_stock_prefix(embed.title)
        if embed.description: embed.description = _arrow_lines(embed.description, g)
        for index, field in enumerate(embed.fields):
            embed.set_field_at(index, name=_clean_stock_prefix(field.name), value=_arrow_lines(field.value, g), inline=field.inline)
        if section == 'economy':
            s = self.settings(g.id)
            red_arrow = _guild_markup(g, RED_ARROW, 'ICON_redarrow', 'icon_redarrow')
            bitcoin = _guild_markup(g, BITCOIN, 'bitcoin')
            embed.add_field(name='Economia e progressão', value=(
                f'{red_arrow} XP por atividade: **{s["xp_gain"]}**\n'
                f'{red_arrow} Cooldown de XP: **{s["xp_cooldown"]}s**\n'
                f'{bitcoin} Recompensa diária: **{s["daily_reward"]}**\n'
                f'{bitcoin} Recompensa de trabalho: **{s["work_reward"]}**'), inline=False)
        return embed
    cp.Center.page = themed_page

    original_diagnostics = cp.Center.diagnostics
    def themed_diagnostics(self, g):
        return _arrow_lines(original_diagnostics(self, g), g)
    cp.Center.diagnostics = themed_diagnostics
