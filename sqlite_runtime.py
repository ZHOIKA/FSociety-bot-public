"""Prepara o SQLite gravável e recupera uma vez snapshots afetados pelo pool antigo."""
import json, os, sqlite3, tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent; _INSTALLED=False; _ORIGINAL_CONNECT=None; _RECOVERY_MARKER='recovery/sqlite-pool-v1.json'; _RECOVERY_QUARANTINE='recovery/pre-repair-latest.db'
def _writable_dir():
    configured=os.getenv('FSOCIETY_DATA_DIR','').strip(); candidates=[Path(configured)] if configured else []; candidates += [Path('/data'),Path(tempfile.gettempdir())/'fsociety-bot']
    for directory in candidates:
        try:directory.mkdir(parents=True,exist_ok=True);probe=directory/'.sqlite-write-test';probe.write_text('ok');probe.unlink(missing_ok=True);return directory
        except OSError:continue
    raise RuntimeError('Nenhum diretório gravável disponível para o SQLite.')
def _storage_config():
    url=os.getenv('SUPABASE_URL','').strip().rstrip('/');key=os.getenv('SUPABASE_SECRET_KEY','').strip() or os.getenv('SUPABASE_SERVICE_ROLE_KEY','').strip();bucket=os.getenv('SUPABASE_BUCKET','fsociety-backups').strip() or 'fsociety-backups';return (url,key,bucket) if url and key and bucket else None
def _storage_headers(key,content_type=None):
    h={'apikey':key}
    if key.startswith('eyJ') and key.count('.')==2:h['Authorization']=f'Bearer {key}'
    if content_type:h['Content-Type']=content_type
    return h
def _object_url(url,bucket,path,authenticated=False):return f"{url}/storage/v1/object/{'authenticated/' if authenticated else ''}{quote(bucket,safe='')}/{quote(path.lstrip('/'),safe='/')}"
def _storage_get(config,path,timeout=20):
    url,key,bucket=config;req=Request(_object_url(url,bucket,path,True),headers=_storage_headers(key),method='GET')
    with urlopen(req,timeout=timeout) as r:return r.read()
def _storage_put(config,path,payload,content_type='application/octet-stream',timeout=30):
    url,key,bucket=config;h=_storage_headers(key,content_type);h['x-upsert']='true';req=Request(_object_url(url,bucket,path),data=payload,headers=h,method='POST')
    with urlopen(req,timeout=timeout) as r:r.read()
def _http_error_detail(exc):
    try:
        raw=exc.read().decode('utf-8','replace').strip();data=json.loads(raw) if raw else {};return str(data.get('message') or data.get('error') or data.get('statusCode') or raw)
    except Exception:return ''
def _marker_exists(config):
    try:_storage_get(config,_RECOVERY_MARKER,8);return True
    except HTTPError as exc:
        detail=_http_error_detail(exc).lower()
        if exc.code==404 or (exc.code==400 and 'object not found' in detail):return False
        raise
def _snapshot_stats(original,data_dir,payload):
    if not payload or not payload.startswith(b'SQLite format 3\x00'):return None
    path=data_dir/'.fsociety-recovery-check.db'
    try:
        path.write_bytes(payload);con=original(str(path),timeout=10)
        try:
            if con.execute('PRAGMA quick_check').fetchone()[0]!='ok':return None
            tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'users' not in tables:return None
            cols={r[1] for r in con.execute('PRAGMA table_info(users)')};metrics=[n for n in ('xp','level','coins','messages','warnings') if n in cols];active_sql=' OR '.join(f'COALESCE({n},0)<>0' for n in metrics) or '0';active=int(con.execute(f'SELECT COUNT(*) FROM users WHERE {active_sql}').fetchone()[0] or 0);users=int(con.execute('SELECT COUNT(*) FROM users').fetchone()[0] or 0);totals={n:int(con.execute(f'SELECT COALESCE(SUM({n}),0) FROM users').fetchone()[0] or 0) if n in cols else 0 for n in ('xp','level','coins','messages')};return {'score':(active,totals['xp'],totals['messages'],users,totals['level'],totals['coins']),'users':users,'active_users':active,**totals}
        finally:con.close()
    except (OSError,sqlite3.DatabaseError):return None
    finally:path.unlink(missing_ok=True)
def _emergency_recover(original,data_dir):
    config=_storage_config()
    if config is None:return
    try:
        if _marker_exists(config):return
    except Exception:return
    candidates=[]
    for order,name in enumerate(['latest/fsociety.db']+[f'history/slot-{s}.db' for s in range(5)]):
        try:
            payload=_storage_get(config,name);stats=_snapshot_stats(original,data_dir,payload)
            if stats:candidates.append({'name':name,'payload':payload,'stats':stats,'order':order})
        except Exception:continue
    if not candidates:return
    best=max(candidates,key=lambda x:(*x['stats']['score'],-x['order']));latest=next((x for x in candidates if x['name']=='latest/fsociety.db'),None)
    try:
        if latest:_storage_put(config,_RECOVERY_QUARANTINE,latest['payload'])
        tmp=data_dir/'.fsociety-recovered.db';tmp.write_bytes(best['payload']);os.replace(tmp,data_dir/'fsociety.db');_storage_put(config,'latest/fsociety.db',best['payload']);marker=json.dumps({'completed_at':datetime.now(timezone.utc).isoformat(),'source':best['name']},separators=(',',':')).encode();_storage_put(config,_RECOVERY_MARKER,marker,'application/json')
    except Exception as exc:print(f'[DB][RECOVERY] Falha: {type(exc).__name__}: {exc}')
def install():
    global _INSTALLED,_ORIGINAL_CONNECT
    if _INSTALLED:return
    data_dir=_writable_dir();original=sqlite3.connect;_ORIGINAL_CONNECT=original;_emergency_recover(original,data_dir);writable_db=data_dir/'fsociety.db'
    def connect(database,*args,**kwargs):
        try:
            requested=Path(os.fspath(database))
            if requested.name=='fsociety.db' and requested.parent.resolve()==ROOT.resolve():database=str(writable_db)
        except (TypeError,ValueError,OSError):pass
        return original(database,*args,**kwargs)
    sqlite3.connect=connect;_INSTALLED=True;print(f'[OK] SQLite antecipado • {writable_db} • sem pool persistente')
