"""Instala Economia V2 e integrações oficiais depois do roteador principal de /configurar."""
import config_router as config_entry

_INSTALLED=False;_ORIGINAL_INSTALL=None
def install():
    global _INSTALLED,_ORIGINAL_INSTALL
    if _INSTALLED:return
    _INSTALLED=True;_ORIGINAL_INSTALL=config_entry.install
    def install_with_economy():
        _ORIGINAL_INSTALL()
        from economy_v2 import install as a;a()
        from profile_theme import install as b;b()
        from economy_xp_boost import install as c;c()
        from economy_extras import install as d;d()
        from shop_ui import install as e;e()
        from premium_store_v3 import install as f;f()
        from store_profile_preview import install as g;g()
        from paid_banner_animation import install as h;h()
        from paid_banner_store import install as i;i()
        from paid_banner_animation_v2 import install as j;j()
        from store_buttons_v2 import install as k;k()
        from economy_emoji_branding import install as l;l()
        from admin_stats import install as m;m()
    config_entry.install=install_with_economy
