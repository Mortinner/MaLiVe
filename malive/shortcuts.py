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

from . import assoc, config, lnk, paths, prefix as pfx
from .session import desktop_shell_env
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
    for full in _program_dir_lnks(prefix):
        try:
            snap.lnks[full] = os.lstat(full).st_mtime
        except OSError:
            pass
    return snap


_SYSTEM_TOP = {"windows", "users", "programdata", "program files", "program files (x86)"}


def _program_dir_lnks(prefix: Path) -> list[str]:
    """Accesos que algunos instaladores dejan en la carpeta del programa en vez
    de en el menú Inicio (el .msi de Armored Warfare: C:\\Games\\Armored Warfare.lnk).
    Solo dos niveles bajo C:\\ y Program Files, para que vigilar sea barato."""
    c = pfx.drive_c(prefix)
    roots = [(c, True), (c / "Program Files", False), (c / "Program Files (x86)", False)]
    out: list[str] = []
    for root, is_c in roots:
        try:
            children = [d for d in root.iterdir() if d.is_dir() and not d.is_symlink()]
        except OSError:
            continue
        for child in children:
            if is_c and child.name.lower() in _SYSTEM_TOP:
                continue
            for pattern in ("*.lnk", "*/*.lnk"):
                out.extend(str(p) for p in child.glob(pattern) if p.is_file())
    return out


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


_TEXT_SUFFIXES = {".log", ".bak", ".json", ".ini", ".cfg", ".conf", ".xml", ".txt", ".yaml", ".yml",
                  ".ldb"}  # .ldb/.log: ajustes de lanzadores Chromium/Electron (Local Storage/leveldb)


def registry_icon_hints(prefix: Path) -> tuple[list[str], list[str]]:
    """Lo estándar de Windows: en «Desinstalar programas» (…\\CurrentVersion\\Uninstall)
    cada instalador anota DisplayIcon (el icono) e InstallLocation (su carpeta)."""
    icons: list[str] = []
    dirs: list[str] = []
    for reg in (prefix / "system.reg", prefix / "user.reg"):
        try:
            text = reg.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        in_uninstall = False
        for line in text.splitlines():
            if line.startswith("["):
                in_uninstall = "\\\\uninstall\\\\" in line.lower()
                continue
            if not in_uninstall:
                continue
            m = re.match(r'"(DisplayIcon|InstallLocation)"="(.*)"\s*$', line)
            if not m:
                continue
            value = m.group(2).replace("\\\\", "\\").strip().strip('"')
            if m.group(1) == "DisplayIcon":
                icons.append(re.sub(r",\s*-?\d+$", "", value).strip('"'))
            elif value:
                dirs.append(value)
    return icons, dirs


_WIN_PATH = re.compile(r'([A-Za-z]:\\{1,2}[^"\'\r\n<>|*?\x00-\x1f]{1,260})')


def icons_from_app_data(prefix: Path, max_files: int = 60) -> list[Path]:
    """Iconos de las carpetas que el propio programa anota en su AppData.

    Muchos lanzadores instalan el juego donde dice el usuario (M:\\Games\\…) y
    dejan allí el icono (GameIcon.ico), mientras su .exe no trae ninguno; la
    ruta la escriben en sus registros o su configuración. Se leen esos ficheros
    de texto (los más recientes, con tope de tamaño), se sacan las rutas de
    Windows, se traducen a Linux y se buscan .ico en ellas."""
    appdata = pfx.drive_c(prefix) / "users" / "steamuser" / "AppData"
    files: list[Path] = []
    for root in (appdata / "Local", appdata / "Roaming"):
        if not root.is_dir():
            continue
        for dirpath, dirs, names in os.walk(root):
            if dirpath[len(str(root)):].count(os.sep) >= 4:
                dirs[:] = []
            dirs[:] = [d for d in dirs if d.lower() not in ("cache", "code cache", "gpucache", "microsoft", "temp",
                                                            "indexeddb", "service worker", "blob_storage")]
            for n in names:
                fp = Path(dirpath) / n
                if fp.suffix.lower() in _TEXT_SUFFIXES or ".log" in n.lower():
                    try:
                        if fp.stat().st_size < 8 * 1024 * 1024:
                            files.append(fp)
                    except OSError:
                        pass
    files.sort(key=lambda f: f.stat().st_mtime if f.exists() else 0, reverse=True)
    c = pfx.drive_c(prefix).resolve()
    found_icos: dict[str, Path] = {}
    seen_dirs: set[str] = set()
    reg_icons, reg_dirs = registry_icon_hints(prefix)
    for win in reg_icons:
        unix = pfx.windows_to_unix(prefix, win)
        if unix is not None and unix.suffix.lower() in (".ico", ".exe") and unix.is_file():
            found_icos.setdefault(str(unix), unix)
    for win in reg_dirs:
        unix = pfx.windows_to_unix(prefix, win)
        if unix is not None and unix.is_dir():
            seen_dirs.add(str(unix))
            for ico in unix.glob("*.ico"):
                found_icos.setdefault(str(ico), ico)
    for f in files[:max_files]:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _WIN_PATH.finditer(text):
            win = m.group(1).replace("\\\\", "\\").rstrip(" .,;:'\\")
            unix = pfx.windows_to_unix(prefix, win)
            if unix is None:
                continue
            if unix.suffix.lower() == ".ico" and unix.is_file():
                found_icos.setdefault(str(unix), unix)
                continue
            key = str(unix)
            if key in seen_dirs or len(seen_dirs) > 300:
                continue
            seen_dirs.add(key)
            try:
                real = unix.resolve()
                if c in real.parents and any(p.name.lower() in ("windows", "users") for p in real.relative_to(c).parents if p.name):
                    continue
                if unix.is_dir():
                    for ico in unix.glob("*.ico"):
                        found_icos.setdefault(str(ico), ico)
            except (OSError, ValueError):
                continue
    def score(p: Path) -> tuple:
        n = p.name.lower()
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        return ("game" in n or "icon" in n or "app" in n, size)
    return sorted(found_icos.values(), key=score, reverse=True)


