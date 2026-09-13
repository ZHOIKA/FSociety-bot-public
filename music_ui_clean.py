"""Interface compacta e organizada para o player de música do F SOCIETY."""
import discord

import music_system as music
import music_experience as exp

_INSTALLED = False


def _short(text, limit):
    text = str(text or '').replace('\n', ' ').strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def clean_search_embed(query, results):
    visible = results[:5]
    lines = []
    for idx, row in enumerate(visible, 1):
        source = str(row.get('source') or '').lower()
        badge = 'Spotify' if source == 'spotify' else 'YouTube'
        lines.append(
            f'`{idx:02d}` **{_short(row.get("title"), 58)}**\n'
            f'     {_short(row.get("uploader"), 38)}  •  `{music._fmt(row.get("duration"))}`  •  {badge}'
        )

    extra = max(0, min(len(results), 10) - len(visible))
    description = (
        f'**Pesquisa**\n`{discord.utils.escape_markdown(_short(query, 90))}`\n\n'
        + ('\n\n'.join(lines) if lines else 'Nenhum resultado encontrado.')
    )
    if extra:
        description += f'\n\n*+ {extra} resultado(s) disponíveis no seletor abaixo.*'

    embed = discord.Embed(
        title='F SOCIETY  •  MUSIC',
        description=description,
        color=0xB91C1C,
    )
    if results and results[0].get('thumbnail'):
        embed.set_thumbnail(url=results[0]['thumbnail'])
    embed.set_footer(text='Selecione uma faixa abaixo • Spotify + YouTube')
    return embed


class CleanSearchSelect(discord.ui.Select):
    def __init__(self, results):
        self.results = results
        options = []
        for idx, row in enumerate(results[:10], 1):
            source = str(row.get('source') or '').lower()
            source_label = 'Spotify' if source == 'spotify' else 'YouTube'
            options.append(discord.SelectOption(
                label=f'{idx:02d} • {_short(row.get("title"), 76)}',
                value=str(idx - 1),
                description=_short(
                    f'{row.get("uploader") or "Desconhecido"} • {music._fmt(row.get("duration"))} • {source_label}',
                    100,
                ),
            ))
        super().__init__(
            placeholder='Escolha uma música',
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, i):
        view = self.view
        if not isinstance(view, CleanSearchView):
            return
        try:
            await music._ensure_same_voice(i, connect=True)
            await i.response.defer()
            chosen = self.results[int(self.values[0])]
            track = await music.resolve_track(chosen['url'], i.user.id)
            player = music.player_for(i.client, i.guild.id)
            before = len(player.queue)
            await player.enqueue(track, i.channel_id)
            now = player.current is track
            status = 'Tocando agora' if now else f'Adicionada à fila • posição {before + 1}'
            msg = await i.edit_original_response(
                content=f'**{status}**  •  {_short(track.title, 105)}',
                embed=player.embed(),
                view=CleanMusicControls(player),
            )
            player.text_channel_id = i.channel_id
            player.panel_message_id = msg.id
        except Exception as exc:
            await music._reply_error(i, exc)


class CleanSearchView(discord.ui.View):
    def __init__(self, owner_id, results, query=''):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.results = results
        self.query = query
        self.add_item(CleanSearchSelect(results))

    async def interaction_check(self, i):
        if i.user.id != self.owner_id:
            await i.response.send_message('Essa pesquisa pertence a quem usou `/play`.', ephemeral=True)
            return False
        return True


class PlayerActionSelect(discord.ui.Select):
    def __init__(self, player):
        self.player = player
        loop_label = {'off': 'desligado', 'track': 'faixa', 'queue': 'fila'}.get(player.loop_mode, player.loop_mode)
        options = [
            discord.SelectOption(label='Ver fila', value='queue', description=f'{len(player.queue)} música(s) aguardando'),
            discord.SelectOption(label='Pesquisar outra música', value='search', description='Abrir uma nova pesquisa'),
            discord.SelectOption(label='Loop', value='loop', description=f'Modo atual: {loop_label}'),
            discord.SelectOption(label='Embaralhar fila', value='shuffle', description='Misturar a ordem das próximas músicas'),
            discord.SelectOption(label='Alterar volume', value='volume', description=f'Volume atual: {round(player.volume * 100)}%'),
            discord.SelectOption(label='Desconectar', value='disconnect', description='Sair do canal de voz'),
        ]
        super().__init__(placeholder='Mais controles', min_values=1, max_values=1, options=options, row=1)

    async def callback(self, i):
        action = self.values[0]
        try:
            await music._ensure_same_voice(i)
            if action == 'queue':
                return await i.response.send_message(embed=self.player.queue_embed(), ephemeral=True)
            if action == 'search':
                return await i.response.send_modal(exp.SearchAgainModal(self.player))
            if action == 'loop':
                modes = {'off': 'track', 'track': 'queue', 'queue': 'off'}
                self.player.loop_mode = modes[self.player.loop_mode]
                return await i.response.edit_message(embed=self.player.embed(), view=CleanMusicControls(self.player))
            if action == 'shuffle':
                if len(self.player.queue) < 2:
                    return await i.response.send_message('A fila precisa de pelo menos 2 músicas.', ephemeral=True)
                self.player.shuffle()
                return await i.response.edit_message(embed=self.player.embed(), view=CleanMusicControls(self.player))
            if action == 'volume':
                return await i.response.send_modal(music.VolumeModal(self.player))
            if action == 'disconnect':
                await self.player.disconnect()
                return await i.response.edit_message(embed=self.player.embed(), view=None)
        except Exception as exc:
            await music._reply_error(i, exc)


