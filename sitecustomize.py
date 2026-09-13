"""Runtime hooks loaded automatically by Python's site module."""

try:
    from sqlite_runtime import install as _install_sqlite_runtime
    _install_sqlite_runtime()
except Exception as exc:
    print(f'[DB] Falha ao preparar SQLite gravável: {type(exc).__name__}: {exc}')

try:
    from supabase_storage_retry import install as _install_supabase_storage_retry
    _install_supabase_storage_retry()
except Exception as exc:
    print(f'[DB] Falha ao carregar retry do Supabase: {type(exc).__name__}: {exc}')

try:
    from commit_monitor import install as _install_commit_monitor
    from commit_boot_check import install as _install_commit_boot_check
    from commit_delivery import install as _install_commit_delivery
    from commit_runtime_hook import install as _install_commit_runtime_hook
    from commit_panel_integration import install as _install_commit_panel_integration
    _install_commit_monitor(); _install_commit_boot_check(); _install_commit_delivery(); _install_commit_runtime_hook(); _install_commit_panel_integration()
except Exception as exc:
    print(f'[COMMITS] Falha ao carregar monitor: {type(exc).__name__}: {exc}')

try:
    from ranking_svg_bootstrap import install as _install_ranking_svg_bootstrap
    _install_ranking_svg_bootstrap()
except Exception as exc:
    print(f'[RANKING] Falha ao preparar ranking SVG: {type(exc).__name__}: {exc}')

try:
    from voice_xp_config_bootstrap import install as _install_voice_xp_config_bootstrap
    _install_voice_xp_config_bootstrap()
except Exception as exc:
    print(f'[VOICE XP] Falha ao preparar painel de configuração: {type(exc).__name__}: {exc}')

try:
    from economy_bootstrap import install as _install_economy_bootstrap
    _install_economy_bootstrap()
except Exception as exc:
    print(f'[ECONOMY] Falha ao preparar Economia V2: {type(exc).__name__}: {exc}')

try:
    from welcome_config import install as _install_welcome_config
    _install_welcome_config()
except Exception as exc:
    print(f'[WELCOME] Falha ao carregar compatibilidade: {type(exc).__name__}: {exc}')

try:
    from community_bridge import install as _install_community_bridge
    _install_community_bridge()
except Exception as exc:
    print(f'[COMMUNITY] Falha ao preparar ponte de atividade: {type(exc).__name__}: {exc}')

try:
    from profile_hub_v2 import install as _install_profile_hub_v2
    _install_profile_hub_v2()
except Exception as exc:
    print(f'[PROFILE] Falha ao preparar central pessoal: {type(exc).__name__}: {exc}')

try:
    from agenda_ui import install as _install_agenda_ui
    _install_agenda_ui()
except Exception as exc:
    print(f'[AGENDA] Falha ao preparar painel visual: {type(exc).__name__}: {exc}')