def find_icon(cand: "Candidate", dest_base: Path, prefix: Path | None = None) -> str | None:
    """El icono del acceso; si no se puede sacar (p. ej. los .msi apuntan a un
    fichero en C:\\windows\\Installer que a veces no está o no trae iconos),
    el del propio .exe y, si no, un .ico de su carpeta."""
    tries = []
    if cand.icon_source and cand.icon_source != cand.exe:
        tries.append((cand.icon_source, cand.icon_index))
    tries.append((cand.exe, cand.icon_index if cand.icon_source in (None, cand.exe) else 0))
    for source, index in tries:
        icon = extract_icon(source, dest_base, index)
        if icon:
            return icon
    try:
        icos = sorted(cand.exe.parent.glob("*.ico"))
    except OSError:
        icos = []
    if icos:
        icon = extract_icon(icos[0], dest_base)
        if icon:
            return icon
    if prefix is not None:
        for ico in icons_from_app_data(prefix)[:5]:
            icon = extract_icon(ico, dest_base)
            if icon:
                return icon
    return None


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


# Ficheros y carpetas que delatan un juego (motores, SDK de juegos, antitrampas)
_GAME_FILES = {
    "unityplayer.dll", "gameassembly.dll", "steam_api.dll", "steam_api64.dll", "crysystem.dll",
    "cryrenderd3d11.dll", "binkw32.dll", "binkw64.dll", "bink2w64.dll", "fmod.dll", "fmod64.dll",
    "fmodex64.dll", "aksoundengine.dll", "physx3_x64.dll", "physx_64.dll", "galaxy64.dll",
    "eossdk-win64-shipping.dll", "nvngx_dlss.dll", "amd_fidelityfx_dx12.dll", "xinput1_3.dll",
}
_GAME_DIRS = {"easyanticheat", "battleye", "engine", "binaries", "paks"}


# Tiendas y lanzadores de juegos: por dentro son programas normales (a menudo
# Chromium), sin pistas de juego. Relación corta que se mantiene una vez.
GAME_LAUNCHER_EXES = {
    "battle.net.exe", "battle.net launcher.exe", "steam.exe", "epicgameslauncher.exe",
    "eadesktop.exe", "ealauncher.exe", "origin.exe", "ubisoftconnect.exe", "upc.exe", "uplay.exe",
    "galaxyclient.exe", "riotclientservices.exe", "riotclientux.exe", "rockstarlauncher.exe",
    "wgc.exe", "gamecenter.exe", "itch.exe", "amazon games.exe", "amazongames.exe",
    "bethesdanetlauncher.exe", "xboxpcapp.exe", "playnite.desktopapp.exe", "playnite.fullscreenapp.exe",
    "legacygames.exe", "glyphclientapp.exe",
}
GAME_LAUNCHER_DIRS = ("battle.net", "ea games", "electronic arts", "ubisoft", "ubisoft game launcher",
                      "rockstar games", "riot games", "gog galaxy", "gog games", "wargaming.net",
                      "my.games", "epic games", "steam", "steamapps", "itch", "amazon games",
                      "games", "bethesda.net launcher", "playnite")


