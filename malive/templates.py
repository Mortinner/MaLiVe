"""Plantillas de contenedor, al estilo de las plantillas de VMware (idea de Marcos).

Una «HAL» que se hace una vez: un Windows base con lo que trae cualquier
Windows normal (fuentes estándar, suavizado tipo ClearType, runtimes de
Visual C++) se prepara una sola vez por versión de Proton. Cada programa nuevo
nace de una copia suya, así que no hay que configurar nada por programa y el
contenedor está listo al instante.

En la carpeta de cada versión de Proton:
  Template_Mono.mlv         la plantilla (Mono hace de .NET, viene con Proton)
  Template_Mono_to_use.mlv  una copia ya preparada para el siguiente programa

Al instalar un programa, la copia preparada se *mueve* a su contenedor (mv:
instantáneo en el mismo disco) y en segundo plano se prepara otra. Si no hay
copia preparada, se copia la plantilla en el momento (cp --reflink: instantáneo
en btrfs/xfs, una copia normal en ext4). Si tampoco hay plantilla (Proton
recién descargado), se crea el contenedor como siempre y la plantilla se
prepara en segundo plano para la próxima vez.

Lo que depende del hardware (NVIDIA, unidades) no va en la plantilla: se
aplica en cada arranque, así sirve aunque cambie el equipo.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import paths
from .i18n import T

log = logging.getLogger("malive")

MARKER = ".malive-template.json"
# Lo que lleva cada tipo de plantilla (verbos de winetricks)
KINDS: dict[str, dict] = {
    "mono": {
        "label": "Mono",
        "verbs": ["corefonts", "fontsmooth=rgb", "vcrun2022"],
    },
}
DEFAULT_KIND = "mono"


def _disabled() -> bool:
    return os.environ.get("MALIVE_NO_TEMPLATES") == "1"


def root() -> Path:
    return paths.data_dir() / "templates"


def _label(kind: str) -> str:
    return KINDS.get(kind, {}).get("label", kind.capitalize())


def base_path(proton_name: str, kind: str = DEFAULT_KIND) -> Path:
    return root() / proton_name / f"Template_{_label(kind)}.mlv"


def spare_path(proton_name: str, kind: str = DEFAULT_KIND) -> Path:
    return root() / proton_name / f"Template_{_label(kind)}_to_use.mlv"


def _marker(d: Path) -> dict | None:
    try:
        return json.loads((d / MARKER).read_text())
    except (OSError, ValueError):
        return None


def base_ready(proton_name: str, kind: str = DEFAULT_KIND) -> bool:
    return _marker(base_path(proton_name, kind)) is not None


def spare_ready(proton_name: str, kind: str = DEFAULT_KIND) -> bool:
    """La copia preparada vale si es de la plantilla actual (no de una anterior)."""
    b, s = _marker(base_path(proton_name, kind)), _marker(spare_path(proton_name, kind))
    return b is not None and s is not None and s.get("built") == b.get("built")


def copy_tree(src: Path, dst: Path) -> None:
    """Copia un contenedor entero, compartiendo bloques si el disco lo permite."""
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    r = subprocess.run(["cp", "-a", "--reflink=auto", str(src), str(tmp)], capture_output=True, text=True)
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise OSError(r.stderr.strip() or f"cp terminó con código {r.returncode}")
    os.replace(tmp, dst)


def _prepare_dest(dest: Path) -> bool:
    """El contenedor de destino no debe existir (o estar vacío)."""
    if dest.is_dir() and not any(dest.iterdir()):
        dest.rmdir()
    return not dest.exists()


def take(proton_name: str, dest: Path, kind: str = DEFAULT_KIND) -> str | None:
    """Da a `dest` un contenedor sacado de la plantilla. Devuelve cómo
    («repuesto», «copia») o None si no hay plantilla para esa versión."""
    if _disabled() or not base_ready(proton_name, kind) or not _prepare_dest(dest):
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _lock(proton_name):
        if spare_ready(proton_name, kind):
            try:
                os.rename(spare_path(proton_name, kind), dest)  # mv: instantáneo
                how = "repuesto"
            except OSError as e:  # otro disco: se copia la plantilla
                log.warning("no se pudo mover el repuesto (%s); se copia la plantilla", e)
                how = None
        else:
            how = None
        if how is None:
            copy_tree(base_path(proton_name, kind), dest)
            how = "copia"
    (dest / MARKER).unlink(missing_ok=True)
    schedule("refill", proton_name, kind)
    log.info("contenedor %s sacado de la plantilla %s/%s (%s)", dest, proton_name, kind, how)
    return how


class _lock:
    """Cerrojo por versión de Proton: que dos instalaciones a la vez no se
    lleven el mismo repuesto ni dos rellenos pisen el mismo fichero."""

    def __init__(self, proton_name: str):
        d = root() / proton_name
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / ".lock"

    def __enter__(self):
        self.f = self.path.open("w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()
        return False


def refill(proton_name: str, kind: str = DEFAULT_KIND) -> bool:
    """Prepara la copia de repuesto si falta (o es de una plantilla anterior)."""
    if not base_ready(proton_name, kind):
        return False
    with _lock(proton_name):
        if spare_ready(proton_name, kind):
            return False
        spare = spare_path(proton_name, kind)
        shutil.rmtree(spare, ignore_errors=True)
        copy_tree(base_path(proton_name, kind), spare)
    log.info("repuesto de la plantilla %s/%s preparado", proton_name, kind)
    return True


def build(proton_name: str, kind: str = DEFAULT_KIND, force: bool = False) -> Path:
    """Crea la plantilla: un contenedor nuevo con Proton más los componentes
    comunes. Tarda unos minutos (descarga fuentes y runtimes); se hace una vez."""
    from . import config
    from . import prefix as pfx

    base = base_path(proton_name, kind)
    if base_ready(proton_name, kind) and not force:
        return base
    work = base.with_name(base.name + ".building")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    app = config.App(id=f"_template-{kind}", name=f"Template {_label(kind)}", kind="portable",
                     prefix=str(work), proton=proton_name)
    cfg = config.load_global()
    try:
        rc, _ = pfx.run_umu(app, cfg, [""], cwd=work, verb="waitforexitandrun", log_mode="w")
        if not pfx.is_created(work):
            raise OSError(T(f"Proton no pudo crear la plantilla (código {rc})",
                            f"Proton could not create the template (code {rc})"))
        verbs = KINDS[kind]["verbs"]
        if verbs:
            rc, _ = pfx.run_umu(app, cfg, ["winetricks", "-q", *verbs], cwd=work)
            if rc != 0:
                raise OSError(T(f"winetricks terminó con código {rc} ({' '.join(verbs)})",
                                f"winetricks exited with code {rc} ({' '.join(verbs)})"))
        pfx.wait_idle(work, timeout=120)
        (work / MARKER).write_text(json.dumps({
            "proton": proton_name, "kind": kind, "verbs": verbs, "built": time.time(),
        }))
        with _lock(proton_name):
            shutil.rmtree(spare_path(proton_name, kind), ignore_errors=True)
            old = base.with_name(base.name + ".old")
            shutil.rmtree(old, ignore_errors=True)
            if base.exists():
                os.replace(base, old)
            os.replace(work, base)
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    log.info("plantilla %s/%s creada", proton_name, kind)
    refill(proton_name, kind)
    return base


def cleanup(keep: set[str]) -> list[str]:
    """Borra las plantillas de versiones de Proton que ya no se usan para apps nuevas."""
    removed = []
    if not root().is_dir():
        return removed
    for d in root().iterdir():
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    return removed


def status() -> list[tuple[str, str, bool, bool]]:
    """[(proton, tipo, plantilla lista, repuesto listo)]"""
    out = []
    if root().is_dir():
        for d in sorted(root().iterdir()):
            if d.is_dir():
                for kind in KINDS:
                    out.append((d.name, kind, base_ready(d.name, kind), spare_ready(d.name, kind)))
    return out


def _cli() -> list[str]:
    from .integrate import malive_cli_path
    cli = malive_cli_path()
    if Path(cli).is_file():
        return [cli]
    return [sys.executable, "-m", "malive.cli"]


def schedule(action: str, proton_name: str, kind: str = DEFAULT_KIND) -> None:
    """Lanza en segundo plano `malive template ACTION`, que sigue aunque se
    cierre el programa que lo pidió."""
    if _disabled():
        return
    if os.environ.get("MALIVE_TEMPLATES_SYNC") == "1":  # tests
        (build if action == "build" else refill)(proton_name, kind)
        return
    try:
        subprocess.Popen([*_cli(), "template", action, "--proton", proton_name, "--kind", kind],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as e:
        log.warning("no se pudo lanzar «template %s» en segundo plano: %s", action, e)
