"""Comandos administrativos para criar/importar emojis personalizados no servidor."""
import asyncio
import io
import re

import aiohttp
import discord
from discord import app_commands
from PIL import Image, ImageOps

import control_panel as cp

_INSTALLED = False
EMOJI_RE = re.compile(r'^<(a?):([A-Za-z0-9_]{2,32}):(\d{15,22})>$')
NAME_RE = re.compile(r'^[A-Za-z0-9_]{2,32}$')
MAX_SOURCE_BYTES = 10 * 1024 * 1024
DISCORD_EMOJI_LIMIT = 2 * 1024 * 1024
READ_TIMEOUT = 8
CREATE_TIMEOUT = 10


def _valid_name(name: str) -> bool:
    return bool(NAME_RE.fullmatch((name or '').strip()))


def _bot_can_manage_emojis(guild: discord.Guild) -> bool:
    me = guild.me
    return bool(me and me.guild_permissions.manage_emojis_and_stickers)


def _prepare_static_128(raw: bytes) -> bytes:
    """Sempre transforma a imagem enviada em PNG 128x128 sem distorcer."""
    with Image.open(io.BytesIO(raw)) as source:
        source = ImageOps.exif_transpose(source).convert('RGBA')
        source.thumbnail((128, 128), Image.Resampling.BILINEAR)
        canvas = Image.new('RGBA', (128, 128), (0, 0, 0, 0))
        x = (128 - source.width) // 2
        y = (128 - source.height) // 2
        canvas.alpha_composite(source, (x, y))
        out = io.BytesIO()
        canvas.save(out, 'PNG', optimize=False, compress_level=4)
        return out.getvalue()


async def _fit_emoji(raw: bytes, filename: str, content_type: str) -> bytes:
    is_gif = filename.lower().endswith('.gif') or content_type == 'image/gif'
    if is_gif:
        if len(raw) > DISCORD_EMOJI_LIMIT:
            raise ValueError('gif_too_large')
        return raw
    return await asyncio.to_thread(_prepare_static_128, raw)


