"""Gestión de GE-Proton: listar, descargar (con verificación SHA-512) y elegir.

MaLiVe guarda sus Proton en ~/.local/share/malive/proton y no en
compatibilitytools.d de Steam, para que nadie más (umu, ProtonUp, Steam)
borre una versión que tiene fijada alguna app.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config, paths

GITHUB_API = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases"
GITHUB_LATEST = GITHUB_API + "/latest"
USER_AGENT = "malive/0.1"
VERSION_RE = re.compile(r"GE-Proton(\d+)-(\d+)")


class ProtonError(Exception):
    pass


def version_key(name: str) -> tuple[int, int]:
    m = VERSION_RE.search(name)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def is_valid(path: Path) -> bool:
    return (path / "proton").is_file() and (path / "toolmanifest.vdf").is_file()


def installed() -> list[str]:
    d = paths.proton_dir()
    if not d.is_dir():
        return []
    names = [p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith(".") and is_valid(p)]
    return sorted(names, key=version_key)


def path_of(name: str) -> Path:
    return paths.proton_dir() / name


def newest_installed() -> str | None:
    names = installed()
    return names[-1] if names else None


@dataclass
class Release:
    name: str
    tarball_url: str
    tarball_name: str
    checksum_url: str | None


def _http_get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (URL fija https)


def latest_release() -> Release:
    return _release(GITHUB_LATEST)


def release_by_name(name: str) -> Release:
    """Para reinstalar una versión fijada que se haya borrado."""
    if not VERSION_RE.fullmatch(name):
        raise ProtonError(f"Nombre de versión no válido: {name}")
    return _release(f"{GITHUB_API}/tags/{name}")


def _release(url: str) -> Release:
    try:
        with _http_get(url) as r:
            data = json.load(r)
    except OSError as e:
        raise ProtonError(f"No se pudo consultar GitHub: {e}") from e
    machine = platform.machine()
    assets = [a for a in data.get("assets", [])
              if machine == "aarch64" or "aarch64" not in a.get("name", "")]
    tarball = next((a for a in assets if a.get("name", "").startswith("GE-Proton")
                    and a["name"].endswith((".tar.gz", ".tar.xz"))), None)
    if not tarball:
        raise ProtonError("La versión de GE-Proton no tiene un archivo descargable para esta arquitectura")
    base = re.sub(r"\.tar\.(gz|xz)$", "", tarball["name"])
    sums = [a for a in assets if a.get("name", "").endswith(".sha512sum")]
    checksum = next((a for a in sums if a["name"].startswith(base)), sums[0] if sums else None)
    return Release(
        name=base,
        tarball_url=tarball["browser_download_url"],
        tarball_name=tarball["name"],
        checksum_url=checksum["browser_download_url"] if checksum else None,
    )


def download(release: Release, on_progress=None) -> str:
    """Descarga, verifica y extrae. Devuelve el nombre de la versión instalada."""
    dest_root = paths.proton_dir()
    dest_root.mkdir(parents=True, exist_ok=True)
    final = dest_root / release.name
    if is_valid(final):
        return release.name

    expected = None
    if release.checksum_url:
        try:
            with _http_get(release.checksum_url) as r:
                text = r.read().decode(errors="replace")
        except OSError as e:
            raise ProtonError(f"No se pudo descargar la suma de verificación: {e}") from e
        lines = [l.split() for l in text.splitlines() if l.strip()]
        match = [l for l in lines if len(l) >= 2 and l[-1].lstrip("*") == release.tarball_name]
        chosen = (match or lines or [[""]])[0]
        expected = chosen[0].lower() if re.fullmatch(r"[0-9a-fA-F]{128}", chosen[0]) else None
        if expected is None:
            raise ProtonError("El fichero de suma de verificación no tiene un formato válido")

    work = Path(tempfile.mkdtemp(prefix=".download-", dir=dest_root))
    try:
        archive = work / release.tarball_name
        h = hashlib.sha512()
        try:
            with _http_get(release.tarball_url, timeout=60) as r, archive.open("wb") as out:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while chunk := r.read(1 << 20):
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
        except OSError as e:
            raise ProtonError(f"Fallo al descargar {release.tarball_name}: {e}") from e

        if expected and h.hexdigest() != expected:
            raise ProtonError(f"La suma SHA-512 de {release.tarball_name} no coincide; descarga descartada")

        extract = work / "x"
        extract.mkdir()
        r = subprocess.run(["tar", "-xf", str(archive), "-C", str(extract)], capture_output=True, text=True)
        if r.returncode != 0:
            raise ProtonError(f"No se pudo extraer {release.tarball_name}: {r.stderr.strip()}")
        candidates = [p for p in extract.iterdir() if p.is_dir() and is_valid(p)]
        if len(candidates) != 1:
            raise ProtonError("El archivo descargado no contiene un Proton válido")
        candidates[0].rename(final)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return release.name


def update(on_progress=None) -> tuple[str, bool]:
    """Instala la última versión si no está. Devuelve (nombre, si_era_nueva)."""
    rel = latest_release()
    already = is_valid(path_of(rel.name))
    if not already:
        download(rel, on_progress)
    _stamp_check()
    return rel.name, not already


def _stamp_check() -> None:
    stamp = paths.data_dir() / ".proton-last-check"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(str(int(time.time())))


def in_use() -> set[str]:
    return {app.proton for app in config.all_apps()}


def cleanup(keep_unused: int = 1) -> list[str]:
    """Borra versiones que ninguna app usa, salvo las `keep_unused` más nuevas."""
    used = in_use()
    unused = [n for n in installed() if n not in used]
    to_remove = unused[:-keep_unused] if keep_unused > 0 else unused
    for name in to_remove:
        shutil.rmtree(path_of(name), ignore_errors=True)
    return to_remove


def ensure_for_new_app(ui=None) -> str:
    """Versión a fijar en una app nueva: la más reciente instalada o, si no
    hay ninguna, la última de GitHub (descargándola con diálogo de progreso)."""
    name = newest_installed()
    if name:
        return name
    if ui is None:
        return update()[0]
    with ui.progress("Descargando GE-Proton (solo la primera vez, ~500 MB)…"):
        name, _ = update()
    return name
