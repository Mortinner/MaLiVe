"""Instalar complementos (códecs, bibliotecas, plugins) dentro de un programa ya
instalado, en vez de como programa nuevo.

MaLiVe no puede saber con certeza a qué programa va un complemento: propone el
más probable y el usuario confirma. Pistas:
  1. qué programas de MaLiVe están abiertos (o se usaron hace un momento);
  2. si el instalador es un complemento conocido (LAV Filters, Visual C++…);
  3. para qué sirve (los códecs encajan con los programas que abren vídeo/audio).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import assoc, config, ui
from . import prefix as pfx
from .config import App
from .i18n import T

log = logging.getLogger("malive")

NEW = "__new__"
RECENT = timedelta(minutes=15)

MEDIA = "media"
RUNTIME = "runtime"

_PATTERNS: list[tuple[str, re.Pattern]] = [
    (MEDIA, re.compile(
        r"\blav ?filters\b|\blav (video|audio|splitter)\b|k-?lite|\bcodecs?\b|ffdshow|madvr|"
        r"vsfilter|xy-?subfilter|haali|directvobsub|opencodec", re.I)),
    (RUNTIME, re.compile(
        r"visual c\+\+.*redist|vc_?redist|vcredist|\.net (framework|desktop runtime|runtime)|"
        r"windowsdesktop-runtime|dotnet-runtime|\bndp\d|directx|dxsetup|dxwebsetup|"
        r"\bopenal\b|physx|xna framework", re.I)),
]


@dataclass
class Addon:
    family: str  # media | runtime
    name: str

    @property
    def label(self) -> str:
        if self.family == MEDIA:
            return T("códecs o filtros de vídeo y audio", "video and audio codecs or filters")
        return T("componente de Windows (bibliotecas)", "Windows component (libraries)")


def classify(det) -> Addon | None:
    """¿Es el instalador un complemento conocido?"""
    v = det.version or {}
    text = " ".join([det.name or "", det.path.name, v.get("ProductName", ""), v.get("FileDescription", "")])
    for family, rx in _PATTERNS:
        if rx.search(text):
            return Addon(family, det.name or det.path.stem)
    return None


def is_media_app(app: App) -> bool:
    for e in app.entries:
        win = pfx.unix_to_windows(app.prefix_path, Path(e.exe))
        if any(m.startswith(("video/", "audio/")) for m in assoc.mimetypes_for(app.prefix_path, win)):
            return True
    return False


def used_recently(app: App, now: datetime | None = None) -> bool:
    if not app.last_used:
        return False
    try:
        t = datetime.fromisoformat(app.last_used)
    except ValueError:
        return False
    return (now or datetime.now(timezone.utc)) - t < RECENT


def _last_used_key(app: App) -> str:
    return app.last_used or ""


def ask(det, force: bool = False) -> list[str] | None:
    """Decide dónde instalar. Devuelve:
      []            → como programa nuevo (lo de siempre, sin preguntar);
      [ids…, NEW?]  → dentro de esos programas (y/o como programa nuevo);
      None          → el usuario canceló.
    force: preguntar siempre (opción «Instalar dentro de un programa…»).

    Complemento reconocido (idea de Marcos): si no hay duda, sin preguntar.
      - candidatos: para códecs, los programas que abren vídeo/audio; si no, todos;
      - si de los candidatos solo uno está abierto → ese;
      - si no hay ninguno abierto y solo hay un candidato (p. ej. un único
        reproductor) → ese;
      - si hay varios → se pregunta, con el último usado marcado.
    Instalador desconocido: nunca se adivina; si hay algún programa abierto se
    pregunta por si es un complemento, con «programa nuevo» marcado."""
    apps = [a for a in config.all_apps() if a.entries or a.kind == "installed"]
    if not apps:
        if force:
            ui.info(T("Todavía no hay programas instalados con MaLiVe; se instalará como programa nuevo.",
                      "There are no programs installed with MaLiVe yet; it will be installed as a new program."))
        return []
    addon = classify(det)
    running = {a.id for a in apps if pfx.is_busy(a.prefix_path)}
    media = {a.id for a in apps if is_media_app(a)} if addon and addon.family == MEDIA else set()

    preselect: str | None = None
    if addon:
        pool = [a for a in apps if a.id in media] if media else apps
        open_pool = [a for a in pool if a.id in running]
        if not force:
            if len(open_pool) == 1:
                log.info("complemento %s → %s (único candidato abierto)", det.path.name, open_pool[0].id)
                return [open_pool[0].id]
            if not open_pool and media and len(pool) == 1:
                log.info("complemento %s → %s (único reproductor)", det.path.name, pool[0].id)
                return [pool[0].id]
        best = max(open_pool or pool, key=_last_used_key)
        preselect = best.id
    elif not force and not running:
        return []

    def hint(a: App) -> str:
        h = []
        if a.id in running:
            h.append(T("abierto ahora", "open now"))
        elif used_recently(a):
            h.append(T("usado hace un momento", "used a moment ago"))
        if a.id in media:
            h.append(T("reproduce vídeo o audio", "plays video or audio"))
        return ", ".join(h)

    ordered = sorted(apps, key=lambda a: (a.id != preselect, a.id not in media, a.id not in running,
                                          not used_recently(a), a.name.lower()))
    items = [(a.id, f"{a.name}  ({hint(a)})" if hint(a) else a.name, a.id == preselect) for a in ordered]
    items.append((NEW, T("Instalarlo como programa nuevo", "Install it as a new program"), preselect is None))

    if ui.backend() is None:
        picked = [preselect] if preselect else []
        log.info("sin interfaz gráfica: destino %s", picked or "programa nuevo")
        return picked

    name = det.name or det.path.stem
    if addon:
        msg = T(f"«{name}» parece un complemento ({addon.label}), no un programa.\n"
                "¿En qué programas quieres instalarlo?",
                f"«{name}» looks like an add-on ({addon.label}), not a program.\n"
                "Which programs do you want to install it into?")
    else:
        msg = T(f"¿Dónde quieres instalar «{name}»?\n"
                "Si es un complemento (códecs, plugins…) de un programa que ya tienes, márcalo.",
                f"Where do you want to install «{name}»?\n"
                "If it is an add-on (codecs, plugins…) for a program you already have, tick it.")
    picked = ui.checklist(msg, items, title=T("Instalar", "Install"))
    if not picked:
        return None
    return picked
