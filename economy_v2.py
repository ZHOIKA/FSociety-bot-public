"""Economia V2 do F SOCIETY: F-Coin, extrato, loja, inventário e cosméticos."""
import random,re,sqlite3,time
from datetime import datetime,timezone
import discord
from discord import app_commands
from discord.ext import commands
import control_panel as cp

_INSTALLED=False;_ORIGINAL_BOT_INIT=None
DEFAULT_PRODUCTS=(('Banner Cyber','Tema roxo/cyber para o banner dinâmico do /perfil.',8000,'banner_theme','cyber',None),('Banner Angelcore','Tema claro angelcore com brilho violeta para o /perfil.',12000,'banner_theme','angelcore',None),('Banner Crimson','Tema escuro vermelho premium para o /perfil.',18000,'banner_theme','crimson',None),('Cargo Personalizado','Voucher para criar um cargo pessoal.',15000,'custom_role','',None),('XP Boost 2x • 1 hora','Dobra o XP recebido por 1 hora.',10000,'xp_boost','3600:2',None))
THEMES={'default':{'name':'F SOCIETY','bg':(8,8,13),'panel':(15,15,23),'panel2':(21,21,31),'text':(242,242,248),'muted':(156,156,174),'accent':None},'cyber':{'name':'CYBER','bg':(5,6,12),'panel':(12,13,25),'panel2':(18,19,36),'text':(244,242,255),'muted':(145,140,170),'accent':(139,92,246)},'angelcore':{'name':'ANGELCORE','bg':(18,17,27),'panel':(29,27,42),'panel2':(39,35,55),'text':(252,248,255),'muted':(201,190,218),'accent':(214,184,255)},'crimson':{'name':'CRIMSON','bg':(11,5,8),'panel':(24,10,15),'panel2':(35,14,21),'text':(255,242,245),'muted':(190,145,156),'accent':(225,48,78)}}
def _db_path():
    from pathlib import Path;return str(Path(__file__).resolve().parent/'fsociety.db')
def _connect():c=sqlite3.connect(_db_path());c.row_factory=sqlite3.Row;return c
def _ensure_schema():
    with _connect() as con:con.executescript('''CREATE TABLE IF NOT EXISTS economy_transactions(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,amount INTEGER NOT NULL,kind TEXT NOT NULL,description TEXT NOT NULL,related_user_id INTEGER,created_at INTEGER NOT NULL);CREATE INDEX IF NOT EXISTS economy_tx_user ON economy_transactions(guild_id,user_id,id DESC);CREATE TABLE IF NOT EXISTS economy_meta(guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,daily_streak INTEGER NOT NULL DEFAULT 0,last_daily_at INTEGER NOT NULL DEFAULT 0,message_coin_at INTEGER NOT NULL DEFAULT 0,boost_until INTEGER NOT NULL DEFAULT 0,boost_multiplier INTEGER NOT NULL DEFAULT 1,active_theme TEXT NOT NULL DEFAULT 'default',PRIMARY KEY(guild_id,user_id));CREATE TABLE IF NOT EXISTS shop_products(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',price INTEGER NOT NULL,type TEXT NOT NULL,value TEXT NOT NULL DEFAULT '',stock INTEGER,active INTEGER NOT NULL DEFAULT 1,created_at INTEGER NOT NULL);CREATE TABLE IF NOT EXISTS inventory(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,user_id INTEGER NOT NULL,product_id INTEGER,item_type TEXT NOT NULL,item_value TEXT NOT NULL DEFAULT '',item_name TEXT NOT NULL,quantity INTEGER NOT NULL DEFAULT 1,active INTEGER NOT NULL DEFAULT 1,acquired_at INTEGER NOT NULL,used_at INTEGER NOT NULL DEFAULT 0);''')
def _ensure_user(con,gid,uid):con.execute('INSERT OR IGNORE INTO users(guild_id,user_id) VALUES(?,?)',(int(gid),int(uid)));con.execute('INSERT OR IGNORE INTO economy_meta(guild_id,user_id) VALUES(?,?)',(int(gid),int(uid)))
def _coin_name(center,gid):
    try:return str(center.settings(gid).get('economy_coin_name') or 'F-Coin')[:24]
    except Exception:return 'F-Coin'
