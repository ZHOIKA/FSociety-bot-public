"""Consulta resiliente de livros com Google Books + fallback Open Library."""
import asyncio
import os

import aiohttp
import discord

import book_system

_INSTALLED = False
_OPEN_LIBRARY_API = 'https://openlibrary.org/search.json'
_GOOGLE_BOOKS_KEY = os.getenv('GOOGLE_BOOKS_API_KEY', '').strip()


class BookAPIError(Exception):
    pass


def _friendly_status(status: int) -> str:
    if status == 400:
        return 'A API de livros rejeitou os parâmetros da pesquisa.'
    if status == 403:
        return 'A API de livros recusou a consulta. Verifique a chave/limite da API.'
    if status == 429:
        return 'A cota diária do Google Books foi atingida.'
    if 500 <= status <= 599:
        return 'A API de livros está temporariamente indisponível.'
    return f'A API de livros respondeu com erro HTTP {status}.'


def _open_library_item(doc):
    key = str(doc.get('key') or '')
    work_url = f'https://openlibrary.org{key}' if key.startswith('/') else 'https://openlibrary.org/'
    cover_i = doc.get('cover_i')
    image_links = {}
    if cover_i:
        image_links['thumbnail'] = f'https://covers.openlibrary.org/b/id/{cover_i}-M.jpg'

    authors = doc.get('author_name') or ['Autor não informado']
    year = doc.get('first_publish_year')
    editions = doc.get('edition_count')
    languages = doc.get('language') or []
    lang = languages[0] if languages else None
    public = (doc.get('ebook_access') == 'public') or bool(doc.get('has_fulltext'))

    return {
        'id': key or str(doc.get('cover_edition_key') or ''),
        'volumeInfo': {
            'title': doc.get('title') or 'Sem título',
            'authors': authors,
            'publishedDate': str(year) if year else None,
            'language': lang,
            'pageCount': None,
            'infoLink': work_url,
            'imageLinks': image_links,
        },
        'saleInfo': {
            'saleability': 'FREE' if public else 'NOT_FOR_SALE',
            'isEbook': public,
        },
        'accessInfo': {
            'viewability': 'ALL_PAGES' if public else 'NO_PAGES',
            'webReaderLink': work_url if public else None,
        },
        '_source': 'Open Library',
        '_edition_count': editions,
    }


async def _fetch_open_library(query, page=1, price_filter='all'):
    if price_filter == 'paid':
        raise BookAPIError(
            'A busca de livros pagos depende do Google Books e a cota diária foi atingida. '
            'Configure GOOGLE_BOOKS_API_KEY na hospedagem para usar uma cota própria.'
        )

    timeout = aiohttp.ClientTimeout(total=25)
    params = {
        'q': query,
        'page': max(1, int(page)),
        'limit': book_system._PAGE_SIZE,
        'fields': 'key,title,author_name,first_publish_year,cover_i,edition_count,language,ebook_access,has_fulltext,cover_edition_key',
    }
    headers = {'Accept': 'application/json', 'User-Agent': 'FSociety-DiscordBot/1.0'}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(_OPEN_LIBRARY_API, params=params) as response:
            if response.status != 200:
                body = (await response.text())[:300]
                print(f'Open Library HTTP {response.status}: {body}')
                raise BookAPIError(f'Open Library respondeu com erro HTTP {response.status}.')
            data = await response.json(content_type=None)

    docs = data.get('docs') or []
    if price_filter == 'free':
        docs = [d for d in docs if d.get('ebook_access') == 'public' or d.get('has_fulltext')]

    return {
        'totalItems': int(data.get('numFound') or len(docs)),
        'items': [_open_library_item(doc) for doc in docs[:book_system._PAGE_SIZE]],
        '_source': 'Open Library',
    }


async def _fetch_google(query, page=1, price_filter='all'):
    start_index = max(0, (page - 1) * book_system._PAGE_SIZE)
    timeout = aiohttp.ClientTimeout(total=25)
    params = {
        'q': query,
        'filter': book_system._google_filter(price_filter),
        'printType': 'books',
        'maxResults': book_system._PAGE_SIZE,
        'startIndex': start_index,
        'orderBy': 'relevance',
    }
    if _GOOGLE_BOOKS_KEY:
        params['key'] = _GOOGLE_BOOKS_KEY

    headers = {'Accept': 'application/json', 'User-Agent': 'FSociety-DiscordBot/1.0'}
    last_error = None
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(book_system._API, params=params) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        data['_source'] = 'Google Books'
                        return data

                    body = (await response.text())[:500]
                    print(f'Google Books HTTP {response.status}: {body}')
                    message = _friendly_status(response.status)
                    if response.status in (429,) or 500 <= response.status <= 599:
                        last_error = BookAPIError(message)
                        if attempt == 0 and response.status >= 500:
                            await asyncio.sleep(1.5)
                            continue
                    raise BookAPIError(message)
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(1.5)
                continue
            raise BookAPIError('Não foi possível conectar ao Google Books agora.') from exc

    if last_error:
        raise BookAPIError(str(last_error))
    raise BookAPIError('Falha inesperada ao consultar o Google Books.')


async def _fetch_books(query, page=1, price_filter='all'):
    try:
        return await _fetch_google(query, page, price_filter)
    except BookAPIError as exc:
        text = str(exc)
        quota_or_outage = ('cota diária' in text.lower()) or ('temporariamente indisponível' in text.lower()) or ('conectar' in text.lower())
        if not quota_or_outage:
            raise
        print(f'Livros: Google indisponível; usando fallback Open Library ({text})')
        return await _fetch_open_library(query, page, price_filter)


async def _run_search(interaction, termo, filtro='default'):
    if interaction.guild:
        allowed_channel = book_system.get_book_channel(interaction.client, interaction.guild.id)
        if allowed_channel and interaction.channel_id != allowed_channel:
            return await interaction.response.send_message(
                f'A pesquisa de livros está configurada para <#{allowed_channel}>.',
                ephemeral=True,
            )

    selected = filtro
    if selected == 'default':
        selected = book_system.get_book_filter(interaction.client, interaction.guild.id) if interaction.guild else 'all'

    await interaction.response.defer(thinking=True)
    try:
        data = await _fetch_books(termo, 1, selected)
    except BookAPIError as exc:
        return await interaction.followup.send(str(exc), ephemeral=True)

    items = (data or {}).get('items') or []
    if not items:
        return await interaction.followup.send('Nenhum livro encontrado para essa pesquisa.')

    total = int((data or {}).get('totalItems') or len(items))
    view = book_system.BookSearchView(
        query=termo,
        items=items,
        total_count=total,
        price_filter=selected,
    )
    message = await interaction.followup.send(
        embed=view.embed(),
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
        wait=True,
    )
    view.message = message


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    book_system._fetch_books = _fetch_books
    book_system._run_search = _run_search