def looks_like_game(exe: Path) -> bool:
    """Heurística barata (sin recorrer el disco entero): lanzadores conocidos,
    la ruta, el nombre del .exe o lo que hay junto a él."""
    if exe.name.lower() in GAME_LAUNCHER_EXES:
        return True
    low = str(exe).lower().replace("\\", "/")
    parts = set(low.split("/"))
    if parts & set(GAME_LAUNCHER_DIRS) or low.endswith("-shipping.exe"):
        return True
    for d in (exe.parent, exe.parent.parent):
        try:
            names = {p.name.lower() for p in d.iterdir()}
        except OSError:
            continue
        if names & _GAME_FILES or names & _GAME_DIRS:
            return True
    return False


# Secciones que el usuario puede elegir con `malive category` (freedesktop)
USER_CATEGORIES = {
    "game": "Game;", "games": "Game;", "juegos": "Game;",
    "multimedia": "AudioVideo;", "audiovideo": "AudioVideo;", "video": "AudioVideo;Video;", "audio": "AudioVideo;Audio;",
    "graphics": "Graphics;", "graficos": "Graphics;", "gráficos": "Graphics;",
    "office": "Office;", "oficina": "Office;", "development": "Development;", "desarrollo": "Development;",
    "network": "Network;", "internet": "Network;", "education": "Education;", "educacion": "Education;",
    "educación": "Education;", "system": "System;", "sistema": "System;",
    "utility": "Utility;", "utilities": "Utility;", "utilidades": "Utility;",
}


def _categories(entry: Entry, mimes: list[str], app: App | None = None) -> str:
    if app is not None and app.category:
        return app.category
    cats = assoc.categories_for(mimes)
    if cats == "Utility;" and looks_like_game(Path(entry.exe)):
        return "Game;"
    return cats


def desktop_text(app: App, entry: Entry) -> str:
    wmclass = Path(entry.exe).name.lower()
    exe_win = pfx.unix_to_windows(app.prefix_path, Path(entry.exe))
    mimes = assoc.mimetypes_for(app.prefix_path, exe_win)
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={_desktop_escape(entry.name)}",
        f"Comment={_desktop_escape(f'{app.name} (Windows, via MaLiVe)')}",
        f"Comment[es]={_desktop_escape(f'{app.name} (Windows, vía MaLiVe)')}",
        # El valor de Exec lleva su propio nivel de escape (\\ -> \) además de las comillas
        # %F: el programa recibe los ficheros de «Abrir con» o arrastrados al icono
        f"Exec={_desktop_escape(exec_quote(malive_run_path()))} --app {app.id} --entry {entry.id} %F",
        f"Icon={entry.icon or 'application-x-ms-dos-executable'}",
        "Terminal=false",
        f"Categories={_categories(entry, mimes, app)}",
        f"StartupWMClass={_desktop_escape(wmclass)}",
        *([f"MimeType={';'.join(mimes)};"] if mimes else []),
        f"X-MaLiVe-App={app.id}",
        # Acción del clic derecho en el menú (Plasma, GNOME…). La opción
        # "Desinstalar y gestionar complementos" de Plasma solo funciona con
        # paquetes del catálogo AppStream, así que ponemos la nuestra.
        "Actions=malive-uninstall;",
        "",
        "[Desktop Action malive-uninstall]",
        f"Name={_desktop_escape(f'Uninstall {app.name}')}",
        f"Name[es]={_desktop_escape(f'Desinstalar {app.name}')}",
        "Icon=edit-delete",
        f"Exec={_desktop_escape(exec_quote(malive_run_path()))} --uninstall {app.id}",
        "",
    ]
    return "\n".join(lines)


def write_desktop(app: App, entry: Entry) -> Path:
    apps_dir = paths.applications_dir()
    apps_dir.mkdir(parents=True, exist_ok=True)
    path = apps_dir / f"malive-{app.id}-{entry.id}.desktop"
    text = desktop_text(app, entry)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o755)
    os.replace(tmp, path)
    return path


def app_icons_dir(app: App) -> Path:
    """Los iconos van dentro del contenedor (C:\\icons, idea de Marcos): al
    desinstalar se borran con él y el contenedor queda autosuficiente."""
    return pfx.drive_c(app.prefix_path) / "icons"


def _icon_base(app: App, entry_id: str) -> Path:
    return app_icons_dir(app) / entry_id


