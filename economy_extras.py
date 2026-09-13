"""Extras da Economia V2: F-Coin por call e ajuste de estoque no painel."""
import time
import discord
import control_panel as cp
import economy_v2 as economy
import voice_activity as voice

_INSTALLED=False
class StockModal(discord.ui.Modal,title='Ajustar estoque do produto'):
    stock=discord.ui.TextInput(label='Estoque',placeholder='Ex.: 10 • deixe vazio para ilimitado',required=False,max_length=8)
    def __init__(self,product_id,owner_id):super().__init__();self.product_id=int(product_id);self.owner_id=owner_id
    async def on_submit(self,i):
        raw=str(self.stock.value or '').strip()
        if raw and (not raw.isdigit() or not 0<=int(raw)<=10_000_000):return await i.response.send_message('Use um estoque entre 0 e 10.000.000, ou deixe vazio para ilimitado.',ephemeral=True)
        value=int(raw) if raw else None
        with economy._connect() as con:con.execute('UPDATE shop_products SET stock=? WHERE id=? AND guild_id=?',(value,self.product_id,i.guild.id))
        await i.response.send_message('Estoque atualizado.',ephemeral=True)
def _install_stock_panel():
    original=economy.StoreAdminView.__init__
    def init(self,center,owner,guild):
        original(self,center,owner,guild)
        async def stock(i):
            if not self.selected:return await cp.reply(i,'Selecione primeiro um produto.')
            await i.response.send_modal(StockModal(self.selected,owner))
        self.button('Ajustar estoque',stock,discord.ButtonStyle.secondary,row=3)
    economy.StoreAdminView.__init__=init
def _install_voice_coin_rewards():
    economy.cp.DEFAULTS.setdefault('economy_voice_reward',5);economy.cp.DEFAULTS.setdefault('economy_voice_interval',600)
    with economy._connect() as con:
        cols={r[1] for r in con.execute('PRAGMA table_info(economy_meta)')}
        if 'voice_coin_credit_seconds' not in cols:con.execute('ALTER TABLE economy_meta ADD COLUMN voice_coin_credit_seconds INTEGER NOT NULL DEFAULT 0')
    original=voice._award_voice_xp
    def award(center,gid,uid,seconds,channel_id=None):
        result=original(center,gid,uid,seconds,channel_id);settings=center.settings(int(gid)) if center else {}
        if not settings.get('economy_enabled',True):return result
        seconds=max(0,int(seconds or 0));interval=max(60,int(settings.get('economy_voice_interval') or 600));reward=max(0,int(settings.get('economy_voice_reward') or 5))
        if not seconds or not reward:return result
        with economy._connect() as con:
            economy._ensure_user(con,gid,uid);row=con.execute('SELECT voice_coin_credit_seconds FROM economy_meta WHERE guild_id=? AND user_id=?',(gid,uid)).fetchone();credit=int(row['voice_coin_credit_seconds'] or 0)+seconds;intervals,remainder=divmod(credit,interval);con.execute('UPDATE economy_meta SET voice_coin_credit_seconds=? WHERE guild_id=? AND user_id=?',(remainder,gid,uid))
            if intervals:
                total=intervals*reward;con.execute('UPDATE users SET coins=coins+? WHERE guild_id=? AND user_id=?',(total,gid,uid));economy._record(con,gid,uid,total,'voice_activity',f'Atividade em call • {intervals} ciclo(s)')
        return result
    voice._award_voice_xp=award
def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True;_install_stock_panel();_install_voice_coin_rewards();print('[OK] Economia V2 • moedas por call e estoque da loja carregados')
