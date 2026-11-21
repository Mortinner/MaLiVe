"""malive-run: lo que ocurre al hacer doble clic en un .exe, .msi o .lnk."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import traceback
from pathlib import Path

from . import config, detect, paths, pe, prefix as pfx, proton, shortcuts, ui
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
           source_dir: Path | None = None) -> int:
    cfg = config.load_global()
    _ensure_ready(app, cfg)
    pfx.apply_drives(app, cfg, source_dir)
    argv = [str(exe), *args]
    if _is_console(exe):
        # Sin esto un programa de consola no muestra ninguna ventana: "start"
        # crea una consola nueva (conhost) para él.
        argv = ["start", "/wait", "/unix", str(exe), *args]
    log.info("lanzando %s en %s: %s", app.id, app.proton, argv)
    rc, secs = pfx.run_umu(app, cfg, argv, cwd=workdir or exe.parent, install_path=exe.parent)
    if rc != 0 and secs < QUICK_FAIL_SECONDS:
        ui.error(
            f"«{app.name}» no ha podido arrancar (código {rc}).\n\n"
            f"Registro: {pfx.log_path(app)}"
        )
    return rc


def _is_console(exe: Path) -> bool:
    try:
        return pe.parse(exe).subsystem == "console"
    except Exception:  # noqa: BLE001
        return False


def run_entry(app_id: str, entry_id: str) -> int:
    app = config.load_app(app_id)
    if app is None:
        raise UserError(f"No existe la app «{app_id}» en MaLiVe. Puede que se haya eliminado.")
    entry = app.entry(entry_id)
    if entry is None:
        raise UserError(f"«{app.name}» no tiene la entrada «{entry_id}».")
    exe = Path(entry.exe)
    if not exe.is_file():
        if ui.question(
            f"El programa «{entry.name}» ya no existe dentro de su entorno:\n{exe}\n\n"
            "¿Quitar su acceso del menú?"
        ):
            if entry.desktop:
                Path(entry.desktop).unlink(missing_ok=True)
            app.entries = [e for e in app.entries if e.id != entry.id]
            app.save()
            shortcuts.refresh_menus()
        return 1
    workdir = Path(entry.workdir) if entry.workdir else None
    return launch(app, exe, entry.args, workdir)


# --------------------------------------------------------------------------
# Instalar
# --------------------------------------------------------------------------

def _installer_argv(app: App, det: detect.Detection, args: list[str]) -> list[str]:
    if det.kind != "msi":
        return [str(det.path), *args]
    winpath = pfx.unix_to_windows(app.prefix_path, det.path)
    if winpath is None:
        raise UserError(f"No se pudo acceder a {det.path} desde el entorno de Windows.")
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
    before = shortcuts.take_snapshot(app.prefix_path)
    argv = _installer_argv(app, det, args)
    log.info("instalando %s (%s) en %s", det.path, det.subtype, app.id)
    rc, _ = pfx.run_umu(app, cfg, argv, cwd=det.path.parent, install_path=det.path.parent)

    # 1ª pasada: en cuanto termina el instalador
    created = shortcuts.register(app, shortcuts.new_from_lnks(app.prefix_path, before, shortcuts.take_snapshot(app.prefix_path)))
    # 2ª pasada: cuando no queda nada vivo (bootstrappers que se relanzan, o la
    # app que el instalador abrió al terminar)
    if pfx.is_busy(app.prefix_path):
        pfx.wait_idle(app.prefix_path)
    after = shortcuts.take_snapshot(app.prefix_path)
    created += shortcuts.register(app, shortcuts.new_from_lnks(app.prefix_path, before, after))

    if not created:
        exes = shortcuts.new_exes(app.prefix_path, before, after)
        nothing_new = not (after.exes - before.exes) and not (set(after.lnks) - set(before.lnks))
        if nothing_new:
            if is_new:
                discard_app(app)
                ui.notify(f"La instalación de «{name}» se canceló o no instaló nada.")
            else:
                ui.notify(f"«{name}»: el instalador no añadió programas nuevos.")
            log.info("instalación sin cambios (rc=%s)", rc)
            return rc
        chosen = _choose_exes(name, exes)
        created += shortcuts.register(app, chosen)

    if created:
        names = ", ".join(e.name for e in created)
        ui.notify(f"«{name}» instalado. Ya está en el menú de aplicaciones: {names}")
    elif not app.entries:
        ui.info(
            f"«{name}» se ha instalado, pero no se encontró su programa principal.\n"
            f"Puedes crear el acceso con:\n  malive shortcuts {app.id} --all"
        )
    return 0


def _choose_exes(name: str, exes: list[shortcuts.Candidate]) -> list[shortcuts.Candidate]:
    if not exes:
        return []
    if len(exes) == 1:
        return exes
    items = [(str(i), f"{c.name}  ({c.exe.parent.name})", i == 0) for i, c in enumerate(exes[:30])]
    picked = ui.checklist(
        f"«{name}» no creó accesos directos. ¿Qué programas quieres en el menú?", items
    )
    if picked is None:
        return exes[:1]  # sin GUI o cancelado: el más grande
    return [exes[int(i)] for i in picked]


# --------------------------------------------------------------------------
# Punto de entrada por fichero
# --------------------------------------------------------------------------

def run_file(path: Path, args: list[str]) -> int:
    path = path.expanduser()
    if not path.is_file():
        raise UserError(f"No existe el fichero:\n{path}")
    path = path.resolve()
    det = detect.detect(path)
    log.info("detectado %s: kind=%s subtype=%s name=%r", path, det.kind, det.subtype, det.name)

    root = pfx.find_prefix_root(path)
    if root is not None:
        app = config.find_by_prefix(root)
        if app is None:
            raise UserError(
                "Este programa está dentro de un entorno de Wine que MaLiVe no gestiona:\n"
                f"{root}\n\nÁbrelo con el programa que creó ese entorno (Lutris, Bottles, Steam…)."
            )
        if det.kind == "lnk":
            return _run_lnk_in_app(app, det)
        if det.is_installer:
            return install(det, args, app)
        if det.kind == "app":
            return launch(app, path, args)
        raise UserError(f"No se puede ejecutar {path.name}: {det.reason}")

    if det.kind == "lnk":
        target = det.shortcut.target if det.shortcut else None
        raise UserError(
            "Este acceso directo de Windows no pertenece a ningún programa instalado con MaLiVe"
            + (f":\n{target}" if target else ".")
        )
    if det.is_installer:
        return install(det, args)
    if det.kind == "app":
        return run_portable(det, args)
    if det.kind == "dos":
        raise UserError(
            f"«{path.name}» es un programa de MS-DOS o de Windows de 16 bits.\n"
            "MaLiVe no los soporta; prueba con DOSBox."
        )
    raise UserError(f"No se puede ejecutar «{path.name}»: {det.reason}")


def _run_lnk_in_app(app: App, det: detect.Detection) -> int:
    cand = shortcuts.candidate_from_lnk(app.prefix_path, det.path)
    sc = det.shortcut
    if cand is None and sc and sc.target:
        exe = pfx.windows_to_unix(app.prefix_path, sc.target)
        if exe and exe.is_file():
            cand = shortcuts.Candidate(name=det.path.stem, exe=exe, args=shortcuts._split_args(sc.arguments))
    if cand is None:
        raise UserError(f"No se pudo resolver el destino de «{det.path.name}».")
    return launch(app, cand.exe, cand.args, cand.workdir)


def run_portable(det: detect.Detection, args: list[str]) -> int:
    cfg = config.load_global()
    app = config.find_portable_by_dir(det.path.parent)
    if app is None:
        app = _new_app(det.name or det.path.stem, "portable", det.path, cfg)
    return launch(app, det.path, args, workdir=det.path.parent, source_dir=det.path.parent)


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="malive-run",
        description="Ejecuta o instala un programa de Windows (.exe, .msi, .lnk).",
    )
    p.add_argument("--app", help="id de una app instalada (lo usan los .desktop)")
    p.add_argument("--entry", help="id de la entrada de la app")
    p.add_argument("file", nargs="?", help="fichero a ejecutar")
    p.add_argument("args", nargs=argparse.REMAINDER, help="argumentos para el programa")
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    opts = build_parser().parse_args(argv)
    try:
        if opts.app:
            if not opts.entry:
                raise UserError("Falta --entry")
            return run_entry(opts.app, opts.entry)
        if not opts.file:
            build_parser().print_help()
            return 2
        return run_file(Path(opts.file), opts.args)
    except (UserError, pfx.PrefixError, proton.ProtonError) as e:
        log.warning("%s", e)
        ui.error(str(e))
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        log.error("error inesperado\n%s", tb)
        ui.error(f"Error inesperado en MaLiVe.\nDetalles en: {paths.main_log()}\n\n{tb.splitlines()[-1]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
