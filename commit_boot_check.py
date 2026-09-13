"""Checagem de commits usada pelo status; o boot automatico fica no runtime hook."""
import asyncio
from datetime import datetime, timezone
import aiohttp
import commit_monitor as cm

_INSTALLED=False
async def _check_once(bot,source='Boot'):
    key=f'last_sha:{cm.GITHUB_REPO}:{cm.GITHUB_BRANCH}';error_key='poll_error';cm._state_set('boot_check_state',f'{source.lower()}:executando')
    try:
        async with aiohttp.ClientSession() as session:commit=await cm._fetch_latest_commit(session)
        sha=str(commit.get('sha') or '')
        if not sha:raise RuntimeError('GitHub retornou commit sem SHA.')
        previous=cm._state_get(key);cm._state_set('poll_ok_at',int(datetime.now(timezone.utc).timestamp()));cm._state_set(error_key,'')
        if sha==previous:cm._state_set('boot_check_state',f'{source.lower()}:sem novidade:{sha}');return False,f'Nenhum commit novo. HEAD atual: `{sha[:12]}`.'
        result=await cm._broadcast(bot,cm._github_payload(commit),'push');sent=result[0] if isinstance(result,tuple) else int(result)
        if sent<1:raise RuntimeError('Commit encontrado, mas nenhum canal configurado pôde receber a mensagem.')
        cm._state_set(key,sha);cm._state_set('boot_check_state',f'{source.lower()}:enviado:{sha}');return True,f'Novo commit `{sha[:12]}` detectado e enviado para {sent} canal(is).'
    except asyncio.CancelledError:raise
    except Exception as exc:
        message=f'{type(exc).__name__}: {exc}';cm._state_set(error_key,message);cm._state_set('boot_check_state',f'{source.lower()}:erro:{message}');print(f'[AVISO][COMMITS] {source} • {message}');return False,f'Falha ao verificar: `{message[:400]}`'
async def _boot_check(bot):return await _check_once(bot,'Boot')
def _status_text(gid):
    cid=cm._get_channel(gid);last=cm._state_get(f'last_sha:{cm.GITHUB_REPO}:{cm.GITHUB_BRANCH}');error=cm._state_get('poll_error') or '';state=cm._state_get('boot_check_state') or 'aguardando on_ready';text=f'**Canal:** {f"<#{cid}>" if cid else "não configurado"}\n**Monitor GitHub:** `boot + Status manual`\n**Repositório:** `{cm.GITHUB_REPO}` (`{cm.GITHUB_BRANCH}`)\n**Autenticação:** `{"configurada" if cm.GITHUB_TOKEN else "AUSENTE"}`\n**Estado:** `{state[:180]}`\n**Último commit enviado:** `{last[:12] if last else "nenhum"}`'
    if error:text+=f'\n**Erro:** `{error[:500]}`'
    return text
def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True
    async def no_poll(bot):return
    cm._poll_loop=no_poll;cm._status_text=_status_text;print('[OK] Commits • boot legado desativado; runtime hook ativo')
