"""CLI `malive`: integración, listado, mantenimiento y diagnóstico."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__, config, integrate, paths, prefix as pfx, proton, runner, shortcuts


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _get_app(app_id: str) -> config.App:
    app = config.load_app(app_id)
    if app is None:
        sys.exit(f"No existe la app «{app_id}». Usa «malive list».")
    return app


def cmd_integrate(a) -> int:
    path = integrate.install_handler()
    print(f"Asociación creada: {path}")
    print("Los .exe, .msi y .lnk se abrirán con MaLiVe al hacer doble clic.")
    if not a.no_timer and config.load_global().get("proton_auto_update", True):
        ok = integrate.install_timer()
        print("Comprobación semanal de GE-Proton: " + ("activada" if ok else "no se pudo activar (systemctl --user)"))
    return 0


def cmd_unintegrate(a) -> int:
    integrate.remove_handler()
    integrate.remove_timer()
    print("Asociaciones y temporizador eliminados. Las apps instaladas se conservan.")
    return 0


def cmd_list(a) -> int:
    apps = config.all_apps()
    if not apps:
        print("No hay apps. Haz doble clic en un instalador .exe o .msi para empezar.")
        return 0
    cfg = config.load_global()
    for app in apps:
        iso = "aislada" if app.effective_isolate(cfg) else "sin aislar"
        size = f", {_human(pfx.disk_usage(app.prefix_path))}" if a.size else ""
        print(f"{app.id}  —  {app.name}  [{app.kind}, {app.proton}, {iso}{size}]")
        for e in app.entries:
            print(f"    · {e.name}: {e.exe}")
    return 0


def cmd_remove(a) -> int:
    app = _get_app(a.app)
    if pfx.is_busy(app.prefix_path):
        sys.exit(f"«{app.name}» está en ejecución. Ciérrala antes de eliminarla.")
    if not a.yes:
        ans = input(f"¿Eliminar «{app.name}» y todo su entorno ({app.prefix})? [s/N] ").strip().lower()
        if ans not in ("s", "si", "sí", "y", "yes"):
            print("Cancelado.")
            return 1
    runner.discard_app(app)
    print(f"«{app.name}» eliminada.")
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
            sel = input("Números a añadir (separados por espacios, vacío = ninguno): ").split()
            cands = [cands[int(i)] for i in sel if i.isdigit() and int(i) < len(cands)]
    created = shortcuts.register(app, cands)
    for e in created:
        print(f"Creado: {e.name} -> {e.desktop}")
    if not created:
        print("No hay accesos nuevos que crear.")
    return 0


def cmd_proton(a) -> int:
    if a.action == "list":
        used = proton.in_use()
        names = proton.installed()
        if not names:
            print("No hay ningún GE-Proton descargado todavía.")
        for n in names:
            print(f"{n}{'  (en uso)' if n in used else ''}")
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
            print(f"{name} {'descargado' if new else 'ya estaba instalado'}. Se usará en las apps nuevas.")
        removed = proton.cleanup(config.load_global().get("proton_keep_unused", 1))
        if removed and not a.quiet:
            print("Eliminadas versiones sin uso: " + ", ".join(removed))
        return 0
    if a.action == "use":
        if not a.app or not a.version:
            sys.exit("Uso: malive proton use APP VERSIÓN")
        app = _get_app(a.app)
        if not proton.is_valid(proton.path_of(a.version)):
            sys.exit(f"{a.version} no está descargado. Versiones: {', '.join(proton.installed()) or 'ninguna'}")
        print(f"Aviso: el prefijo de «{app.name}» se actualizará a {a.version} en el próximo arranque "
              "y no se puede volver atrás sin reinstalar.")
        app.proton = a.version
        app.save()
        print(f"«{app.name}» usará {a.version}.")
        return 0
    return 2


def cmd_set(a) -> int:
    value = {"on": True, "off": False}[a.value]
    if a.key != "isolate":
        sys.exit("Solo se admite «isolate» por ahora.")
    if a.app:
        app = _get_app(a.app)
        app.isolate = value
        app.save()
        print(f"Aislamiento de «{app.name}»: {a.value}")
    else:
        cfg = config.load_global()
        cfg["isolate"] = value
        config.save_global(cfg)
        print(f"Aislamiento por defecto: {a.value} (las apps con valor propio no cambian)")
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


def cmd_winetricks(a) -> int:
    app = _get_app(a.app)
    cfg = config.load_global()
    runner._ensure_ready(app, cfg)
    rc, _ = pfx.run_umu(app, cfg, ["winetricks", *a.verbs], cwd=app.prefix_path)
    print(f"winetricks terminó con código {rc}. Registro: {pfx.log_path(app)}")
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
        print(f"  GE-Proton ya descargado: {name}")
    else:
        print("  Descargando GE-Proton (~500 MB)…")
        try:
            name, _ = proton.update(progress)
        except proton.ProtonError as e:
            print(f"\n  Error: {e}", file=sys.stderr)
            return 1
        print(f"\n  {name} listo.")

    print("  Preparando el runtime de Steam y comprobando que Proton funciona (unos minutos la primera vez)…")
    test = config.App(id="_setup", name="prueba", kind="portable",
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
        print(f"  Proton no ha podido crear un entorno (código {rc}). Registro: {pfx.log_path(test)}",
              file=sys.stderr)
        return 1
    print(f"  Todo correcto ({secs:.0f} s).")
    return 0


def cmd_doctor(a) -> int:
    ok = True

    def check(label: str, good: bool, hint: str = "") -> None:
        nonlocal ok
        print(f"  [{'OK' if good else '--'}] {label}" + ("" if good or not hint else f"  → {hint}"))

    print(f"MaLiVe {__version__}")
    print("Dependencias:")
    umu = shutil.which("umu-run")
    check("umu-run", bool(umu), "sudo pacman -S umu-launcher")
    ok = ok and bool(umu)
    check("wrestool/icotool (iconos)", bool(shutil.which("wrestool") and shutil.which("icotool")),
          "sudo pacman -S icoutils")
    check("msiinfo (nombre de los .msi)", bool(shutil.which("msiinfo")), "sudo pacman -S msitools")
    check("diálogos (zenity o kdialog)", bool(shutil.which("zenity") or shutil.which("kdialog")),
          "sudo pacman -S zenity   (o kdialog en KDE)")
    check("notify-send", bool(shutil.which("notify-send")), "sudo pacman -S libnotify")
    check("update-desktop-database", bool(shutil.which("update-desktop-database")),
          "sudo pacman -S desktop-file-utils")
    print("Integración:")
    check("doble clic en .exe/.msi asociado a MaLiVe", integrate.is_integrated(), "malive integrate")
    check(f"malive-run en {shortcuts.malive_run_path()}", Path(shortcuts.malive_run_path()).is_file(),
          "ejecuta install.sh")
    print("Proton:")
    names = proton.installed()
    check(f"GE-Proton descargado ({', '.join(names) or 'ninguno'})", bool(names),
          "malive proton update (o se descargará solo en el primer uso)")
    print(f"Datos: {paths.data_dir()}\nConfig: {paths.config_dir()}\nRegistros: {paths.logs_dir()}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="malive", description="MaLiVe — programas de Windows con doble clic.")
    p.add_argument("--version", action="version", version=f"malive {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("integrate", help="asociar .exe/.msi/.lnk a MaLiVe")
    s.add_argument("--no-timer", action="store_true", help="sin comprobación semanal de GE-Proton")
    s.set_defaults(func=cmd_integrate)

    sub.add_parser("unintegrate", help="quitar asociaciones y temporizador").set_defaults(func=cmd_unintegrate)

    s = sub.add_parser("list", help="apps instaladas")
    s.add_argument("--size", action="store_true", help="mostrar el espacio que ocupa cada prefijo")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("run", help="igual que malive-run")
    s.add_argument("file")
    s.add_argument("args", nargs=argparse.REMAINDER)
    s.set_defaults(func=lambda a: runner.main([a.file, *a.args]))

    s = sub.add_parser("remove", help="eliminar una app y su prefijo")
    s.add_argument("app")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(func=cmd_remove)

    s = sub.add_parser("shortcuts", help="volver a buscar accesos directos de una app")
    s.add_argument("app")
    s.add_argument("--all", action="store_true", help="ofrecer también los .exe sin acceso directo")
    s.set_defaults(func=cmd_shortcuts)

    s = sub.add_parser("proton", help="gestionar GE-Proton")
    s.add_argument("action", choices=["list", "update", "use"])
    s.add_argument("app", nargs="?")
    s.add_argument("version", nargs="?")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(func=cmd_proton)

    s = sub.add_parser("set", help="cambiar opciones (global o de una app)")
    s.add_argument("key", choices=["isolate"])
    s.add_argument("value", choices=["on", "off"])
    s.add_argument("--app")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("exec", help="ejecutar winecfg, regedit, cmd... en el prefijo de una app")
    s.add_argument("app")
    s.add_argument("program")
    s.add_argument("args", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_exec)

    s = sub.add_parser("winetricks", help="instalar componentes (dotnet48, vcrun2022...) en una app")
    s.add_argument("app")
    s.add_argument("verbs", nargs="+")
    s.set_defaults(func=cmd_winetricks)

    sub.add_parser("doctor", help="comprobar dependencias e integración").set_defaults(func=cmd_doctor)
    sub.add_parser("setup", help="descargar GE-Proton y el runtime y probar que Proton arranca"
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
