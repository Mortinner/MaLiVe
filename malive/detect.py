"""Clasifica un fichero: instalador, MSI, programa, acceso directo...

La detección de instaladores mira firmas conocidas (NSIS, Inno Setup,
InstallShield, WiX Burn, otros) en la cabecera, los recursos y el overlay;
si no hay firma, usa el nombre del fichero y la descripción de versión.
"""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import lnk, pe
from .i18n import T

OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
SEVENZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"

HEAD_SCAN = 8 * 1024 * 1024
OVERLAY_SCAN = 4 * 1024 * 1024

# (subtipo, firmas en bytes). Se buscan en ASCII y en UTF-16.
INSTALLER_SIGNATURES: list[tuple[str, list[bytes]]] = [
    ("nsis", [b"NullsoftInst"]),
    ("inno", [b"Inno Setup Setup Data", b"Inno Setup Messages", b"This installation was built with Inno Setup"]),
    ("installshield", [b"InstallShield"]),
    ("wix-burn", [b".wixburn", b"WixBundle"]),
    ("advanced-installer", [b"Advanced Installer"]),
    ("installaware", [b"InstallAware"]),
    ("setup-factory", [b"Setup Factory"]),
    ("wise", [b"Wise Installation"]),
    ("smart-install-maker", [b"Smart Install Maker"]),
]

GENERIC_PRODUCT_NAMES = {
    "inno setup", "nullsoft install system", "nsis", "installshield", "wix toolset",
    "setup", "installer", "install", "7-zip", "7-zip sfx", "setup factory",
    "advanced installer", "installaware", "windows installer", "self-extracting archive",
    # Instaladores/lanzadores que copian los datos de versión de Windows (Rockstar)
    "microsoft windows operating system", "windows operating system", "operating system",
    "microsoft windows", "windows", "bootstrapper", "web installer", "launcher", "application",
}


def _generic(value: str) -> bool:
    norm = re.sub(r"\s+", " ", re.sub(r"[®™©]", "", value).lower()).strip()
    # Descripciones de piezas de Windows que algunos .exe copian tal cual
    # (el lanzador de Rockstar: «ApiSet Stub DLL»)
    return norm in GENERIC_PRODUCT_NAMES or bool(re.search(r"\b(stub|dll)\b", norm))

NAME_HINT = re.compile(r"(^|[\W_])(setup|install|installer|instalador|instalar|instalacion)([\W_\d]|$)", re.I)

NOISE_TOKENS = re.compile(
    r"^(setup|install|installer|instalador|instalar|portable|win(dows)?(32|64)?|x(86|64)|amd64|arm64|"
    r"(32|64)[-_ ]?bits?|full|final|release|offline|online|web|multilingual|multi|es|en|"
    r"v?\d+(\.\d+)*[a-z]?|build\d*|r\d+)$",
    re.I,
)


@dataclass
class Detection:
    kind: str  # installer | msi | app | lnk | dll | dos | unsupported
    path: Path
    subtype: str | None = None  # nsis, inno, ... para instaladores
    name: str = ""
    pe: pe.PEInfo | None = None
    shortcut: lnk.Shortcut | None = None
    version: dict[str, str] = field(default_factory=dict)
    reason: str = ""

    @property
    def is_installer(self) -> bool:
        return self.kind in ("installer", "msi")


def _contains_any(blob: bytes, needles: list[bytes]) -> bool:
    for n in needles:
        if n in blob or n.decode("latin-1").encode("utf-16-le") in blob:
            return True
    return False


def clean_name(raw: str) -> str:
    """'npp.8.6.2.Installer.x64' -> 'npp'; 'Foo Bar Setup 1.2' -> 'Foo Bar'."""
    raw = raw.strip()
    if raw.lower().endswith((".exe", ".msi")):
        raw = raw[:-4]
    tokens = [t for t in re.split(r"[\s._\-()\[\]]+", raw) if t]
    kept = [t for t in tokens if not NOISE_TOKENS.match(t)]
    name = " ".join(kept).strip() or raw
    return name[:80]


GENERIC_FILENAMES = {"setup", "install", "installer", "instalar", "instalador", "autorun", "start", "launcher"}


def name_from_path(path: Path) -> str:
    """Nombre a partir del fichero; si es genérico (setup.exe), usa la carpeta."""
    name = clean_name(path.name)
    if name.casefold() in GENERIC_FILENAMES and path.parent.name:
        parent = clean_name(path.parent.name)
        if parent and parent.casefold() not in GENERIC_FILENAMES:
            return parent
    return name


