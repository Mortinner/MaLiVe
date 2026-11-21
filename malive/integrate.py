"""Integración con el escritorio: asociación MIME (.exe/.msi/.lnk -> malive-run)
y temporizador systemd de usuario para actualizar GE-Proton.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import paths
from .shortcuts import _desktop_escape, exec_quote, malive_run_path, refresh_menus

DESKTOP_ID = "malive.desktop"
MIME_TYPES = [
    "application/x-ms-dos-executable",
    "application/x-msdownload",
    "application/vnd.microsoft.portable-executable",
    "application/x-dosexec",
    "application/x-msi",
    "application/x-ms-shortcut",
]
DEFAULTS_SECTION = "[Default Applications]"
ADDED_SECTION = "[Added Associations]"
TIMER = "malive-proton-update"


def _previous_file() -> Path:
    return paths.config_dir() / "previous-defaults.json"


# --------------------------------------------------------------------------
# mimeapps.list (editor por líneas que respeta el resto del fichero)
# --------------------------------------------------------------------------

def _read_sections(path: Path) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = [("", [])]
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("[") and line.strip().endswith("]"):
                sections.append((line.strip(), []))
            else:
                sections[-1][1].append(line)
    return sections


def _write_sections(path: Path, sections: list[tuple[str, list[str]]]) -> None:
    out: list[str] = []
    for header, lines in sections:
        if header:
            if out and out[-1] != "":
                out.append("")
            out.append(header)
        while lines and lines[-1] == "":
            lines = lines[:-1]
        out.extend(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out).lstrip("\n") + "\n", encoding="utf-8")


def _section(sections, header) -> list[str]:
    for h, lines in sections:
        if h == header:
            return lines
    lines: list[str] = []
    sections.append((header, lines))
    return lines


def _get(lines: list[str], key: str) -> str | None:
    for line in lines:
        if "=" in line and line.split("=", 1)[0].strip() == key:
            return line.split("=", 1)[1].strip()
    return None


def _set(lines: list[str], key: str, value: str | None) -> None:
    for i, line in enumerate(lines):
        if "=" in line and line.split("=", 1)[0].strip() == key:
            if value is None:
                del lines[i]
            else:
                lines[i] = f"{key}={value}"
            return
    if value is not None:
        lines.append(f"{key}={value}")


def set_default_handler(mimes: list[str] = MIME_TYPES) -> None:
    mf = paths.mimeapps_file()
    sections = _read_sections(mf)
    defaults = _section(sections, DEFAULTS_SECTION)
    added = _section(sections, ADDED_SECTION)

    prev_path = _previous_file()
    previous = json.loads(prev_path.read_text()) if prev_path.is_file() else {}
    for mime in mimes:
        current = _get(defaults, mime)
        if mime not in previous and current and DESKTOP_ID not in current:
            previous[mime] = current
        _set(defaults, mime, f"{DESKTOP_ID};")
        items = [x for x in (_get(added, mime) or "").split(";") if x and x != DESKTOP_ID]
        _set(added, mime, ";".join([DESKTOP_ID, *items]) + ";")
    _write_sections(mf, sections)
    prev_path.parent.mkdir(parents=True, exist_ok=True)
    prev_path.write_text(json.dumps(previous, indent=2))


def unset_default_handler(mimes: list[str] = MIME_TYPES) -> None:
    mf = paths.mimeapps_file()
    if not mf.is_file():
        return
    sections = _read_sections(mf)
    defaults = _section(sections, DEFAULTS_SECTION)
    added = _section(sections, ADDED_SECTION)
    prev_path = _previous_file()
    previous = json.loads(prev_path.read_text()) if prev_path.is_file() else {}
    for mime in mimes:
        current = _get(defaults, mime)
        if current and DESKTOP_ID in current:
            _set(defaults, mime, previous.get(mime))
        items = [x for x in (_get(added, mime) or "").split(";") if x and x != DESKTOP_ID]
        _set(added, mime, ";".join(items) + ";" if items else None)
    _write_sections(mf, sections)
    prev_path.unlink(missing_ok=True)


def handler_desktop_text() -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=MaLiVe",
        "GenericName=Ejecutar programa de Windows",
        "Comment=Ejecuta e instala programas de Windows con doble clic",
        f"Exec={_desktop_escape(exec_quote(malive_run_path()))} %f",
        "Icon=application-x-ms-dos-executable",
        "Terminal=false",
        "NoDisplay=true",
        "MimeType=" + ";".join(MIME_TYPES) + ";",
        "Categories=System;",
        "",
    ])


def install_handler() -> Path:
    d = paths.applications_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / DESKTOP_ID
    path.write_text(handler_desktop_text(), encoding="utf-8")
    set_default_handler()
    refresh_menus()
    return path


def remove_handler() -> None:
    unset_default_handler()
    (paths.applications_dir() / DESKTOP_ID).unlink(missing_ok=True)
    refresh_menus()


def is_integrated() -> bool:
    if not (paths.applications_dir() / DESKTOP_ID).is_file():
        return False
    sections = _read_sections(paths.mimeapps_file())
    defaults = _section(sections, DEFAULTS_SECTION)
    return all(DESKTOP_ID in (_get(defaults, m) or "") for m in MIME_TYPES[:2])


# --------------------------------------------------------------------------
# Temporizador systemd --user
# --------------------------------------------------------------------------

def malive_cli_path() -> str:
    sibling = Path(malive_run_path()).parent / "malive"
    if sibling.is_file():
        return str(sibling)
    found = shutil.which("malive")
    return found or str(Path.home() / ".local/bin/malive")


def install_timer() -> bool:
    d = paths.systemd_user_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{TIMER}.service").write_text(
        "[Unit]\n"
        "Description=MaLiVe: descargar la última versión de GE-Proton\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={malive_cli_path()} proton update --quiet\n"
        "Nice=10\n"
        "IOSchedulingClass=idle\n"
    )
    (d / f"{TIMER}.timer").write_text(
        "[Unit]\n"
        "Description=MaLiVe: comprobar GE-Proton semanalmente\n\n"
        "[Timer]\n"
        "OnCalendar=weekly\n"
        "RandomizedDelaySec=6h\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    if not shutil.which("systemctl"):
        return False
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    r = subprocess.run(["systemctl", "--user", "enable", "--now", f"{TIMER}.timer"], capture_output=True)
    return r.returncode == 0


def remove_timer() -> None:
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{TIMER}.timer"], capture_output=True)
    d = paths.systemd_user_dir()
    for suffix in (".service", ".timer"):
        (d / f"{TIMER}{suffix}").unlink(missing_ok=True)
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