def migrate_icon(app: App, entry: Entry) -> bool:
    """Iconos de versiones anteriores (~/.local/share/malive/icons) → C:\\icons."""
    if not entry.icon:
        return False
    old = Path(entry.icon)
    legacy = paths.icons_dir()
    if legacy not in old.parents or not old.is_file():
        return False
    dest = _icon_base(app, entry.id).with_suffix(old.suffix)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), dest)
    except OSError:
        return False
    entry.icon = str(dest)
    return True


def retry_icon(app: App, entry: Entry) -> bool:
    """Si la entrada no tiene icono, lo busca otra vez (el programa puede haberlo
    dejado después: lanzadores que descargan el juego). Devuelve si cambió."""
    if migrate_icon(app, entry):
        return True
    if entry.icon and Path(entry.icon).exists():
        return False
    exe = Path(entry.exe)
    if not exe.is_file():
        return False
    icon = find_icon(Candidate(name=entry.name, exe=exe, icon_source=exe),
                     _icon_base(app, entry.id), app.prefix_path)
    if icon:
        entry.icon = icon
        log.info("icono de %s/%s: %s", app.id, entry.id, icon)
        return True
    return False


def rewrite_app(app: App) -> bool:
    """Reescribe los .desktop de una app si su contenido ha cambiado (p. ej.
    asociaciones que el instalador registró al final). Devuelve si cambió algo."""
    changed = False
    icons_fixed = False
    for entry in app.entries:
        if not entry.desktop or not Path(entry.desktop).exists():
            continue
        if retry_icon(app, entry):
            icons_fixed = True
        new = desktop_text(app, entry)
        try:
            old = Path(entry.desktop).read_text(encoding="utf-8")
        except OSError:
            old = ""
        if new != old:
            write_desktop(app, entry)
            changed = True
    if icons_fixed:
        app.save()
    return changed


def rewrite_all() -> int:
    """Regenera los .desktop de todas las apps (tras actualizar MaLiVe)."""
    n = 0
    for app in config.all_apps():
        changed = False
        for entry in app.entries:
            if entry.desktop and not Path(entry.desktop).exists():
                continue  # el usuario lo quitó a mano: respetarlo
            retry_icon(app, entry)
            entry.desktop = str(write_desktop(app, entry))
            changed = True
            n += 1
        if changed:
            app.save()
    if n:
        refresh_menus()
    return n


# Variables de las que depende QUÉ base de datos de programas (ksycoca)
# reconstruye kbuildsycoca6: el idioma y las carpetas del sistema. Su nombre es
# ksycoca6_<idioma>_<hash de XDG_DATA_DIRS>.
# Variables que, si faltan, hacen que kbuildsycoca6 construya OTRO menú:
# el idioma (nombre del fichero ksycoca6_<idioma>_…), las carpetas XDG
# (hash del nombre) y XDG_MENU_PREFIX (qué fichero de menú se usa:
# plasma-applications.menu en Plasma; sin ella, el genérico applications.menu,
# y las apps nuevas no salen en las categorías ni en «Todas las aplicaciones»).
DIAG_VARS = ("LANG", "LANGUAGE", "XDG_MENU_PREFIX", "XDG_CURRENT_DESKTOP")

# Reconstrucciones extra tras la inmediata (segundos). Comprobado en CachyOS +
# Plasma 6: crear un .desktop y reconstruir en el mismo instante no hace que
# el menú se redibuje, ni siquiera desde una terminal; repetirlo unos segundos
# después, sí.
DELAYED_REBUILDS = (5, 30)
# Lo que necesita kbuildsycoca6 para construir el mismo menú que usa el
# escritorio; se pasa a las reconstrucciones diferidas.
REBUILD_ENV_VARS = ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "XDG_MENU_PREFIX", "XDG_CURRENT_DESKTOP",
                    "XDG_DATA_DIRS", "XDG_CONFIG_DIRS", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                    "HOME", "PATH")


