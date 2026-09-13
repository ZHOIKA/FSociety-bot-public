"""Entrega confiável de commits para o canal configurado."""
from datetime import datetime, timezone
import aiohttp, discord
import commit_monitor as cm
import commit_boot_check as cb

_INSTALLED=False
async def _deliver_commit(channel,commit):
    payload=cm._github_payload(commit);sha=str(commit.get('sha') or '');existing=await cm._find_commit_message(channel,sha) if sha else None
    if existing is not None:return True,await cm._create_suggestion_thread(existing,payload),'recuperado'
    msg=await channel.send(embed=cm._embed(payload,'push'));return True,await cm._create_suggestion_thread(msg,payload),'enviado'
async def check_now(bot,guild=None,source='Status'):
    key=f'last_sha:{cm.GITHUB_REPO}:{cm.GITHUB_BRANCH}';error_key='poll_error';cm._state_set('boot_check_state',f'{source.lower()}:consultando GitHub')
    try:
        async with aiohttp.ClientSession() as session:commit=await cm._fetch_latest_commit(session)
        sha=str(commit.get('sha') or '')
        if not sha:raise RuntimeError('GitHub retornou commit sem SHA.')
        previous=cm._state_get(key);cm._state_set('poll_ok_at',int(datetime.now(timezone.utc).timestamp()));cm._state_set(error_key,'');guilds=[guild] if guild is not None else list(bot.guilds);sent=threads=0;diagnostics=[]
        for g in guilds:
            cid=cm._get_channel(g.id)
            if not cid:diagnostics.append(f'{g.id}: canal não configurado');continue
            ch=g.get_channel(cid) or bot.get_channel(cid)
            if ch is None:
                try:ch=await bot.fetch_channel(cid)
                except discord.HTTPException as exc:diagnostics.append(f'{g.id}: canal {cid} HTTP {getattr(exc,"status","?")}');continue
            me=g.me
            if me is not None and hasattr(ch,'permissions_for'):
                perms=ch.permissions_for(me);missing=[label for attr,label in (('view_channel','Ver canal'),('send_messages','Enviar mensagens'),('embed_links','Incorporar links'),('create_public_threads','Criar tópicos')) if hasattr(perms,attr) and not getattr(perms,attr)]
                if missing:diagnostics.append(f'{g.id}: faltam permissões: {", ".join(missing)}');continue
            try:
                if sha==previous:
                    existing=await cm._find_commit_message(ch,sha)
                    if existing and await cm._create_suggestion_thread(existing,cm._github_payload(commit)):threads+=1
                    continue
                _,ok,_=await _deliver_commit(ch,commit);sent+=1;threads+=int(ok)
            except discord.HTTPException as exc:diagnostics.append(f'{g.id}: Discord HTTP {getattr(exc,"status","?")}')
        if sha==previous:cm._state_set('boot_check_state',f'{source.lower()}:sem novidade:{sha}:topicos={threads}');return False,f'Nenhum commit novo. HEAD atual: `{sha[:12]}`. Tópicos confirmados/reparados: {threads}.'
        if sent<1:raise RuntimeError(f'Commit {sha[:12]} encontrado, mas não foi enviado: {"; ".join(diagnostics) or "nenhum canal elegível"}')
        if threads<sent:raise RuntimeError(f'Commit {sha[:12]} enviado, mas um ou mais tópicos falharam.')
        cm._state_set(key,sha);cm._state_set(f'threads_ready:{sha}','1');cm._state_set('boot_check_state',f'{source.lower()}:enviado:{sha}:topicos={threads}');return True,f'Novo commit `{sha[:12]}` enviado para {sent} canal(is), com {threads} tópico(s).'
    except Exception as exc:
        message=f'{type(exc).__name__}: {exc}';cm._state_set(error_key,message);cm._state_set('boot_check_state',f'{source.lower()}:erro:{message}');print(f'[AVISO][COMMITS] {source} • {message}');return False,f'Falha ao verificar/enviar: `{message[:700]}`'
class ReliableCommitConfigView(discord.ui.View):
    def __init__(self,bot,owner_id):super().__init__(timeout=600);self.bot=bot;self.owner_id=owner_id;self.add_item(cm.CommitChannelSelect(owner_id))
    async def interaction_check(self,i):
        if i.user.id!=self.owner_id:await i.response.send_message('Este menu pertence a quem o abriu.',ephemeral=True);return False
        return True
    @discord.ui.button(label='Enviar teste',style=discord.ButtonStyle.success,emoji='🚀',row=1)
    async def test(self,i,b):
        cid=cm._get_channel(i.guild.id)
        if not cid:return await i.response.send_message('Escolha um canal primeiro.',ephemeral=True)
        ch=i.guild.get_channel(cid) or self.bot.get_channel(cid)
        if ch is None:return await i.response.send_message('Canal não encontrado.',ephemeral=True)
        payload={'ref':f'refs/heads/{cm.GITHUB_BRANCH}','head_commit':{'id':'abc123def4567890','message':'Teste do monitor de commits','author':{'name':i.user.display_name}}};msg=await ch.send(embed=cm._embed(payload,'push'));ok=await cm._create_suggestion_thread(msg,payload);await i.response.send_message(f'Teste enviado em {ch.mention}{" com tópico." if ok else ", mas o tópico falhou."}',ephemeral=True)
    @discord.ui.button(label='Status',style=discord.ButtonStyle.secondary,emoji='📡',row=1)
    async def status(self,i,b):
        await i.response.defer(ephemeral=True,thinking=True);_,result=await check_now(self.bot,i.guild,'Status');await i.followup.send(f'{result}\n\n{cm._status_text(i.guild.id)}',ephemeral=True)
def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True;cm.CommitConfigView=ReliableCommitConfigView
    async def boot_check(bot,source='Boot'):return await check_now(bot,None,source)
    cb._check_once=boot_check;print('[OK] Commits • entrega direta + tópicos automáticos carregados')
