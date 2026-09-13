"""Mensagens e diagnóstico detalhado para erros do monitor de commits, sem expor metadados do token."""
import os
import aiohttp
import commit_monitor as cm

_INSTALLED=False

def _normalize_token(value):
    token=(value or '').strip()
    if token.startswith('FSOCIETY_GITHUB_TOKEN=') or token.startswith('GITHUB_TOKEN='):token=token.split('=',1)[1].strip()
    if token.lower().startswith('bearer '):token=token[7:].strip()
    elif token.lower().startswith('token '):token=token[6:].strip()
    if len(token)>=2 and token[0]==token[-1] and token[0] in {'"',"'"}:token=token[1:-1].strip()
    return token
def _token_info(token):
    if not token:return 'ausente'
    if token.startswith('github_pat_'):return 'fine-grained PAT configurado'
    if token.startswith('ghp_'):return 'classic PAT configurado'
    return 'token configurado'
async def _auth_diagnostic(session,headers):
    try:
        async with session.get('https://api.github.com/user',headers=headers,timeout=aiohttp.ClientTimeout(total=15)) as response:
            if response.status==200:return True,str((await response.json()).get('login') or 'conta autenticada')
            if response.status==401:return False,'token inválido, expirado, revogado ou valor incorreto no ambiente'
            return False,f'GitHub /user respondeu HTTP {response.status}'
    except Exception as exc:return False,f'falha ao validar token: {type(exc).__name__}: {exc}'
async def _repo_diagnostic(session,headers):
    try:
        async with session.get(f'https://api.github.com/repos/{cm.GITHUB_REPO}',headers=headers,timeout=aiohttp.ClientTimeout(total=15)) as response:return response.status
    except Exception:return 0
async def _fetch_latest_commit(session):
    token=_normalize_token(cm.GITHUB_TOKEN);cm.GITHUB_TOKEN=token;url=f'https://api.github.com/repos/{cm.GITHUB_REPO}/commits/{cm.GITHUB_BRANCH}';headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'FSociety-Commit-Monitor'}
    if token:headers['Authorization']=f'Bearer {token}'
    async with session.get(url,headers=headers,timeout=aiohttp.ClientTimeout(total=20)) as response:
        if response.status==401:raise RuntimeError(f'Token do GitHub inválido, expirado, revogado ou mal formatado (HTTP 401). Diagnóstico seguro: {_token_info(token)}.')
        if response.status==403:
            accepted=response.headers.get('X-Accepted-GitHub-Permissions','');extra=f' Permissão esperada pelo GitHub: {accepted}.' if accepted else '';raise RuntimeError(f'GitHub recusou a consulta (HTTP 403). Verifique permissões do token ou limite da API.{extra}')
        if response.status==404:
            if not token:raise RuntimeError('Repositório privado: FSOCIETY_GITHUB_TOKEN não está configurado.')
            auth_ok,auth_detail=await _auth_diagnostic(session,headers)
            if not auth_ok:raise RuntimeError(f'O token chegou ao bot, mas a autenticação falhou: {auth_detail}. Diagnóstico seguro: {_token_info(token)}.')
            repo_status=await _repo_diagnostic(session,headers)
            if repo_status==200:raise RuntimeError(f'Token autenticado como {auth_detail} e com acesso ao repositório {cm.GITHUB_REPO}, mas a branch/ref `{cm.GITHUB_BRANCH}` não foi encontrada.')
            if repo_status==404:raise RuntimeError(f'Token válido e autenticado como {auth_detail}, mas sem acesso ao repositório {cm.GITHUB_REPO}. No Fine-grained PAT, conceda Contents: Read-only. Diagnóstico seguro: {_token_info(token)}.')
            raise RuntimeError(f'Token autenticado como {auth_detail}, porém o GitHub retornou 404 para {cm.GITHUB_REPO}@{cm.GITHUB_BRANCH}.')
        if response.status>=400:raise RuntimeError(f'GitHub respondeu HTTP {response.status}.')
        return await response.json()
def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True;raw=os.getenv('FSOCIETY_GITHUB_TOKEN','').strip() or os.getenv('GITHUB_TOKEN','').strip();cm.GITHUB_TOKEN=_normalize_token(raw);cm._fetch_latest_commit=_fetch_latest_commit;print(f'[OK] Commits • diagnóstico GitHub carregado • {_token_info(cm.GITHUB_TOKEN)}')
