"""malive-run: lo que ocurre al hacer doble clic en un .exe, .msi o .lnk."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import traceback
from pathlib import Path

from . import addons, config, detect, paths, pe, prefix as pfx, proton, shortcuts, ui
from .i18n import T
from .config import App

log = logging.getLogger("malive")

# Si el programa termina con error antes de esto, asumimos que no llegó a arrancar.
QUICK_FAIL_SECONDS = 8


class UserError(Exception):
    """Error explicable al usuario (se muestra en un diálogo, sin traza)."""


def setup_logging() -> None:
    f = paths.main_log()
    f.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=f, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


# --------------------------------------------------------------------------
# Creación de apps
# --------------------------------------------------------------------------

def _new_app(name: str, kind: str, source: Path, cfg: dict) -> App:
    proton_name = proton.ensure_for_new_app(ui)
    app_id = config.new_app_id(name)
    app = App(
        id=app_id, name=name, kind=kind,
        prefix=str(paths.prefixes_dir() / app_id),
        proton=proton_name, source=str(source), created=config.now_iso(),
    )
    app.save()
    try:
        pfx.create(app, cfg)
    except BaseException:
        discard_app(app)
        raise
    log.info("app %s creada (proton %s, prefijo %s)", app.id, app.proton, app.prefix)
    return app


def discard_app(app: App) -> None:
    shortcuts.remove_entries(app)
    shutil.rmtree(app.prefix_path, ignore_errors=True)
    app.delete_record()
    (paths.logs_dir() / f"{app.id}.log").unlink(missing_ok=True)


def _ensure_ready(app: App, cfg: dict) -> None:
    pfx.ensure_proton_present(app)
    if not pfx.is_created(app.prefix_path):
        pfx.create(app, cfg)


# --------------------------------------------------------------------------
# Lanzar
# --------------------------------------------------------------------------

def launch(app: App, exe: Path, args: list[str], workdir: Path | None = None,
           source_dir: Path | None = None, docs: list[Path] | None = None, on_tick=None) -> int:
    # Un "\0" en un argumento hace fallar a subprocess ("embedded null byte");
    # las apps instaladas con la 0.1.4 o anteriores pueden tenerlo guardado.
    args = [a.replace("\0", "") for a in args]
    args = [a for a in args if a]
    cfg = config.load_global()
    _ensure_ready(app, cfg)
    docs = [d.resolve() for d in (docs or []) if d.exists()]
    if docs and source_dir is None:
        source_dir = docs[0].parent  # si hace falta, su carpeta se monta como Y:
    pfx.apply_drives(app, cfg, source_dir)
    for doc in docs:
        # Los programas de Windows esperan rutas de Windows: /home/u/Documentos/a.txt → D:\a.txt
        win = pfx.unix_to_windows(app.prefix_path, doc)
        if win is None:
            log.warning("%s no es accesible desde el entorno de %s", doc, app.id)
            continue
        args.append(win)
    argv = [str(exe), *args]
    if _is_console(exe):
        # Sin esto un programa de consola no muestra ninguna ventana: "start"
        # crea una consola nueva (conhost) para él.
        argv = ["start", "/wait", "/unix", str(exe), *args]
    log.info("lanzando %s en %s: %s", app.id, app.proton, argv)
    app.last_used = config.now_iso()
    try:
        app.save()
    except OSError:
        pass
    rc, secs = pfx.run_umu(app, cfg, argv, cwd=workdir or exe.parent, install_path=exe.parent, on_tick=on_tick)
    # Accesos sin icono: el programa puede haberlo dejado ahora (lanzadores que
    # instalan el juego y su GameIcon.ico donde dice el usuario)
    if any(not e.icon or not Path(e.icon).exists() for e in app.entries):
        try:
            if shortcuts.rewrite_app(app):
                shortcuts.refresh_menus()
        except OSError as e:
            log.warning("reintento de iconos de %s: %s", app.id, e)
    if rc != 0 and secs < QUICK_FAIL_SECONDS:
        ui.error(T(f"«{app.name}» no ha podido arrancar (código {rc}).\n\nRegistro: {pfx.log_path(app)}",
                   f"«{app.name}» could not start (code {rc}).\n\nLog: {pfx.log_path(app)}"))
    return rc


def uninstall(app_id: str) -> int:
    """Desinstalación desde el menú: confirma y borra la app con su entorno."""
    app = config.load_app(app_id)
    if app is None:
        raise UserError(T("Este programa ya no está instalado.", "This program is no longer installed."))
    if pfx.is_busy(app.prefix_path):
        raise UserError(T(f"«{app.name}» está abierto. Ciérralo y vuelve a intentarlo.",
                          f"«{app.name}» is open. Close it and try again."))
    names = "\n".join(f"  • {e.name}" for e in app.entries)
    also = (T(f"\n\nSe quitarán del menú:\n{names}", f"\n\nThese menu entries will be removed:\n{names}")
            if len(app.entries) > 1 else "")
    if not ui.question(
        T(f"¿Desinstalar «{app.name}»?{also}\n\n"
          "Se borrarán el programa y sus datos guardados dentro de él (configuración, "
          "archivos en «Mis documentos» de Windows…).\n"
          "Lo que haya en tus carpetas y discos (D:, E:, M:, N:…), por ejemplo un juego "
          "instalado en otro disco, no se toca.",
          f"Uninstall «{app.name}»?{also}\n\n"
          "The program and the data saved inside it (settings, files in the Windows "
          "«My Documents» folder…) will be deleted.\n"
          "Anything in your folders and disks (D:, E:, M:, N:…), such as a game installed "
          "on another disk, is not touched."),
        title=T("Desinstalar", "Uninstall"), yes=T("Desinstalar", "Uninstall"), no=T("Cancelar", "Cancel"),
    ):
        return 1
    discard_app(app)
    log.info("app %s desinstalada", app.id)
    ui.notify(T(f"«{app.name}» se ha desinstalado.", f"«{app.name}» has been uninstalled."))
    return 0


def _is_console(exe: Path) -> bool:
    try:
        return pe.parse(exe).subsystem == "console"
    except Exception:  # noqa: BLE001
        return False


def run_entry(app_id: str, entry_id: str, docs: list[Path] | None = None) -> int:
    app = config.load_app(app_id)
    if app is None:
        raise UserError(T(f"No existe la app «{app_id}» en MaLiVe. Puede que se haya eliminado.",
                          f"There is no app «{app_id}» in MaLiVe. It may have been removed."))
    entry = app.entry(entry_id)
    if entry is None:
        raise UserError(T(f"«{app.name}» no tiene la entrada «{entry_id}».",
                          f"«{app.name}» has no entry «{entry_id}»."))
    exe = Path(entry.exe)
    if not exe.is_file():
        if ui.question(T(
            f"El programa «{entry.name}» ya no existe dentro de su entorno:\n{exe}\n\n¿Quitar su acceso del menú?",
            f"The program «{entry.name}» no longer exists in its environment:\n{exe}\n\nRemove it from the menu?",
        )):
            if entry.desktop:
                Path(entry.desktop).unlink(missing_ok=True)
            app.entries = [e for e in app.entries if e.id != entry.id]
            app.save()
            shortcuts.refresh_menus()
        return 1
    workdir = Path(entry.workdir) if entry.workdir else None
    return launch(app, exe, list(entry.args), workdir, docs=docs)


# --------------------------------------------------------------------------
# Instalar
# --------------------------------------------------------------------------

def _installer_argv(app: App, det: detect.Detection, args: list[str]) -> list[str]:
    if det.kind != "msi":
        return [str(det.path), *args]
    winpath = pfx.unix_to_windows(app.prefix_path, det.path)
    if winpath is None:
        raise UserError(T(f"No se pudo acceder a {det.path} desde el entorno de Windows.",
                          f"{det.path} cannot be reached from the Windows environment."))
    # Accesos no "anunciados": así los .lnk apuntan al .exe real
    return ["msiexec", "/i", winpath, "DISABLEADVTSHORTCUTS=1", *args]


def install(det: detect.Detection, args: list[str], app: App | None = None) -> int:
    cfg = config.load_global()
    name = det.name or det.path.stem
    if app is None:
        app = config.find_installed_by_name(name)
    is_new = app is None
    if app is None:
        app = _new_app(name, "installed", det.path, cfg)
    else:
        _ensure_ready(app, cfg)
        log.info("reutilizando el entorno de %s para %s", app.id, det.path)

    pfx.apply_drives(app, cfg, det.path.parent)
    prefix = app.prefix_path
    before = shortcuts.take_snapshot(prefix)
    argv = _installer_argv(app, det, args)
    log.info("instalando %s (%s) en %s", det.path, det.subtype, app.id)

    created: list[config.Entry] = []

    def scan(full: bool = False) -> None:
        """Crea los accesos que hayan aparecido y avisa de los nuevos."""
        snap = shortcuts.take_snapshot(prefix) if full else shortcuts.take_lnk_snapshot(prefix)
        new = shortcuts.register(app, shortcuts.new_from_lnks(prefix, before, snap))
        if new:
            created.extend(new)
            log.info("accesos creados: %s", [e.name for e in new])
            ui.notify(_installed_msg(name, new))

    # Se vigila el menú Inicio mientras el instalador sigue en marcha: muchos
    # abren el programa al terminar («Ejecutar X») y el contenedor no acaba
    # hasta que el usuario lo cierra; el acceso debe aparecer antes.
    rc, _ = pfx.run_umu(app, cfg, argv, cwd=det.path.parent, install_path=det.path.parent, on_tick=scan)
    scan(full=True)
    # Cuando no queda nada vivo (bootstrappers que se relanzan…), última pasada
    if pfx.is_busy(prefix):
        pfx.wait_idle(prefix, on_tick=scan)
    after = shortcuts.take_snapshot(prefix)
    scan(full=True)
    # Las asociaciones de ficheros suelen registrarse al final de la instalación
    if app.entries and shortcuts.rewrite_app(app):
        shortcuts.refresh_menus()

    if not created:
        exes = shortcuts.new_exes(prefix, before, after)
        nothing_new = not (after.exes - before.exes) and not (set(after.lnks) - set(before.lnks))
        if nothing_new:
            if is_new:
                discard_app(app)
                ui.notify(T(f"La instalación de «{name}» se canceló o no instaló nada.",
                            f"The installation of «{name}» was cancelled or installed nothing."))
            else:
                ui.notify(T(f"«{name}»: el instalador no añadió programas nuevos.",
                            f"«{name}»: the installer added no new programs."))
            log.info("instalación sin cambios (rc=%s)", rc)
            return rc
        chosen = shortcuts.register(app, _choose_exes(name, exes))
        if chosen:
            created.extend(chosen)
            ui.notify(_installed_msg(name, chosen))

    if not created and not app.entries:
        ui.info(T(
            f"«{name}» se ha instalado, pero no se encontró su programa principal.\n"
            f"Puedes crear el acceso con:\n  malive shortcuts {app.id} --all",
            f"«{name}» has been installed, but its main program was not found.\n"
            f"You can create the menu entry with:\n  malive shortcuts {app.id} --all",
        ))
    return 0


def install_addon(det: detect.Detection, args: list[str], app: App) -> int:
    """Instala un complemento (códecs, bibliotecas…) dentro del entorno de un
    programa existente. No crea accesos en el menú: los del complemento
    (configuraciones, desinstaladores) solo ensuciarían."""
    cfg = config.load_global()
    _ensure_ready(app, cfg)
    pfx.apply_drives(app, cfg, det.path.parent)
    was_running = pfx.is_busy(app.prefix_path)
    name = det.name or det.path.stem
    argv = _installer_argv(app, det, args)
    log.info("instalando el complemento %s (%s) dentro de %s", det.path, det.subtype, app.id)
    rc, _ = pfx.run_umu(app, cfg, argv, cwd=det.path.parent, install_path=det.path.parent)
    if not was_running and pfx.is_busy(app.prefix_path):
        pfx.wait_idle(app.prefix_path, timeout=300)
    if app.entries and shortcuts.rewrite_app(app):
        shortcuts.refresh_menus()
    log.info("complemento %s en %s: rc=%s", name, app.id, rc)
    if rc == 0:
        extra = T(f"\nSi «{app.name}» está abierto, ciérralo y vuelve a abrirlo.",
                  f"\nIf «{app.name}» is open, close it and open it again.") if was_running else ""
        ui.notify(T(f"«{name}» instalado dentro de «{app.name}».", f"«{name}» installed into «{app.name}».") + extra)
    else:
        ui.notify(T(f"El instalador de «{name}» terminó con código {rc} en «{app.name}».",
                    f"The «{name}» installer exited with code {rc} in «{app.name}»."))
    return rc


def install_into(det: detect.Detection, args: list[str], targets: list[str]) -> int:
    rc = 0
    for target in targets:
        if target == addons.NEW:
            r = install(det, args) if det.is_installer else run_portable(det, args)
        else:
            app = config.load_app(target)
            if app is None:
                continue
            r = install_addon(det, args, app)
        rc = rc or r
    return rc


def _install_new_or_into(det: detect.Detection, args: list[str], choose: bool = False) -> int:
    # Mismo nombre que un programa ya instalado: es una actualización suya
    if not choose and config.find_installed_by_name(det.name or det.path.stem) is not None:
        return install(det, args)
    targets = addons.ask(det, force=choose)
    if targets is None:
        log.info("instalación de %s cancelada al elegir destino", det.path)
        return 1
    if not targets:
        return install(det, args) if det.is_installer else run_portable(det, args)
    return install_into(det, args, targets)


def _installed_msg(name: str, entries) -> str:
    names = ", ".join(e.name for e in entries)
    return T(f"«{name}» instalado. Ya está en el menú de aplicaciones: {names}",
             f"«{name}» installed. It is now in your app menu: {names}")


def _choose_exes(name: str, exes: list[shortcuts.Candidate]) -> list[shortcuts.Candidate]:
    if not exes:
        return []
    if len(exes) == 1:
        return exes
    items = [(str(i), f"{c.name}  ({c.exe.parent.name})", i == 0) for i, c in enumerate(exes[:30])]
    picked = ui.checklist(T(f"«{name}» no creó accesos directos. ¿Qué programas quieres en el menú?",
                            f"«{name}» created no shortcuts. Which programs do you want in the menu?"), items)
    if picked is None:
        return exes[:1]  # sin GUI o cancelado: el más grande
    return [exes[int(i)] for i in picked]


# --------------------------------------------------------------------------
# Punto de entrada por fichero
# --------------------------------------------------------------------------

def run_file(path: Path, args: list[str], choose: bool = False, into: str | None = None) -> int:
    path = path.expanduser()
    if not path.is_file():
        raise UserError(T(f"No existe el fichero:\n{path}", f"File not found:\n{path}"))
    path = path.resolve()
    det = detect.detect(path)
    log.info("detectado %s: kind=%s subtype=%s name=%r", path, det.kind, det.subtype, det.name)

    root = pfx.find_prefix_root(path)
    if root is not None:
        app = config.find_by_prefix(root)
        if app is None:
            raise UserError(T(
                f"Este programa está dentro de un entorno de Wine que MaLiVe no gestiona:\n{root}\n\n"
                "Ábrelo con el programa que creó ese entorno (Lutris, Bottles, Steam…).",
                f"This program is inside a Wine environment that MaLiVe does not manage:\n{root}\n\n"
                "Open it with the program that created that environment (Lutris, Bottles, Steam…).",
            ))
        if det.kind == "lnk":
            return _run_lnk_in_app(app, det)
        if det.is_installer:
            return install(det, args, app)
        if det.kind == "app":
            return launch(app, path, args)
        raise UserError(T(f"No se puede ejecutar {path.name}: {det.reason}",
                          f"{path.name} cannot be run: {det.reason}"))

    if det.kind == "lnk":
        target = det.shortcut.target if det.shortcut else None
        raise UserError(T(
            "Este acceso directo de Windows no pertenece a ningún programa instalado con MaLiVe",
            "This Windows shortcut does not belong to any program installed with MaLiVe",
        ) + (f":\n{target}" if target else "."))
    if into is not None:
        if not det.is_installer and det.kind != "app":
            raise UserError(T(f"No se puede ejecutar «{path.name}»: {det.reason}",
                              f"«{path.name}» cannot be run: {det.reason}"))
        app = config.load_app(into)
        if app is None:
            raise UserError(T(f"No existe la app «{into}» en MaLiVe.", f"There is no app «{into}» in MaLiVe."))
        return install_addon(det, args, app)
    if det.is_installer:
        return _install_new_or_into(det, args, choose)
    if det.kind == "app" and choose:
        return _install_new_or_into(det, args, choose)
    if det.kind == "app":
        return run_portable(det, args)
    if det.kind == "dos":
        raise UserError(T(
            f"«{path.name}» es un programa de MS-DOS o de Windows de 16 bits.\nMaLiVe no los soporta; prueba con DOSBox.",
            f"«{path.name}» is an MS-DOS or 16-bit Windows program.\nMaLiVe does not support them; try DOSBox.",
        ))
    raise UserError(T(f"No se puede ejecutar «{path.name}»: {det.reason}",
                      f"«{path.name}» cannot be run: {det.reason}"))


def _run_lnk_in_app(app: App, det: detect.Detection) -> int:
    cand = shortcuts.candidate_from_lnk(app.prefix_path, det.path)
    sc = det.shortcut
    if cand is None and sc and sc.target:
        exe = pfx.windows_to_unix(app.prefix_path, sc.target)
        if exe and exe.is_file():
            cand = shortcuts.Candidate(name=det.path.stem, exe=exe, args=shortcuts._split_args(sc.arguments))
    if cand is None:
        raise UserError(T(f"No se pudo resolver el destino de «{det.path.name}».",
                          f"The target of «{det.path.name}» could not be resolved."))
    return launch(app, cand.exe, cand.args, cand.workdir)


def run_portable(det: detect.Detection, args: list[str]) -> int:
    """Un .exe que no parece instalador. Muchos lo son de todos modos: los
    lanzadores e instaladores web (de juegos, Steam, Discord…) descargan el
    programa y lo instalan con un .msi u otro instalador. Por eso se vigila el
    menú Inicio también aquí: si aparecen accesos, se crean sus entradas en el
    menú como en una instalación normal."""
    cfg = config.load_global()
    app = config.find_portable_by_source(det.path)
    if app is None:
        app = _new_app(det.name or det.path.stem, "portable", det.path, cfg)
    _ensure_ready(app, cfg)
    prefix = app.prefix_path
    before = shortcuts.take_lnk_snapshot(prefix)
    created: list[config.Entry] = []

    def scan() -> None:
        new = shortcuts.register(app, shortcuts.new_from_lnks(prefix, before, shortcuts.take_lnk_snapshot(prefix)))
        if new:
            created.extend(new)
            log.info("el programa %s instaló accesos: %s", app.id, [e.name for e in new])
            ui.notify(_installed_msg(app.name, new))

    rc = launch(app, det.path, args, workdir=det.path.parent, source_dir=det.path.parent, on_tick=scan)
    scan()
    if created:
        # El instalador lanzado puede seguir vivo un poco (o abrir el programa)
        if pfx.is_busy(prefix):
            pfx.wait_idle(prefix, on_tick=scan)
            scan()
        if shortcuts.rewrite_app(app):
            shortcuts.refresh_menus()
    return rc


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="malive-run",
        description=T("Ejecuta o instala un programa de Windows (.exe, .msi, .lnk).",
                      "Runs or installs a Windows program (.exe, .msi, .lnk)."),
    )
    p.add_argument("--app", help=T("id de una app instalada (lo usan los .desktop)",
                                   "id of an installed app (used by the .desktop files)"))
    p.add_argument("--entry", help=T("id de la entrada de la app", "id of the app's entry"))
    p.add_argument("--uninstall", metavar="APP", help=T("desinstalar una app (con confirmación)",
                                                        "uninstall an app (asks first)"))
    p.add_argument("--choose", action="store_true",
                   help=T("preguntar en qué programa instalarlo (complementos, códecs…)",
                          "ask which program to install it into (add-ons, codecs…)"))
    p.add_argument("--into", metavar="APP", help=T("instalarlo dentro del entorno de esa app",
                                                   "install it into that app's environment"))
    p.add_argument("file", nargs="?", help=T("fichero a ejecutar", "file to run"))
    p.add_argument("args", nargs=argparse.REMAINDER, help=T("argumentos para el programa", "arguments for the program"))
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    opts = build_parser().parse_args(argv)
    try:
        if opts.uninstall:
            return uninstall(opts.uninstall)
        if opts.app:
            if not opts.entry:
                raise UserError(T("Falta --entry", "--entry is missing"))
            # Ficheros de «Abrir con» o arrastrados al icono (%F en el .desktop)
            docs = [Path(p) for p in ([opts.file] if opts.file else []) + list(opts.args)]
            return run_entry(opts.app, opts.entry, docs)
        if not opts.file:
            build_parser().print_help()
            return 2
        return run_file(Path(opts.file), opts.args, choose=opts.choose, into=opts.into)
    except (UserError, pfx.PrefixError, proton.ProtonError) as e:
        log.warning("%s", e)
        ui.error(str(e))
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        log.error("error inesperado\n%s", tb)
        ui.error(T(f"Error inesperado en MaLiVe.\nDetalles en: {paths.main_log()}\n\n{tb.splitlines()[-1]}",
                   f"Unexpected error in MaLiVe.\nDetails in: {paths.main_log()}\n\n{tb.splitlines()[-1]}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
