"""Monitor de CVEs da NVD com tópico de discussão para cada vulnerabilidade."""
import asyncio
import json
import logging
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone, timedelta

import aiohttp
import discord
from discord import app_commands

from control_panel import reply

LOG = logging.getLogger(__name__)
API = 'https://services.nvd.nist.gov/rest/json/cves/2.0'
INTERVAL = 300


def migrate(con):
    con.execute('CREATE TABLE IF NOT EXISTS cve_records(id TEXT PRIMARY KEY, published REAL, payload TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS cve_deliveries(guild_id INTEGER, cve_id TEXT, sent_at REAL, PRIMARY KEY(guild_id,cve_id))')
    columns = {r[1] for r in con.execute('PRAGMA table_info(cve_deliveries)')}
    if 'message_id' not in columns:
        con.execute('ALTER TABLE cve_deliveries ADD COLUMN message_id INTEGER')
    if 'thread_id' not in columns:
        con.execute('ALTER TABLE cve_deliveries ADD COLUMN thread_id INTEGER')
    con.execute('CREATE TABLE IF NOT EXISTS cve_state(key TEXT PRIMARY KEY, value TEXT)')
    con.execute('CREATE INDEX IF NOT EXISTS cve_publication ON cve_records(published)')


def timestamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()


def score(cve):
    metrics = cve.get('metrics') or {}
    for key in ('cvssMetricV40', 'cvssMetricV31', 'cvssMetricV30', 'cvssMetricV2'):
        entries = sorted(metrics.get(key, []), key=lambda x: x.get('type') != 'Primary')
        for entry in entries:
            value = entry.get('cvssData', {}).get('baseScore')
            if isinstance(value, (float, int)) and 0 <= value <= 10:
                return float(value)
    return None


def cvss_data(cve):
    metrics = cve.get('metrics') or {}
    for key in ('cvssMetricV40', 'cvssMetricV31', 'cvssMetricV30', 'cvssMetricV2'):
        entries = sorted(metrics.get(key, []), key=lambda x: x.get('type') != 'Primary')
        if entries:
            data = entries[0].get('cvssData') or {}
            return data, entries[0]
    return {}, {}


def severity(cve):
    value = score(cve)
    return ('Sem classificação' if value is None else 'Crítica' if value >= 9 else
            'Alta' if value >= 7 else 'Média' if value >= 4 else 'Baixa')


def matches(cve, settings):
    if cve.get('vulnStatus') == 'Rejected':
        return False
    value = score(cve)
    return settings['cve_unscored'] if value is None else value >= settings['cve_min_score']


def description_of(cve):
    descriptions = cve.get('descriptions') or []
    descriptions = sorted(descriptions, key=lambda d: {'pt': 0, 'pt-BR': 0, 'en': 1}.get(d.get('lang'), 2))
    return descriptions[0].get('value', '') if descriptions else 'Resumo ainda não disponível na fonte.'


def weaknesses(cve):
    values = []
    for weakness in cve.get('weaknesses') or []:
        for item in weakness.get('description') or []:
            text = item.get('value')
            if text and text not in values:
                values.append(text)
    return values[:6]


def affected_products(cve):
    products = []
    for configuration in cve.get('configurations') or []:
        nodes = configuration.get('nodes') or []
        stack = list(nodes)
        while stack:
            node = stack.pop()
            stack.extend(node.get('children') or [])
            for match in node.get('cpeMatch') or []:
                if not match.get('vulnerable'):
                    continue
                criteria = match.get('criteria') or ''
                parts = criteria.split(':')
                if len(parts) > 5:
                    vendor, product = parts[3], parts[4]
                    label = f'{vendor}/{product}'.replace('_', ' ')
                    if label not in products:
                        products.append(label)
                if len(products) >= 8:
                    return products
    return products


def references(cve):
    result = []
    for ref in cve.get('references') or []:
        url = ref.get('url')
        if url and url not in result:
            result.append(url)
    return result[:8]


def alert_embed(cve):
    ident = cve['id']
    value = score(cve)
    level = severity(cve)
    description = description_of(cve)
    data, metric = cvss_data(cve)
    color = 0xDC2626 if value is not None and value >= 9 else 0xEF4444 if value is not None and value >= 7 else 0xF59E0B
    e = discord.Embed(title=f'{ident} • {level}', url=f'https://nvd.nist.gov/vuln/detail/{ident}',
                      description=discord.utils.escape_mentions(description)[:3000], color=color)
    e.add_field(name='Gravidade', value=level)
    e.add_field(name='CVSS', value=f'{value:g}/10' if value is not None else 'Ainda não informado')
    vector = data.get('vectorString')
    if vector:
        e.add_field(name='Vetor CVSS', value=f'`{vector[:200]}`', inline=False)
    if metric.get('exploitabilityScore') is not None:
        e.add_field(name='Explorabilidade', value=str(metric['exploitabilityScore']))
    if metric.get('impactScore') is not None:
        e.add_field(name='Impacto', value=str(metric['impactScore']))
    e.add_field(name='Publicação na NVD', value=f'<t:{int(timestamp(cve["published"]))}:f>', inline=False)
    e.add_field(name='Fonte oficial', value=f'[NVD / NIST](https://nvd.nist.gov/vuln/detail/{ident})', inline=False)
    e.set_footer(text='F SOCIETY • Cada alerta possui um tópico próprio para análise e discussão')
    return e


