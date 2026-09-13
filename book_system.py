"""Pesquisa legal de livros para o F SOCIETY usando fontes oficiais."""
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands

import control_panel as cp
import tool_config_panel

_INSTALLED=False
_API='https://www.googleapis.com/books/v1/volumes'
_PAGE_SIZE=5
_MAX_NAVIGABLE=200
BOOK_FILTERS=(('all','Todos','Livros gratuitos e pagos'),('free','Gratuitos','Somente e-books gratuitos'),('paid','Pagos','Somente e-books pagos'))


def _db(client):return tool_config_panel._db(client)
def _migrate(client):
    with closing(sqlite3.connect(_db(client))) as con,con:
        con.execute('CREATE TABLE IF NOT EXISTS book_search_preferences(guild_id INTEGER PRIMARY KEY,price_filter TEXT NOT NULL DEFAULT "all",channel_id INTEGER)')
        cols={r[1] for r in con.execute('PRAGMA table_info(book_search_preferences)')}
        if 'channel_id' not in cols:con.execute('ALTER TABLE book_search_preferences ADD COLUMN channel_id INTEGER')
def get_book_filter(client,guild_id):
    _migrate(client)
    with closing(sqlite3.connect(_db(client))) as con:r=con.execute('SELECT price_filter FROM book_search_preferences WHERE guild_id=?',(guild_id,)).fetchone()
    value=str(r[0]) if r else 'all'; return value if value in {'all','free','paid'} else 'all'
def set_book_filter(client,guild_id,value):
    value=value if value in {'all','free','paid'} else 'all'; _migrate(client)
    with closing(sqlite3.connect(_db(client))) as con,con:con.execute('INSERT INTO book_search_preferences(guild_id,price_filter) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET price_filter=excluded.price_filter',(guild_id,value))
def get_book_channel(client,guild_id):
    _migrate(client)
    with closing(sqlite3.connect(_db(client))) as con:r=con.execute('SELECT channel_id FROM book_search_preferences WHERE guild_id=?',(guild_id,)).fetchone()
    return int(r[0]) if r and r[0] else None
def set_book_channel(client,guild_id,channel_id):
    _migrate(client)
    with closing(sqlite3.connect(_db(client))) as con,con:con.execute('INSERT INTO book_search_preferences(guild_id,channel_id) VALUES(?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id',(guild_id,int(channel_id) if channel_id else None))
def _filter_label(value):return {'all':'Todos','free':'Gratuitos','paid':'Pagos'}.get(value,'Todos')
def _google_filter(value):return {'all':'ebooks','free':'free-ebooks','paid':'paid-ebooks'}.get(value,'ebooks')


async def _fetch_books(query,page=1,price_filter='all'):
    start=max(0,(page-1)*_PAGE_SIZE); timeout=aiohttp.ClientTimeout(total=25); params={'q':query,'filter':_google_filter(price_filter),'printType':'books','maxResults':_PAGE_SIZE,'startIndex':start,'orderBy':'relevance'}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(_API,params=params) as response:response.raise_for_status(); return await response.json()


def _book_field(item,position):
    info=item.get('volumeInfo') or {}; sale=item.get('saleInfo') or {}; access=item.get('accessInfo') or {}; title=(info.get('title') or 'Sem título').strip(); subtitle=(info.get('subtitle') or '').strip(); authors=', '.join(info.get('authors') or ['Autor não informado']); published=info.get('publishedDate') or 'N/D'; language=(info.get('language') or 'N/D').upper(); pages=info.get('pageCount') or 'N/D'; rating=info.get('averageRating'); ratings=info.get('ratingsCount'); saleability=sale.get('saleability') or 'NOT_FOR_SALE'; price=sale.get('retailPrice') or sale.get('listPrice') or {}; amount=price.get('amount'); currency=price.get('currencyCode') or ''
    if saleability=='FREE' or access.get('viewability')=='ALL_PAGES':availability='Gratuito / leitura disponível'
    elif saleability=='FOR_SALE':availability=f'Pago • {amount} {currency}' if amount is not None else 'Pago'
    elif sale.get('isEbook'):availability='E-book • preço indisponível na região'
    else:availability='Visualização/compra conforme disponibilidade regional'
    links=[]
    if access.get('webReaderLink'):links.append(f'[Ler/visualizar]({access["webReaderLink"]})')
    if sale.get('buyLink'):links.append(f'[Comprar]({sale["buyLink"]})')
    if info.get('infoLink'):links.append(f'[Detalhes]({info["infoLink"]})')
    rating_text='N/D' if rating is None else f'{rating}/5'+(f' ({ratings})' if ratings else '')
    clean=discord.utils.escape_mentions(title)[:180]
    if subtitle:clean+=f' — {discord.utils.escape_mentions(subtitle)[:120]}'
    value=f'**Autor:** {discord.utils.escape_mentions(authors)[:250]}\n`Ano/Data {published}`  `Idioma {language}`  `Páginas {pages}`\n`Avaliação {rating_text}`\n**Disponibilidade:** {availability}\n'+(' • '.join(links) if links else 'Sem link oficial disponível para esta região.')
    return f'{position:03d} // {clean}',value[:1024]


