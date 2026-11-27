"""Soporte de NVIDIA (CUDA, NVENC/NVDEC, OptiX) dentro de Proton.

Proton trae NVAPI (DXVK-NVAPI, se activa con PROTON_ENABLE_NVAPI=1) pero no los
puentes que dan a los programas de Windows acceso a CUDA y al codificador de
vídeo de la tarjeta. Los aporta el proyecto nvidia-libs de SveSop
(https://github.com/SveSop/nvidia-libs, LGPL), que traduce esas DLL de Windows
a las librerías del driver de NVIDIA instalado en Linux (libcuda,
libnvidia-encode, libnvcuvid…).

MaLiVe descarga una versión, la guarda en ~/.local/share/malive/nvidia-libs y,
en las apps que lo usan, enlaza esas DLL en system32 del entorno (como hace su
setup_nvlibs.sh) y las marca como «nativas» con WINEDLLOVERRIDES al lanzar.
NVAPI se deja a Proton.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from . import paths
from .i18n import T

log = logging.getLogger("malive")

GITHUB_LATEST = "https://api.github.com/repos/SveSop/nvidia-libs/releases/latest"
USER_AGENT = "malive/0.1"
# DLL de 64 bits que enlazamos (NVAPI la pone Proton)
DLLS = ("nvcuda", "nvcuvid", "nvencodeapi64", "nvoptix")
BACKUP_SUFFIX = ".malive-orig"


class NvidiaError(Exception):
    pass


def gpu_present() -> bool:
    """¿Hay una tarjeta NVIDIA con el driver propietario cargado?"""
    return Path("/proc/driver/nvidia/version").is_file()


def driver_version() -> str | None:
    try:
        text = Path("/proc/driver/nvidia/version").read_text(errors="replace")
    except OSError:
        return None
    m = re.search(r"Kernel Module(?: for [\w-]+)?\s+(\d+\.\d+(?:\.\d+)?)", text)
    return m.group(1) if m else None


def libs_root() -> Path:
    return paths.data_dir() / "nvidia-libs"


def _current_file() -> Path:
    return libs_root() / "current.json"


def is_valid(d: Path) -> bool:
    return all((d / "x64" / f"{n}.dll").is_file() for n in DLLS)


def installed() -> Path | None:
    """Carpeta de la versión en uso (la que tiene x64/nvcuda.dll…)."""
    try:
        name = json.loads(_current_file().read_text())["dir"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    d = libs_root() / name
    return d if is_valid(d) else None


def _http_get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (URL fija https)


def _pick_asset(assets: list[dict]) -> dict | None:
    """El paquete principal (no el de 32 bits de nvcuda ni el código fuente)."""
    good = [a for a in assets
            if re.search(r"\.tar\.(xz|gz|zst)$|\.zip$", a.get("name", ""))
            and "32" not in a.get("name", "").replace("x86_64", "")
            and "source" not in a.get("name", "").lower()]
    pref = [a for a in good if a["name"].lower().startswith("nvidia-libs")]
    return (pref or good or [None])[0]


def find_root(extracted: Path) -> Path | None:
    for x64 in sorted(extracted.rglob("x64")):
        if x64.is_dir() and is_valid(x64.parent):
            return x64.parent
    return None


def download(on_progress=None) -> Path:
    """Descarga la última versión (si no está ya) y la deja como la actual."""
    try:
        with _http_get(GITHUB_LATEST) as r:
            data = json.load(r)
    except OSError as e:
        raise NvidiaError(T(f"No se pudo consultar GitHub: {e}", f"Could not reach GitHub: {e}")) from e
    tag = re.sub(r"[^\w.-]", "_", data.get("tag_name") or "latest")
    asset = _pick_asset(data.get("assets", []))
    if asset is None:
        raise NvidiaError(T("La versión de nvidia-libs no tiene un paquete descargable",
                            "This nvidia-libs release has no downloadable package"))
    root = libs_root()
    final = root / tag
    if is_valid(final):
        _set_current(tag)
        return final
    root.mkdir(parents=True, exist_ok=True)
    digest = (asset.get("digest") or "").lower()
    expected = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
    work = Path(tempfile.mkdtemp(prefix=".download-", dir=root))
    try:
        archive = work / asset["name"]
        h = hashlib.sha256()
        try:
            with _http_get(asset["browser_download_url"], timeout=60) as r, archive.open("wb") as out:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while chunk := r.read(1 << 20):
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
        except OSError as e:
            raise NvidiaError(T(f"Fallo al descargar {asset['name']}: {e}", f"Failed to download {asset['name']}: {e}")) from e
        if expected and h.hexdigest() != expected:
            raise NvidiaError(T(f"La suma SHA-256 de {asset['name']} no coincide; descarga descartada",
                                f"The SHA-256 of {asset['name']} does not match; download discarded"))
        extract = work / "x"
        extract.mkdir()
        cmd = ["unzip", "-q", str(archive), "-d", str(extract)] if asset["name"].endswith(".zip") \
            else ["tar", "-xf", str(archive), "-C", str(extract)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise NvidiaError(T(f"No se pudo extraer {asset['name']}: {r.stderr.strip()}",
                                f"Could not extract {asset['name']}: {r.stderr.strip()}"))
        found = find_root(extract)
        if found is None:
            raise NvidiaError(T("El paquete descargado no contiene las DLL esperadas",
                                "The downloaded package does not contain the expected DLLs"))
        found.rename(final)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _set_current(tag)
    log.info("nvidia-libs %s descargado en %s", tag, final)
    return final


def _set_current(tag: str) -> None:
    _current_file().write_text(json.dumps({"dir": tag}))


def ensure(ui=None) -> Path | None:
    """La versión instalada, o la descarga (mostrando «Preparando…»). None si falla:
    el programa debe poder arrancar igualmente, sin aceleración."""
    d = installed()
    if d is not None:
        return d
    try:
        if ui is not None:
            with ui.progress(T("Descargando el soporte de NVIDIA (CUDA, NVENC)…\n(solo la primera vez)",
                               "Downloading NVIDIA support (CUDA, NVENC)…\n(first time only)")):
                return download()
        return download()
    except NvidiaError as e:
        log.warning("nvidia-libs: %s", e)
        return None


def _system32(prefix: Path) -> Path:
    return prefix / "drive_c" / "windows" / "system32"


def link_into(prefix: Path, libs: Path) -> None:
    """Enlaza las DLL en system32 del entorno; guarda las de Proton para poder volver."""
    s32 = _system32(prefix)
    if not s32.is_dir():
        return
    for name in DLLS:
        dst = s32 / f"{name}.dll"
        src = libs / "x64" / f"{name}.dll"
        if dst.is_symlink() and Path(os.readlink(dst)) == src:
            continue
        backup = s32 / f"{name}.dll{BACKUP_SUFFIX}"
        if (dst.exists() or dst.is_symlink()) and not _is_ours(dst):
            if not backup.exists() and not backup.is_symlink():
                dst.rename(backup)
            else:
                dst.unlink()
        elif dst.is_symlink() or dst.exists():
            dst.unlink()
        dst.symlink_to(src)


def _is_ours(p: Path) -> bool:
    try:
        return p.is_symlink() and libs_root() in Path(os.readlink(p)).parents
    except OSError:
        return False


def unlink_from(prefix: Path) -> None:
    """Quita nuestros enlaces y devuelve las DLL de Proton."""
    s32 = _system32(prefix)
    for name in DLLS:
        dst = s32 / f"{name}.dll"
        if _is_ours(dst):
            dst.unlink()
        backup = s32 / f"{name}.dll{BACKUP_SUFFIX}"
        if (backup.exists() or backup.is_symlink()) and not (dst.exists() or dst.is_symlink()):
            backup.rename(dst)


def overrides() -> dict[str, str]:
    return {n: "n" for n in DLLS}
