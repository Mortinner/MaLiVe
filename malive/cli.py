"""CLI `malive`: integración, listado, mantenimiento y diagnóstico."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

from . import __version__, config, integrate, paths, prefix as pfx, proton, runner, shortcuts, updater
from .i18n import T


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _yes(answer: str) -> bool:
    return answer.strip().lower() in ("s", "si", "sí", "y", "yes")


def _get_app(app_id: str) -> config.App:
    app = config.load_app(app_id)
    if app is None:
        sys.exit(T(f"No existe la app «{app_id}». Usa «malive list».",
                   f"There is no app «{app_id}». Use «malive list»."))
    return app


def cmd_integrate(a) -> int:
    path = integrate.install_handler()
    print(T(f"Asociación creada: {path}", f"Association created: {path}"))
    print(T("Los .exe, .msi y .lnk se abrirán con MaLiVe al hacer doble clic.",
            ".exe, .msi and .lnk files will open with MaLiVe on double-click."))
    cfg = config.load_global()
    if not a.no_timer and (cfg.get("auto_update", True) or cfg.get("proton_auto_update", True)):
        ok = integrate.install_timer()
        print(T("Actualizaciones automáticas (MaLiVe y GE-Proton): ", "Automatic updates (MaLiVe and GE-Proton): ")
              + (T("activadas", "enabled") if ok
                 else T("no se pudieron activar (systemctl --user)", "could not be enabled (systemctl --user)")))
    return 0


def cmd_unintegrate(a) -> int:
    integrate.remove_handler()
    integrate.remove_timer()
    print(T("Asociaciones y temporizador eliminados. Las apps instaladas se conservan.",
            "Associations and timer removed. Installed apps are kept."))
    return 0


def cmd_list(a) -> int:
    apps = config.all_apps()
    if not apps:
        print(T("No hay apps. Haz doble clic en un instalador .exe o .msi para empezar.",
                "No apps yet. Double-click a .exe or .msi installer to get started."))
        return 0
    cfg = config.load_global()
    for app in apps:
        iso = T("aislada", "isolated") if app.effective_isolate(cfg) else T("sin aislar", "not isolated")
        size = f", {_human(pfx.disk_usage(app.prefix_path))}" if a.size else ""
        print(f"{app.id}  —  {app.name}  [{app.kind}, {app.proton}, {iso}{size}]")
        for e in app.entries:
            print(f"    · {e.name}: {e.exe}")
    return 0


def cmd_remove(a) -> int:
    app = _get_app(a.app)
    if pfx.is_busy(app.prefix_path):
        sys.exit(T(f"«{app.name}» está en ejecución. Ciérrala antes de eliminarla.",
                   f"«{app.name}» is running. Close it before removing it."))
    if not a.yes:
        ans = input(T(f"¿Eliminar «{app.name}» y todo su entorno ({app.prefix})? [s/N] ",
                      f"Remove «{app.name}» and its whole environment ({app.prefix})? [y/N] "))
        if not _yes(ans):
            print(T("Cancelado.", "Cancelled."))
            return 1
    runner.discard_app(app)
    print(T(f"«{app.name}» eliminada.", f"«{app.name}» removed."))
    return 0


def cmd_shortcuts(a) -> int:
    app = _get_app(a.app)
    cands = shortcuts.all_existing_lnks(app.prefix_path)
    if a.all:
        empty = shortcuts.Snapshot()
        cands += shortcuts.new_exes(app.prefix_path, empty, shortcuts.take_snapshot(app.prefix_path))
        cands = [c for c in cands if "/drive_c/windows/" not in str(c.exe).lower()]
        if sys.stdin.isatty() and cands:
            for i, c in enumerate(cands):
                print(f"  [{i}] {c.name}: {c.exe}")
            sel = input(T("Números a añadir (separados por espacios, vacío = ninguno): ",
                          "Numbers to add (separated by spaces, empty = none): ")).split()
            cands = [cands[int(i)] for i in sel if i.isdigit() and int(i) < len(cands)]
    created = shortcuts.register(app, cands)
    for e in created:
        print(T(f"Creado: {e.name} -> {e.desktop}", f"Created: {e.name} -> {e.desktop}"))
    if not created:
        print(T("No hay accesos nuevos que crear.", "No new menu entries to create."))
    return 0


def cmd_proton(a) -> int:
    if a.action == "list":
        used = proton.in_use()
        names = proton.installed()
        if not names:
            print(T("No hay ningún GE-Proton descargado todavía.", "No GE-Proton downloaded yet."))
        for n in names:
            print(n + (T("  (en uso)", "  (in use)") if n in used else ""))
        return 0
    if a.action == "update":
        def progress(done, total):
            if not a.quiet and total:
                print(f"\r  {done * 100 // total:3d}%  {_human(done)} / {_human(total)}", end="", flush=True)
        try:
            name, new = proton.update(progress)
        except proton.ProtonError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        if not a.quiet:
            print()
            print(T(f"{name} {'descargado' if new else 'ya estaba instalado'}. Se usará en las apps nuevas.",
                    f"{name} {'downloaded' if new else 'was already installed'}. It will be used for new apps."))
        removed = proton.cleanup(config.load_global().get("proton_keep_unused", 1))
        if removed and not a.quiet:
            print(T("Eliminadas versiones sin uso: ", "Removed unused versions: ") + ", ".join(removed))
        return 0
    if a.action == "use":
        if not a.app or not a.version:
            sys.exit(T("Uso: malive proton use APP VERSIÓN", "Usage: malive proton use APP VERSION"))
        app = _get_app(a.app)
        if not proton.is_valid(proton.path_of(a.version)):
            versions = ", ".join(proton.installed()) or T("ninguna", "none")
            sys.exit(T(f"{a.version} no está descargado. Versiones: {versions}",
                       f"{a.version} is not downloaded. Versions: {versions}"))
        print(T(f"Aviso: el prefijo de «{app.name}» se actualizará a {a.version} en el próximo arranque "
                "y no se puede volver atrás sin reinstalar.",
                f"Note: the prefix of «{app.name}» will be upgraded to {a.version} on its next start "
                "and cannot go back without reinstalling."))
        app.proton = a.version
        app.save()
        print(T(f"«{app.name}» usará {a.version}.", f"«{app.name}» will use {a.version}."))
        return 0
    return 2


def cmd_set(a) -> int:
    if a.key == "nvidia":
        cfg = config.load_global()
        cfg["nvidia_mode"] = a.value
        config.save_global(cfg)
        print(T(f"Soporte NVIDIA (CUDA, NVENC) para todos los programas: {a.value}",
                f"NVIDIA support (CUDA, NVENC) for every program: {a.value}"))
        return 0
    if a.value == "auto":
        sys.exit(T("«auto» solo vale para nvidia; usa on u off.", "«auto» is only valid for nvidia; use on or off."))
    value = {"on": True, "off": False}[a.value]
    if a.key == "host-drives":
        cfg = config.load_global()
        cfg["host_drives"] = value
        config.save_global(cfg)
        print(T(f"Discos de datos y carpetas de red como unidades de Windows: {a.value}",
                f"Data disks and network folders as Windows drives: {a.value}"))
        return 0
    if a.key == "auto-update":
        cfg = config.load_global()
        cfg["auto_update"] = value
        config.save_global(cfg)
        print(T(f"Actualizaciones automáticas de MaLiVe: {a.value}", f"MaLiVe automatic updates: {a.value}"))
        return 0
    if a.app:
        app = _get_app(a.app)
        app.isolate = value
        app.save()
        print(T(f"Aislamiento de «{app.name}»: {a.value}", f"Isolation of «{app.name}»: {a.value}"))
    else:
        cfg = config.load_global()
        cfg["isolate"] = value
        config.save_global(cfg)
        print(T(f"Aislamiento por defecto: {a.value} (las apps con valor propio no cambian)",
                f"Default isolation: {a.value} (apps with their own setting are not changed)"))
    return 0


def cmd_exec(a) -> int:
    """Ejecuta una herramienta en el prefijo de una app (winecfg, regedit, cmd, un .exe...)."""
    app = _get_app(a.app)
    cfg = config.load_global()
    runner._ensure_ready(app, cfg)
    pfx.apply_drives(app, cfg)
    target = a.program
    argv = [target, *a.args]
    p = Path(target).expanduser()
    if p.is_file():
        argv[0] = str(p.resolve())
    rc, _ = pfx.run_umu(app, cfg, argv, cwd=app.prefix_path)
    return rc


def _kv_command(a, field: str, label_es: str, label_en: str, valid=None) -> int:
    """malive dll|env APP [CLAVE=VALOR …] [--remove CLAVE …]: sin nada, lista."""
    app = _get_app(a.app)
    store: dict[str, str] = getattr(app, field)
    for item in a.items:
        if "=" not in item:
            sys.exit(T(f"Formato: CLAVE=VALOR (recibido «{item}»)", f"Format: KEY=VALUE (got «{item}»)"))
        key, value = item.split("=", 1)
        key = key.strip()
        if valid:
            valid(key, value)
        store[key] = value
    for key in a.remove or []:
        store.pop(key, None)
    if a.items or a.remove:
        app.save()
    from . import quirks
    kind = "dll" if field == "dll_overrides" else "env"
    known = dict(quirks.for_app(app.id).get(kind, {}))
    for e in app.entries:
        known.update(quirks.for_exe(Path(e.exe).name).get(kind, {}))
    print(T(f"{label_es} de «{app.name}»:", f"{label_en} for «{app.name}»:"))
    for k, v in store.items():
        print(f"  {k}={v}")
    for k, v in known.items():
        if k not in store:
            print(f"  {k}={v}  " + T("(ajuste conocido de MaLiVe)", "(MaLiVe known fix)"))
    if not store and not known:
        print(T("  (ninguno)", "  (none)"))
    return 0


def _check_dll(key: str, value: str) -> None:
    if not re.fullmatch(r"(?:[nb](?:,[nb])?|d|)", value):
        sys.exit(T(f"Modo de DLL no válido «{value}»: usa n, b, n,b, b,n o d (desactivada).",
                   f"Invalid DLL mode «{value}»: use n, b, n,b, b,n or d (disabled)."))


def cmd_dll(a) -> int:
    return _kv_command(a, "dll_overrides", "DLL ajustadas", "DLL overrides", _check_dll)


def cmd_env(a) -> int:
    return _kv_command(a, "env", "Variables de entorno", "Environment variables")


def cmd_nvidia(a) -> int:
    """malive nvidia [APP on|off|default] | malive nvidia update"""
    from . import nvidia
    cfg = config.load_global()
    args = list(a.args)
    if args == ["update"]:
        try:
            d = nvidia.download(_progress_printer())
        except nvidia.NvidiaError as e:
            print(f"\nError: {e}", file=sys.stderr)
            return 1
        print(T(f"\nnvidia-libs listo: {d.name}", f"\nnvidia-libs ready: {d.name}"))
        return 0
    if len(args) == 2:
        app = _get_app(args[0])
        state = args[1].lower()
        if state not in ("on", "off", "default"):
            sys.exit(T("Uso: malive nvidia APP on|off|default", "Usage: malive nvidia APP on|off|default"))
        app.nvidia = None if state == "default" else state == "on"
        app.save()
        if not app.effective_nvidia(cfg):
            pfx.nvidia.unlink_from(app.prefix_path)
        print(T(f"Soporte NVIDIA de «{app.name}»: {state}. Se aplica la próxima vez que lo abras.",
                f"NVIDIA support for «{app.name}»: {state}. It applies the next time you open it."))
        if state == "on" and not nvidia.gpu_present():
            print(T("Aviso: no se detecta una tarjeta NVIDIA con su driver propietario; no tendrá efecto.",
                    "Warning: no NVIDIA card with its proprietary driver was detected; it will have no effect."))
        return 0
    if args:
        sys.exit(T("Uso: malive nvidia [APP on|off|default] | malive nvidia update",
                   "Usage: malive nvidia [APP on|off|default] | malive nvidia update"))
    gpu = nvidia.gpu_present()
    print(T("Tarjeta NVIDIA: ", "NVIDIA card: ") +
          (T(f"sí (driver {nvidia.driver_version() or '?'})", f"yes (driver {nvidia.driver_version() or '?'})")
           if gpu else T("no detectada", "not detected")))
    newest = proton.newest_installed()
    if newest and nvidia.proton_provides(proton.path_of(newest)):
        print(T(f"CUDA/NVENC: integrados en {newest} (PROTON_NVIDIA_LIBS)",
                f"CUDA/NVENC: built into {newest} (PROTON_NVIDIA_LIBS)"))
    libs = nvidia.installed()
    print("nvidia-libs: " + (libs.name if libs else T("sin descargar", "not downloaded")))
    fail = nvidia.last_failure()
    if libs is None and fail:
        print(T(f"  Último intento fallido: {fail.get('error', '?')}", f"  Last failed attempt: {fail.get('error', '?')}"))
        print(T("  (no se reintenta hasta mañana; «malive nvidia update» para probar ya)",
                "  (no retry until tomorrow; «malive nvidia update» to try now)"))
    mode = cfg.get("nvidia_mode", "auto")
    print(T(f"Para todos los programas: {mode}", f"For every program: {mode}"))
    for app in config.all_apps():
        own = "" if app.nvidia is None else T(" (propio)", " (own)")
        print(f"  {app.id}: {'on' if app.effective_nvidia(cfg) else 'off'}{own}")
    return 0


def _progress_printer():
    def progress(done, total):
        if total:
            print(f"\r  {done * 100 // total:3d}%  {_human(done)} / {_human(total)}", end="", flush=True)
    return progress


def cmd_hardware(a) -> int:
    """El equipo y la configuración que MaLiVe aplica a todos los programas."""
    from . import hardware
    hw = hardware.detect()
    for line in hardware.summary(hw):
        print(line)
    env = hardware.profile_env(hw)
    if env and not a.short:
        print(T("Variables comunes: ", "Common variables: ") + " ".join(f"{k}={v}" for k, v in env.items()))
    return 0


def cmd_template(a) -> int:
    """Plantillas de contenedor: build | rebuild | refill | status."""
    from . import templates
    name = a.proton or proton.newest_installed()
    if a.action == "status" or not name:
        rows = templates.status()
        if not rows:
            print(T("No hay plantillas todavía (se crean al instalar MaLiVe o con «malive template build»).",
                    "No templates yet (they are created when MaLiVe is installed or with «malive template build»)."))
        for prot, kind, base, spare in rows:
            print(f"{prot}  {kind}:  " + T("plantilla ", "template ") + ("✓" if base else "✗")
                  + T(", repuesto ", ", spare ") + ("✓" if spare else "✗"))
        return 0
    try:
        if a.action in ("build", "rebuild"):
            print(T(f"Preparando la plantilla {a.kind} para {name} (unos minutos)…",
                    f"Preparing the {a.kind} template for {name} (a few minutes)…"))
            templates.build(name, a.kind, force=a.action == "rebuild")
            print(T("Plantilla lista.", "Template ready."))
        elif a.action == "refill":
            templates.refill(name, a.kind)
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_winetricks(a) -> int:
    app = _get_app(a.app)
    cfg = config.load_global()
    runner._ensure_ready(app, cfg)
    rc, _ = pfx.run_umu(app, cfg, ["winetricks", *a.verbs], cwd=app.prefix_path)
    print(T(f"winetricks terminó con código {rc}. Registro: {pfx.log_path(app)}",
            f"winetricks exited with code {rc}. Log: {pfx.log_path(app)}"))
    return rc


def cmd_setup(a) -> int:
    """Descarga GE-Proton y el runtime de umu y comprueba que Proton arranca,
    creando un prefijo de prueba que luego se borra. Lo usa install.sh para
    que el primer doble clic no tenga que descargar nada."""
    import shutil as _sh

    def progress(done, total):
        if total:
            print(f"\r  {done * 100 // total:3d}%  {_human(done)} / {_human(total)}", end="", flush=True)

    name = proton.newest_installed()
    if name:
        print(T(f"  GE-Proton ya descargado: {name}", f"  GE-Proton already downloaded: {name}"))
    else:
        print(T("  Descargando GE-Proton (~500 MB)…", "  Downloading GE-Proton (~500 MB)…"))
        try:
            name, _ = proton.update(progress)
        except proton.ProtonError as e:
            print(f"\n  Error: {e}", file=sys.stderr)
            return 1
        print(T(f"\n  {name} listo.", f"\n  {name} ready."))

    # Configuración única según el equipo (la misma para todos los programas)
    from . import hardware, nvidia
    hw = hardware.detect()
    for line in hardware.summary(hw):
        print(f"» {line}")
    cfg = config.load_global()
    if (hw.nvidia is not None and cfg.get("nvidia_mode", "auto") != "off" and nvidia.installed() is None
            and not nvidia.proton_provides(proton.path_of(name))):
        print(T("» Descargando el soporte de NVIDIA (CUDA, NVENC)…", "» Downloading NVIDIA support (CUDA, NVENC)…"))
        try:
            nvidia.download(progress)
            print()
        except nvidia.NvidiaError as e:
            # No es grave: se reintenta al abrir el primer programa
            print(f"\n  {T('Aviso', 'Warning')}: {e}", file=sys.stderr)

    print(T("  Preparando el runtime de Steam y comprobando que Proton funciona (unos minutos la primera vez)…",
            "  Preparing the Steam runtime and checking that Proton works (a few minutes the first time)…"))
    test = config.App(id="_setup", name="test", kind="portable",
                      prefix=str(paths.data_dir() / ".setup-test"), proton=name)
    _sh.rmtree(test.prefix_path, ignore_errors=True)
    test.prefix_path.mkdir(parents=True)
    try:
        rc, secs = pfx.run_umu(test, config.load_global(), [""], cwd=test.prefix_path,
                               verb="waitforexitandrun", log_mode="w")
        ok = pfx.is_created(test.prefix_path)
    finally:
        _sh.rmtree(test.prefix_path, ignore_errors=True)
    if not ok:
        print(T(f"  Proton no ha podido crear un entorno (código {rc}). Registro: {pfx.log_path(test)}",
                f"  Proton could not create an environment (code {rc}). Log: {pfx.log_path(test)}"),
              file=sys.stderr)
        return 1
    print(T(f"  Todo correcto ({secs:.0f} s).", f"  All good ({secs:.0f} s)."))
    # El «Windows base» del que nacen todos los programas, una sola vez
    from . import templates
    if not templates.base_ready(name):
        print(T("» Preparando el Windows base para todos los programas (fuentes, Visual C++; unos minutos)…",
                "» Preparing the base Windows for every program (fonts, Visual C++; a few minutes)…"))
        try:
            templates.build(name)
        except OSError as e:
            # No es grave: se reintenta en segundo plano al instalar el primer programa
            print(f"  {T('Aviso', 'Warning')}: {e}", file=sys.stderr)
    return 0


def cmd_update(a) -> int:
    current = updater.installed_version() or __version__
    try:
        if a.check:
            rel = updater.latest_release()
            if updater.parse_version(rel.version) > updater.parse_version(current):
                print(T(f"Hay una versión nueva: {rel.version} (tienes la {current}).\n{rel.url}",
                        f"A new version is available: {rel.version} (you have {current}).\n{rel.url}"))
            else:
                print(T(f"MaLiVe está al día ({current}).", f"MaLiVe is up to date ({current})."))
            return 0
        res = updater.check_and_update(force=True)
    except updater.UpdateError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if res.status == "up-to-date":
        print(T(f"MaLiVe está al día ({current}).", f"MaLiVe is up to date ({current})."))
    elif res.status == "updated":
        print(T(f"MaLiVe actualizado de la {current} a la {res.version}.",
                f"MaLiVe updated from {current} to {res.version}."))
    elif res.status == "needs-packages":
        missing = ", ".join(res.missing)
        print(T(f"La versión {res.version} necesita instalar: {missing}.\nAbriendo el asistente de instalación…",
                f"Version {res.version} needs to install: {missing}.\nOpening the setup wizard…"))
        updater.launch_installer(res.staged)
    return 0


def cmd_auto(a) -> int:
    """Lo que ejecuta el temporizador (cada 3 horas)."""
    updater.auto()
    cfg = config.load_global()
    stamp = paths.data_dir() / ".proton-last-check"
    try:
        old = time.time() - float(stamp.read_text()) > 7 * 86400
    except (OSError, ValueError):
        old = True
    if cfg.get("proton_auto_update", True) and old:
        try:
            proton.update()
            proton.cleanup(cfg.get("proton_keep_unused", 1))
        except proton.ProtonError as e:
            runner.log.warning("actualización de GE-Proton: %s", e)
    # Plantilla para el Proton que usarán las apps nuevas; fuera las de versiones viejas
    from . import templates
    newest = proton.newest_installed()
    if newest:
        templates.cleanup({newest})
        if not templates.base_ready(newest):
            try:
                templates.build(newest)
            except OSError as e:
                runner.log.warning("plantilla de %s: %s", newest, e)
        else:
            try:
                templates.refill(newest)
            except OSError as e:
                runner.log.warning("repuesto de la plantilla: %s", e)
    # nvidia-libs: misma cadencia semanal, solo si hay tarjeta NVIDIA
    from . import nvidia
    if (old and cfg.get("nvidia_mode", "auto") != "off" and nvidia.gpu_present()
            and not (newest and nvidia.proton_provides(proton.path_of(newest)))):
        try:
            nvidia.download()
        except nvidia.NvidiaError as e:
            runner.log.warning("actualización de nvidia-libs: %s", e)
    return 0


def cmd_refresh(a) -> int:
    n = shortcuts.rewrite_all()
    print(T(f"Accesos del menú actualizados: {n}", f"Menu entries updated: {n}"))
    return 0


def cmd_doctor(a) -> int:
    ok = True

    def check(label: str, good: bool, hint: str = "") -> None:
        nonlocal ok
        print(f"  [{'OK' if good else '--'}] {label}" + ("" if good or not hint else f"  → {hint}"))

    print(f"MaLiVe {__version__}")
    print(T("Dependencias:", "Dependencies:"))
    umu = shutil.which("umu-run")
    check("umu-run", bool(umu), "sudo pacman -S umu-launcher")
    ok = ok and bool(umu)
    check(T("wrestool/icotool (iconos)", "wrestool/icotool (icons)"),
          bool(shutil.which("wrestool") and shutil.which("icotool")), "sudo pacman -S icoutils")
    check(T("msiinfo (nombre de los .msi)", "msiinfo (.msi names)"), bool(shutil.which("msiinfo")),
          "sudo pacman -S msitools")
    check(T("diálogos (zenity o kdialog)", "dialogs (zenity or kdialog)"),
          bool(shutil.which("zenity") or shutil.which("kdialog")),
          T("sudo pacman -S zenity   (o kdialog en KDE)", "sudo pacman -S zenity   (or kdialog on KDE)"))
    check("notify-send", bool(shutil.which("notify-send")), "sudo pacman -S libnotify")
    check("update-desktop-database", bool(shutil.which("update-desktop-database")),
          "sudo pacman -S desktop-file-utils")
    print(T("Integración:", "Integration:"))
    check(T("doble clic en .exe/.msi asociado a MaLiVe", "double-click on .exe/.msi opens MaLiVe"),
          integrate.is_integrated(), "malive integrate")
    check(T(f"malive-run en {shortcuts.malive_run_path()}", f"malive-run at {shortcuts.malive_run_path()}"),
          Path(shortcuts.malive_run_path()).is_file(), T("ejecuta install.sh", "run install.sh"))
    print("Proton:")
    names = proton.installed()
    listed = ", ".join(names) or T("ninguno", "none")
    check(T(f"GE-Proton descargado ({listed})", f"GE-Proton downloaded ({listed})"), bool(names),
          T("malive proton update (o se descargará solo en el primer uso)",
            "malive proton update (or it will be downloaded on first use)"))
    print(T(f"Datos: {paths.data_dir()}\nConfig: {paths.config_dir()}\nRegistros: {paths.logs_dir()}",
            f"Data: {paths.data_dir()}\nConfig: {paths.config_dir()}\nLogs: {paths.logs_dir()}"))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="malive", description=T("MaLiVe — programas de Windows con doble clic.",
                                                             "MaLiVe — Windows programs with a double click."))
    p.add_argument("--version", action="version", version=f"malive {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("integrate", help=T("asociar .exe/.msi/.lnk a MaLiVe", "associate .exe/.msi/.lnk with MaLiVe"))
    s.add_argument("--no-timer", action="store_true",
                   help=T("sin actualizaciones automáticas", "without automatic updates"))
    s.set_defaults(func=cmd_integrate)

    sub.add_parser("unintegrate", help=T("quitar asociaciones y temporizador", "remove associations and timer")
                   ).set_defaults(func=cmd_unintegrate)

    s = sub.add_parser("list", help=T("apps instaladas", "installed apps"))
    s.add_argument("--size", action="store_true",
                   help=T("mostrar el espacio que ocupa cada prefijo", "show the disk space of each prefix"))
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("run", help=T("igual que malive-run", "same as malive-run"))
    s.add_argument("file")
    s.add_argument("args", nargs=argparse.REMAINDER)
    s.set_defaults(func=lambda a: runner.main([a.file, *a.args]))

    s = sub.add_parser("remove", help=T("eliminar una app y su prefijo", "remove an app and its prefix"))
    s.add_argument("app")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(func=cmd_remove)

    s = sub.add_parser("shortcuts", help=T("volver a buscar accesos directos de una app", "rescan an app's shortcuts"))
    s.add_argument("app")
    s.add_argument("--all", action="store_true",
                   help=T("ofrecer también los .exe sin acceso directo", "also offer .exe files without a shortcut"))
    s.set_defaults(func=cmd_shortcuts)

    s = sub.add_parser("proton", help=T("gestionar GE-Proton", "manage GE-Proton"))
    s.add_argument("action", choices=["list", "update", "use"])
    s.add_argument("app", nargs="?")
    s.add_argument("version", nargs="?")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_proton)

    s = sub.add_parser("set", help=T("cambiar opciones (global o de una app)", "change settings (global or per app)"))
    s.add_argument("key", choices=["isolate", "auto-update", "host-drives", "nvidia"])
    s.add_argument("value", choices=["on", "off", "auto"])
    s.add_argument("--app")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("exec", help=T("ejecutar winecfg, regedit, cmd... en el prefijo de una app",
                                      "run winecfg, regedit, cmd... in an app's prefix"))
    s.add_argument("app")
    s.add_argument("program")
    s.add_argument("args", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_exec)

    s = sub.add_parser("template", help=T("plantillas de contenedor: status | build | rebuild | refill",
                                          "container templates: status | build | rebuild | refill"))
    s.add_argument("action", choices=["status", "build", "rebuild", "refill"])
    s.add_argument("--proton")
    s.add_argument("--kind", default="mono")
    s.set_defaults(func=cmd_template)

    s = sub.add_parser("hardware", help=T("ver el equipo y la configuración común a todos los programas",
                                          "show the computer and the configuration shared by every program"))
    s.add_argument("--short", action="store_true")
    s.set_defaults(func=cmd_hardware)

    s = sub.add_parser("nvidia", help=T("CUDA/NVENC de NVIDIA para una app: malive nvidia [APP on|off|default] | update",
                                        "NVIDIA CUDA/NVENC for an app: malive nvidia [APP on|off|default] | update"))
    s.add_argument("args", nargs="*")
    s.set_defaults(func=cmd_nvidia)

    s = sub.add_parser("dll", help=T("DLL ajustadas de una app (p. ej. sapi=d desactiva sapi.dll)",
                                     "an app's DLL overrides (e.g. sapi=d disables sapi.dll)"))
    s.add_argument("app")
    s.add_argument("items", nargs="*", metavar="DLL=MODO")
    s.add_argument("--remove", nargs="+", metavar="DLL")
    s.set_defaults(func=cmd_dll)

    s = sub.add_parser("env", help=T("variables de entorno de una app (p. ej. PROTON_ENABLE_NVAPI=1)",
                                     "an app's environment variables (e.g. PROTON_ENABLE_NVAPI=1)"))
    s.add_argument("app")
    s.add_argument("items", nargs="*", metavar="VAR=VALOR")
    s.add_argument("--remove", nargs="+", metavar="VAR")
    s.set_defaults(func=cmd_env)

    s = sub.add_parser("winetricks", help=T("instalar componentes (dotnet48, vcrun2022...) en una app",
                                            "install components (dotnet48, vcrun2022...) in an app"))
    s.add_argument("app")
    s.add_argument("verbs", nargs="+")
    s.set_defaults(func=cmd_winetricks)

    sub.add_parser("doctor", help=T("comprobar dependencias e integración", "check dependencies and integration")
                   ).set_defaults(func=cmd_doctor)
    s = sub.add_parser("update", help=T("actualizar MaLiVe ahora (--check: solo comprobar)",
                                        "update MaLiVe now (--check: only check)"))
    s.add_argument("--check", action="store_true")
    s.set_defaults(func=cmd_update)
    sub.add_parser("auto", help=T("(lo usa el temporizador) actualizar MaLiVe y GE-Proton",
                                  "(used by the timer) update MaLiVe and GE-Proton")
                   ).set_defaults(func=cmd_auto)
    sub.add_parser("refresh", help=T("regenerar los accesos del menú de todas las apps",
                                     "regenerate the menu entries of all apps")
                   ).set_defaults(func=cmd_refresh)
    sub.add_parser("setup", help=T("descargar GE-Proton y el runtime y probar que Proton arranca",
                                   "download GE-Proton and the runtime and test that Proton starts")
                   ).set_defaults(func=cmd_setup)
    return p


def main(argv: list[str] | None = None) -> int:
    runner.setup_logging()
    a = build_parser().parse_args(argv)
    try:
        return a.func(a)
    except (pfx.PrefixError, proton.ProtonError, runner.UserError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