def _fmt(value):return f'{int(value or 0):,}'.replace(',','.')
def _record(con,gid,uid,amount,kind,description,related_user_id=None):con.execute('INSERT INTO economy_transactions(guild_id,user_id,amount,kind,description,related_user_id,created_at) VALUES(?,?,?,?,?,?,?)',(int(gid),int(uid),int(amount),str(kind)[:40],str(description)[:300],related_user_id,int(time.time())))
def balance(gid,uid):
    with _connect() as con:_ensure_user(con,gid,uid);r=con.execute('SELECT coins FROM users WHERE guild_id=? AND user_id=?',(gid,uid)).fetchone();return int(r['coins'] or 0)
def profile_theme(center,gid,uid):
    with _connect() as con:_ensure_user(con,gid,uid);r=con.execute('SELECT active_theme FROM economy_meta WHERE guild_id=? AND user_id=?',(gid,uid)).fetchone()
    key=str(r['active_theme'] or 'default') if r else 'default';return dict(THEMES.get(key,THEMES['default']),key=key)
def xp_multiplier(gid,uid):
    now=int(time.time())
    with _connect() as con:_ensure_user(con,gid,uid);r=con.execute('SELECT boost_until,boost_multiplier FROM economy_meta WHERE guild_id=? AND user_id=?',(gid,uid)).fetchone();return max(1,int(r['boost_multiplier'] or 1)) if r and int(r['boost_until'] or 0)>now else 1
def _seed_products(gid):
    with _connect() as con:
        if con.execute('SELECT COUNT(*) FROM shop_products WHERE guild_id=?',(gid,)).fetchone()[0]:return False
        now=int(time.time());con.executemany('INSERT INTO shop_products(guild_id,name,description,price,type,value,stock,active,created_at) VALUES(?,?,?,?,?,?,?,1,?)',[(gid,*row,now) for row in DEFAULT_PRODUCTS]);return True
def _products(gid,active_only=True):
    _seed_products(gid)
    with _connect() as con:return con.execute('SELECT * FROM shop_products WHERE guild_id=?'+(' AND active=1' if active_only else '')+' ORDER BY price,id',(gid,)).fetchall()
def _inventory(gid,uid):
    with _connect() as con:return con.execute('SELECT * FROM inventory WHERE guild_id=? AND user_id=? AND active=1 ORDER BY id DESC',(gid,uid)).fetchall()
def _product_type_label(k):return {'banner_theme':'Tema de perfil','custom_role':'Cargo personalizado','role':'Cargo','xp_boost':'Boost de XP'}.get(k,k)
def _shop_embed(center,guild,user_id=None):
    p=_products(guild.id);coin=_coin_name(center,guild.id);e=center.embed(guild,'🛍️ F SOCIETY • LOJA','Cosméticos, cargos e vantagens comprados com a moeda do servidor.',banner=False)
    if user_id:e.add_field(name='Seu saldo',value=f'**{_fmt(balance(guild.id,user_id))} {coin}**',inline=False)
    for x in p[:10]:e.add_field(name=f"#{x['id']} • {x['name']}",value=f"{x['description'][:170]}\n**{_fmt(x['price'])} {coin}** • {_product_type_label(x['type'])} • estoque {'∞' if x['stock'] is None else x['stock']}",inline=False)
    return e
def _inventory_embed(center,guild,user):
    items=_inventory(guild.id,user.id);theme=profile_theme(center,guild.id,user.id);e=center.embed(guild,f'🎒 Inventário • {user.display_name}',f'Tema ativo: **{theme["name"]}**',banner=False)
    if not items:e.description+='\n\nSeu inventário está vazio.'
    for x in items[:20]:e.add_field(name=f"#{x['id']} • {x['item_name']}",value=f"{_product_type_label(x['item_type'])} • adquirido <t:{x['acquired_at']}:R>",inline=False)
    return e
def _statement_embed(center,guild,user):
    with _connect() as con:rows=con.execute('SELECT * FROM economy_transactions WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 12',(guild.id,user.id)).fetchall()
    e=center.embed(guild,f'📒 Extrato • {user.display_name}',f'Saldo atual: **{_fmt(balance(guild.id,user.id))} {_coin_name(center,guild.id)}**',banner=False);e.add_field(name='Últimas movimentações',value='\n'.join(f"`{'+' if r['amount']>0 else ''}{_fmt(r['amount'])}` • {r['description']} • <t:{r['created_at']}:R>" for r in rows) or 'Nenhuma movimentação.',inline=False);return e