def topic_embed(cve):
    ident = cve['id']
    value = score(cve)
    level = severity(cve)
    data, metric = cvss_data(cve)
    weak = weaknesses(cve)
    products = affected_products(cve)
    refs = references(cve)
    e = discord.Embed(title=f'Análise • {ident}', color=0x111827,
                      description='Tópico criado automaticamente para centralizar análise, referências e discussão desta CVE.')
    e.add_field(name='Classificação', value=f'**{level}**' + (f' • CVSS **{value:g}/10**' if value is not None else ''), inline=False)
    if data.get('vectorString'):
        e.add_field(name='Vetor', value=f'`{data["vectorString"][:250]}`', inline=False)
    if weak:
        e.add_field(name='Fraquezas / CWE', value='\n'.join(f'• {discord.utils.escape_markdown(x)[:120]}' for x in weak), inline=False)
    if products:
        e.add_field(name='Produtos possivelmente afetados', value='\n'.join(f'• {discord.utils.escape_markdown(x)}' for x in products), inline=False)
    if refs:
        e.add_field(name='Referências', value='\n'.join(f'• {url}' for url in refs)[:1000], inline=False)
    if metric.get('source'):
        e.set_footer(text=f'Fonte da métrica: {metric["source"]}')
    return e


class CVEMonitor:
    def __init__(self, center):
        self.center, self.q = center, center.q
        self.task = None
        self.next_request = 0

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name='fsociety-cve-monitor')

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    def set_active(self, gid, enabled):
        s = self.center.settings(gid)
        if enabled and not s['cve_channel']:
            raise ValueError('Selecione primeiro o canal de alertas de CVE.')
        changes = {'cve_enabled': enabled}
        if enabled and not s['cve_since']:
            changes['cve_since'] = time.time()
        self.center.save(gid, changes)

    async def fetch(self, session):
        now = datetime.now(timezone.utc) - timedelta(minutes=2)
        row = self.q("SELECT value FROM cve_state WHERE key='cursor'", one=True)
        start = datetime.fromisoformat(row['value']) if row else now - timedelta(days=1)
        start = max(start - timedelta(hours=2), now - timedelta(days=30))
        end = min(now, start + timedelta(days=3))
        index = 0
        while True:
            await asyncio.sleep(max(0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + 7
            params = {'lastModStartDate': start.isoformat(timespec='milliseconds'),
                      'lastModEndDate': end.isoformat(timespec='milliseconds'),
                      'resultsPerPage': 2000, 'startIndex': index}
            async with session.get(API, params=params) as response:
                response.raise_for_status()
                data = await response.json()
            entries = data['vulnerabilities']
            if not isinstance(entries, list) or not isinstance(data['totalResults'], int):
                raise ValueError('Resposta inesperada da NVD.')
            records = []
            for entry in entries:
                cve = entry['cve']
                if not re.fullmatch(r'CVE-\d{4}-\d{4,}', cve['id']):
                    raise ValueError('Identificador inesperado na resposta da NVD.')
                records.append((cve['id'], timestamp(cve['published']), json.dumps(cve)))
            with closing(sqlite3.connect(self.center.db_path())) as con, con:
                con.executemany('INSERT INTO cve_records(id,published,payload) VALUES(?,?,?) '
                                'ON CONFLICT(id) DO UPDATE SET published=excluded.published,payload=excluded.payload', records)
            index += len(entries)
            if index >= data['totalResults']:
                break
            if not entries:
                raise ValueError('A NVD retornou uma página incompleta; a consulta será repetida.')
        self.q("INSERT INTO cve_state(key,value) VALUES('cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (end.isoformat(),))
        self.q("INSERT INTO cve_state(key,value) VALUES('checked',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(time.time()),))
        self.q('DELETE FROM cve_records WHERE published<?', (time.time() - 30 * 86400,))

    async def create_topic(self, message, cve):
        name = f'{cve["id"]} • {severity(cve)}'[:100]
        thread = await message.create_thread(name=name, auto_archive_duration=1440,
                                             reason=f'Tópico automático para {cve["id"]}')
        await thread.send(embed=topic_embed(cve), allowed_mentions=discord.AllowedMentions.none())
        await thread.send(
            'Use este tópico para registrar análise técnica, impacto observado, links de patches e validações da equipe.',
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return thread

    async def deliver(self, g):
        s = self.center.settings(g.id)
        if not s['cve_enabled'] or not s['cve_channel'] or not s['cve_since']:
            return
        channel = g.get_channel(s['cve_channel'])
        if not isinstance(channel, discord.TextChannel):
            raise ValueError('Canal de CVEs indisponível; selecione novamente no painel.')
        permissions = channel.permissions_for(g.me)
        required = ('view_channel', 'send_messages', 'embed_links', 'create_public_threads', 'send_messages_in_threads')
        missing = [p for p in required if not getattr(permissions, p)]
        if missing:
            raise ValueError('Faltam permissões no canal de CVEs: ' + ', '.join(missing))

        rows = self.q('SELECT r.* FROM cve_records r WHERE r.published>=? AND NOT EXISTS '
                      '(SELECT 1 FROM cve_deliveries d WHERE d.guild_id=? AND d.cve_id=r.id) ORDER BY r.published,r.id',
                      (max(s['cve_since'], time.time() - 30 * 86400), g.id))
        sent = 0
        for row in rows:
            current = self.center.settings(g.id)
            if not current['cve_enabled'] or current['cve_channel'] != channel.id:
                break
            cve = json.loads(row['payload'])
            if not matches(cve, current):
                continue
            message = await channel.send(embed=alert_embed(cve), allowed_mentions=discord.AllowedMentions.none())
            thread = None
            try:
                thread = await self.create_topic(message, cve)
            except discord.HTTPException:
                LOG.exception('Falha ao criar tópico para %s no servidor %s', cve['id'], g.id)
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
                raise ValueError('Consegui acessar o canal, mas não criar o tópico da CVE. Confira Criar Tópicos Públicos e Enviar Mensagens em Tópicos.')
            self.q('INSERT OR IGNORE INTO cve_deliveries(guild_id,cve_id,sent_at,message_id,thread_id) VALUES(?,?,?,?,?)',
                   (g.id, cve['id'], time.time(), message.id, thread.id))
            sent += 1
            if sent >= 10:
                break
        self.center.save(g.id, {'cve_error': ''})

    async def run(self):
        await self.center.bot.wait_until_ready()
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout, headers={'User-Agent': 'FSociety-CVE-Monitor/3.0'}) as session:
            while not self.center.bot.is_closed():
                try:
                    await self.cycle(session)
                except Exception:
                    LOG.exception('Falha no ciclo do monitor de CVEs; nova tentativa em 5 minutos')
                await asyncio.sleep(INTERVAL)

    async def cycle(self, session):
        targets = [g for g in self.center.bot.guilds if self.center.settings(g.id)['cve_enabled']]
        if not targets:
            return
        try:
            await self.fetch(session)
            self.q("DELETE FROM cve_state WHERE key='error'")
        except Exception as error:
            LOG.exception('Falha na consulta de CVEs; nova tentativa em 5 minutos')
            self.q("INSERT INTO cve_state(key,value) VALUES('error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (f'Consulta NVD falhou ({type(error).__name__}). Nova tentativa automática.',))
        for g in targets:
            try:
                await self.deliver(g)
            except Exception as error:
                LOG.exception('Falha nos alertas de CVE do servidor %s', g.id)
                message = str(error) if isinstance(error, ValueError) else 'Falha no envio ao Discord; confira canal e permissões.'
                self.center.save(g.id, {'cve_error': message[:300]})

    async def test_channel(self, i):
        await i.response.defer(ephemeral=True)
        s = self.center.settings(i.guild.id)
        channel = i.guild.get_channel(s['cve_channel']) if s['cve_channel'] else None
        if not isinstance(channel, discord.TextChannel):
            raise ValueError('Selecione um canal de texto para os alertas de CVE.')
        permissions = channel.permissions_for(i.guild.me)
        required = ('view_channel', 'send_messages', 'embed_links', 'create_public_threads', 'send_messages_in_threads')
        missing = [p for p in required if not getattr(permissions, p)]
        if missing:
            raise ValueError('O teste falhou por falta de permissões: ' + ', '.join(missing))
        e = discord.Embed(title='Teste do sistema de CVE', color=0x22C55E,
                          description='Canal configurado corretamente. Em alertas reais, cada CVE receberá uma mensagem e um tópico próprio para análise.')
        message = await channel.send(embed=e, allowed_mentions=discord.AllowedMentions.none())
        thread = await message.create_thread(name='TESTE-CVE • tópico automático', auto_archive_duration=60,
                                             reason='Teste do sistema de tópicos CVE')
        await thread.send('Tópico de teste criado com sucesso. Você pode apagá-lo depois do teste.',
                          allowed_mentions=discord.AllowedMentions.none())
        await reply(i, f'Teste enviado para {channel.mention} e tópico criado: {thread.mention}.')


def install(center):
    monitor = CVEMonitor(center)
    center.cves = monitor
    group = app_commands.Group(name='cve', description='Alertas contínuos de novas vulnerabilidades',
                               guild_only=True, default_permissions=discord.Permissions(manage_guild=True))

    @group.command(name='configurar', description='Escolhe canal, gravidade e ativa o monitor de CVEs')
    async def configure(i: discord.Interaction):
        await center.open(i, 'cves')

    @group.command(name='status', description='Mostra o estado do monitor e a última consulta')
    async def status(i: discord.Interaction):
        await i.response.send_message(embed=center.page(i.guild, 'cves', True), ephemeral=True)

    @group.command(name='testar', description='Testa mensagem e criação automática de tópico')
    async def test(i: discord.Interaction):
        await monitor.test_channel(i)

    center.bot.tree.add_command(group)
    return monitor
