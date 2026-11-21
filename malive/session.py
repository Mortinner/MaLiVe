"""Datos de la sesión gráfica del usuario (sin dependencias de otros módulos)."""

from __future__ import annotations

import os
from pathlib import Path


def desktop_shell_env(names=("plasmashell", "gnome-shell")) -> dict[str, str] | None:
    """Entorno del proceso del escritorio (de este usuario) que está en marcha.

    Sirve cuando MaLiVe se lanza desde el menú o desde el temporizador de
    systemd y no ha heredado variables como LANG.
    """
    uid = os.getuid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() not in names or entry.stat().st_uid != uid:
                continue
            raw = (entry / "environ").read_bytes()
        except OSError:
            continue
        env = {}
        for item in raw.split(b"\0"):
            key, sep, value = item.partition(b"=")
            if sep:
                env[key.decode(errors="replace")] = value.decode(errors="replace")
        return env
    return None