class BookFilterSelect(discord.ui.Select):
    def __init__(self,owner):self.owner=owner; super().__init__(placeholder='Filtrar livros por preço',options=[discord.SelectOption(label=l,description=d,value=v,default=v==owner.price_filter) for v,l,d in BOOK_FILTERS],row=1)
    async def callback(self,i):await self.owner.apply_filter(i,self.values[0])


class BookSearchView(discord.ui.View):
    def __init__(self,*,query,items,total_count,price_filter='all',page=1,owner_id=None):
        super().__init__(timeout=600); self.query=query; self.items=items; self.total_count=int(total_count or len(items)); self.accessible_count=min(self.total_count,_MAX_NAVIGABLE); self.price_filter=price_filter; self.page=max(1,page); self.owner_id=owner_id; self.loading=False; self.message=None; self.filter_select=BookFilterSelect(self); self.add_item(self.filter_select); self._sync()
    @property
    def pages(self):return max(1,math.ceil(self.accessible_count/_PAGE_SIZE))
    async def interaction_check(self,i):
        if self.owner_id and i.user.id!=self.owner_id:await i.response.send_message('Essa pesquisa pertence a outro usuário.',ephemeral=True); return False
        return True
    def _sync(self):
        self.first.disabled=self.loading or self.page<=1; self.previous.disabled=self.loading or self.page<=1; self.next.disabled=self.loading or self.page>=self.pages; self.last.disabled=self.loading or self.page>=self.pages; self.counter.label=f'{self.page}/{self.pages}'; self.filter_select.disabled=self.loading
    def embed(self):
        start=(self.page-1)*_PAGE_SIZE; e=discord.Embed(title='F SOCIETY // LIVROS',description=f'**Pesquisa:** `{discord.utils.escape_markdown(self.query)}`\n**Filtro:** `{_filter_label(self.price_filter)}`\n**Resultados encontrados:** {self.total_count:,}\n\nResultados e links fornecidos por fontes oficiais.',color=0x991B1B,timestamp=datetime.now(timezone.utc))
        for off,item in enumerate(self.items,start=start+1):name,value=_book_field(item,off); e.add_field(name=name,value=value,inline=False)
        e.set_footer(text=f'Página {self.page}/{self.pages} • Google Books'); return e
    async def _load(self,i,target_page,price_filter=None):
        target_filter=price_filter or self.price_filter; self.loading=True; self._sync(); await i.response.edit_message(view=self)
        try:data=await _fetch_books(self.query,target_page,target_filter); self.items=(data or {}).get('items') or []; self.total_count=int((data or {}).get('totalItems') or 0); self.accessible_count=min(self.total_count,_MAX_NAVIGABLE); self.price_filter=target_filter; self.page=max(1,min(target_page,self.pages))
        except Exception as exc:self.loading=False; self._sync(); await i.edit_original_response(view=self); return await i.followup.send(f'Não consegui carregar os livros agora: `{type(exc).__name__}`.',ephemeral=True)
        self.loading=False; self._sync(); await i.edit_original_response(embed=self.embed(),view=self)
    async def apply_filter(self,i,value):await self._load(i,1,value)
    @discord.ui.button(label='Primeira',style=discord.ButtonStyle.secondary)
    async def first(self,i,b):await self._load(i,1)
    @discord.ui.button(label='Anterior',style=discord.ButtonStyle.secondary)
    async def previous(self,i,b):await self._load(i,self.page-1)
    @discord.ui.button(label='1/1',style=discord.ButtonStyle.danger,disabled=True)
    async def counter(self,i,b):pass
    @discord.ui.button(label='Próxima',style=discord.ButtonStyle.secondary)
    async def next(self,i,b):await self._load(i,self.page+1)
    @discord.ui.button(label='Última',style=discord.ButtonStyle.secondary)
    async def last(self,i,b):await self._load(i,self.pages)