def _purchase(center,gid,uid,pid):
    now=int(time.time())
    with _connect() as con:
        _ensure_user(con,gid,uid);p=con.execute('SELECT * FROM shop_products WHERE id=? AND guild_id=? AND active=1',(pid,gid)).fetchone()
        if not p:raise ValueError('Esse produto não está disponível.')
        if p['stock'] is not None and int(p['stock'])<=0:raise ValueError('Esse produto está esgotado.')
        if not con.execute('UPDATE users SET coins=coins-? WHERE guild_id=? AND user_id=? AND coins>=?',(p['price'],gid,uid,p['price'])).rowcount:raise ValueError('Saldo insuficiente.')
        if p['stock'] is not None:con.execute('UPDATE shop_products SET stock=stock-1 WHERE id=?',(p['id'],))
        _record(con,gid,uid,-int(p['price']),'shop_purchase',f"Compra: {p['name']}")
        if p['type'] in ('banner_theme','custom_role'):con.execute('INSERT INTO inventory(guild_id,user_id,product_id,item_type,item_value,item_name,quantity,active,acquired_at) VALUES(?,?,?,?,?,?,1,1,?)',(gid,uid,p['id'],p['type'],p['value'],p['name'],now))
        elif p['type']=='xp_boost':
            try:duration,mult=(int(v) for v in str(p['value']).split(':',1))
            except Exception:duration,mult=3600,2
            con.execute('UPDATE economy_meta SET boost_until=?,boost_multiplier=? WHERE guild_id=? AND user_id=?',(now+duration,mult,gid,uid))
        return dict(p)
class ShopView(discord.ui.View):
    def __init__(self,center,guild,owner_id):
        super().__init__(timeout=300);self.center=center;self.guild=guild;self.owner_id=owner_id;products=_products(guild.id)[:25];self.by_id={int(p['id']):p for p in products};self.selected=int(products[0]['id']) if products else None
        if products:
            sel=discord.ui.Select(placeholder='Escolha um produto',options=[discord.SelectOption(label=p['name'][:100],value=str(p['id']),description=f"{_fmt(p['price'])} {_coin_name(center,guild.id)}"[:100]) for p in products]);sel.callback=self._choose;self.select=sel;self.add_item(sel)
    async def _choose(self,i):self.selected=int(self.select.values[0]);await i.response.edit_message(embed=self._detail(self.by_id[self.selected]),view=self)
    async def interaction_check(self,i):
        if i.user.id!=self.owner_id:await i.response.send_message('Abra sua própria loja com `/loja`.',ephemeral=True);return False
        return True
    def _detail(self,p):e=_shop_embed(self.center,self.guild,self.owner_id);e.title=f"🛍️ {p['name']}";e.description=f"{p['description']}\n\n**Preço:** {_fmt(p['price'])} {_coin_name(self.center,self.guild.id)}";e.clear_fields();return e
    @discord.ui.button(label='Comprar',style=discord.ButtonStyle.success,row=1)
    async def buy(self,i,b):
        try:p=_purchase(self.center,i.guild.id,i.user.id,self.selected);await i.response.send_message(f"Compra concluída: **{p['name']}**.",ephemeral=True)
        except ValueError as exc:await i.response.send_message(str(exc),ephemeral=True)
    @discord.ui.button(label='Inventário',style=discord.ButtonStyle.secondary,row=1)
    async def inventory(self,i,b):await i.response.send_message(embed=_inventory_embed(self.center,i.guild,i.user),ephemeral=True)
class StoreAdminView(cp.OwnedView):
    def __init__(self,center,owner,guild):
        super().__init__(center,owner,True);_seed_products(guild.id);products=_products(guild.id,False)[:25];self.selected=int(products[0]['id']) if products else None
        if products:
            sel=discord.ui.Select(placeholder='Produto',options=[discord.SelectOption(label=p['name'][:100],value=str(p['id'])) for p in products],row=0)
            async def choose(i):self.selected=int(sel.values[0]);await i.response.defer()
            sel.callback=choose;self.add_item(sel)
        async def toggle(i):
            if self.selected:
                with _connect() as con:r=con.execute('SELECT active FROM shop_products WHERE id=?',(self.selected,)).fetchone();con.execute('UPDATE shop_products SET active=? WHERE id=?',(0 if r['active'] else 1,self.selected))
            await cp.ConfigRouter.show(i,'store',owner)
        async def back(i):await cp.ConfigRouter.show(i,'home',owner)
        self.button('Ativar / pausar',toggle,row=1);self.button('Voltar',back,row=2)
