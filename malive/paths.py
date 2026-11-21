"""Rutas XDG que usa MaLiVe.

Son funciones (no constantes) para que los tests puedan redirigir XDG_* por
variables de entorno.
"""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / default


def data_home() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def state_home() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def data_dir() -> Path:
    """~/.local/share/malive"""
    return data_home() / "malive"


def prefixes_dir() -> Path:
    return data_dir() / "prefixes"


def proton_dir() -> Path:
    return data_dir() / "proton"


def icons_dir() -> Path:
    return data_dir() / "icons"


def config_dir() -> Path:
    """~/.config/malive"""
    return config_home() / "malive"


def apps_dir() -> Path:
    """Un JSON por app registrada."""
    return config_dir() / "apps"


def config_file() -> Path:
    return config_dir() / "config.json"


def logs_dir() -> Path:
    return state_home() / "malive" / "logs"


def main_log() -> Path:
    return state_home() / "malive" / "malive.log"


def applications_dir() -> Path:
    """Donde van los .desktop del usuario."""
    return data_home() / "applications"


def mimeapps_file() -> Path:
    return config_home() / "mimeapps.list"


def systemd_user_dir() -> Path:
    return config_home() / "systemd" / "user"
