from pathlib import Path

from .VERSION import __version__, __release_time__, is_released_version

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional in lightweight local tests
    load_dotenv = None

if load_dotenv:
    default_cfg = Path("/opt/hiddify-manager/hiddify-panel/app.cfg")
    local_cfg = Path(__file__).resolve().parent.parent / "app.cfg"
    if default_cfg.exists():
        load_dotenv(default_cfg)
    elif local_cfg.exists():
        load_dotenv(local_cfg)


def create_app(*args, **kwargs):
    from .base import create_app as _create_app
    return _create_app(*args, **kwargs)


def create_app_wsgi(*args, **kwargs):
    from .base import create_app_wsgi as _create_app_wsgi
    return _create_app_wsgi(*args, **kwargs)


__all__ = ["create_app", "create_app_wsgi"]

# application = create_app_wsgi()
