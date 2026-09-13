"""Logs avançados do F SOCIETY: mensagens apagadas e atividade em canais de voz.

O conteúdo das mensagens é mantido somente em memória RAM por no máximo 30 minutos.
Nada deste cache é gravado em SQLite, MongoDB ou arquivo.
"""
import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timezone

import discord
from discord.ext import commands

_INSTALLED = False
_MESSAGE_CACHE = OrderedDict()
_CACHE_TTL = 30 * 60
_CACHE_LIMIT = 5000
_DELETED_RECENTLY = set()


def _purge_expired(now=None):
    now = time.monotonic() if now is None else now
    while _MESSAGE_CACHE:
        _, item = next(iter(_MESSAGE_CACHE.items()))
        if now - item['cached_at'] < _CACHE_TTL:
            break
        _MESSAGE_CACHE.popitem(last=False)


def _remember(message):
    if not getattr(message, 'guild', None) or getattr(message.author, 'bot', False):
        return
    now = time.monotonic()
    _purge_expired(now)
    _MESSAGE_CACHE[message.id] = {
        'cached_at': now,
        'guild_id': message.guild.id,
        'channel_id': message.channel.id,
        'author_id': message.author.id,
        'author_name': str(message.author),
        'author_display': getattr(message.author, 'display_name', str(message.author)),
        'author_avatar': str(message.author.display_avatar.url) if getattr(message.author, 'display_avatar', None) else None,
        'content': message.content or '',
        'attachments': [a.url for a in message.attachments],
        'created_at': message.created_at,
    }
    _MESSAGE_CACHE.move_to_end(message.id)
    while len(_MESSAGE_CACHE) > _CACHE_LIMIT:
        _MESSAGE_CACHE.popitem(last=False)


def _take_cached(message_id):
    _purge_expired()
    item = _MESSAGE_CACHE.pop(message_id, None)
    if item and time.monotonic() - item['cached_at'] < _CACHE_TTL:
        return item
    return None


def _log_channel(client, guild):
    center = getattr(client, 'center', None)
    if center is None:
        return None
    try:
        settings = center.settings(guild.id)
    except Exception:
        return None
    channel_id = settings.get('log_channel')
    return guild.get_channel(channel_id) if channel_id else None


async def _send_deleted_log(client, *, guild, channel, message_id, cached=None, author=None, content=None, attachments=None):
    log_channel = _log_channel(client, guild)
    if not isinstance(log_channel, discord.TextChannel) or log_channel.id == getattr(channel, 'id', None):
        return

    if cached:
        author_id = cached['author_id']
        author_name = cached['author_name']
        avatar = cached['author_avatar']
        text = cached['content']
        files = cached['attachments']
        created_at = cached['created_at']
    else:
        author_id = getattr(author, 'id', None)
        author_name = str(author) if author else 'Autor desconhecido'
        avatar = str(author.display_avatar.url) if author and getattr(author, 'display_avatar', None) else None
        text = content or ''
        files = attachments or []
        created_at = None

    embed = discord.Embed(title='Mensagem apagada', color=0xEF4444, timestamp=datetime.now(timezone.utc))
    if author_id:
        embed.add_field(name='Autor', value=f'<@{author_id}> (`{author_id}`)', inline=False)
    else:
        embed.add_field(name='Autor', value=author_name, inline=False)
    embed.add_field(name='Canal', value=getattr(channel, 'mention', f'<#{getattr(channel, "id", 0)}>'), inline=False)
    embed.add_field(name='Mensagem', value=(discord.utils.escape_mentions(text)[:1000] if text else '*Conteúdo não disponível (fora da janela de 30 minutos)*'), inline=False)
    if files:
        embed.add_field(name='Anexos', value='\n'.join(files[:5])[:1000], inline=False)
    embed.set_footer(text=f'ID da mensagem: {message_id}' + (f' • Enviada em {created_at:%d/%m/%Y %H:%M:%S} UTC' if created_at else ''))
    if avatar:
        embed.set_thumbnail(url=avatar)
    try:
        await log_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


async def _handle_message_delete(client, message):
    if not getattr(message, 'guild', None) or getattr(message.author, 'bot', False):
        return
    _DELETED_RECENTLY.add(message.id)
    cached = _take_cached(message.id)
    await _send_deleted_log(
        client,
        guild=message.guild,
        channel=message.channel,
        message_id=message.id,
        cached=cached,
        author=message.author,
        content=message.content,
        attachments=[a.url for a in message.attachments],
    )
    await asyncio.sleep(5)
    _DELETED_RECENTLY.discard(message.id)


async def _handle_raw_delete(client, payload):
    if payload.message_id in _DELETED_RECENTLY or not payload.guild_id:
        return
    guild = client.get_guild(payload.guild_id)
    if guild is None:
        return
    channel = guild.get_channel(payload.channel_id)
    cached = _take_cached(payload.message_id)
    await _send_deleted_log(client, guild=guild, channel=channel, message_id=payload.message_id, cached=cached)


async def _handle_voice(client, member, before, after):
    if member.bot or before.channel == after.channel:
        return

    log_channel = _log_channel(client, member.guild)
    if not isinstance(log_channel, discord.TextChannel):
        return

    if before.channel is None and after.channel is not None:
        title = 'Entrou em canal de voz'
        description = f'{member.mention} entrou em {after.channel.mention}.'
        color = 0x22C55E
        before_text = 'Nenhum'
        after_text = after.channel.mention
    elif before.channel is not None and after.channel is None:
        title = 'Saiu de canal de voz'
        description = f'{member.mention} saiu de {before.channel.mention}.'
        color = 0xEF4444
        before_text = before.channel.mention
        after_text = 'Nenhum'
    else:
        title = 'Mudou de canal de voz'
        description = f'{member.mention} saiu de {before.channel.mention} e entrou em {after.channel.mention}.'
        color = 0xF59E0B
        before_text = before.channel.mention
        after_text = after.channel.mention

    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))
    embed.add_field(name='Membro', value=f'{member.mention}\n`{member.id}`', inline=False)
    embed.add_field(name='Antes', value=before_text, inline=True)
    embed.add_field(name='Depois', value=after_text, inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)

    try:
        await log_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


async def _cache_cleanup_loop(client):
    await client.wait_until_ready()
    while not client.is_closed():
        _purge_expired()
        await asyncio.sleep(60)


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_dispatch = discord.Client.dispatch
    cleanup_started = False

    def dispatch(self, event, /, *args, **kwargs):
        nonlocal cleanup_started
        try:
            if not cleanup_started:
                asyncio.create_task(_cache_cleanup_loop(self))
                cleanup_started = True
            if event == 'message' and args:
                _remember(args[0])
            elif event == 'message_delete' and args:
                asyncio.create_task(_handle_message_delete(self, args[0]))
            elif event == 'raw_message_delete' and args:
                asyncio.create_task(_handle_raw_delete(self, args[0]))
        except Exception:
            pass
        return original_dispatch(self, event, *args, **kwargs)

    discord.Client.dispatch = dispatch

    original_bot_init = commands.Bot.__init__

    def bot_init(self, *args, **kwargs):
        original_bot_init(self, *args, **kwargs)

        async def fsociety_voice_log_listener(member, before, after):
            try:
                await _handle_voice(self, member, before, after)
            except Exception:
                pass

        self.add_listener(fsociety_voice_log_listener, 'on_voice_state_update')

    commands.Bot.__init__ = bot_init
