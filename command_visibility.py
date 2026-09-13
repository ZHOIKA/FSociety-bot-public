"""Visibilidade dos slash commands públicos do F SOCIETY.

Comandos de usuário sem permissões administrativas explícitas recebem apenas
`Usar comandos de aplicativo` como permissão padrão. Comandos de configuração,
moderação e grupos preservam as permissões já definidas pelo projeto.
"""
import os
import discord
from discord import app_commands

import control_panel as cp
from commit_monitor import install as install_commit_monitor
from commit_boot_check import install as install_commit_boot_check
from commit_delivery import install as install_commit_delivery
from commit_error_messages import install as install_commit_error_messages
from commit_author_override import install as install_commit_author_override
from commit_runtime_hook import install as install_commit_runtime_hook
from commit_panel_integration import install as install_commit_panel

_INSTALLED = False
_VERBOSE = os.getenv('FSOCIETY_VERBOSE_LOGS','0').strip().lower() in {'1','true','yes','on'}


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    install_commit_monitor()
    install_commit_boot_check()
    install_commit_delivery()
    install_commit_error_messages()
    install_commit_author_override()
    install_commit_runtime_hook()
    install_commit_panel()

    original_sync = cp.CommandTree.sync

    async def sync(self, *args, **kwargs):
        public = []
        protected = []

        for command in self.get_commands():
            if isinstance(command, app_commands.Group):
                protected.append(command.name)
                continue
            if command.default_permissions is None:
                command.default_permissions = discord.Permissions(use_application_commands=True)
                public.append(command.name)
            else:
                protected.append(command.name)

        if _VERBOSE:
            if public:
                print('Comandos públicos para membros: ' + ', '.join(sorted(public)))
            if protected:
                print('Comandos protegidos/grupos preservados: ' + ', '.join(sorted(protected)))

        return await original_sync(self, *args, **kwargs)

    cp.CommandTree.sync = sync
    if _VERBOSE:
        print('Comandos: visibilidade pública de comandos comuns carregada')
