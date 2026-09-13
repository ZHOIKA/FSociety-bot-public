"""Painel dedicado e estável para a seção Boas-vindas do /configurar."""
import logging

import discord
from discord.ext import commands

import control_panel as cp

LOG = logging.getLogger(__name__)
_INSTALLED = False
_ORIGINAL_BOT_INIT = None
CHAT_DEFAULT_MESSAGE = '👋 {user}, seja bem-vindo(a) ao **{server}**! Agora somos **{member_count}** membros.'


class WelcomeMessageModal(discord.ui.Modal, title='Mensagens de boas-vindas'):
    message = discord.ui.TextInput(label='Mensagem do canal de boas-vindas', style=discord.TextStyle.paragraph, max_length=1800)
    gif = discord.ui.TextInput(label='URL HTTPS do GIF (opcional)', required=False, max_length=500)
    chat_message = discord.ui.TextInput(label='Mensagem curta do chat principal', style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, view):
        super().__init__()
        self.view_ref = view
        s = view.center.settings(view.guild_id)
        self.message.default = str(s.get('welcome_message') or '')[:1800]
        self.gif.default = str(s.get('welcome_gif') or '')[:500]
        self.chat_message.default = str(s.get('welcome_chat_message') or CHAT_DEFAULT_MESSAGE)[:1000]

    async def on_submit(self, interaction):
        if not await self.view_ref.interaction_check(interaction):
            return
        chat_text = self.chat_message.value.strip()
        if not chat_text:
            raise ValueError('A mensagem do chat principal não pode ficar vazia.')
        changes = {
            'welcome_message': cp.validate('welcome_message', self.message.value),
            'welcome_gif': cp.validate('welcome_gif', self.gif.value),
            'welcome_chat_message': chat_text,
        }
        self.view_ref.center.save(interaction.guild.id, changes)
        await cp.ConfigRouter.show(interaction, 'welcome', self.view_ref.owner)


class WelcomeView(cp.OwnedView):
    def __init__(self, center, owner, guild):
        super().__init__(center, owner, True)
        self.guild_id = guild.id
        s = center.settings(guild.id)
        channel_default = guild.get_channel(s.get('welcome_channel')) if s.get('welcome_channel') else None
        chat_default = guild.get_channel(s.get('welcome_chat_channel')) if s.get('welcome_chat_channel') else None
        role_default = guild.get_role(s.get('auto_role')) if s.get('auto_role') else None

        channel = discord.ui.ChannelSelect(placeholder='Canal dedicado de boas-vindas', channel_types=[discord.ChannelType.text], min_values=0, max_values=1, row=0, default_values=[channel_default] if channel_default else [])
        async def save_channel(i):
            center.save(i.guild.id, {'welcome_channel': channel.values[0].id if channel.values else None})
            await cp.ConfigRouter.show(i, 'welcome', owner)
        channel.callback = save_channel
        self.add_item(channel)

        chat_channel = discord.ui.ChannelSelect(placeholder='Chat principal para boas-vindas rápidas (opcional)', channel_types=[discord.ChannelType.text], min_values=0, max_values=1, row=1, default_values=[chat_default] if chat_default else [])
        async def save_chat_channel(i):
            value = chat_channel.values[0].id if chat_channel.values else None
            changes = {'welcome_chat_channel': value}
            if value is None:
                changes['welcome_chat_enabled'] = False
            center.save(i.guild.id, changes)
            await cp.ConfigRouter.show(i, 'welcome', owner)
        chat_channel.callback = save_chat_channel
        self.add_item(chat_channel)

        role = discord.ui.RoleSelect(placeholder='Cargo automático (opcional)', min_values=0, max_values=1, row=2, default_values=[role_default] if role_default else [])
        async def save_role(i):
            value = role.values[0] if role.values else None
            if value is not None:
                if value.is_default() or value.managed:
                    return await cp.reply(i, 'Escolha um cargo personalizado, não @everyone nem cargo de integração.')
                if value >= i.guild.me.top_role:
                    return await cp.reply(i, 'O cargo automático precisa ficar abaixo do cargo do bot.')
                if value.permissions.administrator or value.permissions.manage_guild or value.permissions.manage_roles:
                    return await cp.reply(i, 'Escolha um cargo de membro sem permissões administrativas.')
            center.save(i.guild.id, {'auto_role': value.id if value else None})
            await cp.ConfigRouter.show(i, 'welcome', owner)
        role.callback = save_role
        self.add_item(role)

        async def edit_text(i):
            await i.response.send_modal(WelcomeMessageModal(self))
        async def toggle_chat(i):
            current = center.settings(i.guild.id)
            if not current.get('welcome_chat_enabled') and not current.get('welcome_chat_channel'):
                return await cp.reply(i, 'Escolha primeiro o **chat principal** para ativar essa mensagem de boas-vindas.')
            center.save(i.guild.id, {'welcome_chat_enabled': not bool(current.get('welcome_chat_enabled'))})
            await cp.ConfigRouter.show(i, 'welcome', owner)
        async def preview(i):
            settings = center.settings(i.guild.id)
            text = center.fmt(str(settings.get('welcome_message') or ''), i.user, i.guild)[:4000]
            e = center.embed(i.guild, '👋 Boas-vindas', text, banner=False)
            gif = settings.get('welcome_gif') or ''
            if gif:
                e.set_image(url=gif)
            if settings.get('welcome_chat_enabled'):
                chat_text = center.fmt(str(settings.get('welcome_chat_message') or CHAT_DEFAULT_MESSAGE), i.user, i.guild)[:1000]
                e.add_field(name='💬 Prévia no chat principal', value=chat_text, inline=False)
            await i.response.send_message(embed=e, ephemeral=True)
        async def back(i):
            await cp.ConfigRouter.show(i, 'home', owner)

        self.button('Editar mensagens', edit_text, row=3)
        self.button('Chat principal: Ativado' if s.get('welcome_chat_enabled') else 'Chat principal: Desativado', toggle_chat, discord.ButtonStyle.success if s.get('welcome_chat_enabled') else discord.ButtonStyle.secondary, row=3)
        self.button('Prévia', preview, row=3)
        self.button('Voltar', back, row=4)