class CleanMusicControls(discord.ui.View):
    def __init__(self, player):
        super().__init__(timeout=1200)
        self.player = player
        vc = player.voice
        paused = bool(vc and vc.is_paused())
        self.toggle.label = 'Continuar' if paused else 'Pausar'
        self.toggle.style = discord.ButtonStyle.success if paused else discord.ButtonStyle.secondary
        self.toggle.disabled = not bool(player.current)
        self.skip.disabled = not bool(player.current)
        self.stop.disabled = not bool(player.current or player.queue)
        self.add_item(PlayerActionSelect(player))

    async def interaction_check(self, i):
        try:
            await music._ensure_same_voice(i)
            return True
        except Exception as exc:
            await music._reply_error(i, exc)
            return False

    @discord.ui.button(label='Pausar', style=discord.ButtonStyle.secondary, row=0)
    async def toggle(self, i, button):
        vc = self.player.voice
        if not vc:
            return await i.response.send_message('Não estou conectado a um canal de voz.', ephemeral=True)
        if vc.is_paused():
            vc.resume()
        elif vc.is_playing():
            vc.pause()
        else:
            return await i.response.send_message('Não há música tocando.', ephemeral=True)
        await i.response.edit_message(embed=self.player.embed(), view=CleanMusicControls(self.player))

    @discord.ui.button(label='Pular', style=discord.ButtonStyle.primary, row=0)
    async def skip(self, i, button):
        try:
            await self.player.skip()
            await i.response.defer()
        except Exception as exc:
            await music._reply_error(i, exc)

    @discord.ui.button(label='Parar', style=discord.ButtonStyle.danger, row=0)
    async def stop(self, i, button):
        await self.player.stop()
        await i.response.edit_message(embed=self.player.embed(), view=CleanMusicControls(self.player))


def clean_player_embed(self):
    vc = self.voice
    if vc and vc.is_paused():
        state = 'PAUSADO'
    elif self.current:
        state = 'TOCANDO'
    else:
        state = 'PRONTO'

    loop_label = {'off': 'Off', 'track': 'Faixa', 'queue': 'Fila'}.get(self.loop_mode, self.loop_mode)

    if not self.current:
        embed = discord.Embed(
            title='F SOCIETY  •  MUSIC',
            description=(
                '**Player pronto para uso.**\n'
                'Use `/play` para pesquisar uma música.\n\n'
                f'`VOL {round(self.volume * 100)}%`  •  `LOOP {loop_label}`  •  `FILA {len(self.queue)}`'
            ),
            color=0xB91C1C,
        )
        embed.set_footer(text='Controles simplificados • use o menu para opções extras')
        return embed

    requester = self.guild.get_member(self.current.requester_id) if self.guild else None
    title = discord.utils.escape_markdown(_short(self.current.title, 110))
    uploader = discord.utils.escape_markdown(_short(self.current.uploader, 65))
    embed = discord.Embed(
        title=f'F SOCIETY  •  {state}',
        description=(
            f'### [{title}]({self.current.webpage_url})\n'
            f'{uploader}\n\n'
            f'`{music._fmt(self.current.duration)}`  •  `VOL {round(self.volume * 100)}%`  •  '
            f'`LOOP {loop_label}`  •  `FILA {len(self.queue)}`'
        ),
        color=0xB91C1C,
    )
    if self.current.thumbnail:
        embed.set_thumbnail(url=self.current.thumbnail)
    if self.queue:
        nxt = self.queue[0]
        embed.add_field(name='A seguir', value=f'{_short(nxt.title, 75)}  •  `{music._fmt(nxt.duration)}`', inline=False)
    embed.set_footer(text=f'Pedido por {requester.display_name if requester else "membro"}')
    return embed


def clean_queue_embed(self):
    items = list(self.queue)
    if not items:
        return discord.Embed(title='F SOCIETY  •  FILA', description='A fila está vazia.', color=0xB91C1C)

    lines = []
    total = 0
    for idx, track in enumerate(items[:10], 1):
        total += int(track.duration or 0)
        lines.append(f'`{idx:02d}` **{_short(track.title, 58)}**  •  `{music._fmt(track.duration)}`')
    if len(items) > 10:
        lines.append(f'\n*+ {len(items) - 10} música(s) na fila.*')

    embed = discord.Embed(
        title=f'F SOCIETY  •  FILA  ({len(items)})',
        description='\n'.join(lines),
        color=0xB91C1C,
    )
    embed.set_footer(text=f'Duração exibida: {music._fmt(total)}')
    return embed


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    exp.search_embed = clean_search_embed
    exp.BetterSearchSelect = CleanSearchSelect
    exp.BetterSearchView = CleanSearchView

    music.SearchSelect = CleanSearchSelect
    music.SearchView = CleanSearchView
    music.MusicControls = CleanMusicControls
    music.GuildPlayer.embed = clean_player_embed
    music.GuildPlayer.queue_embed = clean_queue_embed

    print('Música: interface compacta e organizada carregada')
