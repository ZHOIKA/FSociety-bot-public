"""Monitor de CVEs de baixa latência com múltiplas fontes e deduplicação por CVE-ID."""
import asyncio
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone, timedelta

import cve_alerts

POLL_INTERVAL = 60
SOURCE_LAG = 15
OVERLAP = timedelta(minutes=10)
NVD_API = cve_alerts.API
GITHUB_ADVISORIES = 'https://api.github.com/advisories'
CISA_KEV = 'https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json'
CVE_RE = re.compile(r'^CVE-\d{4}-\d{4,}$', re.I)


def _valid_id(value):
    return isinstance(value, str) and bool(CVE_RE.fullmatch(value.strip()))


def _iso_timestamp(value, fallback=None):
    if not value:
        return fallback if fallback is not None else time.time()
    try:
        return cve_alerts.timestamp(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
        except (TypeError, ValueError):
            return fallback if fallback is not None else time.time()


def _minimal_cve(ident, published, description, references=None, score=None, source=''):
    cve = {
        'id': ident.upper(),
        'published': published,
        'lastModified': published,
        'vulnStatus': 'Analyzed',
        'descriptions': [{'lang': 'en', 'value': description or 'Resumo ainda não disponível na fonte.'}],
        'references': [{'url': url, 'source': source} for url in (references or []) if url],
        'metrics': {},
        '_fsociety_sources': [source] if source else [],
    }
    if isinstance(score, (int, float)) and 0 <= float(score) <= 10:
        cve['metrics']['cvssMetricV31'] = [{
            'source': source or 'external', 'type': 'Secondary',
            'cvssData': {'version': '3.1', 'baseScore': float(score)},
        }]
    return cve


def _upsert_records(self, records, *, replace=False):
    if not records:
        return
    with closing(sqlite3.connect(self.center.db_path())) as con, con:
        if replace:
            con.executemany(
                'INSERT INTO cve_records(id,published,payload) VALUES(?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET published=excluded.published,payload=excluded.payload',
                records,
            )
        else:
            con.executemany(
                'INSERT OR IGNORE INTO cve_records(id,published,payload) VALUES(?,?,?)',
                records,
            )


async def _fetch_nvd(self, session, start, end):
    index = 0
    records = []
    while True:
        await asyncio.sleep(max(0, self.next_request - time.monotonic()))
        self.next_request = time.monotonic() + 7
        params = {
            'lastModStartDate': start.isoformat(timespec='milliseconds'),
            'lastModEndDate': end.isoformat(timespec='milliseconds'),
            'resultsPerPage': 2000,
            'startIndex': index,
        }
        async with session.get(NVD_API, params=params) as response:
            response.raise_for_status()
            data = await response.json()
        entries = data.get('vulnerabilities')
        total = data.get('totalResults')
        if not isinstance(entries, list) or not isinstance(total, int):
            raise ValueError('Resposta inesperada da NVD.')
        for entry in entries:
            cve = entry.get('cve') or {}
            ident = cve.get('id', '')
            if not _valid_id(ident) or not cve.get('published'):
                continue
            cve['_fsociety_sources'] = ['NVD/NIST']
            records.append((ident.upper(), cve_alerts.timestamp(cve['published']), json.dumps(cve)))
        index += len(entries)
        if index >= total:
            break
        if not entries:
            raise ValueError('A NVD retornou uma página incompleta; a consulta será repetida.')
    _upsert_records(self, records, replace=True)


async def _fetch_github(self, session, since):
    params = {'per_page': 100, 'sort': 'published', 'direction': 'desc'}
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    async with session.get(GITHUB_ADVISORIES, params=params, headers=headers) as response:
        response.raise_for_status()
        entries = await response.json()
    records = []
    for item in entries if isinstance(entries, list) else []:
        ident = item.get('cve_id')
        published = item.get('published_at')
        if not _valid_id(ident) or not published:
            continue
        ts = _iso_timestamp(published)
        if ts < since:
            continue
        cvss = item.get('cvss') or {}
        refs = [item.get('html_url')]
        for vuln in item.get('vulnerabilities') or []:
            package = vuln.get('package') or {}
            if package.get('name'):
                refs.append(f"https://github.com/advisories/{item.get('ghsa_id')}")
                break
        cve = _minimal_cve(
            ident, published, item.get('description') or item.get('summary'), refs,
            cvss.get('score'), 'GitHub Advisory Database',
        )
        records.append((ident.upper(), ts, json.dumps(cve)))
    _upsert_records(self, records, replace=False)


async def _fetch_cisa(self, session, since):
    async with session.get(CISA_KEV) as response:
        response.raise_for_status()
        data = await response.json()
    records = []
    for item in data.get('vulnerabilities', []) if isinstance(data, dict) else []:
        ident = item.get('cveID')
        if not _valid_id(ident):
            continue
        date_added = item.get('dateAdded')
        published = f'{date_added}T00:00:00Z' if date_added else datetime.now(timezone.utc).isoformat()
        ts = _iso_timestamp(published)
        if ts < since:
            continue
        description = item.get('shortDescription') or item.get('vulnerabilityName') or 'CVE presente no catálogo CISA KEV.'
        refs = ['https://www.cisa.gov/known-exploited-vulnerabilities-catalog']
        cve = _minimal_cve(ident, published, description, refs, None, 'CISA KEV')
        cve['_known_exploited'] = True
        cve['_cisa_action'] = item.get('requiredAction') or ''
        records.append((ident.upper(), ts, json.dumps(cve)))
    _upsert_records(self, records, replace=False)


async def _fast_fetch(self, session):
    now = datetime.now(timezone.utc) - timedelta(seconds=SOURCE_LAG)
    row = self.q("SELECT value FROM cve_state WHERE key='cursor'", one=True)
    start = datetime.fromisoformat(row['value']) if row else now - timedelta(hours=1)
    start = max(start - OVERLAP, now - timedelta(days=30))
    end = min(now, start + timedelta(days=3))
    since = max(time.time() - 2 * 86400, start.timestamp())

    results = await asyncio.gather(
        _fetch_nvd(self, session, start, end),
        _fetch_github(self, session, since),
        _fetch_cisa(self, session, since),
        return_exceptions=True,
    )
    names = ('NVD', 'GitHub Advisory Database', 'CISA KEV')
    failures = []
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            failures.append(f'{name}: {type(result).__name__}')
            cve_alerts.LOG.warning('Fonte CVE %s indisponível: %s', name, result)
    if len(failures) == len(names):
        raise RuntimeError('Todas as fontes de CVE falharam: ' + ', '.join(failures))

    self.q(
        "INSERT INTO cve_state(key,value) VALUES('cursor',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (end.isoformat(),),
    )
    self.q(
        "INSERT INTO cve_state(key,value) VALUES('checked',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(time.time()),),
    )
    self.q('DELETE FROM cve_records WHERE published<?', (time.time() - 30 * 86400,))


async def _fast_run(self):
    await self.center.bot.wait_until_ready()
    timeout = cve_alerts.aiohttp.ClientTimeout(total=45)
    headers = {'User-Agent': 'FSociety-CVE-Monitor/5.0 multisource'}
    async with cve_alerts.aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        while not self.center.bot.is_closed():
            started = time.monotonic()
            try:
                await self.cycle(session)
            except Exception:
                cve_alerts.LOG.exception('Falha no ciclo do monitor de CVEs; nova tentativa em até 60 segundos')
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(5, POLL_INTERVAL - elapsed))


def install():
    cve_alerts.INTERVAL = POLL_INTERVAL
    cve_alerts.CVEMonitor.fetch = _fast_fetch
    cve_alerts.CVEMonitor.run = _fast_run
