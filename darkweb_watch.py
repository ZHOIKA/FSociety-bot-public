"""Vigia de fontes na dark web — coleta de inteligência de ameaças (CTI) somente com metadados.

O módulo consulta fontes previamente configuradas e trabalha apenas com
metadados de tópicos (título, link e data). A interface pública permite que
qualquer membro veja o menu, as fontes, os termos e os resultados. Ações de
administração continuam exigindo a permissão Gerenciar servidor.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urljoin, urlparse

import aiohttp
import discord
from discord import app_commands

from control_panel import reply

LOG = logging.getLogger(__name__)

UA = ('Mozilla/5.0 (F-Society DarkWebWatch/1.0; monitor de metadados; '
      'pesquisa de segurança)')
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_TOTAL = 20
RUN_BUDGET = 55.0
MAX_SOURCES = 25
MAX_TERMS = 30
SCAN_CAP = 60
SHOW_PER_SOURCE = 10
FIELDS_PER_EMBED = 20
MAX_EMBEDS = 3
AUDIT_KEEP = 200
_FETCH_SEM = asyncio.Semaphore(3)
ONION_HOST = re.compile(r'\.onion(?::\d+)?$', re.I)
_URL_SCHEME = re.compile(r'^https?://', re.I)
_THREAD_HREF = re.compile(r'(?:showthread|viewtopic|viewthread|thread|tid=|t=|topic=)', re.I)
_ANCHOR = re.compile(r'<a\b(?=[^>]*\bhref=)([^>]*)>(.*?)</a>', re.I | re.S)
_HREF = re.compile(r'''\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))''', re.I)
_FEED_ROOT = re.compile(r'<(?:rss|feed|rdf:RDF)\b', re.I)
_FEED_BLOCK = re.compile(r'<(item|entry)\b[^>]*>(.*?)</\1>', re.I | re.S)
_TITLE = re.compile(r'<title\b[^>]*>(.*?)</title>', re.I | re.S)
_RESTRICTED = (
    'error_nopermission', '<!-- start: error -->',
    'not logged in or do not have permission', 'no permission to view this page',
    'você não está logado ou não tem permissão', 'voce nao esta logado ou nao tem permissao',
)
_LOGIN_HINTS = re.compile(
    r'type=["\']password["\']|name=["\'](?:username|user|login|email)["\']'
    r'|action=["\'][^"\']*(?:login|signin|entrar)[^"\']*["\']', re.I)
_SKIP_ANCHOR_SCHEMES = ('javascript:', 'mailto:', 'tel:', 'data:', '#')


class FetchError(Exception):
    pass


class RestrictedError(FetchError):
    pass


def _clean(raw):
    text = re.sub(r'<!\[CDATA\[(.*?)\]\]>', lambda m: m.group(1), raw or '', flags=re.S)
    text = re.sub(r'<[^>]+>', '', text)
    text = _html.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def _truncate(text, limit, suffix='…'):
    if len(text) <= limit:
        return text
    return text[:limit-len(suffix)].rstrip() + suffix


def _coerce_ts(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _fmt_ts(ts):
    if not ts:
        return ''
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%d/%m/%Y %H:%M UTC')


def _normalize(url, base):
    if not url:
        return ''
    url = url.strip()
    if not url or url.startswith(_SKIP_ANCHOR_SCHEMES) or '#' in url[:8] or len(url) > 600:
        return ''
    try:
        return urljoin(base, url)
    except ValueError:
        return ''


def _item_key(title, url):
    return (title.lower(), url.lower())


def _feed_link(block):
    m = re.search(r'<link\b([^>]*)>(?:([^<]*))?</link>', block, re.I | re.S)
    if m:
        attrs, inner = m.groups()
        hm = _HREF.search(attrs)
        if hm:
            return next(g for g in hm.groups() if g)
        return (inner or '').strip()
    m = re.search(r'<link\b([^>]*)/>', block, re.I)
    if m:
        hm = _HREF.search(m.group(1))
        if hm:
            return next(g for g in hm.groups() if g)
    m = re.search(r'<guid\b[^>]*>(.*?)</guid>', block, re.I | re.S)
    return _clean(m.group(1)) if m else ''


def _feed_date(block):
    for tag in ('pubdate', 'published', 'updated', 'date'):
        m = re.search(r'<[a-z]*:?' + tag + r'\b[^>]*>(.*?)</[a-z]*:?' + tag + r'>', block, re.I | re.S)
        if not m:
            continue
        raw = _clean(m.group(1))
        ts = _coerce_ts(raw)
        if ts is not None:
            return raw[:80], ts
    return '', None


def parse_feed(text, base_url, cap=SCAN_CAP):
    items, seen = [], set()
    for match in _FEED_BLOCK.finditer(text):
        block = match.group(2)
        tm = _TITLE.search(block)
        title = _clean(tm.group(1)) if tm else ''
        if not title:
            continue
        url = _normalize(_feed_link(block), base_url)
        if not url or not _THREAD_HREF.search(url):
            continue
        raw_date, ts = _feed_date(block)
        key = _item_key(title, url)
        if key in seen:
            continue
        seen.add(key)
        items.append({'title': title, 'url': url, 'ts': ts, 'raw_date': raw_date, 'kind': 'feed'})
        if len(items) >= cap:
            break
    return items


def parse_html_links(text, base_url, cap=SCAN_CAP):
    lowered = text.lower()
    for marker in _RESTRICTED:
        if marker in lowered:
            raise RestrictedError('página restrita (erro de permissão/login)')
    items, seen = [], set()
    for match in _ANCHOR.finditer(text):
        attrs, inner = match.groups()
        hm = _HREF.search(attrs)
        if not hm:
            continue
        href = next(g for g in hm.groups() if g is not None)
        if not _THREAD_HREF.search(href):
            continue
        url = _normalize(href, base_url)
        if not url:
            continue
        title = _clean(inner)
        if not title or len(title) < 3:
            continue
        low = title.lower()
        if low in ('next', 'previous', 'prev', 'next page', 'previous page') or low.startswith(('»','«')):
            continue
        key = _item_key(title, url)
        if key in seen:
            continue
        seen.add(key)
        items.append({'title': title, 'url': url, 'ts': None, 'raw_date': '', 'kind': 'html'})
        if len(items) >= cap:
            break
    if not items and _LOGIN_HINTS.search(text):
        raise RestrictedError('possível página de login — recusada')
    return items


def parse_content(text, base_url):
    lowered = text.lower()
    for marker in _RESTRICTED:
        if marker in lowered:
            raise RestrictedError('página restrita (erro de permissão/login)')
    if _FEED_ROOT.search(text[:20000]):
        return parse_feed(text, base_url)
    return parse_html_links(text, base_url)


def _proxy_url():
    return (os.environ.get('FSOCIETY_ONION_PROXY') or '').strip() or None


def _is_onion(url):
    try:
        host = urlparse(url).netloc
    except ValueError:
        return False
    return bool(ONION_HOST.search(host))


async def fetch_page(url):
    if not _URL_SCHEME.match(url):
        raise FetchError('URL deve começar com http:// ou https://')
    onion = _is_onion(url)
    proxy = _proxy_url()
    if onion and not proxy:
        raise FetchError('fonte .onion exige proxy Tor (defina FSOCIETY_ONION_PROXY e rode o daemon Tor)')
    request_proxy = None
    if onion:
        if proxy.startswith(('socks4://','socks5://','socks5h://')):
            try:
                from aiohttp_socks import ProxyConnector
            except ImportError:
                raise FetchError('pacote aiohttp_socks ausente — rode "pip install aiohttp_socks" no container')
            connector = ProxyConnector.from_url(proxy)
        elif proxy.startswith(('http://','https://')):
            connector = aiohttp.TCPConnector(limit=4)
            request_proxy = proxy
        else:
            raise FetchError(f'proxy Tor não suportado: {proxy}')
    else:
        connector = aiohttp.TCPConnector(limit=4)
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_TOTAL, connect=12)
    headers = {'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,application/rss+xml,application/atom+xml;q=0.9,*/*;q=0.5', 'Accept-Language': 'pt-BR,en;q=0.8'}
    ssl_opt = False if onion else None
    async with _FETCH_SEM:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
            async with session.get(url, proxy=request_proxy, ssl=ssl_opt, allow_redirects=True) as response:
                if response.status >= 400:
                    raise FetchError(f'HTTP {response.status}')
                data = await response.content.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise FetchError('página acima do limite de 2 MB (provável página completa — recusada)')
                return data.decode('utf-8','replace'), str(response.url)


class DarkWebWatch:
    def __init__(self, center):
        self.center = center
        self.q = center.q
        self._locks = {}
        self._setup()

    def _setup(self):
        for statement in (
            'CREATE TABLE IF NOT EXISTS darkweb_terms(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL, term TEXT NOT NULL,added_by INTEGER, added_at REAL NOT NULL)',
            'CREATE UNIQUE INDEX IF NOT EXISTS darkweb_terms_uq ON darkweb_terms(guild_id, term)',
            'CREATE TABLE IF NOT EXISTS darkweb_sources(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL, url TEXT NOT NULL, name TEXT,added_by INTEGER, added_at REAL NOT NULL,last_check REAL, last_status TEXT)',
            'CREATE UNIQUE INDEX IF NOT EXISTS darkweb_sources_uq ON darkweb_sources(guild_id, url)',
            'CREATE TABLE IF NOT EXISTS darkweb_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL, user_id INTEGER,action TEXT NOT NULL, detail TEXT, at REAL NOT NULL)',
            'CREATE INDEX IF NOT EXISTS darkweb_audit_guild ON darkweb_audit(guild_id, id)',
        ):
            self.q(statement)

    async def require_manage_guild(self, i):
        if i.guild is None:
            return False
        if not i.user.guild_permissions.manage_guild and not i.user.guild_permissions.administrator:
            await reply(i, '❌ Você precisa da permissão **Gerenciar servidor** para alterar o DarkWeb Watch.')
            return False
        return True

    def _audit(self, guild_id, user_id, action, detail=''):
        self.q('INSERT INTO darkweb_audit(guild_id,user_id,action,detail,at) VALUES(?,?,?,?,?)',(guild_id,user_id,action,detail[:400],time.time()))
        self.q('DELETE FROM darkweb_audit WHERE guild_id=? AND id NOT IN (SELECT id FROM darkweb_audit WHERE guild_id=? ORDER BY id DESC LIMIT ?)',(guild_id,guild_id,AUDIT_KEEP))

    def add_term(self, guild_id, user_id, term):
        term=' '.join((term or '').split())
        if not (2 <= len(term) <= 64): raise ValueError('O termo deve ter entre 2 e 64 caracteres.')
        rows=self.q('SELECT COUNT(*) AS n FROM darkweb_terms WHERE guild_id=?',(guild_id,),one=True)
        if rows['n'] >= MAX_TERMS: raise ValueError(f'Limite de {MAX_TERMS} termos por servidor.')
        if self.q('SELECT id FROM darkweb_terms WHERE guild_id=? AND term=?',(guild_id,term),one=True): raise ValueError('Este termo já está cadastrado.')
        self.q('INSERT INTO darkweb_terms(guild_id,term,added_by,added_at) VALUES(?,?,?,?)',(guild_id,term,user_id,time.time()))
        self._audit(guild_id,user_id,'termo_add',term)

    def remove_term(self,guild_id,user_id,term_id):
        row=self.q('SELECT term FROM darkweb_terms WHERE id=? AND guild_id=?',(term_id,guild_id),one=True)
        if not row: raise ValueError('Termo não encontrado (confira o id em /darkweb termos).')
        self.q('DELETE FROM darkweb_terms WHERE id=? AND guild_id=?',(term_id,guild_id)); self._audit(guild_id,user_id,'termo_remove',str(row['term']))

    def list_terms(self,guild_id): return self.q('SELECT id,term,added_at FROM darkweb_terms WHERE guild_id=? ORDER BY id',(guild_id,))

    @staticmethod
    def _validate_source_url(url):
        url=(url or '').strip()
        if len(url)>300: raise ValueError('URL muito longa (máx. 300 caracteres).')
        parsed=urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.netloc: raise ValueError('URL inválida: use http(s)://host/pagina-de-listagem')
        if parsed.username or parsed.password: raise ValueError('Não use credenciais na URL.')
        return url.rstrip('/') or url

    def add_source(self,guild_id,user_id,url,name=''):
        url=self._validate_source_url(url)
        rows=self.q('SELECT COUNT(*) AS n FROM darkweb_sources WHERE guild_id=?',(guild_id,),one=True)
        if rows['n']>=MAX_SOURCES: raise ValueError(f'Limite de {MAX_SOURCES} fontes por servidor.')
        name=' '.join((name or '').split()) or urlsplit(url).netloc; name=_truncate(name,60)
        if self.q('SELECT id FROM darkweb_sources WHERE guild_id=? AND url=?',(guild_id,url),one=True): raise ValueError('Esta URL já está cadastrada neste servidor.')
        self.q('INSERT INTO darkweb_sources(guild_id,url,name,added_by,added_at) VALUES(?,?,?,?,?)',(guild_id,url,name,user_id,time.time())); self._audit(guild_id,user_id,'fonte_add',f'{url} ({name})'); return url,name

    def remove_source(self,guild_id,user_id,source_id):
        row=self.q('SELECT url FROM darkweb_sources WHERE id=? AND guild_id=?',(source_id,guild_id),one=True)
        if not row: raise ValueError('Fonte não encontrada (confira o id em /darkweb fontes).')
        self.q('DELETE FROM darkweb_sources WHERE id=? AND guild_id=?',(source_id,guild_id)); self._audit(guild_id,user_id,'fonte_remove',str(row['url']))

    def list_sources(self,guild_id): return self.q('SELECT id,url,name,last_check,last_status FROM darkweb_sources WHERE guild_id=? ORDER BY id',(guild_id,))
    def _term_patterns(self,guild_id):
        rows=self.q('SELECT term FROM darkweb_terms WHERE guild_id=? ORDER BY id',(guild_id,)); return [re.compile(re.escape(r['term']),re.I) for r in rows] or None
    @staticmethod
    def _matches_terms(item,patterns):
        if not patterns:return True
        return any(p.search(f"{item['title']} {item['url']}") for p in patterns)

    async def _scan_source(self,source):
        try: text,final_url=await fetch_page(source['url'])
        except RestrictedError as e:return 'restrito',str(e)
        except Exception as e:return 'erro',f'{type(e).__name__}: {e}'[:200]
        try:return 'ok',parse_content(text,final_url or source['url'])
        except RestrictedError as e:return 'restrito',str(e)
        except Exception as e:return 'erro',f'{type(e).__name__}: {e}'[:200]

    async def verify(self,guild_id,user_id):
        sources=self.q('SELECT * FROM darkweb_sources WHERE guild_id=? ORDER BY id',(guild_id,))
        if not sources: raise ValueError('Nenhuma fonte configurada. Use /darkweb fonte adicionar <url>.')
        patterns=self._term_patterns(guild_id); started=time.time(); per_source=[]; checked=failed=refused=0
        for source in sources:
            if RUN_BUDGET-(time.time()-started)<=5: break
            status,payload=await self._scan_source(source); checked+=1
            if status=='ok': items=payload
            else:
                items=[]; refused += status=='restrito'; failed += status!='restrito'
            self.q('UPDATE darkweb_sources SET last_check=?,last_status=? WHERE id=?',(time.time(),status,source['id']))
            if status=='ok':
                matched=[it for it in items if self._matches_terms(it,patterns)]; matched.sort(key=lambda it:it['ts'] or 0,reverse=True)
                per_source.append({'name':source['name'] or urlsplit(source['url']).netloc,'url':source['url'],'status':status,'shown':matched[:SHOW_PER_SOURCE],'total':len(matched),'extra':max(0,len(matched)-SHOW_PER_SOURCE)})
            else:
                per_source.append({'name':source['name'] or urlsplit(source['url']).netloc,'url':source['url'],'status':status,'shown':[],'total':0,'extra':0,'detail':payload})
        self._audit(guild_id,user_id,'verificar',f'{checked} fonte(s)'); return {'per_source':per_source,'checked':checked,'refused':refused,'failed':failed,'elapsed':time.time()-started,'terms_active':bool(patterns)}

    @staticmethod
    def _send(i,**kwargs):
        kwargs.setdefault('ephemeral',False)
        sender=i.followup.send if i.response.is_done() else i.response.send_message
        return sender(**kwargs)

    def _menu_embed(self, guild):
        sources=self.list_sources(guild.id)
        terms=self.list_terms(guild.id)
        online=sum(1 for s in sources if s['last_status']=='ok')
        restricted=sum(1 for s in sources if s['last_status']=='restrito')
        proxy='configurado' if _proxy_url() else 'não configurado'
        e=discord.Embed(
            title='⟦ F:SOCIETY ⟧  DARKWEB WATCH',
            description=(
                'Central de inteligência de ameaças baseada em **metadados públicos de fontes configuradas**.\n'
                'Use os comandos abaixo para consultar o monitoramento do servidor.'
            ),
            color=0x111827,
            timestamp=datetime.now(timezone.utc))
        e.add_field(name='📡 Fontes',value=f'**{len(sources)}** cadastradas\n**{online}** online · **{restricted}** restritas',inline=True)
        e.add_field(name='🎯 Termos',value=f'**{len(terms)}** filtros ativos',inline=True)
        e.add_field(name='🧅 Tor/Proxy',value=f'**{proxy}**',inline=True)
        e.add_field(name='🔎 Consultar',value='`/darkweb verificar`\n`/darkweb fontes`\n`/darkweb termos`',inline=False)
        e.add_field(name='⚙ Administração',value='`/darkweb fonte adicionar` · `/darkweb fonte remover`\n`/darkweb termo adicionar` · `/darkweb termo remover`',inline=False)
        e.set_footer(text='F SOCIETY • DarkWeb Watch • resultados visíveis no canal')
        return e

    def _build_verify_embeds(self,result):
        rows=result['per_source']; hits=[]
        for src in rows:
            for item in src['shown']: hits.append((src['name'],item))
        color=0xEAB308 if result['refused'] or result['failed'] else 0x22C55E
        desc=[]
        for src in rows:
            if src['status']=='ok': desc.append(f"🟢 **{src['name']}** — {src['total']} tópico(s)")
            elif src['status']=='restrito': desc.append(f"🔒 **{src['name']}** — página restrita")
            else: desc.append(f"🟠 **{src['name']}** — {src['detail']}")
        summary=(f"**{result['checked']}** fonte(s) verificadas em **{result['elapsed']:.0f}s**\n"
                 f"**{len(hits)}** resultado(s) exibidos")
        if result['terms_active']:
            summary += '\n🎯 Filtro por termos de interesse ativo'
        embed=discord.Embed(title='🛰️ DARKWEB WATCH • RESULTADOS',description=(summary+'\n\n'+'\n'.join(desc))[:4000],color=color,timestamp=datetime.now(timezone.utc))
        embed.set_footer(text='F SOCIETY • somente metadados de tópicos')
        embeds=[embed]
        for n,(src,item) in enumerate(hits[:20],1):
            value=f"**Fonte:** {_truncate(src,60)}\n{item['url'] or 'sem link'}"
            if item['ts']: value+=f"\n**Data:** {_fmt_ts(item['ts'])}"
            embed.add_field(name=_truncate(f"{n:02d} • {item['title']}",100),value=value,inline=False)
        return embeds

    async def cmd_menu(self,i):
        if i.guild is None:return
        await self._send(i,embed=self._menu_embed(i.guild))

    async def cmd_termo_add(self,i,termo):
        if not await self.require_manage_guild(i): return
        try:self.add_term(i.guild.id,i.user.id,termo)
        except ValueError as e:return await reply(i,f'❌ {e}')
        await reply(i,f'✅ Termo de interesse adicionado: **{termo.strip()}**')
    async def cmd_termo_remove(self,i,id):
        if not await self.require_manage_guild(i): return
        try:self.remove_term(i.guild.id,i.user.id,id)
        except ValueError as e:return await reply(i,f'❌ {e}')
        await reply(i,f'✅ Termo removido (id {id}).')
    async def cmd_termos(self,i):
        if i.guild is None:return
        rows=self.list_terms(i.guild.id)
        e=discord.Embed(title='🎯 DARKWEB WATCH • TERMOS',description='Filtros de interesse configurados neste servidor.',color=0x7C3AED)
        if not rows:
            e.description='Nenhum termo de interesse configurado ainda.'
        else:
            for r in rows:e.add_field(name=f'#{r["id"]}',value=f'`{_truncate(r["term"],80)}`',inline=True)
        e.set_footer(text='F SOCIETY • DarkWeb Watch')
        await self._send(i,embed=e)
    async def cmd_fonte_add(self,i,url,nome):
        if not await self.require_manage_guild(i): return
        try:clean,name=self.add_source(i.guild.id,i.user.id,url,nome or '')
        except ValueError as e:return await reply(i,f'❌ {e}')
        await reply(i,f'✅ Fonte registrada **{name}**\n<{clean}>')
    async def cmd_fonte_remove(self,i,id):
        if not await self.require_manage_guild(i): return
        try:self.remove_source(i.guild.id,i.user.id,id)
        except ValueError as e:return await reply(i,f'❌ {e}')
        await reply(i,f'✅ Fonte removida (id {id}).')
    async def cmd_fontes(self,i):
        if i.guild is None:return
        rows=self.list_sources(i.guild.id)
        e=discord.Embed(title='📡 DARKWEB WATCH • FONTES',description='Fontes monitoradas por este servidor.',color=0x0EA5E9)
        if not rows:
            e.description='Nenhuma fonte configurada ainda.'
        else:
            for r in rows:
                status={'ok':'🟢 online','restrito':'🔒 restrita',None:'⚪ não verificada'}.get(r['last_status'],f'🟠 {r["last_status"]}')
                last=f'\nÚltima verificação: {_fmt_ts(r["last_check"])}' if r['last_check'] else ''
                e.add_field(name=f'#{r["id"]} {_truncate(r["name"] or r["url"],60)}',value=f'{status}{last}\n<{r["url"]}>',inline=False)
        e.set_footer(text='F SOCIETY • DarkWeb Watch')
        await self._send(i,embed=e)
    async def cmd_verificar(self,i):
        if i.guild is None:return
        await i.response.defer(ephemeral=False)
        lock=self._locks.setdefault(i.guild.id,asyncio.Lock())
        if lock.locked():return await i.followup.send('⚠ Já existe uma verificação em andamento neste servidor.')
        async with lock:
            try:result=await self.verify(i.guild.id,i.user.id)
            except ValueError as e:return await i.followup.send(f'❌ {e}')
            except Exception:
                LOG.exception('Falha inesperada no DarkWeb Watch')
                return await i.followup.send('❌ Falha inesperada durante a verificação. Veja os logs do bot.')
        await self._send(i,embeds=self._build_verify_embeds(result))
    async def cmd_historico(self,i):
        if i.guild is None:return
        rows=self.q('SELECT action,detail,user_id,at FROM darkweb_audit WHERE guild_id=? ORDER BY id DESC LIMIT 15',(i.guild.id,))
        e=discord.Embed(title='📜 DARKWEB WATCH • HISTÓRICO',color=0x3B82F6)
        if not rows:
            e.description='Nenhuma ação registrada ainda.'
        else:
            for r in rows:e.add_field(name=f'{_fmt_ts(r["at"])} • {r["action"]}',value=_truncate(str(r['detail'] or ''),120) or '—',inline=False)
        e.set_footer(text='F SOCIETY • auditoria do módulo')
        await self._send(i,embed=e)


def install(center):
    watch=DarkWebWatch(center); center.darkweb=watch
    root=app_commands.Group(name='darkweb',description='Central pública de monitoramento DarkWeb Watch',guild_only=True)
    termo=app_commands.Group(name='termo',description='Gerencia termos de interesse')
    fonte=app_commands.Group(name='fonte',description='Gerencia fontes monitoradas')
    @root.command(name='menu',description='Mostra o painel público do DarkWeb Watch')
    async def menu(i:discord.Interaction): await watch.cmd_menu(i)
    @termo.command(name='adicionar',description='Adiciona um termo de interesse (staff)')
    async def termo_add(i:discord.Interaction,termo:str): await watch.cmd_termo_add(i,termo)
    @termo.command(name='remover',description='Remove um termo pelo id (staff)')
    async def termo_remove(i:discord.Interaction,id:int): await watch.cmd_termo_remove(i,id)
    @fonte.command(name='adicionar',description='Adiciona URL de listagem/feed (staff)')
    async def fonte_add(i:discord.Interaction,url:str,nome:str=''): await watch.cmd_fonte_add(i,url,nome)
    @fonte.command(name='remover',description='Remove uma fonte pelo id (staff)')
    async def fonte_remove(i:discord.Interaction,id:int): await watch.cmd_fonte_remove(i,id)
    @root.command(name='termos',description='Mostra publicamente os termos de interesse')
    async def termos(i:discord.Interaction): await watch.cmd_termos(i)
    @root.command(name='fontes',description='Mostra publicamente as fontes monitoradas')
    async def fontes(i:discord.Interaction): await watch.cmd_fontes(i)
    @root.command(name='verificar',description='Verifica as fontes e publica os resultados no canal')
    async def verificar(i:discord.Interaction): await watch.cmd_verificar(i)
    @root.command(name='historico',description='Mostra o histórico público do módulo')
    async def historico(i:discord.Interaction): await watch.cmd_historico(i)
    root.add_command(termo); root.add_command(fonte); center.bot.tree.add_command(root)
    LOG.info('Módulo DarkWeb Watch instalado (grupo /darkweb).')
    return watch
