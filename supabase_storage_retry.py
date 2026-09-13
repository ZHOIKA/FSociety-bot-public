"""Retry pequeno para falhas transitórias do Supabase Storage."""
import os
import time
import urllib.request
from urllib.error import HTTPError

_INSTALLED = False
_ORIGINAL_URLOPEN = None
RETRY_STATUS = {502, 503, 504}


def install():
    global _INSTALLED, _ORIGINAL_URLOPEN
    if _INSTALLED:
        return

    original = urllib.request.urlopen
    supabase_url = os.getenv('SUPABASE_URL', '').strip().rstrip('/')

    def should_retry_target(target):
        try:
            url = getattr(target, 'full_url', target)
            url = str(url)
        except Exception:
            return False
        if '/storage/v1/object/' not in url:
            return False
        return not supabase_url or url.startswith(supabase_url)

    def urlopen_with_retry(url, *args, **kwargs):
        if not should_retry_target(url):
            return original(url, *args, **kwargs)
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                return original(url, *args, **kwargs)
            except HTTPError as exc:
                if exc.code not in RETRY_STATUS or attempt >= attempts:
                    raise
                delay = 2 ** (attempt - 1)
                print(f'[AVISO][DB] Supabase HTTP {exc.code} • nova tentativa em {delay}s ({attempt}/{attempts})')
                time.sleep(delay)

    _ORIGINAL_URLOPEN = original
    urllib.request.urlopen = urlopen_with_retry
    _INSTALLED = True
    print('[OK] Supabase Storage • retry para HTTP 502/503/504 carregado')
