"""Juegos que se abren a través de su lanzador (Battle.net…).

Los accesos que crean estos lanzadores para cada juego solo abren el lanzador
en la página del juego y se quedan esperando a que pulses «Jugar» (en Windows
también). Desde el menú de MaLiVe lo esperado es que se abra el juego, así que
la entrada se lanza con la orden del lanzador que abre el juego directamente.

Todo sale de lo que el propio lanzador guarda en el contenedor, sin nada por
juego: Battle.net anota en su product.db cada juego instalado con su código y
su carpeta.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import prefix as pfx


@dataclass
class Handoff:
    launcher: Path          # el .exe del lanzador
    args: list[str]         # orden que abre el juego
    game_dir: str           # carpeta del juego (ruta de Windows, en minúsculas)
    name: str = ""          # «Battle.net»
    game_dir_unix: str = ""


# ---------------------------------------------------------------- protobuf --
# product.db es un mensaje protobuf; basta con leer los campos de texto.

def _varint(data: bytes, i: int) -> tuple[int, int]:
    shift = value = 0
    while True:
        b = data[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7


def _fields(data: bytes) -> list[tuple[int, bytes | int]]:
    out: list[tuple[int, bytes | int]] = []
    i = 0
    try:
        while i < len(data):
            key, i = _varint(data, i)
            num, wire = key >> 3, key & 7
            if wire == 0:
                value, i = _varint(data, i)
            elif wire == 2:
                n, i = _varint(data, i)
                value, i = data[i:i + n], i + n
            elif wire == 1:
                value, i = data[i:i + 8], i + 8
            elif wire == 5:
                value, i = data[i:i + 4], i + 4
            else:
                break
            out.append((num, value))
    except IndexError:
        pass
    return out


# ------------------------------------------------------------- Battle.net --

# Código de producto del product.db → código para «--exec="launch …"»
# (el mismo que usan los accesos de Blizzard; los que no están aquí se usan tal cual)
BNET_LAUNCH_CODES = {
    "s1": "S1", "s2": "S2", "wow": "WoW", "wow_classic": "WoW_wow_classic",
    "wow_classic_era": "WoW_wow_classic_era", "pro": "Pro", "w1r": "W1R", "w2r": "W2R",
    "w2bn": "W2BN", "w3": "W3", "gryphon": "GRY", "hsb": "WTCG", "hero": "Hero",
    "d3": "D3", "d3cn": "D3CN", "fenris": "Fen", "osi": "OSI", "anbs": "ANBS", "rtro": "RTRO",
    "wlby": "WLBY", "viper": "VIPR", "odin": "ODIN", "lazarus": "LAZR", "zeus": "ZEUS",
    "fore": "FORE", "auks": "AUKS",
}
BNET_NOT_GAMES = {"agent", "bna", "bts", "catalogs"}


def battlenet_products(prefix: Path) -> list[tuple[str, str]]:
    """(código de producto, carpeta de instalación) de cada juego de Battle.net."""
    db = pfx.drive_c(prefix) / "ProgramData/Battle.net/Agent/product.db"
    try:
        data = db.read_bytes()
    except OSError:
        return []
    found = []
    for num, install in _fields(data):
        if num != 1 or not isinstance(install, bytes):
            continue
        code = path = ""
        for f, v in _fields(install):
            if f == 2 and isinstance(v, bytes):
                code = v.decode("utf-8", "replace")
            elif f == 3 and isinstance(v, bytes):
                for g, w in _fields(v):
                    if g == 1 and isinstance(w, bytes):
                        path = w.decode("utf-8", "replace")
        if code and path and code.lower() not in BNET_NOT_GAMES:
            found.append((code, path))
    return found


def _win_norm(path: str) -> str:
    return path.replace("/", "\\").rstrip("\\").lower()


def _battlenet_exe(prefix: Path) -> Path | None:
    c = pfx.drive_c(prefix)
    for d in ("Program Files (x86)", "Program Files"):
        exe = c / d / "Battle.net" / "Battle.net.exe"
        if exe.is_file():
            return exe
    return None


def handoff(app, exe: Path) -> Handoff | None:
    """Cómo abrir el juego de `exe` (el .exe al que apunta su acceso) a través
    de su lanzador, o None si no es un juego de un lanzador conocido."""
    prefix = app.prefix_path
    win = pfx.unix_to_windows(prefix, exe)
    if not win:
        return None
    win = _win_norm(win)
    products = battlenet_products(prefix)
    if not products:
        return None
    bnet = _battlenet_exe(prefix)
    if bnet is None:
        return None
    for code, path in products:
        game_dir = _win_norm(path)
        if win.startswith(game_dir + "\\"):
            launch = BNET_LAUNCH_CODES.get(code.lower(), code)
            unix = pfx.windows_to_unix(prefix, path.replace("/", "\\"))
            return Handoff(bnet, [f"--exec=launch {launch}"], game_dir, "Battle.net",
                           str(unix).lower() if unix else "")
    return None


# --------------------------------------------------------------- procesos --

def _cmdline(pid: str) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", "replace")


def running(prefix: Path, needle: str) -> bool:
    """¿Hay un proceso del contenedor cuya orden contenga `needle` (ruta de
    Windows, sin distinguir mayúsculas ni el tipo de barra)?"""
    needle = _win_norm(needle)
    for pid in pfx.running_pids(prefix):
        if needle in _win_norm(_cmdline(str(pid))):
            return True
    return False


def game_running(prefix: Path, h: Handoff) -> bool:
    """El juego, no su lanzador: un proceso que se ejecuta desde su carpeta y
    no es el «… Launcher.exe» que deja Blizzard en ella."""
    for pid in pfx.running_pids(prefix):
        raw = _cmdline(str(pid)).lower()
        for cmd, base in ((_win_norm(raw), h.game_dir + "\\"),
                          (raw, h.game_dir_unix + "/" if h.game_dir_unix else "")):
            i = cmd.find(base) if base else -1
            if i < 0:
                continue
            exe = cmd[i:].split(".exe", 1)[0].replace("\\", "/")
            if "launcher" not in os.path.basename(exe):
                return True
    return False
