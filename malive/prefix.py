"""Prefijos: creación vía umu, unidades (aislamiento), rutas Windows<->Unix,
detección de procesos vivos y lanzamiento con umu-run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import config, paths, proton, ui
from .config import App

EMPTY_DIR = "malive-empty"  # destino de Z: cuando se aísla
STATE_FILE = "malive-drives.json"
SOURCE_LETTER = "y"
STEAMUSER = "steamuser"


class PrefixError(Exception):
    pass


# --------------------------------------------------------------------------
# Unidades
# --------------------------------------------------------------------------

def dosdevices(prefix: Path) -> Path:
    return prefix / "dosdevices"


def drive_c(prefix: Path) -> Path:
    return prefix / "drive_c"


def is_created(prefix: Path) -> bool:
    return drive_c(prefix).is_dir() and dosdevices(prefix).is_dir()


def drives(prefix: Path) -> dict[str, Path]:
    """{'c': /ruta/real, ...} solo con letras simples (ignora 'd::' de dispositivos)."""
    out: dict[str, Path] = {}
    dd = dosdevices(prefix)
    if not dd.is_dir():
        return out
    for entry in dd.iterdir():
        name = entry.name.lower()
        if len(name) == 2 and name[1] == ":" and name[0].isalpha() and entry.is_symlink():
            target = Path(os.readlink(entry))
            if not target.is_absolute():
                target = dd / target
            out[name[0]] = Path(os.path.realpath(target))
    return out


def _set_link(link: Path, target: Path) -> None:
    if link.is_symlink() or link.exists():
        if link.is_symlink() and Path(os.readlink(link)) == target:
            return
        link.unlink()
    link.symlink_to(target)


def apply_drives(app: App, cfg: dict, source_dir: Path | None = None) -> dict[str, str]:
    """Configura Z: y las unidades compartidas antes de cada lanzamiento.

    Proton recrea Z: -> / en cada arranque *si no existe*, así que en vez de
    borrarla la apuntamos a una carpeta vacía. Devuelve {letra: ruta}
    de las unidades gestionadas por MaLiVe.
    """
    prefix = app.prefix_path
    dd = dosdevices(prefix)
    if not dd.is_dir():
        return {}
    state_path = prefix / STATE_FILE
    try:
        managed = json.loads(state_path.read_text()) if state_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        managed = {}
    for letter in managed:
        link = dd / f"{letter}:"
        if link.is_symlink():
            link.unlink()

    new: dict[str, str] = {}
    empty = prefix / EMPTY_DIR
    z = dd / "z:"
    if app.effective_isolate(cfg):
        empty.mkdir(exist_ok=True)
        _set_link(z, empty)
        taken = set(drives(prefix))
        letters = [c for c in "defghijklmnopqrstuvwx" if c not in taken]
        covered: list[Path] = [drive_c(prefix).resolve()]
        for folder in config.shared_folders(cfg):
            if not letters:
                break
            letter = letters.pop(0)
            (dd / f"{letter}:").symlink_to(folder)
            new[letter] = str(folder)
            covered.append(folder.resolve())
        if source_dir is not None:
            src = source_dir.resolve()
            if not any(src == c or c in src.parents for c in covered):
                link = dd / f"{SOURCE_LETTER}:"
                if link.is_symlink():
                    link.unlink()
                if not link.exists():
                    link.symlink_to(src)
                    new[SOURCE_LETTER] = str(src)
    else:
        if z.is_symlink() and Path(os.readlink(z)) == empty:
            z.unlink()
        if not z.exists():
            z.symlink_to("/")
    state_path.write_text(json.dumps(new))
    return new


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------

def _env_vars() -> dict[str, str]:
    u = rf"C:\users\{STEAMUSER}"
    return {
        "programfiles": r"C:\Program Files",
        "programw6432": r"C:\Program Files",
        "programfiles(x86)": r"C:\Program Files (x86)",
        "commonprogramfiles": r"C:\Program Files\Common Files",
        "commonprogramfiles(x86)": r"C:\Program Files (x86)\Common Files",
        "systemroot": r"C:\windows",
        "windir": r"C:\windows",
        "systemdrive": "C:",
        "homedrive": "C:",
        "homepath": rf"\users\{STEAMUSER}",
        "userprofile": u,
        "appdata": rf"{u}\AppData\Roaming",
        "localappdata": rf"{u}\AppData\Local",
        "programdata": r"C:\ProgramData",
        "allusersprofile": r"C:\ProgramData",
        "public": r"C:\users\Public",
    }


def expand_vars(winpath: str) -> str:
    import re

    table = _env_vars()
    return re.sub(r"%([^%]+)%", lambda m: table.get(m.group(1).lower(), m.group(0)), winpath)


def _ci_join(base: Path, parts: list[str]) -> Path:
    """Une componentes ignorando mayúsculas, como haría Windows."""
    cur = base
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            cur = cur.parent
            continue
        exact = cur / part
        if exact.exists():
            cur = exact
            continue
        match = None
        if cur.is_dir():
            low = part.casefold()
            for child in cur.iterdir():
                if child.name.casefold() == low:
                    match = child
                    break
        cur = match or exact
    return cur


def windows_to_unix(prefix: Path, winpath: str) -> Path | None:
    winpath = expand_vars(winpath.strip().strip('"'))
    if winpath.startswith("\\\\?\\"):
        winpath = winpath[4:]
    if winpath.lower().startswith("unix\\") or winpath.startswith("/"):
        return Path(winpath.replace("\\", "/").removeprefix("unix"))
    if len(winpath) < 2 or winpath[1] != ":":
        return None
    root = drives(prefix).get(winpath[0].lower())
    if root is None:
        return None
    return _ci_join(root, winpath[2:].replace("/", "\\").split("\\"))


def unix_to_windows(prefix: Path, path: Path) -> str | None:
    real = Path(os.path.realpath(path))
    best: tuple[int, str, Path] | None = None
    for letter, root in drives(prefix).items():
        if real == root or root in real.parents:
            depth = len(root.parts)
            if best is None or depth > best[0]:
                best = (depth, letter, root)
    if best is None:
        return None
    _, letter, root = best
    rest = real.relative_to(root)
    return f"{letter.upper()}:\\" + "\\".join(rest.parts)


def find_prefix_root(path: Path) -> Path | None:
    """Si `path` está dentro de un drive_c, devuelve la carpeta del prefijo."""
    real = Path(os.path.realpath(path))
    for parent in real.parents:
        if parent.name == "drive_c":
            return parent.parent
    return None


# --------------------------------------------------------------------------
# Procesos
# --------------------------------------------------------------------------

def _process_prefixes(pid: str) -> set[str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return set()
    found = set()
    for item in raw.split(b"\0"):
        if item.startswith((b"WINEPREFIX=", b"STEAM_COMPAT_DATA_PATH=")):
            value = item.split(b"=", 1)[1].decode(errors="replace").rstrip("/")
            if value:
                found.add(os.path.realpath(value))
    return found


def running_pids(prefix: Path) -> list[int]:
    target = os.path.realpath(prefix)
    me = os.getpid()
    pids = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        if target in _process_prefixes(entry.name):
            pids.append(int(entry.name))
    return pids


def is_busy(prefix: Path) -> bool:
    return bool(running_pids(prefix))


def wait_idle(prefix: Path, poll: float = 2.0, settle: int = 2, timeout: float | None = None) -> bool:
    """Espera a que no quede ningún proceso del prefijo (incluido wineserver).
    `settle` comprobaciones seguidas sin procesos para evitar falsos positivos
    entre un proceso que termina y otro que arranca."""
    start = time.monotonic()
    quiet = 0
    while True:
        if is_busy(prefix):
            quiet = 0
        else:
            quiet += 1
            if quiet >= settle:
                return True
        if timeout is not None and time.monotonic() - start > timeout:
            return False
        time.sleep(poll)


# --------------------------------------------------------------------------
# umu
# --------------------------------------------------------------------------

def umu_binary() -> str:
    exe = os.environ.get("MALIVE_UMU_RUN") or shutil.which("umu-run")
    if not exe:
        raise PrefixError(
            "No se encuentra umu-run. Instálalo con:\n  sudo pacman -S umu-launcher"
        )
    return exe


def ensure_proton_present(app: App) -> Path:
    path = proton.path_of(app.proton)
    if proton.is_valid(path):
        return path
    with ui.progress(f"Descargando {app.proton}, la versión de Proton que usa {app.name}…"):
        proton.download(proton.release_by_name(app.proton))
    return path


def build_env(app: App, cfg: dict, verb: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        WINEPREFIX=str(app.prefix_path),
        GAMEID=cfg.get("gameid", "umu-default"),
        PROTONPATH=str(proton.path_of(app.proton)),
        STORE="none",
    )
    if verb:
        env["PROTON_VERB"] = verb
    if ui.has_zenity():
        env["UMU_ZENITY"] = "1"
    return env


def log_path(app: App) -> Path:
    d = paths.logs_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{app.id}.log"


def run_umu(app: App, cfg: dict, argv: list[str], cwd: Path | None = None,
            verb: str | None = None, log_mode: str = "a",
            install_path: Path | None = None) -> tuple[int, float]:
    """Ejecuta umu-run y espera a que termine. Devuelve (código, segundos).

    install_path: carpeta que el contenedor debe ver aunque argv[0] no sea un
    fichero (p. ej. "msiexec"); umu la monta como STEAM_COMPAT_INSTALL_PATH.
    """
    exe = umu_binary()
    if verb is None:
        # Si ya hay algo corriendo en el prefijo, "waitforexitandrun" esperaría
        # a que se cerrase; "run" lanza en paralelo con el mismo wineserver.
        verb = "run" if is_busy(app.prefix_path) else "waitforexitandrun"
    env = build_env(app, cfg, verb)
    if install_path is not None:
        env["STEAM_COMPAT_INSTALL_PATH"] = str(install_path)
    log = log_path(app)
    start = time.monotonic()
    with log.open(log_mode, encoding="utf-8", errors="replace") as lf:
        lf.write(f"\n===== {time.strftime('%F %T')} umu-run {argv!r} (verb={verb}, proton={app.proton})\n")
        lf.flush()
        proc = subprocess.Popen(
            [exe, *argv], env=env, cwd=str(cwd) if cwd else None,
            stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        rc = proc.wait()
        lf.write(f"===== exit {rc}\n")
    return rc, time.monotonic() - start


def create(app: App, cfg: dict) -> None:
    """Crea el prefijo con Proton (umu-run "") mostrando "Preparando…"."""
    prefix = app.prefix_path
    prefix.mkdir(parents=True, exist_ok=True)
    with ui.progress(f"Preparando el entorno para {app.name}…\n(solo la primera vez; puede tardar un par de minutos)"):
        rc, _ = run_umu(app, cfg, [""], cwd=prefix, verb="waitforexitandrun", log_mode="w")
    if not is_created(prefix):
        raise PrefixError(
            f"No se pudo crear el entorno (umu-run terminó con código {rc}).\n"
            f"Revisa el registro: {log_path(app)}"
        )
    apply_drives(app, cfg)


def disk_usage(prefix: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(prefix):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total
