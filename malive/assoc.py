"""Asociaciones de ficheros: qué tipos abre cada programa de Windows.

Se leen del registro del prefijo (system.reg y user.reg, en texto) las
extensiones que el instalador asoció al .exe y se traducen a tipos MIME de
Linux con la base de datos del sistema (shared-mime-info, globs2). Con eso
el acceso del menú lleva MimeType= y el programa sale en «Abrir con».
"""

from __future__ import annotations

import os
import re
from pathlib import Path

CLASSES = "software\\classes\\"


def _unescape(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt == "x":
                m = re.match(r"[0-9a-fA-F]{1,4}", raw[i + 2:])
                if m:
                    out.append(chr(int(m.group(0), 16)))
                    i += 2 + len(m.group(0))
                    continue
            out.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_VALUE = re.compile(r'^(@|"(?P<name>(?:[^"\\]|\\.)*)")=(?:str\(\d\):)?"(?P<data>(?:[^"\\]|\\.)*)"\s*$')


def read_classes(reg: Path) -> dict[str, dict[str, str]]:
    """Claves de Software\\Classes de un .reg de Wine → {clave: {valor: dato}}.

    Claves en minúsculas y sin el prefijo Software\\Classes\\; el valor por
    defecto se guarda con el nombre "". Solo valores de texto.
    """
    out: dict[str, dict[str, str]] = {}
    try:
        text = reg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    current: dict[str, str] | None = None
    for line in text.splitlines():
        if line.startswith("["):
            end = line.rfind("]")
            key = _unescape(line[1:end]).lower() if end > 0 else ""
            if key.startswith(CLASSES):
                current = out.setdefault(key[len(CLASSES):], {})
            else:
                current = None
            continue
        if current is None:
            continue
        m = _VALUE.match(line)
        if m:
            name = "" if line.startswith("@") else _unescape(m.group("name")).lower()
            current[name] = _unescape(m.group("data"))
    return out


def prefix_classes(prefix: Path) -> dict[str, dict[str, str]]:
    """HKLM y HKCU combinados (lo del usuario manda, como en Windows)."""
    classes = read_classes(prefix / "system.reg")
    for key, values in read_classes(prefix / "user.reg").items():
        classes.setdefault(key, {}).update(values)
    return classes


def _command_targets(command: str, exe_win: str, exe_name: str) -> bool:
    cmd = command.replace('"', "").casefold()
    return exe_win.casefold() in cmd or re.search(rf"(^|\\){re.escape(exe_name)}(\s|$)", cmd) is not None


def extensions_for(classes: dict[str, dict[str, str]], exe_win: str) -> set[str]:
    """Extensiones (".txt") que el registro asocia a ese .exe (ruta Windows)."""
    exe_name = exe_win.replace("/", "\\").rsplit("\\", 1)[-1].casefold()
    progids = {
        key.rsplit("\\shell\\", 1)[0]
        for key, values in classes.items()
        if "\\shell\\" in key and key.endswith("\\command")
        and _command_targets(values.get("", ""), exe_win, exe_name)
        and not key.startswith(("*\\", "directory\\", "folder\\", "drive\\", "applications\\"))
    }
    exts: set[str] = set()
    for key, values in classes.items():
        if key.startswith(".") and "\\" not in key and values.get("", "").lower() in progids:
            exts.add(key)
        elif key.startswith(".") and key.endswith("\\openwithprogids"):
            if any(name in progids for name in values):
                exts.add(key.split("\\", 1)[0])
        elif key.startswith(".") and key.endswith(f"\\openwithlist\\{exe_name}"):
            exts.add(key.split("\\", 1)[0])
    supported = classes.get(f"applications\\{exe_name}\\supportedtypes", {})
    exts.update(name for name in supported if name.startswith("."))
    return {e for e in exts if re.fullmatch(r"\.[a-z0-9_+\-]{1,16}", e)}


def _mime_dirs() -> list[Path]:
    home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(home) / "mime", *(Path(d) / "mime" for d in dirs.split(":") if d)]


def glob_table() -> dict[str, set[str]]:
    """".ext" → tipos MIME, según shared-mime-info."""
    table: dict[str, set[str]] = {}
    for d in _mime_dirs():
        try:
            lines = (d / "globs2").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) >= 3 and re.fullmatch(r"\*\.[^*?\[\]/]+", parts[2]):
                table.setdefault(parts[2][1:].lower(), set()).add(parts[1])
    return table


def mimetypes_for(prefix: Path, exe_win: str | None) -> list[str]:
    if not exe_win:
        return []
    classes = prefix_classes(prefix)
    exts = extensions_for(classes, exe_win)
    if not exts:
        return []
    table = glob_table()
    mimes: set[str] = set()
    for ext in exts:
        mimes |= table.get(ext, set())
        declared = classes.get(ext, {}).get("content type", "")
        if re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", declared):
            mimes.add(declared)
    # Nunca "todos los ficheros": convertiría el programa en opción para cualquier cosa
    mimes.discard("application/octet-stream")
    return sorted(mimes)


# Tipos de archivo comprimido (para la categoría «Archiving» del menú)
_ARCHIVE_MIMES = {
    "application/zip", "application/x-7z-compressed", "application/vnd.rar",
    "application/x-rar", "application/x-rar-compressed", "application/x-tar",
    "application/gzip", "application/x-gzip", "application/x-bzip2",
    "application/x-xz", "application/x-lzma", "application/zstd",
    "application/x-zstd", "application/x-cab", "application/vnd.ms-cab-compressed",
    "application/x-iso9660-image", "application/x-arj", "application/x-lzh-compressed",
    "application/x-compressed-tar", "application/x-bzip2-compressed-tar",
    "application/x-xz-compressed-tar", "application/x-zstd-compressed-tar",
}


def categories_for(mimes: list[str]) -> str:
    """Categoría del menú según los tipos de fichero que abre el programa.
    Los reproductores registran vídeo y audio a la vez: manda el vídeo."""
    kinds = {m.split("/", 1)[0] for m in mimes}
    if "video" in kinds:
        return "AudioVideo;Video;Player;"
    if "audio" in kinds:
        return "AudioVideo;Audio;Player;"
    if "image" in kinds:
        return "Graphics;Viewer;"
    if any(m in _ARCHIVE_MIMES for m in mimes):
        return "Utility;Archiving;Compression;"
    if "text" in kinds:
        return "Utility;TextEditor;"
    return "Utility;"
