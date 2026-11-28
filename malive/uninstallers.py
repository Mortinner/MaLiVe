"""«Agregar o quitar programas» de cada contenedor.

Como en Windows, cada programa instalado se anota en el registro (claves
…\\CurrentVersion\\Uninstall\\…) con su nombre, su carpeta y la orden para
desinstalarlo. MaLiVe lee esas claves de los .reg del contenedor para
desinstalar un programa concreto sin tocar los demás (Diablo IV sin Battle.net).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Uninstaller:
    key: str
    name: str = ""
    command: str = ""
    quiet_command: str = ""
    location: str = ""
    icon: str = ""


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append(value[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_VALUE = re.compile(r'^"(DisplayName|UninstallString|QuietUninstallString|InstallLocation|DisplayIcon)"="(.*)"\s*$')


def read(prefix: Path) -> list[Uninstaller]:
    found: list[Uninstaller] = []
    for reg in (prefix / "system.reg", prefix / "user.reg"):
        try:
            text = reg.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        current: Uninstaller | None = None
        for line in text.splitlines():
            if line.startswith("["):
                key = line[1:line.rfind("]")] if "]" in line else line[1:]
                low = key.lower()
                if "\\\\uninstall\\\\" in low:
                    current = Uninstaller(key=_unescape(key).rsplit("\\", 1)[-1])
                    found.append(current)
                else:
                    current = None
                continue
            if current is None:
                continue
            m = _VALUE.match(line)
            if not m:
                continue
            value = _unescape(m.group(2))
            field = {"DisplayName": "name", "UninstallString": "command", "QuietUninstallString": "quiet_command",
                     "InstallLocation": "location", "DisplayIcon": "icon"}[m.group(1)]
            setattr(current, field, value)
    return [u for u in found if u.command or u.quiet_command]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _win_dir(path: str) -> str:
    path = path.strip().strip('"')
    path = re.sub(r",\s*-?\d+$", "", path).strip('"')
    return path.rsplit("\\", 1)[0].lower() if "\\" in path else ""


def split_command(command: str) -> tuple[str, list[str]]:
    """Orden de Windows → (programa, argumentos). «"C:\\x y\\u.exe" --a "b c"»,
    «C:\\x\\u.exe /S» o «MsiExec.exe /X{GUID}»."""
    command = command.strip()
    if command.startswith('"'):
        end = command.find('"', 1)
        exe, rest = (command[1:end], command[end + 1:]) if end > 0 else (command.strip('"'), "")
    else:
        m = re.match(r"(.+?\.exe)(\s|$)", command, re.I)
        exe, rest = (m.group(1), command[m.end(1):]) if m else (command.split(" ", 1)[0], command.partition(" ")[2])
    return exe, _argv(rest)


def _argv(rest: str) -> list[str]:
    """Como CommandLineToArgvW: los espacios dentro de comillas no separan y las
    comillas se quitan (--displayname="Diablo IV" → --displayname=Diablo IV)."""
    args: list[str] = []
    cur: list[str] = []
    in_quote = False
    started = False
    for ch in rest:
        if ch == '"':
            in_quote = not in_quote
            started = True
        elif ch.isspace() and not in_quote:
            if started:
                args.append("".join(cur))
                cur, started = [], False
        else:
            cur.append(ch)
            started = True
    if started:
        args.append("".join(cur))
    return args


def match(entries: list[Uninstaller], exe_win: str, entry_name: str) -> Uninstaller | None:
    """El desinstalador de un programa a partir de su .exe (ruta de Windows) y su nombre."""
    exe_low = exe_win.lower()
    exe_dir = exe_low.rsplit("\\", 1)[0]
    best: tuple[int, Uninstaller] | None = None
    for u in entries:
        score = 0
        loc = u.location.strip().strip('"').rstrip("\\").lower()
        if loc and (exe_low.startswith(loc + "\\")):
            score = max(score, 50 + len(loc))
        icon = re.sub(r",\s*-?\d+$", "", u.icon.strip().strip('"')).strip('"').lower()
        if icon and icon == exe_low:
            score = max(score, 300)
        elif icon and _win_dir(icon) == exe_dir:
            score = max(score, 200)
        if u.name and _norm(u.name) == _norm(entry_name):
            score = max(score, 250)
        elif u.name and _norm(entry_name) and _norm(entry_name) in _norm(u.name):
            score = max(score, 40)
        exe_u, _ = split_command(u.command or u.quiet_command)
        if score < 200 and _win_dir('"' + exe_u + '"') == exe_dir and loc:
            score = max(score, 30)
        if score and (best is None or score > best[0]):
            best = (score, u)
    return best[1] if best else None
