"""Busca de livros ampliada, concorrente e com interface em cartão."""
import asyncio
import math
import os
import time

import aiohttp
import discord

import book_system
import book_api

_INSTALLED=False
_OPEN_LIBRARY_API='https://openlibrary.org/search.json'
_BATCH=10
_MAX_RESULTS=500
_GOOGLE_KEY=os.getenv('GOOGLE_BOOKS_API_KEY','').strip()
_google_quota_exhausted=False
_MAX_CONCURRENT_REQUESTS=10
_CACHE_TTL=300
_CACHE_MAX=250
_REQUEST_SEMAPHORE=asyncio.Semaphore(_MAX_CONCURRENT_REQUESTS)
_CACHE={}
_INFLIGHT={}
_INFLIGHT_LOCK=asyncio.Lock()
_USER_LAST_SEARCH={}
_USER_COOLDOWN=1.5


def _cover(item):
    images=(item.get('volumeInfo') or {}).get('imageLinks') or {}; return images.get('large') or images.get('medium') or images.get('thumbnail') or images.get('smallThumbnail')
def _source(item):return item.get('_source') or 'Catálogo'
def _availability(item):
    sale=item.get('saleInfo') or {}; access=item.get('accessInfo') or {}; saleability=sale.get('saleability') or 'NOT_FOR_SALE'; price=sale.get('retailPrice') or sale.get('listPrice') or {}
    if saleability=='FREE' or access.get('viewability')=='ALL_PAGES':return 'Gratuito / leitura disponível'
    if saleability=='FOR_SALE':
        amount=price.get('amount'); currency=price.get('currencyCode') or ''; return f'Pago • {amount} {currency}' if amount is not None else 'Pago'
    if sale.get('isEbook'):return 'E-book • preço indisponível na região'
    return 'Disponibilidade conforme a fonte'
def _dedupe(items):
    seen=set(); out=[]
    for item in items:
        info=item.get('volumeInfo') or {}; title=(info.get('title') or '').strip().lower(); authors=tuple(a.strip().lower() for a in (info.get('authors') or [])); key=(title,authors[:2])
        if not title or key in seen:continue
        seen.add(key); out.append(item)
    return out

def _cache_key(query,page,price_filter):return (str(query).strip().casefold(),int(page),str(price_filter))
def _cache_get(key):
    now=time.monotonic(); entry=_CACHE.get(key)
    if not entry:return None
    expires,data=entry
    if expires<=now:_CACHE.pop(key,None); return None
    return data
def _cache_put(key,data):
    now=time.monotonic()
    for k,(expiry,_) in list(_CACHE.items()):
        if expiry<=now:_CACHE.pop(k,None)
    while len(_CACHE)>=_CACHE_MAX:
        try:_CACHE.pop(next(iter(_CACHE)))
        except StopIteration:break
    _CACHE[key]=(now+_CACHE_TTL,data)


async def _open_library(query,page=1,price_filter='all',limit=_BATCH):
    if price_filter=='paid':return {'totalItems':0,'items':[],'_source':'Open Library'}
    fields='key,title,subtitle,author_name,first_publish_year,publish_year,cover_i,edition_count,language,ebook_access,has_fulltext,cover_edition_key,number_of_pages_median,ratings_average,ratings_count,subject'; params={'q':query,'page':max(1,int(page)),'limit':limit,'fields':fields}; timeout=aiohttp.ClientTimeout(total=25); headers={'Accept':'application/json','User-Agent':'FSociety-DiscordBot/1.2'}
    async with _REQUEST_SEMAPHORE:
        async with aiohttp.ClientSession(timeout=timeout,headers=headers) as session:
            async with session.get(_OPEN_LIBRARY_API,params=params) as response:
                if response.status!=200:raise book_api.BookAPIError(f'Open Library respondeu com erro HTTP {response.status}.')
                data=await response.json(content_type=None)
    docs=data.get('docs') or []
    if price_filter=='free':docs=[d for d in docs if d.get('ebook_access')=='public' or d.get('has_fulltext')]
    items=[]
    for doc in docs:
        item=book_api._open_library_item(doc); info=item['volumeInfo']; info['subtitle']=doc.get('subtitle'); info['pageCount']=doc.get('number_of_pages_median')
        if doc.get('ratings_average') is not None:info['averageRating']=round(float(doc['ratings_average']),1)
        if doc.get('ratings_count') is not None:info['ratingsCount']=int(doc['ratings_count'])
        if doc.get('subject'):info['categories']=(doc.get('subject') or [])[:5]
        items.append(item)
    return {'totalItems':min(int(data.get('numFound') or len(items)),_MAX_RESULTS),'items':items,'_source':'Open Library'}