def _welcome_embed(center, guild):
    s = center.settings(guild.id)
    enabled = bool(s.get('welcome_chat_enabled'))
    e = center.embed(guild, '👋 Boas-vindas', 'Configure o canal dedicado e, opcionalmente, uma saudação curta também no chat principal.', banner=False)
    e.add_field(name='Canal dedicado', value=f"<#{s['welcome_channel']}>" if s.get('welcome_channel') else 'Não configurado')
    e.add_field(name='Chat principal', value=(f"<#{s['welcome_chat_channel']}>\n{'🟢 Ativado' if enabled else '⚫ Desativado'}" if s.get('welcome_chat_channel') else 'Não configurado\n⚫ Desativado'))
    e.add_field(name='Cargo automático', value=f"<@&{s['auto_role']}>" if s.get('auto_role') else 'Nenhum')
    e.add_field(name='Mensagem do canal dedicado', value=str(s.get('welcome_message') or 'Não configurada')[:1000], inline=False)
    e.add_field(name='Mensagem do chat principal', value=str(s.get('welcome_chat_message') or CHAT_DEFAULT_MESSAGE)[:1000], inline=False)
    e.add_field(name='Variáveis', value='`{user}` `{username}` `{server}` `{id}` `{member_count}`', inline=False)
    return e


async def _send_main_chat_welcome(bot, member):
    center = getattr(bot, 'center', None)
    if center is None or member.bot:
        return
    try:
        settings = center.settings(member.guild.id)
        if not settings.get('welcome_chat_enabled'):
            return
        channel_id = settings.get('welcome_chat_channel')
        if not channel_id:
            return
        channel = member.guild.get_channel(channel_id) or bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except discord.HTTPException:
                LOG.exception('Chat principal de boas-vindas não encontrado no servidor %s', member.guild.id)
                return
        message = center.fmt(str(settings.get('welcome_chat_message') or CHAT_DEFAULT_MESSAGE), member, member.guild)[:2000]
        await channel.send(message)
    except discord.HTTPException:
        LOG.exception('Falha ao enviar boas-vindas no chat principal do servidor %s', member.guild.id)
    except Exception:
        LOG.exception('Falha inesperada nas boas-vindas do chat principal do servidor %s', member.guild.id)


def _install_join_listener():
    global _ORIGINAL_BOT_INIT
    if _ORIGINAL_BOT_INIT is not None:
        return
    _ORIGINAL_BOT_INIT = commands.Bot.__init__
    def bot_init(self, *args, **kwargs):
        _ORIGINAL_BOT_INIT(self, *args, **kwargs)
        if not getattr(self, '_fsociety_welcome_chat_listener', False):
            self._fsociety_welcome_chat_listener = True
            async def welcome_chat_on_member_join(member):
                await _send_main_chat_welcome(self, member)
            self.add_listener(welcome_chat_on_member_join, 'on_member_join')
    commands.Bot.__init__ = bot_init


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    cp.DEFAULTS.setdefault('welcome_chat_enabled', False)
    cp.DEFAULTS.setdefault('welcome_chat_channel', None)
    cp.DEFAULTS.setdefault('welcome_chat_message', CHAT_DEFAULT_MESSAGE)
    router = cp.ConfigRouter
    original_build = router.build
    @staticmethod
    def build(center, guild, owner_id, section, admin=True):
        if section == 'welcome':
            return _welcome_embed(center, guild), WelcomeView(center, owner_id, guild)
        return original_build(center, guild, owner_id, section, admin)
    router.build = build
    _install_join_listener()
    print('[OK] Boas-vindas • canal dedicado + chat principal opcional carregados')
