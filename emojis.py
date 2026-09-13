"""Identidade visual de emojis do F SOCIETY."""

YUUPIII = "<a:yuupiii:1365538724603105421>"
WELCOME = "<a:gif_opa:693060586417684520>"
LOADING = "<a:emoji_129:1500923197950918828>"
VERIFIED = "<a:emoji_97:1500120095836602388>"
BANNED = "<a:emoji_174:1501390027236704379>"
ADMIN = "<:emoji_282:1506672642772697309>"
ARROW = "<a:emoji_34:1441772275102912522>"

SAD = "<a:gif_Solitario:749722183550631956>"
RED_ARROW = "<a:ICON_redarrow:1535880559342002226>"
BITCOIN = "<:bitcoin:1546190853192679454>"
BLUE_ARROW = "<a:Atnxttk2:1491781108243890346>"
BAN_GIRL = "<a:ban_emoji:1474426145922351240>"


def line(text: str) -> str:
    """Prefixo padrão para cada linha textual das interfaces."""
    return f"{RED_ARROW} {text}"


def money(text: str) -> str:
    """Padroniza qualquer valor ou mensagem financeira com a moeda oficial."""
    return f"{BITCOIN} {text}"