async def _google(query,page=1,price_filter='all',limit=_BATCH):
    global _google_quota_exhausted
    if _google_quota_exhausted and not _GOOGLE_KEY:raise book_api.BookAPIError('A cota diária do Google Books foi atingida.')
    params={'q':query,'filter':book_system._google_filter(price_filter),'printType':'books','maxResults':limit,'startIndex':max(0,(page-1)*limit),'orderBy':'relevance'}
    if _GOOGLE_KEY:params['key']=_GOOGLE_KEY
    timeout=aiohttp.ClientTimeout(total=25); headers={'Accept':'application/json','User-Agent':'FSociety-DiscordBot/1.2'}
    async with _REQUEST_SEMAPHORE:
        async with aiohttp.ClientSession(timeout=timeout,headers=headers) as session:
            async with session.get(book_system._API,params=params) as response:
                if response.status==200:
                    data=await response.json(content_type=None); data['_source']='Google Books'
                    for item in data.get('items') or []:item['_source']='Google Books'
                    return data
                if response.status==429:_google_quota_exhausted=True; raise book_api.BookAPIError('A cota diária do Google Books foi atingida.')
                raise book_api.BookAPIError(f'Google Books respondeu com erro HTTP {response.status}.')


async def _fetch_books_uncached(query,page=1,price_filter='all'):
    if price_filter=='paid':return await _google(query,page,price_filter)
    primary=await _open_library(query,page,price_filter); items=list(primary.get('items') or [])
    if _GOOGLE_KEY:
        try:google=await _google(query,page,price_filter); items=_dedupe(items+list(google.get('items') or []))[:_BATCH]
        except book_api.BookAPIError:pass
    primary['items']=items; primary['_source']='Open Library' if not _GOOGLE_KEY else 'Open Library + Google Books'; return primary


async def _fetch_books(query,page=1,price_filter='all'):
    key=_cache_key(query,page,price_filter); cached=_cache_get(key)
    if cached is not None:return cached
    async with _INFLIGHT_LOCK:
        task=_INFLIGHT.get(key)
        if task is None:task=asyncio.create_task(_fetch_books_uncached(query,page,price_filter)); _INFLIGHT[key]=task
    try:data=await asyncio.shield(task); _cache_put(key,data); return data
    finally:
        if task.done():
            async with _INFLIGHT_LOCK:
                if _INFLIGHT.get(key) is task:_INFLIGHT.pop(key,None)


