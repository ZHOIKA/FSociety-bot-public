"""Banner gráfico dinâmico para o /perfil do F SOCIETY.

Gera uma imagem PNG individual com avatar, nível, XP, ranking, mensagens,
moedas, cargos e tempo no servidor. O layout é renderizado em memória e não
precisa manter arquivos de perfil no disco.
"""
import asyncio
import io
import math
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

import profile_improvements as profile

_INSTALLED = False

WIDTH = 1400
HEIGHT = 650
BG = (8, 8, 13)
PANEL = (15, 15, 23)
PANEL_2 = (21, 21, 31)
TEXT = (242, 242, 248)
MUTED = (156, 156, 174)
WHITE = (255, 255, 255)


def _font(size, bold=False):
    names = ('DejaVuSans-Bold.ttf', 'Arial Bold.ttf') if bold else ('DejaVuSans.ttf', 'Arial.ttf')
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(text, limit):
    text = str(text or '')
    return text if len(text) <= limit else text[:max(1, limit - 1)] + '…'


def _accent(member):
    value = getattr(getattr(member, 'color', None), 'value', 0) or 0x8B5CF6
    if value < 0x202020:
        value = 0x8B5CF6
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


def _mix(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def _rounded(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _text(draw, xy, text, size, fill=TEXT, bold=False, anchor=None):
    draw.text(xy, str(text), font=_font(size, bold), fill=fill, anchor=anchor)


def _progress(draw, x, y, w, h, pct, accent):
    _rounded(draw, (x, y, x + w, y + h), h // 2, (38, 38, 52))
    fill_w = int(max(h, w * max(0, min(100, pct)) / 100))
    if pct <= 0:
        fill_w = 0
    if fill_w:
        _rounded(draw, (x, y, x + fill_w, y + h), h // 2, accent)


def _circle_avatar(raw, size=190):
    try:
        avatar = Image.open(io.BytesIO(raw)).convert('RGB')
    except Exception:
        avatar = Image.new('RGB', (size, size), (45, 45, 58))
    avatar = ImageOps.fit(avatar, (size, size), method=Image.Resampling.LANCZOS)
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(avatar, (0, 0), mask)
    return out


async def _avatar_bytes(member):
    url = member.display_avatar.replace(size=512, format='png').url
    timeout = aiohttp.ClientTimeout(total=12)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.read()
    except Exception:
        pass
    return b''


def _render_sync(member, guild, stats, avatar_raw):
    accent = _accent(member)
    accent_soft = _mix(accent, WHITE, 0.28)
    img = Image.new('RGB', (WIDTH, HEIGHT), BG)
    glow = Image.new('RGBA', img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((850, -260, 1510, 400), fill=(*accent, 72))
    gd.ellipse((-260, 360, 360, 900), fill=(*accent_soft, 36))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    img = Image.alpha_composite(img.convert('RGBA'), glow)
    draw = ImageDraw.Draw(img)
    _rounded(draw, (18, 18, WIDTH - 18, HEIGHT - 18), 30, (10, 10, 16, 238), (*accent, 160), 2)
    _text(draw, (56, 48), 'F SOCIETY', 28, TEXT, True)
    _text(draw, (56, 82), 'IDENTITY // MEMBER PROFILE', 15, MUTED)
    _text(draw, (WIDTH - 56, 52), 'DIFFERENT MINDS — SAME PURPOSE', 14, MUTED, anchor='ra')
    avatar = _circle_avatar(avatar_raw, 190)
    ring = Image.new('RGBA', (214, 214), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring); rd.ellipse((2, 2, 212, 212), outline=(*accent, 255), width=6)
    img.alpha_composite(ring, (54, 132)); img.alpha_composite(avatar, (66, 144)); draw = ImageDraw.Draw(img)
    _text(draw, (300, 140), _fit(member.display_name, 25), 44, TEXT, True)
    _text(draw, (302, 194), '@' + _fit(member.name, 30), 20, MUTED)
    _rounded(draw, (300, 232, 700, 288), 18, PANEL_2, (*accent, 145), 2)
    top_role = member.top_role.name if member.top_role and not member.top_role.is_default() else 'Membro'
    _text(draw, (322, 249), _fit(top_role, 28).upper(), 17, accent_soft, True)
    _text(draw, (322, 274), f'ID {member.id}', 13, MUTED)
    _rounded(draw, (760, 126, 1342, 292), 24, PANEL, (54, 54, 72), 2)
    _text(draw, (790, 151), 'NÍVEL', 16, MUTED, True); _text(draw, (790, 176), str(stats['level']), 64, TEXT, True)
    _text(draw, (930, 163), f"{profile._fmt(stats['xp'])} / {profile._fmt(stats['target'])} XP", 21, TEXT, True)
    _progress(draw, 930, 210, 350, 22, stats['pct'], accent); _text(draw, (1302, 221), f"{stats['pct']}%", 18, accent_soft, True, anchor='rm')
    cards = [('MENSAGENS', profile._fmt(stats['messages']), f"ranking #{stats['msg_pos']}"),('MOEDAS', profile._fmt(stats['coins']), f"ranking #{stats['coin_pos']}"),('XP GLOBAL', profile._fmt(stats['xp']), f"ranking #{stats['xp_pos']}"),('AVISOS', str(stats['warnings']), 'moderação')]
    x = 54
    for label, value, sub in cards:
        _rounded(draw, (x, 374, x + 300, 494), 20, PANEL, (43, 43, 58), 1); _text(draw, (x + 24, 394), label, 14, MUTED, True); _text(draw, (x + 24, 425), value, 31, TEXT, True); _text(draw, (x + 24, 465), sub, 14, accent_soft); x += 322
    joined = member.joined_at
    joined_text = f'{max(0, (datetime.now(timezone.utc) - joined).days)} dias no servidor • entrou em {joined.strftime("%d/%m/%Y")}' if joined else 'Tempo no servidor indisponível'
    _text(draw, (58, 540), joined_text, 17, MUTED)
    roles = [r.name for r in reversed(member.roles) if not r.is_default()][:4]
    roles_text = '  •  '.join(_fit(r, 18) for r in roles) if roles else 'Membro'
    _text(draw, (58, 578), _fit(roles_text, 75), 16, TEXT); _text(draw, (WIDTH - 58, 585), f"#{stats['xp_pos']} XP  //  {guild.name}", 16, accent_soft, True, anchor='ra')
    output = io.BytesIO(); img.convert('RGB').save(output, format='PNG', optimize=True, quality=92); output.seek(0); return output


async def render_banner(center, guild, member):
    u = profile._ensure_user(center, guild.id, member.id); level = int(u['level'] or 0); xp = int(u['xp'] or 0); _, target, pct, _ = profile._xp_progress(level, xp)
    stats = {'level':level,'xp':xp,'target':target,'pct':pct,'coins':int(u['coins'] or 0),'messages':int(u['messages'] or 0),'warnings':int(u['warnings'] or 0),'xp_pos':profile._position(center,guild.id,member.id,'xp'),'coin_pos':profile._position(center,guild.id,member.id,'coins'),'msg_pos':profile._position(center,guild.id,member.id,'messages')}
    raw = await _avatar_bytes(member); return await asyncio.to_thread(_render_sync, member, guild, stats, raw)


class BannerProfileView(discord.ui.View):
    def __init__(self, center, guild, target, owner_id):
        super().__init__(timeout=300); self.center=center; self.guild=guild; self.target=target; self.owner_id=owner_id; self.page='banner'; self._sync()
    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('Abra seu próprio perfil com `/perfil`.', ephemeral=True); return False
        return True
    def _sync(self):
        self.banner.disabled=self.page=='banner'; self.overview.disabled=self.page=='profile'; self.rankings.disabled=self.page=='ranking'; self.member_info.disabled=self.page=='server'
    async def _show_embed(self, interaction, page):
        self.page=page; self._sync(); embed=profile.ranking_embed(self.center,self.guild,self.target) if page=='ranking' else profile.server_embed(self.center,self.guild,self.target) if page=='server' else profile.profile_embed(self.center,self.guild,self.target)
        await interaction.response.edit_message(embed=embed,attachments=[],view=self)
    @discord.ui.button(label='Banner',style=discord.ButtonStyle.primary,row=0)
    async def banner(self,interaction,button):
        await interaction.response.defer(); data=await render_banner(self.center,self.guild,self.target); file=discord.File(data,filename=f'perfil-{self.target.id}.png'); embed=discord.Embed(color=_accent_color(self.target)); embed.set_image(url=f'attachment://perfil-{self.target.id}.png'); embed.set_footer(text='F SOCIETY • identidade dinâmica'); self.page='banner'; self._sync(); await interaction.edit_original_response(embed=embed,attachments=[file],view=self)
    @discord.ui.button(label='Perfil',style=discord.ButtonStyle.secondary,row=0)
    async def overview(self,interaction,button):await self._show_embed(interaction,'profile')
    @discord.ui.button(label='Posições',style=discord.ButtonStyle.secondary,row=0)
    async def rankings(self,interaction,button):await self._show_embed(interaction,'ranking')
    @discord.ui.button(label='Servidor',style=discord.ButtonStyle.secondary,row=0)
    async def member_info(self,interaction,button):await self._show_embed(interaction,'server')


def _accent_color(member):
    return discord.Color.from_rgb(*_accent(member))


async def _slash_profile_banner(interaction:discord.Interaction,usuario:discord.Member=None):
    if not interaction.guild:return await interaction.response.send_message('Use `/perfil` dentro de um servidor.',ephemeral=True)
    member=usuario or interaction.user; center=interaction.client.center; await interaction.response.defer()
    try:
        data=await render_banner(center,interaction.guild,member); filename=f'perfil-{member.id}.png'; file=discord.File(data,filename=filename); embed=discord.Embed(color=_accent_color(member)); embed.set_image(url=f'attachment://{filename}'); embed.set_footer(text='F SOCIETY • identidade dinâmica • dados atualizados em tempo real'); await interaction.followup.send(embed=embed,file=file,view=BannerProfileView(center,interaction.guild,member,interaction.user.id))
    except Exception:
        await interaction.followup.send(embed=profile.profile_embed(center,interaction.guild,member),view=BannerProfileView(center,interaction.guild,member,interaction.user.id))


def install():
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True; original_tree_command=app_commands.CommandTree.command
    def tree_command(self,*args,**kwargs):
        decorator=original_tree_command(self,*args,**kwargs)
        def wrapped(func):
            command=decorator(func)
            if command.name=='perfil':command._callback=_slash_profile_banner; command.description='Banner visual com nível, XP, ranking e estatísticas'
            return command
        return wrapped
    app_commands.CommandTree.command=tree_command
    print('[OK] Perfil • banner gráfico dinâmico carregado')
