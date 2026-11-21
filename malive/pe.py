"""Lectura mínima de ejecutables PE (Windows) sin dependencias externas.

Solo lo que necesita MaLiVe: arquitectura, subsistema, si es DLL o .NET,
dónde empieza el overlay (datos añadidos tras la última sección, donde los
instaladores NSIS/Inno guardan su carga) y las cadenas de versión
(ProductName, FileDescription...).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import T

MACHINES = {0x14C: "x86", 0x8664: "x64", 0xAA64: "arm64", 0x1C4: "arm"}
SUBSYSTEMS = {2: "gui", 3: "console", 1: "native", 10: "efi"}

DIR_RESOURCE = 2
DIR_CLR = 14  # cabecera .NET


@dataclass
class Section:
    name: str
    virtual_address: int
    virtual_size: int
    raw_ptr: int
    raw_size: int


@dataclass
class PEInfo:
    machine: str = "other"
    subsystem: str = "other"
    is_dll: bool = False
    is_dotnet: bool = False
    is_64: bool = False
    sections: list[Section] = field(default_factory=list)
    overlay_offset: int = 0
    file_size: int = 0
    resource_raw: tuple[int, int] | None = None  # (offset, tamaño) en el fichero

    def section_names(self) -> set[str]:
        return {s.name for s in self.sections}

    @property
    def has_overlay(self) -> bool:
        return self.file_size > self.overlay_offset > 0


class NotPE(Exception):
    pass


def parse(path: str | Path) -> PEInfo:
    """Lee las cabeceras de un PE. Lanza NotPE si no lo es."""
    path = Path(path)
    with path.open("rb") as f:
        head = f.read(4096)
        size = path.stat().st_size
        if len(head) < 64 or head[:2] != b"MZ":
            raise NotPE(T("sin firma MZ", "no MZ signature"))
        (pe_off,) = struct.unpack_from("<I", head, 0x3C)
        if pe_off + 24 > size or pe_off > 16 * 1024 * 1024:
            raise NotPE(T("e_lfanew fuera de rango (¿ejecutable DOS?)", "e_lfanew out of range (DOS executable?)"))
        f.seek(pe_off)
        hdr = f.read(4096)
        base = 0
        if len(hdr) < 24 or hdr[base : base + 4] != b"PE\0\0":
            raise NotPE(T("sin firma PE (¿ejecutable DOS o NE de 16 bits?)", "no PE signature (DOS or 16-bit NE executable?)"))

        coff = base + 4
        machine, nsections = struct.unpack_from("<HH", hdr, coff)
        (opt_size,) = struct.unpack_from("<H", hdr, coff + 16)
        (characteristics,) = struct.unpack_from("<H", hdr, coff + 18)
        opt = coff + 20
        (magic,) = struct.unpack_from("<H", hdr, opt)
        is_64 = magic == 0x20B
        if magic not in (0x10B, 0x20B):
            raise NotPE(T(f"cabecera opcional desconocida 0x{magic:x}", f"unknown optional header 0x{magic:x}"))
        (subsystem,) = struct.unpack_from("<H", hdr, opt + 68)
        ndirs_off = opt + (108 if is_64 else 92)
        (ndirs,) = struct.unpack_from("<I", hdr, ndirs_off)
        dirs_off = ndirs_off + 4
        dirs = []
        for i in range(min(ndirs, 16)):
            rva, sz = struct.unpack_from("<II", hdr, dirs_off + 8 * i)
            dirs.append((rva, sz))

        sect_off = opt + opt_size
        need = sect_off + 40 * min(nsections, 96)
        if need > len(hdr):
            f.seek(pe_off)
            hdr = f.read(need)
        sections = []
        for i in range(min(nsections, 96)):
            o = sect_off + 40 * i
            if o + 40 > len(hdr):
                break
            raw_name = hdr[o : o + 8].split(b"\0", 1)[0]
            vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", hdr, o + 8)
            sections.append(
                Section(raw_name.decode("latin-1"), vaddr, vsize, rptr, rsize)
            )

    info = PEInfo(
        machine=MACHINES.get(machine, "other"),
        subsystem=SUBSYSTEMS.get(subsystem, "other"),
        is_dll=bool(characteristics & 0x2000),
        is_64=is_64,
        sections=sections,
        file_size=size,
    )
    info.overlay_offset = max(
        (s.raw_ptr + s.raw_size for s in sections if s.raw_size), default=0
    )
    if len(dirs) > DIR_CLR and dirs[DIR_CLR][0] and dirs[DIR_CLR][1]:
        info.is_dotnet = True
    if len(dirs) > DIR_RESOURCE and dirs[DIR_RESOURCE][0]:
        rva, sz = dirs[DIR_RESOURCE]
        for s in sections:
            if s.virtual_address <= rva < s.virtual_address + max(s.virtual_size, s.raw_size):
                off = s.raw_ptr + (rva - s.virtual_address)
                info.resource_raw = (off, min(sz, s.raw_size - (rva - s.virtual_address)))
                break
    return info


def read_resources(path: str | Path, info: PEInfo, limit: int = 32 * 1024 * 1024) -> bytes:
    if not info.resource_raw:
        return b""
    off, size = info.resource_raw
    if size <= 0:
        return b""
    with Path(path).open("rb") as f:
        f.seek(off)
        return f.read(min(size, limit))


def version_strings(rsrc: bytes, keys=("ProductName", "FileDescription", "CompanyName",
                                         "OriginalFilename", "Comments", "ProductVersion")) -> dict[str, str]:
    """Extrae cadenas de StringFileInfo buscando las claves en UTF-16.

    Estructura String: wLength, wValueLength (en WCHAR), wType (1=texto),
    szKey, relleno hasta 32 bits, Value. Validamos wType para no confundir
    la clave con texto suelto.
    """
    out: dict[str, str] = {}
    for key in keys:
        needle = key.encode("utf-16-le") + b"\0\0"
        start = 0
        while True:
            idx = rsrc.find(needle, start)
            if idx < 0:
                break
            start = idx + 2
            if idx < 6:
                continue
            w_len, w_val_len, w_type = struct.unpack_from("<HHH", rsrc, idx - 6)
            if w_type != 1 or w_len < len(needle) + 6:
                continue
            pos = idx + len(needle)
            pos += (-(pos - (idx - 6))) % 4  # alinear respecto al inicio del struct
            end_struct = idx - 6 + w_len
            raw = rsrc[pos : min(end_struct, pos + 2 * w_val_len)] if w_val_len else b""
            value = raw.decode("utf-16-le", errors="replace").split("\0", 1)[0].strip()
            if value:
                out[key] = value
                break
            if w_val_len == 0:
                break
    return out
