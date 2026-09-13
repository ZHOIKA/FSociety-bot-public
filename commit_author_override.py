"""Força o nome exibido no campo Autor das notificações de commit."""
import copy
import commit_monitor as cm
_INSTALLED=False
def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True;original_embed=cm._embed
    def embed_with_fixed_author(payload,event='deploy'):
        data=copy.deepcopy(payload);head=data.get('head_commit') or data.get('commit')
        if not isinstance(head,dict):head={};data['head_commit']=head
        head['author']={'name':'zk'};return original_embed(data,event)
    cm._embed=embed_with_fixed_author;print('[OK] Commits • autor fixo configurado como zk')
