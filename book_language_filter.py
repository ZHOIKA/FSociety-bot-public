"""Transforma o filtro do /livro pesquisar em idioma e remove o dropdown de preço da busca."""
import discord
from discord import app_commands

import book_api
import book_network as network
import book_search_enhanced as books
import book_system
import control_panel as cp

_INSTALLED = False
_MARKER = "|||fs_lang="
LANGUAGES = {
    "all": ("Todos os idiomas", None, None), "pt": ("Português", "por", "pt"),
    "en": ("Inglês", "eng", "en"), "es": ("Espanhol", "spa", "es"),
    "fr": ("Francês", "fre", "fr"), "de": ("Alemão", "ger", "de"),
    "it": ("Italiano", "ita", "it"), "ja": ("Japonês", "jpn", "ja"),
    "ko": ("Coreano", "kor", "ko"), "ru": ("Russo", "rus", "ru"),
    "zh": ("Chinês", "chi", "zh"),
}

def _lang_label(value): return LANGUAGES.get(value, LANGUAGES['all'])[0]
def _encode_query(query, language): return f"{str(query).strip()}{_MARKER}{language if language in LANGUAGES else 'all'}"
def _split_query(query):
    text=str(query)
    if _MARKER not in text:return text,'all'
    raw,language=text.rsplit(_MARKER,1); return raw.strip(),language if language in LANGUAGES else 'all'

async def _open_library(query,page=1,price_filter='all',limit=None):
    raw,language=_split_query(query); code=LANGUAGES[language][1]
    if code:raw=f'{raw} language:{code}'
    return await network._open_library(raw,page,price_filter,limit)

async def _google(query,page=1,price_filter='all',limit=None):
    raw,language=_split_query(query); limit=limit or books._BATCH
    if books._google_quota_exhausted and not books._GOOGLE_KEY:raise book_api.BookAPIError('A cota diária do Google Books foi atingida.')
    params={'q':raw,'filter':books.book_system._google_filter(price_filter),'printType':'books','maxResults':limit,'startIndex':max(0,(page-1)*limit),'orderBy':'relevance'}
    code=LANGUAGES[language][2]
    if code:params['langRestrict']=code
    if books._GOOGLE_KEY:params['key']=books._GOOGLE_KEY
    try:data=await network._request_json(books.book_system._API,params=params,headers={'Accept':'application/json','User-Agent':'FSociety-DiscordBot/1.4'},label='Google Books')
    except book_api.BookAPIError as exc:
        if 'limite de consultas' in str(exc).lower() or 'cota diária' in str(exc).lower():books._google_quota_exhausted=True; raise book_api.BookAPIError('A cota diária do Google Books foi atingida.') from exc
        raise
    data['_source']='Google Books'
    for item in data.get('items') or []:item['_source']='Google Books'
    return data

async def _fetch_books_uncached(query,page=1,price_filter='all'):
    if price_filter=='paid':return await _google(query,page,price_filter)
    open_error=None
    try:primary=await _open_library(query,page,price_filter)
    except book_api.BookAPIError as exc:open_error=exc; primary=None
    if primary is not None:
        items=list(primary.get('items') or [])
        if books._GOOGLE_KEY:
            try:google=await _google(query,page,price_filter); items=books._dedupe(items+list(google.get('items') or []))[:books._BATCH]
            except book_api.BookAPIError:pass
        primary['items']=items; primary['_source']='Open Library' if not books._GOOGLE_KEY else 'Open Library + Google Books'; return primary
    try:return await _google(query,page,price_filter)
    except book_api.BookAPIError as google_error:raise book_api.BookAPIError(f'As fontes de livros estão temporariamente indisponíveis. Open Library: {open_error} Google Books: {google_error}') from google_error

