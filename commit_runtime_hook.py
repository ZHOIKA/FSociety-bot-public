"""Hook final do monitor de commits: boot e Status sem depender de wrappers de run()."""
import asyncio
from discord.ext import commands
import commit_monitor as cm
import commit_delivery as delivery

_INSTALLED=False
def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True;original_bot_init=commands.Bot.__init__
    def bot_init(self,*args,**kwargs):
        original_bot_init(self,*args,**kwargs);cm.CommitConfigView=delivery.ReliableCommitConfigView
        if not getattr(self,'_fsociety_commit_runtime_listener',False):
            self._fsociety_commit_runtime_listener=True
            async def commit_runtime_on_ready():
                if getattr(self,'_fsociety_commit_runtime_checked',False):return
                self._fsociety_commit_runtime_checked=True
                try:await delivery.check_now(self,None,'Boot')
                except asyncio.CancelledError:raise
                except Exception as exc:cm._state_set('boot_check_state',f'boot:erro:{type(exc).__name__}: {exc}');cm._state_set('poll_error',f'{type(exc).__name__}: {exc}');print(f'[AVISO][COMMITS] Runtime boot • {type(exc).__name__}: {exc}')
            self.add_listener(commit_runtime_on_ready,'on_ready')
    commands.Bot.__init__=bot_init;print('[OK] Commits • hook runtime direto no Bot.__init__ carregado')