async def _create(interaction: discord.Interaction, name: str, image: bytes, reason: str):
    existing = discord.utils.get(interaction.guild.emojis, name=name)
    if existing:
        return await interaction.followup.send(f'Já existe um emoji chamado `{name}`: {existing}', ephemeral=True)
    try:
        created = await asyncio.wait_for(
            interaction.guild.create_custom_emoji(name=name, image=image, reason=reason),
            timeout=CREATE_TIMEOUT,
        )
        await interaction.followup.send(f'Emoji criado: {created}  •  `{created.name}`', ephemeral=True)
    except asyncio.TimeoutError:
        await interaction.followup.send('A API do Discord não confirmou a criação em 10 segundos.', ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send('Não tenho permissão para criar emojis neste servidor.', ephemeral=True)
    except discord.HTTPException as exc:
        await interaction.followup.send(f'O Discord recusou a criação do emoji: `{exc}`', ephemeral=True)


class CopyEmojiModal(discord.ui.Modal, title='Copiar emoji'):
    emoji = discord.ui.TextInput(label='Emoji', placeholder='<:nome:123456789012345678>', max_length=80)
    nome = discord.ui.TextInput(label='Novo nome (opcional)', required=False, max_length=32)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild or not _bot_can_manage_emojis(interaction.guild):
            return await interaction.followup.send('O bot não tem permissão para gerenciar emojis.', ephemeral=True)
        match = EMOJI_RE.fullmatch(str(self.emoji).strip())
        if not match:
            return await interaction.followup.send('Envie o código completo do emoji.', ephemeral=True)
        animated_flag, source_name, emoji_id = match.groups()
        target_name = (str(self.nome).strip() or source_name)
        if not _valid_name(target_name):
            return await interaction.followup.send('Nome de emoji inválido.', ephemeral=True)
        extension = 'gif' if animated_flag else 'png'
        url = f'https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?quality=lossless'
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return await interaction.followup.send('Não consegui obter esse emoji.', ephemeral=True)
                    image = await response.read()
            await _create(interaction, target_name, image, f'Emoji copiado por {interaction.user} via F SOCIETY')
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await interaction.followup.send('Falha ao baixar o emoji de origem.', ephemeral=True)


class DeleteEmojiSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild):
        emojis = list(guild.emojis)[:25]
        options = [
            discord.SelectOption(label=e.name[:100], value=str(e.id), emoji=e)
            for e in emojis
        ]
        if not options:
            options = [discord.SelectOption(label='Nenhum emoji disponível', value='none')]
        super().__init__(placeholder='Selecione um emoji para apagar', min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == 'none':
            return await interaction.response.send_message('Não há emojis para apagar.', ephemeral=True)
        if not interaction.guild or not _bot_can_manage_emojis(interaction.guild):
            return await interaction.response.send_message('O bot não tem permissão para gerenciar emojis.', ephemeral=True)
        emoji = discord.utils.get(interaction.guild.emojis, id=int(self.values[0]))
        if not emoji:
            return await interaction.response.send_message('Esse emoji não foi encontrado.', ephemeral=True)
        try:
            name = emoji.name
            await emoji.delete(reason=f'Apagado por {interaction.user} via F SOCIETY')
            await interaction.response.edit_message(
                content=f'🗑️ Emoji `{name}` apagado com sucesso.',
                embed=None,
                view=EmojiPanelView(interaction.guild),
            )
        except discord.Forbidden:
            await interaction.response.send_message('Não tenho permissão para apagar esse emoji.', ephemeral=True)
        except discord.HTTPException as exc:
            await interaction.response.send_message(f'Falha ao apagar: `{exc}`', ephemeral=True)


class DeleteEmojiView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=120)
        self.add_item(DeleteEmojiSelect(guild))