def schedule_delayed_rebuilds(exe: str, env: dict[str, str], delays=DELAYED_REBUILDS) -> list[str]:
    """Programa kbuildsycoca6 --noincremental dentro de unos segundos.

    Con `systemd-run --user --on-active`: un temporizador de systemd propio,
    que sigue vivo aunque malive-run termine (lanzado desde el menú o desde
    Dolphin, todo lo que deja en segundo plano muere con él).
    """
    runner = shutil.which("systemd-run")
    done = []
    for secs in delays:
        if runner:
            cmd = [runner, "--user", "--quiet", "--collect", f"--on-active={secs}s",
                   "--timer-property=AccuracySec=1s"]
            cmd += [f"--setenv={v}={env[v]}" for v in REBUILD_ENV_VARS if v in env]
            cmd += [exe, "--noincremental"]
        else:  # sin systemd: mejor esfuerzo
            cmd = ["sh", "-c", f'sleep {secs}; exec "$0" --noincremental', exe]
        try:
            if runner:
                r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                   timeout=15, env=env)
                done.append(f"+{secs}s: {r.returncode}")
                if r.returncode != 0:
                    log.warning("no se pudo programar la reconstrucción del menú: %s", r.stderr.strip()[-300:])
            else:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env=env, start_new_session=True)
                done.append(f"+{secs}s: sh")
        except (OSError, subprocess.TimeoutExpired) as e:
            done.append(f"+{secs}s: {e}")
    return done


def _menu_rebuild_env() -> dict[str, str]:
    """El entorno COMPLETO del escritorio en marcha (Plasma o GNOME Shell).

    MaLiVe puede arrancar desde el menú, desde Dolphin (como servicio de
    systemd) o desde el temporizador, con un entorno distinto al de la sesión.
    Copiar variables sueltas falló dos veces (primero LANG, luego
    XDG_MENU_PREFIX), así que se usa el entorno entero del propio Plasma: es
    lo mismo que ejecutar kbuildsycoca6 desde una terminal de la sesión.
    """
    shell = desktop_shell_env()
    if not shell:
        return dict(os.environ)
    env = dict(shell)
    for var in ("PATH", "HOME", "USER"):  # imprescindibles si faltaran
        if var not in env and var in os.environ:
            env[var] = os.environ[var]
    return env


def refresh_menus() -> None:
    """Avisa al escritorio de que han cambiado los accesos.

    Se espera a que termine: lanzado en segundo plano, Plasma puede matar el
    proceso al cerrarse malive-run (cada lanzamiento va en su propia unidad
    de systemd).
    """
    apps_dir = paths.applications_dir()
    env = _menu_rebuild_env()
    done = []
    # --noincremental: con la reconstrucción incremental, Plasma actualiza su
    # base de datos (el buscador encuentra el programa) pero la lista de
    # «Todas las aplicaciones» del menú no se redibuja. Comprobado en
    # CachyOS + Plasma 6. La reconstrucción completa tarda uno o dos segundos.
    for cmd in (["update-desktop-database", str(apps_dir)],
                ["kbuildsycoca6", "--noincremental"], ["kbuildsycoca5", "--noincremental"]):
        exe = shutil.which(cmd[0])
        if not exe:
            done.append(f"{cmd[0]}: no está")
            continue
        try:
            r = subprocess.run([exe, *cmd[1:]], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               text=True, timeout=60, env=env)
            done.append(f"{cmd[0]}: {r.returncode}")
            if r.returncode != 0:
                log.warning("%s terminó con código %s: %s", cmd[0], r.returncode, r.stderr.strip()[-300:])
        except (OSError, subprocess.TimeoutExpired) as e:
            done.append(f"{cmd[0]}: {e}")
            log.warning("no se pudo ejecutar %s: %s", cmd[0], e)
        if cmd[0] in ("kbuildsycoca6", "kbuildsycoca5"):
            done += schedule_delayed_rebuilds(exe, env)
        if cmd[0] == "kbuildsycoca6":
            break  # con la de Plasma 6 no hace falta la de Plasma 5
    used = ", ".join(f"{v}={env.get(v, '-')}" for v in DIAG_VARS)
    own = ", ".join(f"{v}={os.environ.get(v, '-')}" for v in DIAG_VARS)
    log.info("menú refrescado (%s) con [%s]; entorno propio [%s]", ", ".join(done), used, own)


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
        icon = find_icon(cand, _icon_base(app, eid), app.prefix_path)
        entry = Entry(id=eid, name=cand.name, exe=str(cand.exe), args=cand.args,
                      workdir=str(cand.workdir) if cand.workdir else None, icon=icon)
        entry.desktop = str(write_desktop(app, entry))
        app.entries.append(entry)
        created.append(entry)
    if created:
        app.save()
        refresh_menus()
    return created


def remove_entry(app: App, entry: Entry) -> None:
    """Quita una sola entrada del menú (y su icono)."""
    for f in (entry.desktop, entry.icon):
        if f and Path(f).exists() and (f.startswith(str(paths.data_home())) or f.startswith(str(app.prefix_path))):
            Path(f).unlink()
    app.entries = [e for e in app.entries if e.id != entry.id]
    refresh_menus()


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