class EnhancedBookView(discord.ui.View):
    def __init__(self,*,query,items,total_count,price_filter='all',page=1,source='Catálogo',owner_id=None):
        super().__init__(timeout=600); self.query=query; self.items=items; self.total_count=min(int(total_count or len(items)),_MAX_RESULTS); self.price_filter=price_filter; self.api_page=max(1,page); self.index=0; self.source=source; self.message=None; self.loading=False; self.owner_id=owner_id; self.filter_select=book_system.BookFilterSelect(self); self.add_item(self.filter_select); self._sync()
    async def interaction_check(self,i):
        if self.owner_id and i.user.id!=self.owner_id:await i.response.send_message('Essa pesquisa pertence a outro usuário. Use `/livro pesquisar` para abrir a sua.',ephemeral=True); return False
        return True
    @property
    def absolute_index(self):return (self.api_page-1)*_BATCH+self.index
    @property
    def total_pages(self):return max(1,math.ceil(self.total_count/_BATCH))
    def _sync(self):
        pos=self.absolute_index; self.first.disabled=self.loading or pos<=0; self.previous.disabled=self.loading or pos<=0; self.next.disabled=self.loading or pos>=self.total_count-1; self.last.disabled=self.loading or pos>=self.total_count-1; self.counter.label=f'{pos+1}/{max(1,self.total_count)}'; self.filter_select.disabled=self.loading
    def embed(self):
        item=self.items[self.index]; info=item.get('volumeInfo') or {}; sale=item.get('saleInfo') or {}; access=item.get('accessInfo') or {}; title=info.get('title') or 'Sem título'; subtitle=info.get('subtitle'); authors=', '.join(info.get('authors') or ['Autor não informado']); published=info.get('publishedDate') or 'N/D'; language=(info.get('language') or 'N/D').upper(); pages=info.get('pageCount') or 'N/D'; rating=info.get('averageRating'); ratings=info.get('ratingsCount'); rating_text='N/D' if rating is None else f'{rating}/5'+(f' ({ratings})' if ratings else ''); desc=f'**{discord.utils.escape_mentions(title)}**'+(f'\n*{discord.utils.escape_mentions(str(subtitle))}*' if subtitle else '')+f'\n\n**Autor:** {discord.utils.escape_mentions(authors)}'; e=discord.Embed(title='F SOCIETY // LIVRO',description=desc[:4096],color=0x991B1B); e.add_field(name='Informações',value=f'`Data {published}`\n`Idioma {language}`\n`Páginas {pages}`\n`Avaliação {rating_text}`',inline=True); e.add_field(name='Disponibilidade',value=_availability(item),inline=True)
        if info.get('categories'):e.add_field(name='Assuntos',value=', '.join(str(x) for x in info['categories'][:5])[:1024],inline=False)
        links=[]
        if access.get('webReaderLink'):links.append(f'[Ler/visualizar]({access["webReaderLink"]})')
        if sale.get('buyLink'):links.append(f'[Comprar]({sale["buyLink"]})')
        if info.get('infoLink'):links.append(f'[Detalhes]({info["infoLink"]})')
        if links:e.add_field(name='Links',value=' • '.join(links),inline=False)
        cover=_cover(item)
        if cover:e.set_thumbnail(url=cover.replace('http://','https://'))
        e.set_footer(text=f'Resultado {self.absolute_index+1}/{max(1,self.total_count)} • {_source(item)} • filtro {book_system._filter_label(self.price_filter)}'); return e
    async def _reload(self,i,api_page,index,price_filter=None):
        self.loading=True; self._sync(); await i.response.edit_message(view=self); target_filter=price_filter or self.price_filter
        try:data=await _fetch_books(self.query,api_page,target_filter); items=data.get('items') or []; 
        except Exception as exc:self.loading=False; self._sync(); await i.edit_original_response(view=self); return await i.followup.send(f'Não consegui carregar os livros: `{type(exc).__name__}`.',ephemeral=True)
        if not items:self.loading=False; self._sync(); await i.edit_original_response(view=self); return await i.followup.send('Nenhum livro encontrado nessa página.',ephemeral=True)
        self.items=items; self.total_count=min(int(data.get('totalItems') or len(items)),_MAX_RESULTS); self.api_page=max(1,api_page); self.index=max(0,min(index,len(items)-1)); self.price_filter=target_filter; self.source=data.get('_source') or 'Catálogo'; self.loading=False; self._sync(); await i.edit_original_response(embed=self.embed(),view=self)
    async def apply_filter(self,i,value):await self._reload(i,1,0,value)
    @discord.ui.button(label='Primeiro',style=discord.ButtonStyle.secondary)
    async def first(self,i,b):await self._reload(i,1,0)
    @discord.ui.button(label='Anterior',style=discord.ButtonStyle.secondary)
    async def previous(self,i,b):
        if self.index>0:self.index-=1; self._sync(); return await i.response.edit_message(embed=self.embed(),view=self)
        await self._reload(i,max(1,self.api_page-1),_BATCH-1)
    @discord.ui.button(label='1/1',style=discord.ButtonStyle.danger,disabled=True)
    async def counter(self,i,b):pass
    @discord.ui.button(label='Próximo',style=discord.ButtonStyle.secondary)
    async def next(self,i,b):
        if self.index+1<len(self.items):self.index+=1; self._sync(); return await i.response.edit_message(embed=self.embed(),view=self)
        await self._reload(i,self.api_page+1,0)
    @discord.ui.button(label='Último lote',style=discord.ButtonStyle.secondary)
    async def last(self,i,b):await self._reload(i,self.total_pages,0)


async def _run_search(i,termo,filtro='default'):
    allowed=book_system.get_book_channel(i.client,i.guild.id) if i.guild else None
    if allowed and i.channel_id!=allowed:return await i.response.send_message(f'A pesquisa de livros está configurada para <#{allowed}>.',ephemeral=True)
    now=time.monotonic(); last=_USER_LAST_SEARCH.get(i.user.id,0.0)
    if now-last<_USER_COOLDOWN:return await i.response.send_message('Aguarde um instante antes de pesquisar novamente.',ephemeral=True)
    _USER_LAST_SEARCH[i.user.id]=now; selected=book_system.get_book_filter(i.client,i.guild.id) if filtro=='default' and i.guild else (filtro if filtro!='default' else 'all'); await i.response.defer(thinking=True)
    try:data=await _fetch_books(termo,1,selected)
    except book_api.BookAPIError as exc:return await i.followup.send(str(exc),ephemeral=True)
    items=data.get('items') or []
    if not items:return await i.followup.send('Nenhum livro encontrado para essa pesquisa.')
    view=EnhancedBookView(query=termo,items=items,total_count=data.get('totalItems'),price_filter=selected,source=data.get('_source') or 'Catálogo',owner_id=i.user.id); msg=await i.followup.send(embed=view.embed(),view=view,wait=True); view.message=msg


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; book_system._fetch_books=_fetch_books; book_system._run_search=_run_search; book_system.BookSearchView=EnhancedBookView; print('[OK] Livros • busca ampliada, cache e cartões carregados')
