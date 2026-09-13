"""Funções extras do F SOCIETY: AFK, lembretes, sugestões e utilitários do servidor."""
import asyncio
import random
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

import discord
from discord import app_commands

import control_panel as cp

_INSTALLED = False
_AFK = {}
_REMINDERS = defaultdict(set)
_DURATION_RE = re.compile(r'^(\d+)(s|m|h|d)$', re.I)


def _duration(value: str) -> int:
    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        raise ValueError('Use um tempo como `30s`, `10m`, `2h` ou `1d`.')
    amount = int(match.group(1))
    unit = match.group(2).lower()
    factor = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit]
    seconds = amount * factor
    if seconds < 5 or seconds > 30 * 86400:
        raise ValueError('O lembrete precisa ficar entre 5 segundos e 30 dias.')
    return seconds


def _embed(title: str, description: str, *, color=0xB91C1C):
    return discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(timezone.utc))


async def _remind(client, user_id: int, channel_id: int, seconds: int, text: str):
    task = asyncio.current_task()
    try:
        await asyncio.sleep(seconds)
        channel = client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(channel_id)
            except discord.HTTPException:
                return
        await channel.send(
            f'<@{user_id}> lembrete: **{discord.utils.escape_mentions(text)[:1800]}**',
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    finally:
        if task:
            _REMINDERS[user_id].discard(task)


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_tree_init = cp.CommandTree.__init__

    def tree_init(self, *args, **kwargs):
        original_tree_init(self, *args, **kwargs)

        @app_commands.command(name='afk', description='Marca você como ausente no servidor')
        @app_commands.describe(motivo='Motivo opcional da ausência')
        async def afk(interaction: discord.Interaction, motivo: str = 'AFK'):
            if interaction.guild is None:
                return await interaction.response.send_message('Use este comando em um servidor.', ephemeral=True)
            _AFK[(interaction.guild.id, interaction.user.id)] = {
                'reason': motivo[:200],
                'since': int(time.time()),
            }
            await interaction.response.send_message(
                embed=_embed('MODO AFK', f'{interaction.user.mention} está ausente.\n**Motivo:** {discord.utils.escape_mentions(motivo[:200])}'),
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @app_commands.command(name='afk_status', description='Mostra se um membro está AFK')
        async def afk_status(interaction: discord.Interaction, usuario: discord.Member):
            data = _AFK.get((interaction.guild.id, usuario.id)) if interaction.guild else None
            if not data:
                return await interaction.response.send_message(f'{usuario.mention} não está AFK.', ephemeral=True)
            await interaction.response.send_message(
                embed=_embed('STATUS AFK', f'{usuario.mention}\n**Motivo:** {discord.utils.escape_mentions(data["reason"])}\n**Desde:** <t:{data["since"]}:R>'),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @app_commands.command(name='lembrete', description='Cria um lembrete pessoal')
        @app_commands.describe(tempo='Ex.: 30s, 10m, 2h ou 1d', mensagem='O que devo lembrar')
        async def lembrete(interaction: discord.Interaction, tempo: str, mensagem: app_commands.Range[str, 1, 500]):
            try:
                seconds = _duration(tempo)
            except ValueError as exc:
                return await interaction.response.send_message(str(exc), ephemeral=True)
            if len(_REMINDERS[interaction.user.id]) >= 5:
                return await interaction.response.send_message('Você já possui 5 lembretes ativos.', ephemeral=True)
            task = asyncio.create_task(_remind(interaction.client, interaction.user.id, interaction.channel_id, seconds, mensagem))
            _REMINDERS[interaction.user.id].add(task)
            await interaction.response.send_message(
                f'Lembrete criado para <t:{int(time.time()) + seconds}:R>.', ephemeral=True
            )

        @app_commands.command(name='sugerir', description='Envia uma sugestão para votação no canal atual')
        @app_commands.describe(sugestao='Texto da sugestão')
        async def sugerir(interaction: discord.Interaction, sugestao: app_commands.Range[str, 1, 1500]):
            embed = _embed(
                'NOVA SUGESTÃO',
                f'{discord.utils.escape_mentions(sugestao)}\n\nEnviado por {interaction.user.mention}',
                color=0x7F1D1D,
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            message = await interaction.original_response()
            try:
                await message.add_reaction('👍')
                await message.add_reaction('👎')
            except discord.HTTPException:
                pass

        @app_commands.command(name='moeda', description='Gira uma moeda e retorna cara ou coroa')
        async def moeda(interaction: discord.Interaction):
            resultado = random.choice(('Cara', 'Coroa'))
            simbolo = '🪙' if resultado == 'Cara' else '🌑'
            embed = _embed(
                'CARA OU COROA',
                f'{simbolo} {interaction.user.mention} girou a moeda...\n\n**Resultado: {resultado.upper()}!**',
                color=0xF59E0B,
            )
            embed.set_footer(text='F SOCIETY • 50% cara • 50% coroa')
            await interaction.response.send_message(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @app_commands.command(name='roleinfo', description='Mostra informações de um cargo')
        async def roleinfo(interaction: discord.Interaction, cargo: discord.Role):
            permissions = [name.replace('_', ' ') for name, enabled in cargo.permissions if enabled]
            desc = (
                f'**Nome:** {discord.utils.escape_markdown(cargo.name)}\n'
                f'**ID:** `{cargo.id}`\n'
                f'**Membros:** {len(cargo.members)}\n'
                f'**Posição:** {cargo.position}\n'
                f'**Cor:** `{cargo.color}`\n'
                f'**Criado:** <t:{int(cargo.created_at.timestamp())}:F>\n'
                f'**Permissões principais:** {", ".join(permissions[:12]) or "Nenhuma"}'
            )
            await interaction.response.send_message(embed=_embed('INFORMAÇÕES DO CARGO', desc), ephemeral=True)

        @app_commands.command(name='canalinfo', description='Mostra informações do canal atual ou de outro canal')
        async def canalinfo(interaction: discord.Interaction, canal: discord.TextChannel | None = None):
            canal = canal or interaction.channel
            if not isinstance(canal, discord.TextChannel):
                return await interaction.response.send_message('Selecione um canal de texto.', ephemeral=True)
            desc = (
                f'**Canal:** {canal.mention}\n'
                f'**ID:** `{canal.id}`\n'
                f'**Categoria:** {canal.category.mention if canal.category else "Sem categoria"}\n'
                f'**Modo lento:** {canal.slowmode_delay}s\n'
                f'**NSFW:** {"Sim" if canal.is_nsfw() else "Não"}\n'
                f'**Criado:** <t:{int(canal.created_at.timestamp())}:F>'
            )
            await interaction.response.send_message(embed=_embed('INFORMAÇÕES DO CANAL', desc), ephemeral=True)

        @app_commands.command(name='serverstats', description='Mostra estatísticas rápidas do servidor')
        async def serverstats(interaction: discord.Interaction):
            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message('Use este comando em um servidor.', ephemeral=True)
            humans = sum(1 for m in guild.members if not m.bot)
            bots = sum(1 for m in guild.members if m.bot)
            online = sum(1 for m in guild.members if m.status is not discord.Status.offline)
            voice = sum(1 for m in guild.members if m.voice and m.voice.channel)
            desc = (
                f'**Membros:** {guild.member_count}\n'
                f'**Pessoas:** {humans}\n'
                f'**Bots:** {bots}\n'
                f'**Online:** {online}\n'
                f'**Em voz:** {voice}\n'
                f'**Canais:** {len(guild.channels)}\n'
                f'**Cargos:** {len(guild.roles)}\n'
                f'**Boosts:** {guild.premium_subscription_count}'
            )
            embed = _embed(f'STATS // {guild.name}', desc)
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            await interaction.response.send_message(embed=embed)

        for command in (afk, afk_status, lembrete, sugerir, moeda, roleinfo, canalinfo, serverstats):
            self.add_command(command)

    cp.CommandTree.__init__ = tree_init

    original_dispatch = discord.Client.dispatch

    def dispatch(self, event, /, *args, **kwargs):
        if event == 'message' and args:
            message = args[0]
            try:
                if message.guild and not message.author.bot:
                    key = (message.guild.id, message.author.id)
                    data = _AFK.pop(key, None)
                    if data:
                        asyncio.create_task(message.channel.send(
                            f'{message.author.mention}, seu modo AFK foi removido.',
                            delete_after=6,
                            allowed_mentions=discord.AllowedMentions.none(),
                        ))
                    mentioned = []
                    for member in message.mentions:
                        afk_data = _AFK.get((message.guild.id, member.id))
                        if afk_data and member.id != message.author.id:
                            mentioned.append(
                                f'{member.display_name} está AFK desde <t:{afk_data["since"]}:R>: '
                                f'{discord.utils.escape_mentions(afk_data["reason"])}'
                            )
                    if mentioned:
                        asyncio.create_task(message.channel.send(
                            '\n'.join(mentioned)[:1800], delete_after=10,
                            allowed_mentions=discord.AllowedMentions.none(),
                        ))
            except Exception:
                pass
        return original_dispatch(self, event, *args, **kwargs)

    discord.Client.dispatch = dispatch