class EmojiPanelView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.guild = guild

    @discord.ui.button(label='Criar', emoji='➕', style=discord.ButtonStyle.success)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            'Use `/emoji_criar` e envie a imagem junto com o nome. A imagem será convertida automaticamente para **128×128**.',
            ephemeral=True,
        )

    @discord.ui.button(label='Copiar', emoji='📥', style=discord.ButtonStyle.primary)
    async def copy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CopyEmojiModal())

    @discord.ui.button(label='Apagar', emoji='🗑️', style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            'Escolha o emoji que deseja apagar:',
            view=DeleteEmojiView(interaction.guild),
            ephemeral=True,
        )


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    original_init = cp.CommandTree.__init__

    def tree_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        @app_commands.command(name='emojis', description='Abre o painel para criar, copiar e apagar emojis')
        @app_commands.default_permissions(manage_emojis_and_stickers=True)
        async def emojis_panel(interaction: discord.Interaction):
            if interaction.guild is None:
                return await interaction.response.send_message('Use este comando dentro de um servidor.', ephemeral=True)
            embed = discord.Embed(
                title='F SOCIETY • Gerenciador de Emojis',
                description='Gerencie os emojis do servidor pelos botões abaixo.',
                color=0x7C3AED,
            )
            embed.add_field(name='➕ Criar', value='Transforma uma imagem em emoji 128×128.', inline=False)
            embed.add_field(name='📥 Copiar', value='Importa um emoji de outro servidor.', inline=False)
            embed.add_field(name='🗑️ Apagar', value='Remove um emoji deste servidor.', inline=False)
            await interaction.response.send_message(embed=embed, view=EmojiPanelView(interaction.guild), ephemeral=True)

        @app_commands.command(name='emoji_criar', description='Transforma uma imagem em emoji 128x128')
        @app_commands.describe(imagem='Imagem para transformar em emoji', nome='Nome do emoji')
        @app_commands.default_permissions(manage_emojis_and_stickers=True)
        async def emoji_criar(interaction: discord.Interaction, imagem: discord.Attachment, nome: str):
            await interaction.response.defer(ephemeral=True, thinking=True)
            if interaction.guild is None:
                return await interaction.followup.send('Use este comando dentro de um servidor.', ephemeral=True)
            if not _bot_can_manage_emojis(interaction.guild):
                return await interaction.followup.send('O bot não tem permissão para gerenciar emojis.', ephemeral=True)
            nome = nome.strip()
            if not _valid_name(nome):
                return await interaction.followup.send('O nome precisa ter 2–32 caracteres: letras, números ou `_`.', ephemeral=True)
            if imagem.size > MAX_SOURCE_BYTES:
                return await interaction.followup.send('Use uma imagem de origem de até 10 MB.', ephemeral=True)
            content_type = (imagem.content_type or '').lower()
            if content_type and not content_type.startswith('image/'):
                return await interaction.followup.send('O arquivo precisa ser uma imagem.', ephemeral=True)
            try:
                raw = await asyncio.wait_for(imagem.read(), timeout=READ_TIMEOUT)
                image = await _fit_emoji(raw, imagem.filename, content_type)
            except ValueError as exc:
                if str(exc) == 'gif_too_large':
                    return await interaction.followup.send('Esse GIF passa de 2 MB. Envie um GIF menor para manter a animação.', ephemeral=True)
                return await interaction.followup.send('Não consegui processar essa imagem.', ephemeral=True)
            except asyncio.TimeoutError:
                return await interaction.followup.send('O download do anexo excedeu 8 segundos.', ephemeral=True)
            except Exception as exc:
                return await interaction.followup.send(f'Não consegui preparar a imagem: `{type(exc).__name__}`', ephemeral=True)
            if len(image) > DISCORD_EMOJI_LIMIT:
                return await interaction.followup.send('A imagem 128x128 ainda ficou acima do limite de 2 MB.', ephemeral=True)
            await _create(interaction, nome, image, f'Emoji criado por {interaction.user} ({interaction.user.id}) via F SOCIETY')

        @app_commands.command(name='emoji_adicionar', description='Importa um emoji personalizado já existente')
        @app_commands.describe(emoji='Ex.: <a:ICON_redarrow:1535880559342002226>', nome='Nome opcional')
        @app_commands.default_permissions(manage_emojis_and_stickers=True)
        async def emoji_adicionar(interaction: discord.Interaction, emoji: str, nome: str | None = None):
            await interaction.response.defer(ephemeral=True, thinking=True)
            if interaction.guild is None:
                return await interaction.followup.send('Use este comando dentro de um servidor.', ephemeral=True)
            if not _bot_can_manage_emojis(interaction.guild):
                return await interaction.followup.send('O bot não tem permissão para gerenciar emojis.', ephemeral=True)
            match = EMOJI_RE.fullmatch(emoji.strip())
            if not match:
                return await interaction.followup.send('Envie o código completo do emoji.', ephemeral=True)
            animated_flag, source_name, emoji_id = match.groups()
            target_name = (nome or source_name).strip()
            if not _valid_name(target_name):
                return await interaction.followup.send('Nome de emoji inválido.', ephemeral=True)
            extension = 'gif' if animated_flag else 'png'
            url = f'https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?quality=lossless'
            try:
                timeout = aiohttp.ClientTimeout(total=8)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        if response.status != 200:
                            return await interaction.followup.send('Não consegui obter esse emoji.', ephemeral=True)
                        image = await response.read()
                await _create(interaction, target_name, image, f'Emoji importado por {interaction.user} via F SOCIETY')
            except (aiohttp.ClientError, asyncio.TimeoutError):
                await interaction.followup.send('Falha ao baixar o emoji de origem.', ephemeral=True)

        self.add_command(emojis_panel)
        self.add_command(emoji_criar)
        self.add_command(emoji_adicionar)

    cp.CommandTree.__init__ = tree_init
