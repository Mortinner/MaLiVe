"""Diálogos y notificaciones: kdialog (KDE), zenity (GNOME y resto) o notify-send.

malive-run se lanza desde el gestor de archivos, sin terminal: todo lo que
el usuario deba ver pasa por aquí. Con MALIVE_NO_GUI=1 (tests, scripts) se
escribe en stderr y las preguntas usan el valor por defecto.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
from contextlib import contextmanager

APP_TITLE = "MaLiVe"


def _no_gui() -> bool:
    return os.environ.get("MALIVE_NO_GUI") == "1" or not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


def _is_kde() -> bool:
    return "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper()


def backend() -> str | None:
    if _no_gui():
        return None
    if _is_kde() and shutil.which("kdialog"):
        return "kdialog"
    if shutil.which("zenity"):
        return "zenity"
    if shutil.which("kdialog"):
        return "kdialog"
    return None


def has_zenity() -> bool:
    return not _no_gui() and shutil.which("zenity") is not None


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _can_notify() -> bool:
    # Las notificaciones van por D-Bus: funcionan también desde el temporizador
    # de systemd, que puede no tener DISPLAY.
    if os.environ.get("MALIVE_NO_GUI") == "1" or not shutil.which("notify-send"):
        return False
    return bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS") or os.environ.get("DISPLAY")
                or os.environ.get("WAYLAND_DISPLAY"))


def notify(message: str, title: str = APP_TITLE, icon: str = "application-x-ms-dos-executable") -> None:
    if not _can_notify():
        print(f"[{title}] {message}", file=sys.stderr)
        return
    # Se espera a notify-send (es instantáneo): en segundo plano, systemd o
    # Plasma pueden matarlo al terminar MaLiVe y la notificación no sale.
    try:
        subprocess.run(["notify-send", "-a", APP_TITLE, "-i", icon, title, message],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        print(f"[{title}] {message}", file=sys.stderr)


def notify_action(message: str, actions: dict[str, str], title: str = APP_TITLE,
                  icon: str = "application-x-ms-dos-executable", timeout: int = 8 * 3600) -> str | None:
    """Notificación con botones. Devuelve la clave del botón pulsado o None."""
    if not _can_notify():
        print(f"[{title}] {message}", file=sys.stderr)
        return None
    cmd = ["notify-send", "-a", APP_TITLE, "-i", icon, "--wait"]
    for key, label in actions.items():
        cmd.append(f"--action={key}={label}")
    try:
        r = subprocess.run([*cmd, title, message], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    choice = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    return choice if choice in actions else None


def info(message: str, title: str = APP_TITLE) -> None:
    b = backend()
    if b == "kdialog":
        _run(["kdialog", "--title", title, "--msgbox", message])
    elif b == "zenity":
        _run(["zenity", "--info", "--title", title, "--width=420", "--text", message])
    else:
        notify(message, title)


def error(message: str, title: str = APP_TITLE) -> None:
    b = backend()
    if b == "kdialog":
        _run(["kdialog", "--title", title, "--error", message])
    elif b == "zenity":
        _run(["zenity", "--error", "--title", title, "--width=460", "--text", message])
    else:
        print(f"[{title}] ERROR: {message}", file=sys.stderr)
        notify(message, title, icon="dialog-error")


def question(message: str, title: str = APP_TITLE, default: bool = False,
             yes: str | None = None, no: str | None = None) -> bool:
    b = backend()
    if b == "kdialog":
        labels = (["--yes-label", yes] if yes else []) + (["--no-label", no] if no else [])
        return _run(["kdialog", "--title", title, *labels, "--warningyesno" if yes else "--yesno", message]).returncode == 0
    if b == "zenity":
        labels = ([f"--ok-label={yes}"] if yes else []) + ([f"--cancel-label={no}"] if no else [])
        return _run(["zenity", "--question", "--title", title, "--width=420", *labels, "--text", message]).returncode == 0
    if sys.stdin.isatty() and os.environ.get("MALIVE_NO_GUI") != "1":
        answer = input(f"{message} [{'S/n' if default else 's/N'}] ").strip().lower()
        return default if not answer else answer in ("s", "si", "sí", "y", "yes")
    return default


def checklist(message: str, items: list[tuple[str, str, bool]], title: str = APP_TITLE) -> list[str] | None:
    """items: (id, etiqueta, marcado). Devuelve ids elegidos o None si se cancela/no hay GUI."""
    b = backend()
    if b == "kdialog":
        cmd = ["kdialog", "--title", title, "--separate-output", "--checklist", message]
        for ident, label, on in items:
            cmd += [ident, label, "on" if on else "off"]
        r = _run(cmd)
        return [l for l in r.stdout.splitlines() if l] if r.returncode == 0 else None
    if b == "zenity":
        cmd = ["zenity", "--list", "--checklist", "--title", title, "--text", message,
               "--width=560", "--height=380", "--column", "", "--column", "id",
               "--column", "Programa", "--hide-column=2", "--print-column=2", "--separator=\n"]
        for ident, label, on in items:
            cmd += ["TRUE" if on else "FALSE", ident, label]
        r = _run(cmd)
        return [l for l in r.stdout.splitlines() if l] if r.returncode == 0 else None
    return None


class _Progress:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.kdialog_ref: tuple[str, str] | None = None

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.write("100\n")
                    self.proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
        if self.kdialog_ref:
            service, path = self.kdialog_ref
            if shutil.which("busctl"):
                r = _run(["busctl", "--user", "call", service, path, "org.kde.kdialog.ProgressDialog", "close"])
                if r.returncode == 0:
                    return
            m = re.search(r"kdialog-(\d+)", service)
            if m:
                try:
                    os.kill(int(m.group(1)), signal.SIGTERM)
                except OSError:
                    pass


@contextmanager
def progress(message: str, title: str = APP_TITLE):
    """Diálogo de progreso indeterminado ("Preparando…") mientras dura el bloque."""
    p = _Progress()
    b = backend()
    try:
        if b == "zenity":
            p.proc = subprocess.Popen(
                ["zenity", "--progress", "--pulsate", "--no-cancel", "--auto-close",
                 "--title", title, "--width=420", "--text", message],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
            )
        elif b == "kdialog":
            r = _run(["kdialog", "--title", title, "--progressbar", message, "0"])
            parts = r.stdout.split()
            if r.returncode == 0 and len(parts) >= 2:
                p.kdialog_ref = (parts[0], parts[1])
        else:
            notify(message, title)
    except OSError:
        pass
    try:
        yield p
    finally:
        p.close()