class DefaultBookFilterSelect(discord.ui.Select):
    def __init__(self,owner,current):self.owner=owner; super().__init__(placeholder='Filtro padrão da busca de livros',options=[discord.SelectOption(label=f'Livros: {l}',description=d,value=v,default=v==current) for v,l,d in BOOK_FILTERS],row=3)
    async def callback(self,i):set_book_filter(i.client,i.guild.id,self.values[0]); self.owner.rebuild(i.client,i.guild.id); await i.response.edit_message(embed=tool_config_panel._config_embed(i.client,i.guild),view=self.owner)


class BookChannelSelect(discord.ui.ChannelSelect):
    def __init__(self,owner):self.owner=owner; super().__init__(placeholder='Canal permitido para pesquisar livros',channel_types=[discord.ChannelType.text],min_values=1,max_values=1,row=4)
    async def callback(self,i):
        channel=self.values[0]; perms=channel.permissions_for(i.guild.me)
        if not perms.view_channel or not perms.send_messages or not perms.embed_links:return await i.response.send_message('O bot precisa de Ver canal, Enviar mensagens e Inserir links nesse canal.',ephemeral=True)
        set_book_channel(i.client,i.guild.id,channel.id); self.owner.rebuild(i.client,i.guild.id); await i.response.edit_message(embed=tool_config_panel._config_embed(i.client,i.guild),view=self.owner)


def _patch_tool_config():
    old=tool_config_panel.ToolConfigView.rebuild; old_embed=tool_config_panel._config_embed
    def rebuild(self,client,gid):old(self,client,gid); self.add_item(DefaultBookFilterSelect(self,get_book_filter(client,gid))); self.add_item(BookChannelSelect(self))
    def config_embed(client,guild):e=old_embed(client,guild); cid=get_book_channel(client,guild.id); e.add_field(name='Busca de livros',value=f'Filtro padrão: `{_filter_label(get_book_filter(client,guild.id))}`\nCanal de pesquisa: {f"<#{cid}>" if cid else "Todos os canais"}',inline=False); return e
    tool_config_panel.ToolConfigView.rebuild=rebuild; tool_config_panel._config_embed=config_embed


async def _run_search(i,termo,filtro='default'):
    allowed=get_book_channel(i.client,i.guild.id) if i.guild else None
    if allowed and i.channel_id!=allowed:return await i.response.send_message(f'A pesquisa de livros está configurada para <#{allowed}>.',ephemeral=True)
    selected=get_book_filter(i.client,i.guild.id) if filtro=='default' and i.guild else (filtro if filtro!='default' else 'all'); await i.response.defer(thinking=True); data=await _fetch_books(termo,1,selected); items=(data or {}).get('items') or []
    if not items:return await i.followup.send('Nenhum livro encontrado para essa pesquisa.')
    view=BookSearchView(query=termo,items=items,total_count=int((data or {}).get('totalItems') or len(items)),price_filter=selected,owner_id=i.user.id); msg=await i.followup.send(embed=view.embed(),view=view,wait=True); view.message=msg


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; _patch_tool_config(); old=cp.CommandTree.__init__
    def tree_init(self,*a,**kw):
        old(self,*a,**kw)
        if self.get_command('livro') is not None:return
        group=app_commands.Group(name='livro',description='Pesquisa livros em fontes oficiais',guild_only=True)
        @group.command(name='pesquisar',description='Pesquisa livros por título, autor, assunto ou ISBN')
        @app_commands.describe(termo='Título, autor, assunto ou ISBN',filtro='Filtro de disponibilidade')
        @app_commands.choices(filtro=[app_commands.Choice(name='Padrão do servidor',value='default'),app_commands.Choice(name='Todos',value='all'),app_commands.Choice(name='Gratuitos',value='free'),app_commands.Choice(name='Pagos',value='paid')])
        async def pesquisar(i:discord.Interaction,termo:app_commands.Range[str,2,120],filtro:str='default'):
            try:await _run_search(i,termo,filtro)
            except Exception as exc:
                if i.response.is_done():await i.followup.send(f'Não consegui consultar os livros agora: `{type(exc).__name__}`.',ephemeral=True)
                else:await i.response.send_message('Não consegui consultar os livros agora.',ephemeral=True)
        self.add_command(group)
    cp.CommandTree.__init__=tree_init; print('[OK] Livros • pesquisa, filtros e canal configurável carregados')