_PAREN_NOISE = re.compile(r"\s*[(\[][^)\]]*(bit|x64|x86|amd64|arm64|edition|version|versi[oó]n|build)[^)\]]*[)\]]", re.I)
_VERSION_TOKEN = re.compile(r"^(v?\d+(\.\d+)+[a-z]?|release|version|versi[oó]n|build|x64|x86|amd64|arm64|"
                            r"(32|64)-?bits?|win(32|64)|setup|installer|instalador)$", re.I)


def clean_product_name(value: str) -> str:
    """'PuTTY release 0.85 (64-bit)' -> 'PuTTY'; '7-Zip 24.08 (x64 edition)' -> '7-Zip';
    'Office 2010' se queda igual (un número sin puntos no es una versión)."""
    value = _PAREN_NOISE.sub("", value).strip()
    tokens = value.split()
    while len(tokens) > 1 and _VERSION_TOKEN.match(tokens[-1].strip(",;-")):
        tokens.pop()
    kept = [t for i, t in enumerate(tokens) if i == 0 or not _VERSION_TOKEN.match(t.strip(",;-"))]
    return " ".join(kept).strip(" -,") or value


def _product_name(version: dict[str, str], path: Path) -> str:
    for key in ("ProductName", "FileDescription"):
        value = version.get(key, "").strip()
        if value and not _generic(value):
            cleaned = clean_product_name(value)
            if cleaned and not _generic(cleaned):
                return cleaned[:80]
    return name_from_path(path)


def msi_product_name(path: Path) -> str | None:
    """Lee ProductName de la tabla Property (requiere msitools)."""
    if not shutil.which("msiinfo"):
        return None
    try:
        out = subprocess.run(
            ["msiinfo", "export", str(path), "Property"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if line.startswith("ProductName\t"):
            return line.split("\t", 1)[1].strip() or None
    return None


def detect(path: str | Path) -> Detection:
    path = Path(path)
    with path.open("rb") as f:
        head = f.read(HEAD_SCAN)

    if lnk.is_lnk(head):
        try:
            sc = lnk.parse_bytes(head)
        except (lnk.NotLnk, struct.error) as e:
            return Detection("unsupported", path, reason=T(f".lnk dañado: {e}", f"damaged .lnk: {e}"))
        return Detection("lnk", path, shortcut=sc, name=path.stem)

    if head[:8] == OLE_MAGIC:
        msi_clsid = bytes.fromhex("84100c0000000000c000000000000046")
        if path.suffix.lower() == ".msi" or msi_clsid in head:
            raw = msi_product_name(path)
            name = clean_product_name(raw) if raw else name_from_path(path)
            return Detection("msi", path, subtype="msi", name=name)
        return Detection("unsupported", path, reason=T("documento OLE que no es un MSI", "OLE document that is not an MSI"))

    if head[:2] != b"MZ":
        return Detection("unsupported", path, reason=T("no es un ejecutable de Windows", "not a Windows executable"))

    try:
        info = pe.parse(path)
    except (pe.NotPE, struct.error) as e:
        return Detection("dos", path, reason=T(f"ejecutable sin cabecera PE: {e}", f"executable without a PE header: {e}"))

    if info.is_dll:
        return Detection("dll", path, pe=info, reason=T("es una biblioteca (.dll), no un programa", "it is a library (.dll), not a program"))

    rsrc = pe.read_resources(path, info)
    version = pe.version_strings(rsrc) if rsrc else {}
    det = Detection("app", path, pe=info, version=version)
    det.name = _product_name(version, path)

    overlay = b""
    if info.has_overlay:
        with path.open("rb") as f:
            f.seek(info.overlay_offset)
            overlay = f.read(OVERLAY_SCAN)

    sections = info.section_names()
    if ".wixburn" in sections:
        det.kind, det.subtype = "installer", "wix-burn"
        return det
    for subtype, sigs in INSTALLER_SIGNATURES:
        if _contains_any(overlay, sigs) or _contains_any(head, sigs) or _contains_any(rsrc[:HEAD_SCAN], sigs):
            det.kind, det.subtype = "installer", subtype
            return det
    if overlay.startswith(SEVENZIP_MAGIC) or SEVENZIP_MAGIC in overlay[:4096]:
        det.kind, det.subtype = "installer", "7z-sfx"
        return det

    desc = " ".join(version.get(k, "") for k in ("FileDescription", "OriginalFilename", "ProductName"))
    if NAME_HINT.search(path.stem) or NAME_HINT.search(desc):
        det.kind, det.subtype = "installer", "generic"
        det.reason = "nombre o descripción de instalador"
        return det
    return det