def _admin_embed(center,guild):
    p=_products(guild.id,False);e=center.embed(guild,'💰 Loja e Economia','Gerencie a moeda e os produtos compráveis.',banner=False);e.add_field(name='Produtos',value=f'{sum(1 for x in p if x["active"])} ativos • {len(p)} total');return e
async def _award_message_coins(bot,message):
    if not message.guild or message.author.bot:return
    center=getattr(bot,'center',None)
    if not center:return
    s=center.settings(message.guild.id)
    if not s.get('economy_enabled',True):return
    reward=max(0,int(s.get('economy_message_reward') or 2));cool=max(10,int(s.get('economy_message_cooldown') or 60));now=int(time.time())
    with _connect() as con:
        _ensure_user(con,message.guild.id,message.author.id);r=con.execute('SELECT message_coin_at FROM economy_meta WHERE guild_id=? AND user_id=?',(message.guild.id,message.author.id)).fetchone()
        if now-int(r['message_coin_at'] or 0)<cool:return
        con.execute('UPDATE economy_meta SET message_coin_at=? WHERE guild_id=? AND user_id=?',(now,message.guild.id,message.author.id));con.execute('UPDATE users SET coins=coins+? WHERE guild_id=? AND user_id=?',(reward,message.guild.id,message.author.id));_record(con,message.guild.id,message.author.id,reward,'message_activity','Atividade no chat')
def install():
    global _INSTALLED,_ORIGINAL_BOT_INIT
    if _INSTALLED:return
    _INSTALLED=True;_ensure_schema();cp.DEFAULTS.setdefault('economy_coin_name','F-Coin');cp.DEFAULTS.setdefault('economy_message_reward',2);cp.DEFAULTS.setdefault('economy_message_cooldown',60);cp.SECTIONS.setdefault('store',('Loja e Economia','💰','Produtos, moeda, inventário e cosméticos'))
    if hasattr(cp,'ConfigRouter'):
        old=cp.ConfigRouter.build
        @staticmethod
        def build(center,guild,owner,section,admin=True):
            if section=='store':return _admin_embed(center,guild),StoreAdminView(center,owner,guild)
            return old(center,guild,owner,section,admin)
        cp.ConfigRouter.build=build
    oldtree=cp.CommandTree.__init__
    def tree(self,*a,**kw):
        oldtree(self,*a,**kw)
        @app_commands.command(name='loja',description='Abre a loja do servidor')
        async def loja(i):await i.response.send_message(embed=_shop_embed(i.client.center,i.guild,i.user.id),view=ShopView(i.client.center,i.guild,i.user.id),ephemeral=True)
        @app_commands.command(name='inventario',description='Mostra seus itens comprados')
        async def inventario(i):await i.response.send_message(embed=_inventory_embed(i.client.center,i.guild,i.user),ephemeral=True)
        @app_commands.command(name='extrato',description='Mostra seu extrato de F-Coin')
        async def extrato(i):await i.response.send_message(embed=_statement_embed(i.client.center,i.guild,i.user),ephemeral=True)
        @app_commands.command(name='usar',description='Ativa um item do seu inventário')
        async def usar(i,item:int):
            with _connect() as con:r=con.execute('SELECT * FROM inventory WHERE id=? AND guild_id=? AND user_id=? AND active=1',(item,i.guild.id,i.user.id)).fetchone()
            if not r:return await i.response.send_message('Item não encontrado.',ephemeral=True)
            if r['item_type']=='banner_theme':
                with _connect() as con:con.execute('UPDATE economy_meta SET active_theme=? WHERE guild_id=? AND user_id=?',(r['item_value'],i.guild.id,i.user.id));return await i.response.send_message(f'Tema **{r["item_name"]}** ativado.',ephemeral=True)
            await i.response.send_message('Esse item é aplicado automaticamente ou exige configuração específica.',ephemeral=True)
        for cmd in (loja,inventario,extrato,usar):
            if self.get_command(cmd.name) is None:self.add_command(cmd)
    cp.CommandTree.__init__=tree;_ORIGINAL_BOT_INIT=commands.Bot.__init__
    def bot_init(self,*a,**kw):_ORIGINAL_BOT_INIT(self,*a,**kw);self.add_listener(lambda m:_award_message_coins(self,m),'on_message')
    commands.Bot.__init__=bot_init;print('[OK] Economia V2 • F-Coin, loja, inventário e extrato carregados')
