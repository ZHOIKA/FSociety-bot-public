"""Paginação dinâmica para /ferramentas pesquisar.

Busca somente metadados públicos de repositórios no GitHub. Cada página é carregada
sob demanda para reduzir consumo de memória e evitar baixar centenas de resultados de uma vez.
"""
import math
from datetime import datetime, timezone

import aiohttp
import discord

import tool_alerts

_PAGE_SIZE = 5
_GITHUB_SEARCH_CAP = 1000
_STAR_FILTERS = (
    (0, 'Todas as estrelas', 'Sem mínimo de estrelas'),
    (10, '10+ estrelas', 'Projetos com pelo menos 10 estrelas'),
    (100, '100+ estrelas', 'Projetos com pelo menos 100 estrelas'),
    (1000, '1.000+ estrelas', 'Projetos com pelo menos 1 mil estrelas'),
    (10000, '10.000+ estrelas', 'Projetos com pelo menos 10 mil estrelas'),
    (50000, '50.000+ estrelas', 'Projetos com pelo menos 50 mil estrelas'),
)


def _compact_number(value):
    value = int(value or 0)
    if value >= 1_000_000:
        return f'{value / 1_000_000:.1f}M'
    if value >= 1_000:
        return f'{value / 1_000:.1f}k'
    return str(value)


def _updated_text(item):
    raw = item.get('updated_at')
    if not raw:
        return 'indisponível'
    try:
        stamp = int(datetime.fromisoformat(raw.replace('Z', '+00:00')).timestamp())
        return f'<t:{stamp}:R>'
    except ValueError:
        return 'indisponível'


def _tool_field(item, position):
    full_name = item.get('full_name') or item.get('name') or 'projeto'
    url = item.get('html_url') or 'https://github.com'
    desc = discord.utils.escape_mentions(
        (item.get('description') or 'Sem descrição publicada.').replace('\n', ' ').strip()
    )[:300]
    language = item.get('language') or 'N/D'
    license_data = item.get('license') or {}
    license_name = license_data.get('spdx_id') or license_data.get('name') or 'N/D'
    stars = _compact_number(item.get('stargazers_count'))
    forks = _compact_number(item.get('forks_count'))
    issues = _compact_number(item.get('open_issues_count'))
    archived = 'Sim' if item.get('archived') else 'Não'
    value = (
        f'{desc}\n'
        f'`Stars {stars}`  `Forks {forks}`  `Issues {issues}`\n'
        f'`Lang {language}`  `Licença {license_name}`  `Arquivado {archived}`\n'
        f'Atualizado {_updated_text(item)} • [Abrir repositório]({url})'
    )
    return f'{position:03d} // {full_name}', value[:1024]


def _star_label(value):
    value = int(value or 0)
    if value <= 0:
        return 'Todas'
    return f'{value:,}+'.replace(',', '.')


async def _fetch_page(query, page, min_stars=0):
    timeout = aiohttp.ClientTimeout(total=25)
    star_filter = f' stars:>={int(min_stars)}' if min_stars else ''
    async with aiohttp.ClientSession(timeout=timeout) as session:
        return await tool_alerts._github_json(
            session,
            '/search/repositories',
            params={
                'q': f'{query} security in:name,description,topics{star_filter}',
                'sort': 'stars',
                'order': 'desc',
                'per_page': _PAGE_SIZE,
                'page': page,
            },
        )


