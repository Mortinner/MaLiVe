"""Actualizaciones automáticas de MaLiVe desde las releases de GitHub.

Cada release publica `malive-X.Y.Z.zip` y `malive-X.Y.Z.zip.sha256`
(los crea GitHub Actions al etiquetar vX.Y.Z). El zip lleva `release.json`
con el software del sistema que necesita esa versión.

- Si no hace falta instalar paquetes nuevos: se instala en segundo plano,
  se prueba que arranca y, si algo falla, se vuelve a la versión anterior.
  Después avisa con una notificación.
- Si hacen falta paquetes (requieren contraseña): avisa y ofrece abrir el
  asistente de instalación de la versión nueva.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, config, paths, ui
from .i18n import T

log = logging.getLogger("malive")

REPO = "Mortinner/MaLiVe"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
CHECK_EVERY = int(2.5 * 3600)  # el temporizador va cada 3 h; margen por si se adelanta


class UpdateError(Exception):
    pass


def parse_version(v: str) -> tuple[int, ...]:
    v = v.strip().lstrip("vV")
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", v)
    if not m:
        raise UpdateError(f"versión no válida: {v!r}")
    return tuple(int(x) for x in m.groups())


def lib_dir() -> Path:
    """Donde vive el código instalado (lo crea install.sh)."""
    return paths.data_dir() / "lib"


def bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def installed_version() -> str | None:
    init = lib_dir() / "malive" / "__init__.py"
    try:
        m = re.search(r'__version__ = "([^"]+)"', init.read_text())
    except OSError:
        return None
    return m.group(1) if m else None


@dataclass
class Release:
    version: str
    zip_url: str
    sha_url: str
    notes: str = ""
    url: str = RELEASES_PAGE


@dataclass
class Result:
    status: str  # up-to-date | updated | needs-packages | disabled | skipped
    version: str | None = None
    missing: list[str] = field(default_factory=list)
    staged: Path | None = None


def _get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"malive/{__version__}", "Accept": "application/vnd.github+json",
    })
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (https fijo)


def latest_release() -> Release:
    try:
        with _get(API_LATEST) as r:
            data = json.load(r)
    except OSError as e:
        raise UpdateError(T(f"No se pudo consultar GitHub: {e}", f"Could not reach GitHub: {e}")) from e
    tag = data.get("tag_name", "")
    version = tag.lstrip("vV")
    parse_version(version)
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets", [])}
    zname = f"malive-{version}.zip"
    if zname not in assets or f"{zname}.sha256" not in assets:
        raise UpdateError(T(f"La release {tag} no tiene {zname} y su .sha256", f"Release {tag} has no {zname} and its .sha256"))
    return Release(version, assets[zname], assets[f"{zname}.sha256"],
                   notes=(data.get("body") or "").strip(), url=data.get("html_url") or RELEASES_PAGE)


def _download(rel: Release, dest: Path) -> Path:
    zpath = dest / f"malive-{rel.version}.zip"
    try:
        with _get(rel.sha_url) as r:
            expected = r.read().decode(errors="replace").split()[0].lower()
        h = hashlib.sha256()
        with _get(rel.zip_url, timeout=60) as r, zpath.open("wb") as out:
            while chunk := r.read(1 << 16):
                out.write(chunk)
                h.update(chunk)
    except OSError as e:
        raise UpdateError(T(f"Fallo al descargar la versión {rel.version}: {e}", f"Failed to download version {rel.version}: {e}")) from e
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or h.hexdigest() != expected:
        raise UpdateError(T("La suma SHA-256 de la descarga no coincide; actualización descartada",
                            "The SHA-256 of the download does not match; update discarded"))
    return zpath


def _extract(zpath: Path, dest: Path, version: str) -> Path:
    root = f"malive-{version}/"
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            name = info.filename
            if not name.startswith(root) or ".." in Path(name).parts or name.startswith("/"):
                raise UpdateError(T(f"Contenido inesperado en el zip: {name}", f"Unexpected content in the zip: {name}"))
        zf.extractall(dest)
        for info in zf.infolist():  # zipfile no restaura el bit de ejecución
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                os.chmod(dest / info.filename, mode)
    src = dest / root
    if not (src / "malive" / "__init__.py").is_file():
        raise UpdateError(T("El zip no contiene el paquete malive", "The zip does not contain the malive package"))
    return src


def missing_packages(manifest: dict) -> list[str]:
    """Paquetes del sistema que pide release.json y no están instalados."""
    if not shutil.which("pacman"):
        return []
    def installed(pkg: str) -> bool:
        return subprocess.run(["pacman", "-Qq", pkg], capture_output=True).returncode == 0
    missing = [p for p in manifest.get("packages", []) if not installed(p)]
    for group in manifest.get("any_of", []):
        if group and not any(installed(p) for p in group):
            missing.append(" o ".join(group))
    return missing


def _python() -> str:
    return "/usr/bin/python3" if os.access("/usr/bin/python3", os.X_OK) else sys.executable


def write_wrappers(lib: Path, bindir: Path) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    for name, mod in (("malive", "cli"), ("malive-run", "runner")):
        p = bindir / name
        tmp = p.with_suffix(".new")
        tmp.write_text(
            f"#!{_python()}\nimport sys\nsys.path.insert(0, {str(lib)!r})\n"
            f"from malive.{mod} import main\nsys.exit(main())\n"
        )
        tmp.chmod(0o755)
        os.replace(tmp, p)


def _smoke_test(lib: Path, expected: str) -> None:
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import malive, malive.cli, malive.runner; "
            "print(malive.__version__)")
    r = subprocess.run([_python(), "-I", "-c", code, str(lib)], capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or r.stdout.strip() != expected:
        raise UpdateError(T("La versión nueva no arranca: ", "The new version does not start: ") + (r.stderr or r.stdout).strip()[-400:])


def install_from(src: Path, version: str) -> None:
    """Instala el paquete de `src` sustituyendo al actual, con vuelta atrás."""
    lib = lib_dir()
    lib.mkdir(parents=True, exist_ok=True)
    staging = lib / ".staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    shutil.copytree(src / "malive", staging / "malive",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    try:
        _smoke_test(staging, version)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    current, previous = lib / "malive", lib / "malive.prev"
    shutil.rmtree(previous, ignore_errors=True)
    if current.exists():
        current.rename(previous)
    (staging / "malive").rename(current)
    shutil.rmtree(staging, ignore_errors=True)

    # Pasos posteriores con el código NUEVO: asociaciones, temporizador, accesos
    try:
        write_wrappers(lib, bin_dir())
        for args in (["integrate"], ["refresh"]):
            r = subprocess.run([str(bin_dir() / "malive"), *args], capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                raise UpdateError(T(f"«malive {' '.join(args)}» falló: ", f"«malive {' '.join(args)}» failed: ") + r.stderr.strip()[-300:])
    except (UpdateError, OSError, subprocess.TimeoutExpired) as e:
        log.error("actualización a %s fallida, volviendo atrás: %s", version, e)
        shutil.rmtree(current, ignore_errors=True)
        if previous.exists():
            previous.rename(current)
        write_wrappers(lib, bin_dir())
        raise UpdateError(T(f"No se pudo completar la actualización ({e}); se mantiene la versión anterior",
                            f"The update could not be completed ({e}); the previous version is kept")) from e


def _stamp() -> Path:
    return paths.data_dir() / ".update-last-check"


def due(now: float | None = None) -> bool:
    try:
        last = float(_stamp().read_text())
    except (OSError, ValueError):
        return True
    return (now or time.time()) - last >= CHECK_EVERY


def check_and_update(force: bool = False, stage_dir: Path | None = None) -> Result:
    cfg = config.load_global()
    if not force and not cfg.get("auto_update", True):
        return Result("disabled")
    if not force and not due():
        return Result("skipped")
    current = installed_version() or __version__
    rel = latest_release()
    _stamp().parent.mkdir(parents=True, exist_ok=True)
    _stamp().write_text(str(int(time.time())))
    if parse_version(rel.version) <= parse_version(current):
        return Result("up-to-date", current)

    log.info("hay una versión nueva: %s (instalada %s)", rel.version, current)
    work = Path(tempfile.mkdtemp(prefix="malive-update-"))
    try:
        zpath = _download(rel, work)
        src = _extract(zpath, work, rel.version)
        manifest = {}
        if (src / "release.json").is_file():
            manifest = json.loads((src / "release.json").read_text())
        missing = missing_packages(manifest)
        if missing:
            # Se guarda el asistente de la versión nueva para abrirlo si el usuario quiere
            keep = stage_dir or (Path.home() / ".cache" / "malive" / f"malive-{rel.version}")
            shutil.rmtree(keep, ignore_errors=True)
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), keep)
            return Result("needs-packages", rel.version, missing, keep)
        install_from(src, rel.version)
        log.info("MaLiVe actualizado a %s", rel.version)
        return Result("updated", rel.version)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def first_line(notes: str) -> str:
    for line in notes.splitlines():
        line = line.strip(" #*-\t")
        if line and not line.lower().startswith("malive"):
            return line[:160]
    return ""


def launch_installer(staged: Path) -> None:
    """Abre el asistente de la versión nueva (se abre solo en una terminal).

    Desde el temporizador corremos dentro de una unidad de systemd que, al
    terminar, mata todo lo que lanzó; con `systemd-run --scope` la terminal
    vive en su propia unidad.
    """
    cmd = ["bash", str(staged / "install.sh")]
    if shutil.which("systemd-run"):
        cmd = ["systemd-run", "--user", "--scope", "--quiet", "--collect", *cmd]
    subprocess.run(cmd, cwd=staged, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def auto() -> int:
    """Lo que ejecuta el temporizador: actualizar MaLiVe y avisar."""
    try:
        res = check_and_update()
    except UpdateError as e:
        log.warning("actualización automática: %s", e)
        return 0  # sin conexión, etc.: se reintentará mañana
    if res.status == "updated":
        ui.notify(T(f"MaLiVe se ha actualizado a la versión {res.version}.", f"MaLiVe has been updated to version {res.version}."),
                  icon="system-software-update")
    elif res.status == "needs-packages" and res.staged:
        choice = ui.notify_action(
            T(f"Hay una versión nueva de MaLiVe ({res.version}) que necesita instalar: {', '.join(res.missing)}.",
              f"A new version of MaLiVe ({res.version}) needs to install: {', '.join(res.missing)}."),
            actions={"install": T("Actualizar ahora", "Update now")},
            icon="system-software-update",
        )
        if choice == "install":
            launch_installer(res.staged)
    return 0
