import struct

import pylnk3

from malive import lnk


def build_lnk(local_path: str, args: str = "", env_target: str | None = None) -> bytes:
    """.lnk mínimo con LinkInfo (ANSI) como los que escribe Wine."""
    flags = lnk.HAS_LINKINFO | lnk.IS_UNICODE
    if args:
        flags |= lnk.HAS_ARGS
    header = bytearray(0x4C)
    struct.pack_into("<I", header, 0, 0x4C)
    header[4:20] = lnk.LNK_CLSID
    struct.pack_into("<I", header, 0x14, flags)
    # LinkInfo: cabecera 0x1C, VolumeID mínimo, LocalBasePath, sufijo vacío
    volume = struct.pack("<IIII", 16, 3, 0, 16) + b"\0"
    base = local_path.encode("cp1252") + b"\0"
    hdr_size = 0x1C
    vol_off = hdr_size
    base_off = vol_off + len(volume)
    suffix_off = base_off + len(base)
    body = volume + base + b"\0"
    total = hdr_size + len(body)
    linkinfo = struct.pack("<IIIIIII", total, hdr_size, 1, vol_off, base_off, 0, suffix_off) + body
    out = bytes(header) + linkinfo
    if args:
        out += struct.pack("<H", len(args)) + args.encode("utf-16-le")
    if env_target:
        block = bytearray(788)
        struct.pack_into("<II", block, 0, 788, lnk.ENV_BLOCK)
        enc = env_target.encode("utf-16-le")
        block[268 : 268 + len(enc)] = enc
        out += bytes(block)
    return out + b"\0\0\0\0"


def test_linkinfo():
    sc = lnk.parse_bytes(build_lnk(r"C:\Program Files\App\app.exe", args="-x 1"))
    assert sc.target == r"C:\Program Files\App\app.exe"
    assert sc.arguments == "-x 1"


def test_env_block_only():
    sc = lnk.parse_bytes(build_lnk("", env_target=r"%ProgramFiles%\App\app.exe"))
    assert sc.target == r"%ProgramFiles%\App\app.exe"


def test_idlist_from_pylnk3(tmp_path):
    f = tmp_path / "a.lnk"
    pylnk3.for_file(r"C:\Program Files (x86)\Mi Juego\juego.exe", str(f),
                    arguments="--ventana", work_dir=r"C:\Program Files (x86)\Mi Juego")
    sc = lnk.parse(f)
    assert sc.target == r"C:\Program Files (x86)\Mi Juego\juego.exe"
    assert sc.arguments == "--ventana"
    assert sc.working_dir == r"C:\Program Files (x86)\Mi Juego"


def test_not_lnk():
    try:
        lnk.parse_bytes(b"MZ" + b"\0" * 100)
    except lnk.NotLnk:
        return
    raise AssertionError("debería fallar")


def test_null_char_in_arguments_is_dropped():
    # El instalador de Notepad++ guarda los argumentos vacíos como "\0" (cuenta 1)
    sc = lnk.parse_bytes(build_lnk(r"C:\Program Files\Notepad++\notepad++.exe", args="\0"))
    assert sc.arguments == ""