class StarFilterSelect(discord.ui.Select):
    def __init__(self, owner):
        self.owner = owner
        options = [
            discord.SelectOption(
                label=label,
                description=description,
                value=str(value),
                default=value == owner.min_stars,
            )
            for value, label, description in _STAR_FILTERS
        ]
        super().__init__(
            placeholder='Filtrar por quantidade de estrelas',
            min_values=1,
            max_values=1,
            options=options,
            custom_id='fsociety:tools:dynamic:stars',
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.owner._apply_star_filter(interaction, int(self.values[0]))


class DynamicToolSearchView(discord.ui.View):
    def __init__(self, *, query, items, total_count, page=1, min_stars=0):
        super().__init__(timeout=600)
        self.query = query
        self.items = items
        self.total_count = int(total_count or len(items))
        self.accessible_count = min(self.total_count, _GITHUB_SEARCH_CAP)
        self.page = max(1, page)
        self.min_stars = max(0, int(min_stars or 0))
        self.message = None
        self.loading = False
        self.star_select = StarFilterSelect(self)
        self.add_item(self.star_select)
        self._sync_buttons()

    @property
    def pages(self):
        return max(1, math.ceil(self.accessible_count / _PAGE_SIZE))

    def _sync_buttons(self):
        self.first.disabled = self.loading or self.page <= 1
        self.previous.disabled = self.loading or self.page <= 1
        self.next.disabled = self.loading or self.page >= self.pages
        self.last.disabled = self.loading or self.page >= self.pages
        self.counter.label = f'{self.page}/{self.pages}'
        self.star_select.disabled = self.loading
        for option in self.star_select.options:
            option.default = option.value == str(self.min_stars)

    def embed(self):
        start = (self.page - 1) * _PAGE_SIZE
        embed = discord.Embed(
            title='F SOCIETY // TOOL SEARCH',
            description=(
                f'**Consulta:** `{discord.utils.escape_markdown(self.query)}`\n'
                f'**Filtro de estrelas:** `{_star_label(self.min_stars)}`\n'
                f'**Resultados encontrados:** {self.total_count:,}\n'
                f'**Navegáveis pela API:** {self.accessible_count:,}\n\n'
                'As páginas e os filtros são carregados sob demanda diretamente do GitHub.'
            ),
            color=0xB91C1C,
            timestamp=datetime.now(timezone.utc),
        )
        for offset, item in enumerate(self.items, start=start + 1):
            name, value = _tool_field(item, offset)
            embed.add_field(name=name, value=value, inline=False)
        end = start + len(self.items)
        embed.set_footer(
            text=(
                f'Página {self.page}/{self.pages} • resultados {start + 1}-{end} '
                f'• estrelas {_star_label(self.min_stars)} • navegação pública'
            )
        )
        return embed

    async def _load(self, interaction, target_page):
        if self.loading:
            return await interaction.response.send_message('Uma página já está sendo carregada.', ephemeral=True)
        target_page = max(1, min(self.pages, target_page))
        if target_page == self.page:
            return await interaction.response.defer()

        self.loading = True
        self._sync_buttons()
        await interaction.response.edit_message(view=self)
        try:
            data = await _fetch_page(self.query, target_page, self.min_stars)
            items = (data or {}).get('items') or []
            if not items:
                self.loading = False
                self._sync_buttons()
                return await interaction.edit_original_response(view=self)
            self.items = items
            self.page = target_page
            remote_total = int((data or {}).get('total_count') or self.total_count)
            self.total_count = remote_total
            self.accessible_count = min(remote_total, _GITHUB_SEARCH_CAP)
        except Exception as exc:
            self.loading = False
            self._sync_buttons()
            await interaction.edit_original_response(view=self)
            return await interaction.followup.send(
                f'Não consegui carregar essa página agora: `{type(exc).__name__}`.',
                ephemeral=True,
            )

        self.loading = False
        self._sync_buttons()
        await interaction.edit_original_response(embed=self.embed(), view=self)

    async def _apply_star_filter(self, interaction, min_stars):
        if self.loading:
            return await interaction.response.send_message('A pesquisa já está sendo atualizada.', ephemeral=True)
        min_stars = max(0, int(min_stars or 0))
        if min_stars == self.min_stars:
            return await interaction.response.defer()

        self.loading = True
        self._sync_buttons()
        await interaction.response.edit_message(view=self)
        try:
            data = await _fetch_page(self.query, 1, min_stars)
            items = (data or {}).get('items') or []
            remote_total = int((data or {}).get('total_count') or 0)
            self.min_stars = min_stars
            self.page = 1
            self.items = items
            self.total_count = remote_total
            self.accessible_count = min(remote_total, _GITHUB_SEARCH_CAP)
        except Exception as exc:
            self.loading = False
            self._sync_buttons()
            await interaction.edit_original_response(view=self)
            return await interaction.followup.send(
                f'Não consegui aplicar o filtro agora: `{type(exc).__name__}`.',
                ephemeral=True,
            )

        self.loading = False
        self._sync_buttons()
        if not self.items:
            empty = discord.Embed(
                title='F SOCIETY // TOOL SEARCH',
                description=(
                    f'**Consulta:** `{discord.utils.escape_markdown(self.query)}`\n'
                    f'**Filtro de estrelas:** `{_star_label(self.min_stars)}`\n\n'
                    'Nenhuma ferramenta encontrada com esse filtro.'
                ),
                color=0x7F1D1D,
                timestamp=datetime.now(timezone.utc),
            )
            return await interaction.edit_original_response(embed=empty, view=self)
        await interaction.edit_original_response(embed=self.embed(), view=self)

    @discord.ui.button(label='Primeira', style=discord.ButtonStyle.secondary, custom_id='fsociety:tools:dynamic:first')
    async def first(self, interaction, button):
        await self._load(interaction, 1)

    @discord.ui.button(label='Anterior', style=discord.ButtonStyle.secondary, custom_id='fsociety:tools:dynamic:previous')
    async def previous(self, interaction, button):
        await self._load(interaction, self.page - 1)

    @discord.ui.button(label='1/1', style=discord.ButtonStyle.danger, disabled=True, custom_id='fsociety:tools:dynamic:counter')
    async def counter(self, interaction, button):
        pass

    @discord.ui.button(label='Próxima', style=discord.ButtonStyle.secondary, custom_id='fsociety:tools:dynamic:next')
    async def next(self, interaction, button):
        await self._load(interaction, self.page + 1)

    @discord.ui.button(label='Última', style=discord.ButtonStyle.secondary, custom_id='fsociety:tools:dynamic:last')
    async def last(self, interaction, button):
        await self._load(interaction, self.pages)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def _dynamic_search(interaction, query):
    await interaction.response.defer(thinking=True)
    data = await _fetch_page(query, 1, 0)
    items = (data or {}).get('items') or []
    if not items:
        return await interaction.followup.send('Nenhuma ferramenta encontrada para essa pesquisa.')

    total_count = int((data or {}).get('total_count') or len(items))
    view = DynamicToolSearchView(query=query, items=items, total_count=total_count, min_stars=0)
    message = await interaction.followup.send(
        embed=view.embed(),
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
        wait=True,
    )
    view.message = message


def install():
    """Substitui a busca fixa de tool_alerts pela versão paginada sob demanda."""
    tool_alerts._search_tools = _dynamic_search
