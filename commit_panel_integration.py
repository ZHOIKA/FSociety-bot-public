"""Integração explícita do monitor de commits com o bot e /configurar."""
import discord
from discord.ext import commands
import control_panel as cp
import commit_monitor as cm

_INSTALLED=False
def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True;cp.SECTIONS.setdefault('commits',('Commits e deploys','🚀','Canal, testes e status das notificações'))
    original_page=cp.Center.page
    def page(self,g,section='home',admin=False):
        embed=original_page(self,g,section,admin)
        if section=='commits':
            cid=cm._get_channel(g.id);embed.add_field(name='Canal de notificações',value=f'<#{cid}>' if cid else 'Não configurado');embed.add_field(name='Monitor GitHub',value=f'`{cm.GITHUB_REPO}@{cm.GITHUB_BRANCH}`');embed.add_field(name='Configuração',value='Escolha o canal e use **Enviar teste** para confirmar.',inline=False)
        return embed
    cp.Center.page=page;original_panel_init=cp.Panel.__init__
    def panel_init(self,center,owner,section='home',admin=True):
        original_panel_init(self,center,owner,section,admin)
        if admin and section=='commits':
            async def open_config(i):
                cid=cm._get_channel(i.guild.id);embed=discord.Embed(title='🚀 Commits & Deploys',description=f'**Canal atual:** {f"<#{cid}>" if cid else "não configurado"}\n\nEscolha o canal abaixo e depois envie um teste.',color=0x5865F2);await i.response.edit_message(embed=embed,view=cm.CommitConfigView(center.bot,owner))
            self.button('Configurar commits',open_config,discord.ButtonStyle.primary,row=1,emoji='🚀')
    cp.Panel.__init__=panel_init;original_bot_init=commands.Bot.__init__
    def bot_init(self,*a,**kw):original_bot_init(self,*a,**kw);cm._register_commands(self)
    commands.Bot.__init__=bot_init;print('[OK] Commits • integração direta + menu /configurar carregados')
