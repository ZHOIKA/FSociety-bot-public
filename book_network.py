"""Resiliência de rede para buscas de livros em ambientes de hospedagem."""
import asyncio
import socket

import aiohttp

import book_api
import book_search_enhanced as books

_INSTALLED = False


async def _request_json(url, *, params, headers, label, attempts=3):
    """GET JSON com IPv4, cache DNS e retry para falhas transitórias."""
    last_exc = None
    for attempt in range(attempts):
        timeout = aiohttp.ClientTimeout(total=25, connect=8, sock_connect=8, sock_read=20)
        connector = aiohttp.TCPConnector(
            family=socket.AF_INET,
            ttl_dns_cache=300,
            limit=20,
            enable_cleanup_closed=True,
        )
        try:
            async with books._REQUEST_SEMAPHORE:
                async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
                    async with session.get(url, params=params) as response:
                        text = await response.text()
                        if response.status == 200:
                            try:
                                return await response.json(content_type=None)
                            except Exception as exc:
                                raise book_api.BookAPIError(f'{label} retornou uma resposta inválida.') from exc
                        if response.status == 429:
                            raise book_api.BookAPIError(f'{label} atingiu o limite de consultas.')
                        if response.status in (500, 502, 503, 504):
                            last_exc = book_api.BookAPIError(f'{label} está temporariamente indisponível.')
                        else:
                            raise book_api.BookAPIError(f'{label} respondeu com erro HTTP {response.status}.')
                        print(f'{label} HTTP {response.status}: {text[:250]}')
        except book_api.BookAPIError as exc:
            last_exc = exc
            if 'limite de consultas' in str(exc).lower():
                raise
        except (aiohttp.ClientConnectorError, aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError, aiohttp.ClientOSError, asyncio.TimeoutError) as exc:
            last_exc = exc
            print(f'{label}: falha de conexão {type(exc).__name__} (tentativa {attempt + 1}/{attempts})')
        except aiohttp.ClientError as exc:
            last_exc = exc
            print(f'{label}: erro HTTP de transporte {type(exc).__name__} (tentativa {attempt + 1}/{attempts})')
        if attempt < attempts - 1:
            await asyncio.sleep(0.8 * (2 ** attempt))
    if isinstance(last_exc, book_api.BookAPIError):
        raise last_exc
    raise book_api.BookAPIError(f'Não foi possível conectar ao {label} agora. Tente novamente em alguns segundos.') from last_exc


async def _open_library(query, page=1, price_filter='all', limit=None):
    limit = limit or books._BATCH
    if price_filter == 'paid':
        return {'totalItems': 0, 'items': [], '_source': 'Open Library'}
    fields = ('key,title,subtitle,author_name,first_publish_year,publish_year,cover_i,'
              'edition_count,language,ebook_access,has_fulltext,cover_edition_key,'
              'number_of_pages_median,ratings_average,ratings_count,subject')
    data = await _request_json(
        books._OPEN_LIBRARY_API,
        params={'q': query, 'page': max(1, int(page)), 'limit': limit, 'fields': fields},
        headers={'Accept': 'application/json', 'User-Agent': 'FSociety-DiscordBot/1.3'},
        label='Open Library',
    )
    docs = data.get('docs') or []
    if price_filter == 'free':
        docs = [d for d in docs if d.get('ebook_access') == 'public' or d.get('has_fulltext')]
    items = []
    for doc in docs:
        item = book_api._open_library_item(doc)
        info = item['volumeInfo']
        info['subtitle'] = doc.get('subtitle')
        info['pageCount'] = doc.get('number_of_pages_median')
        if doc.get('ratings_average') is not None:
            info['averageRating'] = round(float(doc['ratings_average']), 1)
        if doc.get('ratings_count') is not None:
            info['ratingsCount'] = int(doc['ratings_count'])
        if doc.get('subject'):
            info['categories'] = (doc.get('subject') or [])[:5]
        items.append(item)
    return {'totalItems': min(int(data.get('numFound') or len(items)), books._MAX_RESULTS), 'items': items, '_source': 'Open Library'}


async def _google(query, page=1, price_filter='all', limit=None):
    limit = limit or books._BATCH
    if books._google_quota_exhausted and not books._GOOGLE_KEY:
        raise book_api.BookAPIError('A cota diária do Google Books foi atingida.')
    params = {
        'q': query,
        'filter': books.book_system._google_filter(price_filter),
        'printType': 'books',
        'maxResults': limit,
        'startIndex': max(0, (page - 1) * limit),
        'orderBy': 'relevance',
    }
    if books._GOOGLE_KEY:
        params['key'] = books._GOOGLE_KEY
    try:
        data = await _request_json(
            books.book_system._API,
            params=params,
            headers={'Accept': 'application/json', 'User-Agent': 'FSociety-DiscordBot/1.3'},
            label='Google Books',
        )
    except book_api.BookAPIError as exc:
        if 'limite de consultas' in str(exc).lower() or 'cota diária' in str(exc).lower():
            books._google_quota_exhausted = True
            raise book_api.BookAPIError('A cota diária do Google Books foi atingida.') from exc
        raise
    data['_source'] = 'Google Books'
    for item in data.get('items') or []:
        item['_source'] = 'Google Books'
    return data


async def _fetch_books_uncached(query, page=1, price_filter='all'):
    if price_filter == 'paid':
        return await _google(query, page, price_filter)
    open_error = None
    try:
        primary = await _open_library(query, page, price_filter)
    except book_api.BookAPIError as exc:
        open_error = exc
        primary = None
    if primary is not None:
        items = list(primary.get('items') or [])
        if books._GOOGLE_KEY:
            try:
                google = await _google(query, page, price_filter)
                items = books._dedupe(items + list(google.get('items') or []))[:books._BATCH]
            except book_api.BookAPIError:
                pass
        primary['items'] = items
        primary['_source'] = 'Open Library' if not books._GOOGLE_KEY else 'Open Library + Google Books'
        return primary
    try:
        return await _google(query, page, price_filter)
    except book_api.BookAPIError as google_error:
        raise book_api.BookAPIError(
            f'As fontes de livros estão temporariamente indisponíveis. Open Library: {open_error} Google Books: {google_error}'
        ) from google_error


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    books._open_library = _open_library
    books._google = _google
    books._fetch_books_uncached = _fetch_books_uncached