class LanguageBookView(books.EnhancedBookView):
    def __init__(self,*,query,display_query,language,items,total_count,price_filter='all',page=1,source='Catálogo',owner_id=None):
        self.display_query=display_query; self.language=language if language in LANGUAGES else 'all'; super().__init__(query=query,items=items,total_count=total_count,price_filter=price_filter,page=page,source=source,owner_id=owner_id)
        if hasattr(self,'filter_select'):
            try:self.remove_item(self.filter_select)
            except ValueError:pass
    def _sync(self):
        pos=self.absolute_index; self.first.disabled=self.loading or pos<=0; self.previous.disabled=self.loading or pos<=0; self.next.disabled=self.loading or pos>=self.total_count-1; self.last.disabled=self.loading or pos>=self.total_count-1; self.counter.label=f'{pos+1}/{max(1,self.total_count)}'
    def embed(self):
        e=super().embed(); e.title='F SOCIETY // LIVRO'; e.insert_field_at(0,name='Pesquisa',value=f'`{discord.utils.escape_markdown(self.display_query)}`\nIdioma: **{_lang_label(self.language)}**'[:1024],inline=False); item=self.items[self.index]; e.set_footer(text=f'Resultado {self.absolute_index+1}/{max(1,self.total_count)} • {books._source(item)} • {_lang_label(self.language)}'); return e

async def _run_search(i,termo,idioma='all'):
    if i.guild:
        allowed=book_system.get_book_channel(i.client,i.guild.id)
        if allowed and i.channel_id!=allowed:return await i.response.send_message(f'A pesquisa de livros está configurada para <#{allowed}>.',ephemeral=True)
    idioma=idioma if idioma in LANGUAGES else 'all'; price_filter=book_system.get_book_filter(i.client,i.guild.id) if i.guild else 'all'; query=_encode_query(termo,idioma); await i.response.defer(thinking=True)
    try:data=await books._fetch_books(query,1,price_filter)
    except book_api.BookAPIError as exc:return await i.followup.send(str(exc),ephemeral=True)
    items=data.get('items') or []
    if not items:return await i.followup.send(f'Nenhum livro encontrado em **{_lang_label(idioma)}** para essa pesquisa.',ephemeral=True)
    view=LanguageBookView(query=query,display_query=str(termo),language=idioma,items=items,total_count=data.get('totalItems'),price_filter=price_filter,source=data.get('_source') or 'Catálogo',owner_id=i.user.id); msg=await i.followup.send(embed=view.embed(),view=view,allowed_mentions=discord.AllowedMentions.none(),wait=True); view.message=msg

def _install_command_patch():
    old=cp.CommandTree.__init__
    def tree_init(self,*args,**kwargs):
        old(self,*args,**kwargs); existing=self.get_command('livro')
        if existing is not None:self.remove_command('livro')
        group=app_commands.Group(name='livro',description='Pesquisa livros em fontes oficiais')
        choices=[app_commands.Choice(name=label,value=value) for value,(label,_,__) in LANGUAGES.items()]
        @group.command(name='pesquisar',description='Pesquisa livros por título, autor, assunto ou ISBN')
        @app_commands.describe(termo='Título, autor, assunto ou ISBN',idioma='Filtrar os resultados por idioma')
        @app_commands.choices(idioma=choices)
        async def pesquisar(i:discord.Interaction,termo:app_commands.Range[str,2,120],idioma:str='all'):
            try:await _run_search(i,termo,idioma)
            except Exception as exc:
                print(f'Livros: erro em /livro pesquisar: {type(exc).__name__}: {exc}')
                if i.response.is_done():await i.followup.send('Não consegui consultar os livros agora.',ephemeral=True)
                else:await i.response.send_message('Não consegui consultar os livros agora.',ephemeral=True)
        self.add_command(group)
    cp.CommandTree.__init__=tree_init

def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; books._open_library=_open_library; books._google=_google; books._fetch_books_uncached=_fetch_books_uncached; books.EnhancedBookView=LanguageBookView; books._run_search=_run_search; book_system.BookSearchView=LanguageBookView; book_system._run_search=_run_search; book_api._run_search=_run_search; _install_command_patch(); print('[OK] Livros • filtro por idioma carregado')
