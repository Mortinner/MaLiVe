"""Configuración global y registro de apps (un JSON por app)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import paths

DEFAULTS = {
    # Sustituye la unidad Z: (raíz del sistema) por una carpeta vacía y
    # comparte solo las carpetas de shared_folders.
    "isolate": True,
    # Nombres de xdg-user-dir o rutas absolutas.
    "shared_folders": ["DOCUMENTS", "DOWNLOAD"],
    # Comprobar semanalmente si hay un GE-Proton nuevo (solo para apps nuevas).
    "proton_auto_update": True,
    # Instalar solas las versiones nuevas de MaLiVe (y avisar después).
    "auto_update": True,
    # GE-Proton descargados que se conservan aunque ninguna app los use.
    "proton_keep_unused": 1,
    "gameid": "umu-default",
}


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def load_global() -> dict:
    cfg = dict(DEFAULTS)
    f = paths.config_file()
    if f.is_file():
        try:
            cfg.update(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save_global(cfg: dict) -> None:
    _write_json_atomic(paths.config_file(), cfg)


def xdg_user_dir(name: str) -> Path | None:
    if os.path.isabs(name):
        return Path(name)
    if shutil.which("xdg-user-dir"):
        try:
            out = subprocess.run(["xdg-user-dir", name], capture_output=True, text=True, timeout=5).stdout.strip()
            if out and Path(out) != Path.home():
                return Path(out)
        except (OSError, subprocess.TimeoutExpired):
            pass
    fallback = {"DOCUMENTS": "Documents", "DOWNLOAD": "Downloads", "DESKTOP": "Desktop",
                "PICTURES": "Pictures", "MUSIC": "Music", "VIDEOS": "Videos"}
    if name in fallback:
        return Path.home() / fallback[name]
    return None


def shared_folders(cfg: dict) -> list[Path]:
    out: list[Path] = []
    for name in cfg.get("shared_folders", []):
        p = xdg_user_dir(name)
        if p and p.is_dir() and p not in out:
            out.append(p)
    return out


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"\+", "plus", text)
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:48] or "app"


@dataclass
class Entry:
    id: str
    name: str
    exe: str  # ruta Unix dentro del prefijo
    args: list[str] = field(default_factory=list)
    workdir: str | None = None
    desktop: str | None = None  # ruta del .desktop creado
    icon: str | None = None


@dataclass
class App:
    id: str
    name: str
    kind: str  # installed | portable
    prefix: str
    proton: str  # nombre de la versión fijada, p. ej. GE-Proton10-25
    source: str = ""  # instalador o ejecutable original
    created: str = ""
    isolate: bool | None = None  # None = usar el valor global
    last_used: str = ""  # último lanzamiento (ISO); para proponer destino a los complementos
    entries: list[Entry] = field(default_factory=list)

    @property
    def prefix_path(self) -> Path:
        return Path(self.prefix)

    def entry(self, entry_id: str) -> Entry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def effective_isolate(self, cfg: dict) -> bool:
        return cfg.get("isolate", True) if self.isolate is None else self.isolate

    def save(self) -> None:
        _write_json_atomic(paths.apps_dir() / f"{self.id}.json", asdict(self))

    def delete_record(self) -> None:
        (paths.apps_dir() / f"{self.id}.json").unlink(missing_ok=True)

    @classmethod
    def from_dict(cls, d: dict) -> "App":
        entries = [Entry(**e) for e in d.pop("entries", [])]
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(entries=entries, **known)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_app(app_id: str) -> App | None:
    f = paths.apps_dir() / f"{app_id}.json"
    if not f.is_file():
        return None
    return App.from_dict(json.loads(f.read_text(encoding="utf-8")))


def all_apps() -> list[App]:
    d = paths.apps_dir()
    if not d.is_dir():
        return []
    apps = []
    for f in sorted(d.glob("*.json")):
        try:
            apps.append(App.from_dict(json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return apps


def find_by_prefix(prefix: Path) -> App | None:
    prefix = prefix.resolve()
    for app in all_apps():
        if Path(app.prefix).resolve() == prefix:
            return app
    return None


def find_installed_by_name(name: str) -> App | None:
    """Busca la app ignorando versión y arquitectura, para que instalar la
    versión nueva de un programa reutilice su entorno."""
    from .detect import clean_product_name

    key = clean_product_name(name).casefold()
    for app in all_apps():
        if app.kind == "installed" and clean_product_name(app.name).casefold() == key:
            return app
    return None


def find_portable_by_dir(directory: Path) -> App | None:
    directory = directory.resolve()
    for app in all_apps():
        if app.kind == "portable" and app.source and Path(app.source).resolve().parent == directory:
            return app
    return None


def new_app_id(name: str) -> str:
    base = slugify(name)
    candidate = base
    n = 2
    while (paths.apps_dir() / f"{candidate}.json").exists() or (paths.prefixes_dir() / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate
