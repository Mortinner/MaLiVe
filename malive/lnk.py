"""Parser mínimo de accesos directos de Windows (.lnk, MS-SHLLINK).

Devuelve la ruta Windows del destino, argumentos, carpeta de trabajo e
icono. Prueba, por orden: LinkInfo (ruta local), bloque de variables de
entorno (rutas tipo %ProgramFiles%\\...) y la lista de IDs de shell.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

LNK_CLSID = bytes.fromhex("0114020000000000c000000000000046")

HAS_IDLIST = 0x01
HAS_LINKINFO = 0x02
HAS_NAME = 0x04
HAS_RELPATH = 0x08
HAS_WORKDIR = 0x10
HAS_ARGS = 0x20
HAS_ICON = 0x40
IS_UNICODE = 0x80
HAS_DARWIN = 0x1000  # acceso "anunciado" de MSI

ENV_BLOCK = 0xA0000001
ICON_ENV_BLOCK = 0xA0000007
DARWIN_BLOCK = 0xA0000006


@dataclass
class Shortcut:
    target: str | None = None  # ruta Windows, p. ej. C:\Program Files\App\app.exe
    arguments: str = ""
    working_dir: str | None = None
    icon_location: str | None = None
    icon_index: int = 0
    description: str | None = None
    relative_path: str | None = None
    advertised: bool = False  # acceso MSI "anunciado" (sin ruta real)


class NotLnk(Exception):
    pass


def is_lnk(data: bytes) -> bool:
    return len(data) >= 20 and data[:4] == b"\x4c\x00\x00\x00" and data[4:20] == LNK_CLSID


def _cstr(data: bytes, off: int, unicode: bool) -> str:
    if unicode:
        end = off
        while end + 1 < len(data) and data[end : end + 2] != b"\0\0":
            end += 2
        return data[off:end].decode("utf-16-le", errors="replace")
    end = data.find(b"\0", off)
    if end < 0:
        end = len(data)
    return data[off:end].decode("cp1252", errors="replace")


def _parse_idlist(data: bytes) -> str | None:
    """Reconstruye una ruta a partir de los ItemID de shell (mejor esfuerzo)."""
    parts: list[str] = []
    pos = 0
    while pos + 2 <= len(data):
        (size,) = struct.unpack_from("<H", data, pos)
        if size == 0:
            break
        item = data[pos + 2 : pos + size]
        pos += size
        if not item:
            continue
        kind = item[0]
        if kind & 0x70 == 0x20:  # volumen, p. ej. "C:\"
            name = item[1:].split(b"\0", 1)[0].decode("cp1252", errors="replace")
            if len(name) >= 2 and name[1] == ":":
                parts = [name[:2]]
        elif kind & 0x70 == 0x30:  # fichero o carpeta
            name = _item_long_name(item) or _item_short_name(item)
            if name:
                parts.append(name)
    if len(parts) >= 2 and parts[0].endswith(":"):
        return parts[0] + "\\" + "\\".join(parts[1:])
    return None


def _item_short_name(item: bytes) -> str | None:
    if len(item) < 13:
        return None
    unicode = bool(item[0] & 0x04)
    return _cstr(item, 12, unicode) or None


def _item_long_name(item: bytes) -> str | None:
    sig = struct.pack("<I", 0xBEEF0004)
    idx = item.find(sig)
    if idx < 4:
        return None
    ext = item[idx - 4 :]
    try:
        size, version = struct.unpack_from("<HH", ext, 0)
        pos = 18
        if version >= 7:
            pos += 18
        if version >= 3:
            pos += 2
        if version >= 9:
            pos += 4
        if version >= 8:
            pos += 4
        if pos >= size:
            return None
        name = _cstr(ext[:size], pos, True)
        return name or None
    except struct.error:
        return None


def parse_bytes(data: bytes) -> Shortcut:
    if not is_lnk(data):
        raise NotLnk("no es un .lnk")
    (flags,) = struct.unpack_from("<I", data, 0x14)
    (icon_index,) = struct.unpack_from("<i", data, 0x38)
    sc = Shortcut(icon_index=icon_index, advertised=bool(flags & HAS_DARWIN))
    unicode = bool(flags & IS_UNICODE)
    pos = 0x4C

    idlist_path = None
    if flags & HAS_IDLIST:
        (size,) = struct.unpack_from("<H", data, pos)
        idlist_path = _parse_idlist(data[pos + 2 : pos + 2 + size])
        pos += 2 + size

    if flags & HAS_LINKINFO:
        (li_size, li_hdr, li_flags, _vol, base_off, _net, suffix_off) = struct.unpack_from(
            "<IIIIIII", data, pos
        )
        li = data[pos : pos + li_size]
        base = suffix = ""
        if li_hdr >= 0x24:
            base_u, suffix_u = struct.unpack_from("<II", li, 28)
            if li_flags & 1 and base_u:
                base = _cstr(li, base_u, True)
            if suffix_u:
                suffix = _cstr(li, suffix_u, True)
        if not base and li_flags & 1 and base_off:
            base = _cstr(li, base_off, False)
        if not suffix and suffix_off:
            suffix = _cstr(li, suffix_off, False)
        if base:
            sc.target = base + suffix if (not suffix or base.endswith("\\")) else base + "\\" + suffix
        pos += li_size

    def read_string() -> str:
        nonlocal pos
        (count,) = struct.unpack_from("<H", data, pos)
        pos += 2
        if unicode:
            s = data[pos : pos + 2 * count].decode("utf-16-le", errors="replace")
            pos += 2 * count
        else:
            s = data[pos : pos + count].decode("cp1252", errors="replace")
            pos += count
        # Algunos programas (p. ej. el instalador de Notepad++) incluyen el
        # carácter nulo final en la cuenta: un argumento vacío llega como "\0"
        return s.split("\0", 1)[0]

    if flags & HAS_NAME:
        sc.description = read_string()
    if flags & HAS_RELPATH:
        sc.relative_path = read_string()
    if flags & HAS_WORKDIR:
        sc.working_dir = read_string()
    if flags & HAS_ARGS:
        sc.arguments = read_string()
    if flags & HAS_ICON:
        sc.icon_location = read_string()

    # Bloques extra
    env_target = env_icon = None
    while pos + 8 <= len(data):
        (bsize,) = struct.unpack_from("<I", data, pos)
        if bsize < 8:
            break
        (bsig,) = struct.unpack_from("<I", data, pos + 4)
        block = data[pos : pos + bsize]
        if bsig in (ENV_BLOCK, ICON_ENV_BLOCK) and len(block) >= 788:
            value = _cstr(block, 268, True) or _cstr(block, 8, False)
            if bsig == ENV_BLOCK:
                env_target = value
            else:
                env_icon = value
        elif bsig == DARWIN_BLOCK:
            sc.advertised = True
        pos += bsize

    if not sc.target and env_target:
        sc.target = env_target
    if not sc.target and idlist_path:
        sc.target = idlist_path
    if env_icon and not sc.icon_location:
        sc.icon_location = env_icon
    return sc


def parse(path: str | Path) -> Shortcut:
    data = Path(path).read_bytes()
    return parse_bytes(data)
