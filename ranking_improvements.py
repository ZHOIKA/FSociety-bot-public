"""Ranking interativo do F SOCIETY."""
import math

import discord
from discord import app_commands
from discord.ext import commands

_INSTALLED = False

CATEGORIES = {
    'xp': ('XP', 'xp', 'level', 'XP'),
    'coins': ('Moedas', 'coins', None, 'moedas'),
    'messages': ('Mensagens', 'messages', None, 'mensagens'),
}
PAGE_SIZE = 10
MAX_ROWS = 100


def _query(center, guild_id, category):
    _, column, _, _ = CATEGORIES[category]
    return center.q(
        f'SELECT user_id, level, xp, coins, messages FROM users '
        f'WHERE guild_id=? ORDER BY {column} DESC, user_id ASC LIMIT ?',
        (guild_id, MAX_ROWS),
    )


def _position(center, guild_id, user_id, category):
    _, column, _, _ = CATEGORIES[category]
    row = center.q(
        f'SELECT 1 + COUNT(*) AS pos FROM users AS u '
        f'WHERE u.guild_id=? AND ('
        f'u.{column} > COALESCE((SELECT t.{column} FROM users AS t WHERE t.guild_id=? AND t.user_id=?),0) '
        f'OR (u.{column} = COALESCE((SELECT t.{column} FROM users AS t WHERE t.guild_id=? AND t.user_id=?),0) '
        f'AND u.user_id < ?))',
        (guild_id, guild_id, user_id, guild_id, user_id, user_id),
        True,
    )
    return int(row['pos']) if row else 1


def _value_line(row, category):
    if category == 'xp':
        return f'nível **{row["level"]}** · **{row["xp"]:,} XP**'.replace(',', '.')
    if category == 'coins':
        return f'**{row["coins"]:,} moedas**'.replace(',', '.')
    return f'**{row["messages"]:,} mensagens**'.replace(',', '.')


def ranking_embed(center, guild, viewer_id, category='xp', page=0):
    rows = _query(center, guild.id, category)
    total_pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    current = rows[start:start + PAGE_SIZE]
    title, _, _, unit = CATEGORIES[category]

    lines = []
    for index, row in enumerate(current, start=start + 1):
        marker = '🥇' if index == 1 else '🥈' if index == 2 else '🥉' if index == 3 else f'**{index}.**'
        lines.append(f'{marker} <@{row["user_id"]}> — {_value_line(row, category)}')

    embed = discord.Embed(
        title=f'Ranking • {title}',
        description='\n'.join(lines) if lines else 'Ainda não há dados suficientes para este ranking.',
        color=0x111827,
    )
    position = _position(center, guild.id, viewer_id, category)
    viewer = center.q(
        'SELECT level,xp,coins,messages FROM users WHERE guild_id=? AND user_id=?',
        (guild.id, viewer_id), True,
    )
    if viewer:
        value = viewer['xp'] if category == 'xp' else viewer['coins'] if category == 'coins' else viewer['messages']
        extra = f' • nível {viewer["level"]}' if category == 'xp' else ''
        embed.add_field(
            name='Sua posição',
            value=f'**#{position}** • **{value:,} {unit}**{extra}'.replace(',', '.'),
            inline=False,
        )
    embed.set_footer(text=f'Página {page + 1}/{total_pages} • até {MAX_ROWS} membros • F SOCIETY')
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed, total_pages


class RankingSelect(discord.ui.Select):
    def __init__(self, view):
        self.ranking_view = view
        options = [
            discord.SelectOption(label='Ranking de XP', value='xp', description='Níveis e experiência'),
            discord.SelectOption(label='Ranking de moedas', value='coins', description='Maiores saldos do servidor'),
            discord.SelectOption(label='Ranking de mensagens', value='messages', description='Membros mais ativos'),
        ]
        super().__init__(placeholder='Escolha o tipo de ranking', options=options, row=0)

    async def callback(self, interaction):
        if interaction.user.id != self.ranking_view.owner_id:
            return await interaction.response.send_message('Abra seu próprio ranking com `/ranking`.', ephemeral=True)
        self.ranking_view.category = self.values[0]
        self.ranking_view.page = 0
        await self.ranking_view.refresh(interaction)


class RankingView(discord.ui.View):
    def __init__(self, center, guild, owner_id, category='xp'):
        super().__init__(timeout=300)
        self.center = center
        self.guild = guild
        self.owner_id = owner_id
        self.category = category
        self.page = 0
        self.add_item(RankingSelect(self))
        self._sync_buttons()

    def _sync_buttons(self):
        _, total_pages = ranking_embed(self.center, self.guild, self.owner_id, self.category, self.page)
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= total_pages - 1

    async def refresh(self, interaction):
        embed, total_pages = ranking_embed(self.center, self.guild, self.owner_id, self.category, self.page)
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= total_pages - 1
        await interaction.response.edit_message(embed=embed, view=self)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('Este ranking pertence a quem abriu o painel.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Anterior', style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction, button):
        self.page = max(0, self.page - 1)
        await self.refresh(interaction)

    @discord.ui.button(label='Próxima', style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction, button):
        self.page += 1
        await self.refresh(interaction)


async def _slash_ranking(interaction):
    bot = interaction.client
    center = bot.center
    embed, _ = ranking_embed(center, interaction.guild, interaction.user.id, 'xp', 0)
    await interaction.response.send_message(
        embed=embed,
        view=RankingView(center, interaction.guild, interaction.user.id),
    )


async def _prefix_ranking(ctx):
    center = ctx.bot.center
    embed, _ = ranking_embed(center, ctx.guild, ctx.author.id, 'xp', 0)
    await ctx.send(embed=embed, view=RankingView(center, ctx.guild, ctx.author.id))


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_tree_command = app_commands.CommandTree.command
    def tree_command(self, *args, **kwargs):
        decorator = original_tree_command(self, *args, **kwargs)
        def wrapped(func):
            command = decorator(func)
            if command.name == 'ranking':
                command._callback = _slash_ranking
                command.description = 'Ranking interativo de XP, moedas e mensagens'
            return command
        return wrapped
    app_commands.CommandTree.command = tree_command

    original_add_command = commands.Bot.add_command
    def add_command(self, command, /, *args, **kwargs):
        if command.name == 'ranking':
            command.callback = _prefix_ranking
        return original_add_command(self, command, *args, **kwargs)
    commands.Bot.add_command = add_command
