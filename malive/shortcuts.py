"""Después de instalar: encontrar los programas nuevos y crear sus .desktop.

Proton desactiva winemenubuilder, así que los accesos directos de Windows
(.lnk) no llegan solos al menú de Linux: los buscamos nosotros comparando
una instantánea del prefijo antes y después de instalar.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config, lnk, paths, prefix as pfx
from .config import App, Entry

SKIP_DIRS = {"temp", "tmp", "cache", "inetcache", "crashdumps"}
SKIP_TOP = {"windows"}  # solo drive_c/windows (no ...\Microsoft\Windows\Start Menu)
# Accesos/ejecutables que no son "el programa"
log = logging.getLogger("malive")

JUNK = re.compile(
    r"(unins\d*|uninst|uninstall|desinstal|remove|readme|l[eé]ame|help|ayuda|manual|"
    r"license|licencia|eula|website|web site|homepage|changelog|release notes|"
    r"crash|report|bugreport|updater?|autoupdate|vc_?redist|dxsetup|dotnet|"
    r"notification_helper|elevate|helper|setup|install|service|svc|repair)",
    re.I,
)
JUNK_EXE_EXACT = {"unitycrashhandler64.exe", "unitycrashhandler32.exe", "crashpad_handler.exe",
                  "createdump.exe", "cefsharp.browsersubprocess.exe", "squirrel.exe", "update.exe"}


@dataclass
class Snapshot:
    lnks: dict[str, float] = field(default_factory=dict)
    exes: set[str] = field(default_factory=set)


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        top = dirpath == str(root)
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in SKIP_DIRS and not (top and d.lower() in SKIP_TOP)]
        yield dirpath, filenames


def take_snapshot(prefix: Path) -> Snapshot:
    snap = Snapshot()
    c = pfx.drive_c(prefix)
    if not c.is_dir():
        return snap
    for dirpath, files in _walk(c):
        for name in files:
            low = name.lower()
            full = os.path.join(dirpath, name)
            if low.endswith(".lnk"):
                try:
                    snap.lnks[full] = os.lstat(full).st_mtime
                except OSError:
                    pass
            elif low.endswith(".exe"):
                snap.exes.add(full)
    return snap


def menu_dirs(prefix: Path) -> list[Path]:
    """Carpetas donde los instaladores dejan accesos (menú Inicio y escritorio)."""
    c = pfx.drive_c(prefix)
    dirs = [c / "ProgramData/Microsoft/Windows/Start Menu"]
    users = c / "users"
    if users.is_dir():
        for u in users.iterdir():
            if u.is_dir() and not u.is_symlink():
                dirs += [u / "Desktop", u / "AppData/Roaming/Microsoft/Windows/Start Menu"]
    return [d for d in dirs if d.is_dir()]


def take_lnk_snapshot(prefix: Path) -> Snapshot:
    """Instantánea ligera (solo .lnk del menú y el escritorio) para vigilar
    mientras el instalador sigue en marcha."""
    snap = Snapshot()
    for d in menu_dirs(prefix):
        for dirpath, _dirs, files in os.walk(d):
            for name in files:
                if name.lower().endswith(".lnk"):
                    full = os.path.join(dirpath, name)
                    try:
                        snap.lnks[full] = os.lstat(full).st_mtime
                    except OSError:
                        pass
    return snap


@dataclass
class Candidate:
    name: str
    exe: Path
    args: list[str] = field(default_factory=list)
    workdir: Path | None = None
    icon_source: Path | None = None
    icon_index: int = 0
    from_lnk: bool = False


def _split_args(arguments: str) -> list[str]:
    """Separa argumentos de Windows (comillas dobles), sin interpretar barras."""
    out, cur, quoted = [], "", False
    for ch in arguments.replace("\0", ""):
        if ch == '"':
            quoted = not quoted
        elif ch.isspace() and not quoted:
            if cur:
                out.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def candidate_from_lnk(prefix: Path, lnk_path: Path) -> Candidate | None:
    try:
        sc = lnk.parse(lnk_path)
    except Exception:  # noqa: BLE001 - .lnk raros: los ignoramos
        return None
    if not sc.target:
        return None
    target = pfx.windows_to_unix(prefix, sc.target)
    if target is None or target.suffix.lower() != ".exe" or not target.is_file():
        return None
    name = lnk_path.stem
    if JUNK.search(name) or JUNK.search(target.stem) or target.name.lower() in JUNK_EXE_EXACT:
        return None
    workdir = pfx.windows_to_unix(prefix, sc.working_dir) if sc.working_dir else None
    icon_src = None
    if sc.icon_location:
        icon_src = pfx.windows_to_unix(prefix, sc.icon_location)
        if icon_src is not None and not icon_src.is_file():
            icon_src = None
    return Candidate(
        name=name, exe=target, args=_split_args(sc.arguments or ""),
        workdir=workdir if workdir and workdir.is_dir() else None,
        icon_source=icon_src or target, icon_index=sc.icon_index if icon_src else 0,
        from_lnk=True,
    )


def new_from_lnks(prefix: Path, before: Snapshot, after: Snapshot) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    # Primero el menú Inicio, luego el escritorio (mismo destino = duplicado)
    def order(p: str) -> int:
        return 0 if "start menu" in p.lower() else 1
    for path in sorted(after.lnks, key=order):
        if path in before.lnks and before.lnks[path] == after.lnks[path]:
            continue
        cand = candidate_from_lnk(prefix, Path(path))
        if cand is None:
            continue
        key = (str(cand.exe).lower(), tuple(cand.args))
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out


def new_exes(prefix: Path, before: Snapshot, after: Snapshot) -> list[Candidate]:
    out = []
    for path in sorted(after.exes - before.exes):
        p = Path(path)
        if p.name.lower() in JUNK_EXE_EXACT or JUNK.search(p.stem):
            continue
        out.append(Candidate(name=p.stem, exe=p, icon_source=p))
    # Los más grandes primero: suele ser el programa principal
    out.sort(key=lambda c: c.exe.stat().st_size if c.exe.exists() else 0, reverse=True)
    return out


def all_existing_lnks(prefix: Path) -> list[Candidate]:
    """Para `malive shortcuts`: todos los .lnk del prefijo, sin comparar."""
    return new_from_lnks(prefix, Snapshot(), take_snapshot(prefix))


# --------------------------------------------------------------------------
# Iconos
# --------------------------------------------------------------------------

def _png_size(name: str) -> int:
    m = re.search(r"_(\d+)x(\d+)x(\d+)\.png$", name)
    return int(m.group(1)) * 100 + int(m.group(3)) if m else 0


def extract_icon(source: Path, dest_base: Path, index: int = 0) -> str | None:
    """Extrae el icono más grande. Necesita icoutils (wrestool/icotool).
    Devuelve la ruta del PNG (o .ico) creado, o None."""
    if not source or not source.is_file():
        return None
    dest_base.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        icos: list[Path] = []
        if source.suffix.lower() == ".ico":
            icos = [source]
        elif shutil.which("wrestool"):
            subprocess.run(["wrestool", "-x", "-t", "14", "-o", tmp, str(source)],
                           capture_output=True, timeout=60)
            icos = sorted(tmpd.glob("*.ico"))
            if icos and 0 <= index < len(icos):
                icos = [icos[index]] + [i for i in icos if i != icos[index]]
        if not icos:
            return None
        ico = icos[0]
        if shutil.which("icotool"):
            out = tmpd / "png"
            out.mkdir()
            subprocess.run(["icotool", "-x", "-o", str(out), str(ico)], capture_output=True, timeout=60)
            pngs = sorted(out.glob("*.png"), key=lambda p: _png_size(p.name), reverse=True)
            if pngs:
                dest = dest_base.with_suffix(".png")
                shutil.copyfile(pngs[0], dest)
                return str(dest)
        dest = dest_base.with_suffix(".ico")
        shutil.copyfile(ico, dest)
        return str(dest)


# --------------------------------------------------------------------------
# .desktop
# --------------------------------------------------------------------------

def _desktop_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", " ").replace("\t", " ").replace("\r", "")


def exec_quote(arg: str) -> str:
    """Cita un argumento para Exec= según la especificación de .desktop."""
    if not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def malive_run_path() -> str:
    """Ruta absoluta de malive-run para Exec= (los .desktop no deben depender del PATH)."""
    env = os.environ.get("MALIVE_RUN_BIN")
    if env:
        return env
    sibling = Path(sys.argv[0]).resolve().parent / "malive-run" if sys.argv and sys.argv[0] else None
    if sibling and Path(sys.argv[0]).name in ("malive", "malive-run") and sibling.is_file():
        return str(sibling)
    found = shutil.which("malive-run")
    if found:
        return found
    return str(Path.home() / ".local/bin/malive-run")


def write_desktop(app: App, entry: Entry) -> Path:
    apps_dir = paths.applications_dir()
    apps_dir.mkdir(parents=True, exist_ok=True)
    path = apps_dir / f"malive-{app.id}-{entry.id}.desktop"
    wmclass = Path(entry.exe).name.lower()
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={_desktop_escape(entry.name)}",
        f"Comment={_desktop_escape(f'{app.name} (Windows, vía MaLiVe)')}",
        # El valor de Exec lleva su propio nivel de escape (\\ -> \) además de las comillas
        f"Exec={_desktop_escape(exec_quote(malive_run_path()))} --app {app.id} --entry {entry.id}",
        f"Icon={entry.icon or 'application-x-ms-dos-executable'}",
        "Terminal=false",
        "Categories=Utility;",
        f"StartupWMClass={_desktop_escape(wmclass)}",
        f"X-MaLiVe-App={app.id}",
        # Acción del clic derecho en el menú (Plasma, GNOME…). La opción
        # "Desinstalar y gestionar complementos" de Plasma solo funciona con
        # paquetes del catálogo AppStream, así que ponemos la nuestra.
        "Actions=malive-uninstall;",
        "",
        "[Desktop Action malive-uninstall]",
        f"Name={_desktop_escape(f'Desinstalar {app.name}')}",
        "Icon=edit-delete",
        f"Exec={_desktop_escape(exec_quote(malive_run_path()))} --uninstall {app.id}",
        "",
    ]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.chmod(0o755)
    os.replace(tmp, path)
    return path


def rewrite_all() -> int:
    """Regenera los .desktop de todas las apps (tras actualizar MaLiVe)."""
    n = 0
    for app in config.all_apps():
        changed = False
        for entry in app.entries:
            if entry.desktop and not Path(entry.desktop).exists():
                continue  # el usuario lo quitó a mano: respetarlo
            entry.desktop = str(write_desktop(app, entry))
            changed = True
            n += 1
        if changed:
            app.save()
    if n:
        refresh_menus()
    return n


def refresh_menus() -> None:
    """Avisa al escritorio de que han cambiado los accesos.

    Se espera a que termine: lanzado en segundo plano, Plasma puede matar el
    proceso al cerrarse malive-run (cada lanzamiento va en su propia unidad
    de systemd) y el menú se queda con entradas borradas.
    """
    apps_dir = paths.applications_dir()
    done = []
    for cmd in (["update-desktop-database", str(apps_dir)], ["kbuildsycoca6"], ["kbuildsycoca5"]):
        exe = shutil.which(cmd[0])
        if not exe:
            done.append(f"{cmd[0]}: no está")
            continue
        try:
            r = subprocess.run([exe, *cmd[1:]], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, timeout=60)
            done.append(f"{cmd[0]}: {r.returncode}")
            if r.returncode != 0:
                log.warning("%s terminó con código %s: %s", cmd[0], r.returncode, r.stderr.strip()[-300:])
        except (OSError, subprocess.TimeoutExpired) as e:
            done.append(f"{cmd[0]}: {e}")
            log.warning("no se pudo ejecutar %s: %s", cmd[0], e)
        if cmd[0] == "kbuildsycoca6":
            break  # con la de Plasma 6 no hace falta la de Plasma 5
    log.info("menú refrescado (%s)", ", ".join(done))


def register(app: App, candidates: list[Candidate]) -> list[Entry]:
    """Añade entradas a la app (sin duplicar el mismo exe+args) y crea sus .desktop."""
    created: list[Entry] = []
    existing = {(e.exe.lower(), tuple(e.args)) for e in app.entries}
    used_ids = {e.id for e in app.entries}
    for cand in candidates:
        key = (str(cand.exe).lower(), tuple(cand.args))
        if key in existing:
            continue
        base = config.slugify(cand.name)
        eid, n = base, 2
        while eid in used_ids:
            eid, n = f"{base}-{n}", n + 1
        used_ids.add(eid)
        existing.add(key)
        icon = extract_icon(cand.icon_source or cand.exe, paths.icons_dir() / f"{app.id}-{eid}", cand.icon_index)
        entry = Entry(id=eid, name=cand.name, exe=str(cand.exe), args=cand.args,
                      workdir=str(cand.workdir) if cand.workdir else None, icon=icon)
        entry.desktop = str(write_desktop(app, entry))
        app.entries.append(entry)
        created.append(entry)
    if created:
        app.save()
        refresh_menus()
    return created


def remove_entries(app: App) -> None:
    for e in app.entries:
        for f in (e.desktop, e.icon):
            if f and f.startswith(str(paths.data_home())) and Path(f).exists():
                Path(f).unlink()
    # Por si quedó algún .desktop huérfano de esta app
    for f in paths.applications_dir().glob(f"malive-{app.id}-*.desktop"):
        f.unlink(missing_ok=True)
    app.entries = []
    refresh_menus()
